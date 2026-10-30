"""Idle-only shared galleries. ComfyUI remains the sole image execution authority."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..demo_reel_models import DemoReel, DemoReelConfig, DemoReelEntry
from ..demo_reel_schemas import DemoReelUpdate
from ..domain.checkpoint_identity import checkpoint_identity_v1
from ..domain.publication import publication_kind
from ..errors import AppError
from ..models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AutoGeneration,
    Collection,
    ExpectationCheck,
    Favorite,
    Generation,
    GenerationPreparation,
    GenerationStatus,
    LoraOperation,
    PromptGenerationRun,
    User,
    UserPreference,
    UserRole,
    UserState,
    WorkflowProfile,
    WorkflowState,
    utcnow,
)
from ..schemas import GenerationCreate, SourceRevision
from .demo_reel_activity import IDLE_SECONDS, SYSTEM_OWNER_ID, record_activity
from .lora_groups import grouping
from .lora_library import Library, LibraryMember, catalog_identities, libraries, library_key
from .lora_thumbnails import refresh as refresh_thumbnails
from .user_state import lock_user_state

if TYPE_CHECKING:
    from ..container import AppContainer

logger = logging.getLogger(__name__)

# How often the idle loop re-checks every LoRA's display image against its reel. A
# favorite refreshes its own LoRA immediately, so this only has to catch deletions,
# invalidations, and settings changes.
THUMBNAIL_SWEEP_SECONDS = 20.0


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def render_revision(profile: WorkflowProfile) -> str:
    """Ignore catalog growth and descriptive metadata; retain executable graph changes."""
    graph = copy.deepcopy(profile.source_api_json)
    for node in graph.values():
        node.pop("_meta", None)
        inputs = node.get("inputs", {})
        kind = node.get("class_type", "")
        if kind.startswith("CIF"):
            for field in ("label", "description", "group", "order", "advanced"):
                inputs.pop(field, None)
        if kind == "CIFLoraStack":
            inputs.pop("catalog_json", None)
            inputs.pop("value", None)
        elif kind.startswith("CIF") and kind.endswith("Parameter"):
            inputs.pop("value", None)
            inputs.pop("options_json", None)
    return digest(graph)


def checkpoint_choices(member: LibraryMember | WorkflowProfile) -> dict[str, tuple[str, str, str]]:
    profile = member.profile if isinstance(member, LibraryMember) else member
    for control in profile.resolved_contract_json.get("inputs", []):
        if control.get("type") != "choice" or not (
            control.get("semantic_role") in {"model", "checkpoint"}
            or control.get("id") == "checkpoint"
        ):
            continue
        choices = {}
        for option in control.get("choices", []):
            value = option.get("value", option.get("id"))
            identity = checkpoint_identity_v1(
                control, value, profile.source_api_json, str(profile.source_key)
            )
            if identity:
                choices[identity] = (control["id"], value, option.get("label", value))
        return choices
    return {}


@dataclass
class Candidate:
    reel: DemoReel
    slot: dict[str, Any]
    checkpoint_id: str
    checkpoint_label: str
    signature: str
    member: LibraryMember | None
    parameters: dict[str, Any]
    error: str | None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.reel.id, self.slot["id"], self.checkpoint_id


class DemoReelService:
    def __init__(self, container: AppContainer) -> None:
        self.container = container
        self.idle_seconds = IDLE_SECONDS
        self.foreground_requests = 0
        self._was_busy = False
        self._quiet_reset_requested = False
        self._configured = False
        self._task: asyncio.Task[None] | None = None
        self._cancellations: set[asyncio.Task[None]] = set()
        self._cancel_keys: set[tuple[str, str]] = set()
        self._thumbnails_at = 0.0

    def config(self, session: Session) -> DemoReelConfig:
        row = session.get(DemoReelConfig, 1)
        if row is None:
            row = DemoReelConfig(id=1)
            session.add(row)
            session.flush()
        if session.get(User, SYSTEM_OWNER_ID) is None:
            session.add(
                User(
                    id=SYSTEM_OWNER_ID,
                    username="Demo Reel system",
                    username_normalized="__demo_reel__",
                    password_hash="!",  # noqa: S106 -- deliberately unusable, login also rejects is_system
                    role=UserRole.USER,
                    is_system=True,
                    must_change_password=False,
                )
            )
            session.flush()
        self._configured = bool(row.slots)
        return row

    async def recover(self) -> None:
        def recover_transaction() -> list[tuple[str, str]]:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                self.config(session)
                targets = record_activity(session)
                session.commit()
                return targets

        self.cancel_targets(await run_blocking(recover_transaction))

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="demo-reels")

    async def stop(self) -> None:
        tasks = list(self._cancellations)
        if self._task:
            tasks.append(self._task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None

    def cancel_targets(self, targets: list[tuple[str, str]]) -> None:
        for instance, prompt in set(targets):
            key = (instance, prompt)
            if key in self._cancel_keys:
                continue
            self._cancel_keys.add(key)
            task = asyncio.create_task(self._cancel(instance, prompt))
            self._cancellations.add(task)
            task.add_done_callback(partial(self._cancel_done, key))

    def _cancel_done(self, key: tuple[str, str], task: asyncio.Task[None]) -> None:
        self._cancel_keys.discard(key)
        self._cancellations.discard(task)

    async def _cancel(self, instance: str, prompt: str) -> None:
        try:
            await self.container.comfyui_instances.get(instance).cancel(prompt, running=True)
        except Exception:
            # Discard is durable even if safe interruption is temporarily unavailable.
            logger.info("demo_reel_interrupt_deferred", extra={"instance_id": instance})

    async def activity(self) -> None:
        if not self._configured:
            return

        def transaction() -> list[tuple[str, str]]:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                targets = record_activity(session)
                session.commit()
                return targets

        self.cancel_targets(await run_blocking(transaction))

    def foreground_finished(self) -> None:
        # A response finalizer may already be inside an AnyIO cancellation scope.
        # Do not start another database await there; the coordinator resets the
        # durable timer before its next admission, and recovery resets it on restart.
        self._quiet_reset_requested = True

    async def _run(self) -> None:
        while True:
            try:
                self.cancel_targets(await run_blocking(self.step))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("demo_reel_step_failed")
            await asyncio.sleep(max(1.0, self.container.settings.dispatch_poll_seconds))

    def _libraries(self, session: Session) -> dict[str, Library]:
        return libraries(session, self.container.comfyui_instances.default_id)

    def _reel(self, session: Session, key: str, identity: str, label: str) -> DemoReel:
        reel = session.scalar(
            select(DemoReel).where(
                DemoReel.library_key == key,
                DemoReel.lora_identity == identity,
            )
        )
        if reel is None:
            collection = Collection(owner_id=SYSTEM_OWNER_ID, name=f"{label[:100]} Demo Reel")
            session.add(collection)
            session.flush()
            reel = DemoReel(library_key=key, lora_identity=identity, collection_id=collection.id)
            session.add(reel)
            session.flush()
        return reel

    def _matrix(self, session: Session, config: DemoReelConfig) -> list[Candidate]:
        preference = (
            session.get(UserPreference, config.rating_user_id) if config.rating_user_id else None
        )
        ratings = preference.checkpoint_tiers_json if preference else {}
        rank_order = list(dict.fromkeys([*ratings.get("A", []), *ratings.get("B", [])]))
        rating_owner = session.get(User, config.rating_user_id) if config.rating_user_id else None
        if rating_owner is None or rating_owner.state != UserState.ACTIVE:
            # Losing the rating account pauses generation; it must not erase existing images.
            rank_order = list(
                session.scalars(
                    select(DemoReelEntry.checkpoint_id)
                    .distinct()
                    .order_by(DemoReelEntry.checkpoint_id)
                )
            )
        result = []
        inventory: dict[str, dict[str, tuple[str, str, str]]] = {}
        for profile in session.scalars(
            select(WorkflowProfile).where(
                WorkflowProfile.instance_id == self.container.comfyui_instances.default_id,
                WorkflowProfile.is_current.is_(True),
                WorkflowProfile.state == WorkflowState.VALID,
            )
        ):
            if publication_kind(profile.resolved_contract_json) == "image":
                inventory.setdefault(library_key(profile.manifest_json)[0], {}).update(
                    checkpoint_choices(profile)
                )
        for key, library in sorted(self._libraries(session).items()):
            recipe = config.recipes.get(key, {})
            member = next(
                (m for m in library.members if m.source_key == recipe.get("source_key")), None
            )
            all_choices = inventory.get(key, {})
            supported = checkpoint_choices(member) if member else {}
            current_render_revision = render_revision(member.profile) if member else None
            identities = catalog_identities(library.canonical)
            groups = grouping(session, key, identities.values())
            group_order = {group["id"]: i for i, group in enumerate(groups["groups"])}
            items = sorted(
                library.canonical,
                key=lambda item: (
                    group_order.get(groups["assignments"].get(identities.get(item["id"])), 0),
                    item["label"].casefold(),
                    item["id"],
                ),
            )
            for item in items:
                identity = identities.get(item["id"])
                if not identity:
                    continue
                reel = self._reel(session, key, identity, item["label"])
                if reel.disabled:
                    # An administrator took this LoRA out of the Demo Reel: no candidates means
                    # its images are reconciled away below, exactly like a retired LoRA's.
                    continue
                for slot in config.slots:
                    for checkpoint_id in rank_order:
                        if checkpoint_id not in all_choices:
                            continue
                        error = None
                        if not member:
                            error = "Choose an available Demo Reel workflow for this model family."
                        elif library.conflicts:
                            error = "Repair the LoRA library conflicts before generating."
                        elif recipe.get("render_revision") != current_render_revision:
                            error = (
                                "Rendering workflow changed. Review and save its Demo Reel recipe."
                            )
                        elif checkpoint_id not in supported:
                            error = (
                                "This A/B checkpoint is not available in the configured workflow."
                            )
                        elif item["id"] not in {entry["id"] for entry in member.catalog}:
                            error = "Sync this LoRA into the configured workflow."
                        parameters = copy.deepcopy(recipe.get("parameters", {}))
                        prompt = slot["prompt"].replace(
                            "{subject}", item.get("trigger_word") or item["label"]
                        )
                        strength = config.strengths.get(key, {}).get(
                            identity, recipe.get("strength", 1.0)
                        )
                        if member:
                            parameters.update(slot.get("resolutions", {}).get(key, {}))
                            for control in member.profile.resolved_contract_json.get("inputs", []):
                                if control.get("semantic_role") == "positive_prompt":
                                    parameters[control["id"]] = prompt
                                if control.get("type") == "seed":
                                    parameters[control["id"]] = None
                                if control.get("type") == "lora_stack":
                                    parameters[control["id"]] = [
                                        {
                                            "id": entry["id"],
                                            "strength": strength
                                            if entry["id"] == item["id"]
                                            else 0,
                                        }
                                        for entry in control["items"]
                                    ]
                            if checkpoint_id in supported:
                                control_id, value, _ = supported[checkpoint_id]
                                parameters[control_id] = value
                        signature = digest(
                            [
                                prompt,
                                slot.get("resolutions", {}).get(key, {}),
                                recipe,
                                strength,
                                checkpoint_id,
                                identity,
                                current_render_revision,
                                error,
                            ]
                        )
                        result.append(
                            Candidate(
                                reel,
                                slot,
                                checkpoint_id,
                                all_choices[checkpoint_id][2],
                                signature,
                                member,
                                parameters,
                                error,
                            )
                        )
        return result

    @staticmethod
    def _busy(session: Session) -> bool:
        queries = [
            select(Generation.id).where(
                Generation.owner_id != SYSTEM_OWNER_ID, Generation.status.in_(ACTIVE_STATUSES)
            ),
            select(AutoGeneration.user_id).where(AutoGeneration.enabled.is_(True)),
            select(GenerationPreparation.id).where(
                GenerationPreparation.status.in_(["preparing", "refining", "ready"])
            ),
            select(PromptGenerationRun.id).where(
                PromptGenerationRun.status.in_(["queued", "dispatching", "submitting", "running"])
            ),
            select(ExpectationCheck.id).where(
                ExpectationCheck.status.in_(["composing", "generating", "evaluating"])
            ),
            select(LoraOperation.id).where(
                LoraOperation.status.in_(["awaiting_upload", "running", "repair_required"])
            ),
        ]
        return any(session.scalar(query.limit(1)) is not None for query in queries)

    def _discard(self, session: Session, entry: DemoReelEntry) -> list[tuple[str, str]]:
        generation = session.get(Generation, entry.generation_id) if entry.generation_id else None
        targets = []
        if generation:
            if generation.status in TERMINAL_STATUSES:
                generation.pending_delete = True
                entry.generation_id = None
            else:
                entry.state = "discarded"
                generation.pending_delete = True
                generation.cancel_requested_at = utcnow()
                if generation.status == GenerationStatus.QUEUED:
                    generation.status = GenerationStatus.CANCELLED_WITHOUT_ARTIFACTS
                    generation.completed_at = utcnow()
                else:
                    generation.status = GenerationStatus.CANCEL_REQUESTED
                if generation.comfyui_instance_id and generation.comfyui_prompt_id:
                    targets.append((generation.comfyui_instance_id, generation.comfyui_prompt_id))
        return targets

    def reconcile(
        self, session: Session, config: DemoReelConfig
    ) -> tuple[list[Candidate], list[tuple[str, str]]]:
        candidates = self._matrix(session, config)
        desired = {candidate.key: candidate for candidate in candidates}
        rows = list(session.scalars(select(DemoReelEntry)))
        existing = {(entry.reel_id, entry.slot_id, entry.checkpoint_id): entry for entry in rows}
        targets = []
        now = utcnow()
        for key, entry in existing.items():
            candidate = desired.get(key)
            obsolete = candidate is None or entry.signature != candidate.signature
            if obsolete or entry.state == "discarded":
                targets.extend(self._discard(session, entry))
                if entry.generation_id and session.get(Generation, entry.generation_id):
                    continue
                entry.generation_id = None
                if candidate is None:
                    session.delete(entry)
                    continue
                entry.signature = candidate.signature
                entry.failures = 0
                entry.next_retry_at = None
                entry.state = "missing"
                entry.error = None
            if entry.state == "running":
                generation = (
                    session.get(Generation, entry.generation_id) if entry.generation_id else None
                )
                if generation is None or generation.status in TERMINAL_STATUSES:
                    if (
                        generation
                        and generation.status == GenerationStatus.SUCCEEDED
                        and generation.canonical_artifact_id
                        and not generation.pending_delete
                    ):
                        entry.state = "ready"
                        entry.error = None
                    else:
                        entry.failures += 1
                        entry.error = (
                            generation.error_message if generation else None
                        ) or "Generation did not produce a final image."
                        targets.extend(self._discard(session, entry))
                        entry.state = "failed" if entry.failures >= 3 else "missing"
                        entry.next_retry_at = now + timedelta(
                            seconds=60 * 5 ** (entry.failures - 1)
                        )
            if candidate and entry.state not in {
                "running",
                "discarded",
                "ready",
                "failed",
                "blocked",
            }:
                entry.state = "blocked" if candidate.error else "missing"
                entry.error = candidate.error
        for key, candidate in desired.items():
            if key not in existing:
                session.add(
                    DemoReelEntry(
                        reel_id=key[0],
                        slot_id=key[1],
                        checkpoint_id=key[2],
                        signature=candidate.signature,
                        state="blocked" if candidate.error else "missing",
                        error=candidate.error,
                    )
                )
        # Retired LoRAs leave no hidden collections or empty reel records behind.
        live_reels = {candidate.reel.id for candidate in candidates}
        live_identities = {
            (key, identity)
            for key, library in self._libraries(session).items()
            for identity in catalog_identities(library.canonical).values()
        }
        session.flush()
        for reel in session.scalars(select(DemoReel)):
            if (
                reel.id not in live_reels
                and (reel.library_key, reel.lora_identity) not in live_identities
                and session.scalar(
                    select(DemoReelEntry.id).where(DemoReelEntry.reel_id == reel.id).limit(1)
                )
                is None
            ):
                collection = session.get(Collection, reel.collection_id)
                session.delete(reel)
                session.flush()
                if collection:
                    session.delete(collection)
        session.flush()
        return candidates, targets

    def step(self) -> list[tuple[str, str]]:
        with self.container.db.session_factory() as session:
            lock_user_state(session)
            config = self.config(session)
            now = utcnow()
            if self._quiet_reset_requested:
                config.last_activity_at = now
                self._quiet_reset_requested = False
            if not config.slots and session.scalar(select(DemoReelEntry.id).limit(1)) is None:
                session.commit()
                self.cleanup()
                self.thumbnails()
                return []
            candidates, targets = self.reconcile(session, config)
            if self.foreground_requests or self._busy(session):
                self._was_busy = True
                targets.extend(record_activity(session, now))
            elif self._was_busy:
                self._was_busy = False
                config.last_activity_at = now
            elif (
                not config.paused
                and config.rating_user_id is not None
                and (rating_owner := session.get(User, config.rating_user_id)) is not None
                and rating_owner.state == UserState.ACTIVE
                and (now - aware(config.last_activity_at)).total_seconds() >= self.idle_seconds
            ):
                active = session.scalar(
                    select(Generation.id)
                    .where(
                        Generation.owner_id == SYSTEM_OWNER_ID,
                        Generation.status.in_(ACTIVE_STATUSES),
                    )
                    .limit(1)
                )
                if (
                    active is None
                    and self.container.image_pool.snapshot(session).free_slot_count > 0
                ):
                    rows = {
                        (row.reel_id, row.slot_id, row.checkpoint_id): row
                        for row in session.scalars(select(DemoReelEntry))
                    }
                    for candidate in candidates:
                        entry = rows[candidate.key]
                        if entry.state != "missing" or candidate.error or candidate.member is None:
                            continue
                        if entry.next_retry_at and aware(entry.next_retry_at) > now:
                            continue
                        try:
                            with session.begin_nested():
                                user = session.get(User, SYSTEM_OWNER_ID)
                                assert user is not None
                                parameters = copy.deepcopy(candidate.parameters)
                                for control in candidate.member.profile.resolved_contract_json.get(
                                    "inputs", []
                                ):
                                    if control.get("type") == "seed":
                                        minimum, maximum, step = (
                                            control["minimum"],
                                            control["maximum"],
                                            control.get("step", 1),
                                        )
                                        parameters[control["id"]] = str(
                                            minimum
                                            + step
                                            * secrets.randbelow((maximum - minimum) // step + 1)
                                        )
                                request = GenerationCreate(
                                    source_key=candidate.member.source_key,
                                    revision=SourceRevision(**candidate.member.revision),
                                    parameters=parameters,
                                    collection_id=candidate.reel.collection_id,
                                )
                                generation, _ = self.container.generations._prepare_accept(
                                    session, user=user, request=request
                                )
                                entry.generation_id = generation.id
                                entry.state = "running"
                        except AppError as error:
                            if error.status_code >= 500 or error.code in {
                                "lora_maintenance",
                                "lora_runtime_unavailable",
                            }:
                                entry.error = error.message
                                break
                            entry.error = error.message
                            entry.state = "blocked"
                        break
            session.commit()
        self.cleanup()
        self.thumbnails()
        return targets

    def thumbnails(self, *, force: bool = False) -> None:
        """Give every LoRA the display image its Demo Reel now supplies.

        Reconciling is idempotent and only rewrites LoRAs whose reel image actually
        changed, so an interval is enough to converge after a deletion, an
        invalidation, or a settings change without touching every LoRA each second.
        """

        now = time.monotonic()
        if not force and now - self._thumbnails_at < THUMBNAIL_SWEEP_SECONDS:
            return
        self._thumbnails_at = now
        try:
            refresh_thumbnails(self.container)
        except Exception:
            # A LoRA display image is presentation only: never disturb reel work, and
            # retry on the next pass instead of waiting out the whole interval.
            self._thumbnails_at = 0.0
            logger.exception("demo_reel_thumbnail_sweep_failed")

    def thumbnail(self, library_key: str, lora_identity: str) -> None:
        """Re-derive one LoRA's display image, as soon as its reel was favorited."""

        try:
            refresh_thumbnails(self.container, identities={(library_key, lora_identity)})
        except Exception:
            logger.exception("demo_reel_thumbnail_refresh_failed")

    def cleanup(self) -> None:
        with self.container.db.session_factory() as session:
            ids = list(
                session.scalars(
                    select(Generation.id).where(
                        Generation.owner_id == SYSTEM_OWNER_ID,
                        Generation.pending_delete.is_(True),
                        Generation.status.in_(TERMINAL_STATUSES),
                    )
                )
            )
        for generation_id in ids:
            with self.container.db.session_factory() as session:
                lock_user_state(session)
                generation = session.get(Generation, generation_id)
                if generation and generation.status in TERMINAL_STATUSES:
                    self.container.generations.delete_terminal(session, generation)

    def save(self, session: Session, actor_id: str, payload: DemoReelUpdate) -> dict[str, Any]:
        lock_user_state(session)
        config = self.config(session)
        if config.revision != payload.revision:
            raise AppError(
                "demo_reel_conflict",
                "Demo Reel settings changed elsewhere. Reload before saving.",
                status_code=409,
            )
        owner_id = (
            str(payload.rating_user_id)
            if payload.rating_user_id
            else config.rating_user_id or actor_id
        )
        owner = session.get(User, owner_id)
        if owner is None or owner.is_system or owner.state != UserState.ACTIVE:
            raise AppError(
                "demo_reel_ratings_unavailable",
                "Choose an active checkpoint-rating account.",
                status_code=422,
            )
        catalogs = self._libraries(session)
        recipes = {}
        for key, value in payload.recipes.items():
            library = catalogs.get(key)
            member = (
                next((m for m in library.members if m.source_key == value.source_key), None)
                if library
                else None
            )
            if member is None:
                raise AppError(
                    "demo_reel_recipe_invalid",
                    "Choose a published LoRA workflow from this model family.",
                    status_code=422,
                )
            parameters = copy.deepcopy(value.parameters)
            controls = member.profile.resolved_contract_json.get("inputs", [])
            if not any(control.get("type") == "seed" for control in controls):
                raise AppError(
                    "demo_reel_recipe_invalid",
                    "Choose a workflow with a published seed control.",
                    status_code=422,
                )
            for control in controls:
                if control.get("type") in {"image", "image_upload", "mask_upload"} and (
                    control.get("required") or parameters.get(control["id"])
                ):
                    raise AppError(
                        "demo_reel_recipe_invalid",
                        "Demo Reel recipes cannot require uploaded images.",
                        status_code=422,
                    )
                if (
                    control.get("type") == "seed"
                    or control.get("type") == "lora_stack"
                    or control.get("semantic_role") == "positive_prompt"
                ):
                    parameters.pop(control["id"], None)
                elif (
                    control.get("type") not in {"image", "image_upload", "mask_upload"}
                    and control["id"] not in parameters
                ):
                    parameters[control["id"]] = copy.deepcopy(control.get("default"))
            dimension_ids = {
                control["id"]
                for control in controls
                if control.get("semantic_role") in {"width", "height"}
            }
            for slot in payload.slots:
                if set(slot.resolutions.get(key, {})) - dimension_ids:
                    raise AppError(
                        "demo_reel_recipe_invalid",
                        "Resolution overrides must use published width and height controls.",
                        status_code=422,
                    )
            recipes[key] = {
                "source_key": value.source_key,
                "parameters": parameters,
                "strength": value.strength,
                "render_revision": render_revision(member.profile),
            }
        config.rating_user_id = owner_id
        config.slots = [slot.model_dump(mode="json") for slot in payload.slots]
        config.recipes = recipes
        config.strengths = copy.deepcopy(payload.strengths)
        config.paused = payload.paused
        config.revision += 1
        record_activity(session)
        candidates, _ = self.reconcile(session, config)
        # Compile all configured variants now: invalid settings never enter the background queue.
        for candidate in candidates:
            if candidate.error or candidate.member is None:
                continue
            self.container.compiler.compile(
                contract=candidate.member.profile.resolved_contract_json,
                api_document=candidate.member.profile.source_api_json,
                requested_controls=candidate.parameters,
            )
        session.commit()
        return self.settings(session)

    def set_enabled(
        self, session: Session, library_key: str, identity: str, *, enabled: bool
    ) -> dict[str, Any]:
        """Take one LoRA out of the Demo Reel, or put it back.

        Switching a LoRA off is the same retirement a removed LoRA gets: below, it drops out of
        the candidate matrix, so its images are discarded and their files deleted.  It happens
        in this request rather than on the next pass because the LoRA gallery answers straight
        afterwards and an administrator should not be looking at a picture that no longer
        exists.  Switching it back on leaves nothing to restore: the reel starts over.
        """

        lock_user_state(session)
        config = self.config(session)
        library = self._libraries(session).get(library_key)
        item = (
            next(
                (
                    item
                    for item in library.canonical
                    if catalog_identities([item]).get(item["id"]) == identity
                ),
                None,
            )
            if library
            else None
        )
        if item is None:
            raise AppError("not_found", "LoRA was not found.", status_code=404)
        reel = self._reel(session, library_key, identity, item["label"])
        produced = int(
            session.scalar(
                select(func.count())
                .select_from(DemoReelEntry)
                .where(DemoReelEntry.reel_id == reel.id, DemoReelEntry.generation_id.is_not(None))
            )
            or 0
        )
        changed = bool(reel.disabled) != (not enabled)
        reel.disabled = not enabled
        if enabled:
            session.commit()
            return {
                "library_key": library_key,
                "lora_identity": identity,
                "demo_reel_enabled": True,
                "images_removed": 0,
                "changed": changed,
            }
        self.reconcile(session, config)
        session.commit()
        self.cleanup()
        self.thumbnail(library_key, identity)
        return {
            "library_key": library_key,
            "lora_identity": identity,
            "demo_reel_enabled": False,
            "images_removed": produced,
            "changed": changed,
        }

    def settings(self, session: Session) -> dict[str, Any]:
        config = self.config(session)
        owner = session.get(User, config.rating_user_id) if config.rating_user_id else None
        catalogs = self._libraries(session)
        counts = dict(
            session.execute(
                select(DemoReelEntry.state, func.count()).group_by(DemoReelEntry.state)
            ).all()
        )
        disabled_reels = int(
            session.scalar(select(func.count()).select_from(DemoReel).where(DemoReel.disabled)) or 0
        )
        return {
            "revision": config.revision,
            "paused": config.paused,
            "rating_user_id": config.rating_user_id,
            "rating_username": owner.username if owner else None,
            "slots": config.slots,
            "recipes": {
                key: {k: v for k, v in recipe.items() if k != "render_revision"}
                for key, recipe in config.recipes.items()
            },
            "strengths": config.strengths,
            "idle_seconds": self.idle_seconds,
            "disabled_reels": disabled_reels,
            "progress": {
                "completed": counts.get("ready", 0),
                "expected": sum(counts.values()),
                "states": counts,
            },
            "libraries": [
                {
                    "key": key,
                    "label": library.label,
                    "sources": [
                        {"source_key": member.source_key, "label": member.profile.display_name}
                        for member in library.members
                    ],
                }
                for key, library in catalogs.items()
            ],
        }

    def view(
        self,
        session: Session,
        viewer_id: str,
        library_key: str,
        identity: str,
        *,
        offset: int = 0,
        limit: int = 60,
    ) -> dict[str, Any]:
        lock_user_state(session)
        config = self.config(session)
        library = self._libraries(session).get(library_key)
        item = (
            next(
                (
                    item
                    for item in library.canonical
                    if catalog_identities([item]).get(item["id"]) == identity
                ),
                None,
            )
            if library
            else None
        )
        if item is None:
            raise AppError("not_found", "LoRA was not found.", status_code=404)
        reel = self._reel(session, library_key, identity, item["label"])
        candidates, _ = self.reconcile(session, config)
        rows = {
            (row.slot_id, row.checkpoint_id): row
            for row in session.scalars(
                select(DemoReelEntry).where(DemoReelEntry.reel_id == reel.id)
            )
        }
        viewer = session.get(User, viewer_id)
        admin = viewer is not None and viewer.role == UserRole.ADMIN
        entries = []
        page_index = 0
        for candidate in candidates:
            if candidate.reel.id != reel.id:
                continue
            row = rows[candidate.slot["id"], candidate.checkpoint_id]
            in_page = offset <= page_index < offset + limit
            page_index += 1
            generation = (
                session.get(Generation, row.generation_id)
                if row.generation_id and in_page
                else None
            )
            summary = None
            if row.state == "ready" and generation and not generation.pending_delete:
                summary = self.container.generations.summary(session, generation).model_dump(
                    mode="json"
                )
                summary["is_favorite"] = (
                    session.scalar(
                        select(Favorite.id).where(
                            Favorite.owner_id == viewer_id, Favorite.generation_id == generation.id
                        )
                    )
                    is not None
                )
                summary["collection_id"] = None
                summary["demo_reel"] = {
                    "library_key": library_key,
                    "lora_identity": identity,
                    "slot_id": candidate.slot["id"],
                    "can_manage": admin,
                }
            entries.append(
                {
                    "id": row.id,
                    "slot_id": row.slot_id,
                    "checkpoint_id": row.checkpoint_id,
                    "checkpoint_label": candidate.checkpoint_label,
                    "state": row.state,
                    "error": row.error,
                    "generation": summary,
                }
            )
        complete = sum(entry["state"] == "ready" for entry in entries)
        status = (
            "off"
            if reel.disabled
            else "paused"
            if config.paused
            else "generating"
            if any(entry["state"] == "running" for entry in entries)
            else "blocked"
            if any(entry["state"] == "blocked" for entry in entries)
            else "failed"
            if any(entry["state"] == "failed" for entry in entries)
            else "complete"
            if entries and complete == len(entries)
            else "waiting"
        )
        rating_owner = session.get(User, config.rating_user_id) if config.rating_user_id else None
        rating_unavailable = bool(config.slots) and (
            rating_owner is None or rating_owner.state != UserState.ACTIVE
        )
        if rating_unavailable and not config.paused:
            status = "blocked"
        result = {
            "library_key": library_key,
            "lora_identity": identity,
            "label": item["label"],
            "subject": item.get("trigger_word") or item["label"],
            "slots": config.slots,
            "entries": entries,
            "completed": complete,
            "expected": len(entries),
            "status": status,
            "demo_reel_enabled": not reel.disabled,
            "can_manage": admin,
            "paused": config.paused,
            "revision": config.revision,
            "rating_username": rating_owner.username if rating_owner else None,
            "message": "Rating account unavailable. Select an active account in Demo Reel settings."
            if rating_unavailable
            else "Demo Reel is off for this LoRA."
            if reel.disabled
            else None,
        }
        session.commit()
        return result

    def readable(
        self, session: Session, library_key: str, identity: str, generation_id: str
    ) -> Generation:
        lock_user_state(session)
        config = self.config(session)
        desired = {
            candidate.key: candidate.signature for candidate in self._matrix(session, config)
        }
        entry = session.scalar(
            select(DemoReelEntry)
            .join(DemoReel)
            .where(
                DemoReel.library_key == library_key,
                DemoReel.lora_identity == identity,
                DemoReelEntry.generation_id == generation_id,
                DemoReelEntry.state == "ready",
            )
        )
        generation = session.get(Generation, generation_id)
        if (
            entry is None
            or entry.signature != desired.get((entry.reel_id, entry.slot_id, entry.checkpoint_id))
            or generation is None
            or generation.pending_delete
            or generation.status != GenerationStatus.SUCCEEDED
        ):
            raise AppError("not_found", "Demo Reel image was not found.", status_code=404)
        return generation
