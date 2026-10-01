"""Prompt Re-run: queue the exact retained prompts of a selection with new settings.

The selected generations contribute only their accepted ``final_prompt`` (and,
when requested, their resolution and seed). Creative Direction, the Prompt
Assistant and the prompt generator never participate: every item is an
ordinary manual generation whose positive prompt is the historical text.

One SQLite transaction creates the destination folder, accepts every planned
item and records an idempotency receipt, so a retried request after a lost
reply never creates a second folder or duplicate jobs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..errors import AppError
from ..models import Generation, GenerationSubmission, User, UserState, WorkflowProfile
from ..schemas import (
    PROMPT_RERUN_MAX_ITEMS,
    CollectionCreate,
    GallerySelection,
    GenerationCreate,
    PromptRerunCreate,
    PromptRerunPreview,
    PromptRerunPromptPreview,
    PromptRerunResult,
)
from .submissions import accept_items, project_items, request_digest
from .user_state import lock_user_state, require_manual_generation

if TYPE_CHECKING:
    from .collections import CollectionService
    from .generations import GenerationService

logger = logging.getLogger(__name__)

ENDPOINT = "prompt_rerun"
PREVIEW_LIMIT = 50
EXCERPT_LENGTH = 160
BATCH_SIZE = 400


@dataclass(frozen=True)
class SourcePrompt:
    generation_id: str
    prompt: str
    accepted_at: datetime
    width: int | None = None
    height: int | None = None
    seed: str | None = None


@dataclass
class PromptPlan:
    generation_count: int = 0
    skipped_count: int = 0
    duplicate_count: int = 0
    unique_prompt_count: int = 0
    prompts: list[SourcePrompt] = field(default_factory=list)


@dataclass(frozen=True)
class TargetInputs:
    prompt_id: str
    width: Mapping[str, Any] | None
    height: Mapping[str, Any] | None
    seed_id: str | None


def _contract_inputs(contract: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(contract, Mapping):
        return []
    inputs = contract.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        inputs = contract.get("controls")
    return [item for item in inputs or [] if isinstance(item, Mapping)]


def _dimension(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def original_dimensions(
    contract: Mapping[str, Any] | None, effective: Mapping[str, Any] | None
) -> tuple[int | None, int | None]:
    """Width and height a historical generation was accepted with, if recorded."""

    effective = effective if isinstance(effective, Mapping) else {}
    width: int | None = None
    height: int | None = None
    for control in _contract_inputs(contract):
        value = effective.get(str(control.get("id")))
        role = control.get("semantic_role")
        if role == "width" and _dimension(value) is not None:
            width = _dimension(value)
        elif role == "height" and _dimension(value) is not None:
            height = _dimension(value)
        elif control.get("type") == "resolution" and isinstance(value, Mapping):
            width = width or _dimension(value.get("width"))
            height = height or _dimension(value.get("height"))
    return width, height


def original_seed(
    contract: Mapping[str, Any] | None, resolved_seeds: Mapping[str, Any] | None
) -> str | None:
    seeds = resolved_seeds if isinstance(resolved_seeds, Mapping) else {}
    for control in _contract_inputs(contract):
        if control.get("type") == "seed" and str(control.get("id")) in seeds:
            return str(seeds[str(control.get("id"))])
    for value in seeds.values():
        return str(value)
    return None


def plan_prompts(rows: Iterable[SourcePrompt], *, skip_duplicates: bool) -> PromptPlan:
    """Order prompts oldest first and optionally drop exact-duplicate texts."""

    plan = PromptPlan()
    seen: set[str] = set()
    for row in sorted(rows, key=lambda item: (item.accepted_at, item.generation_id)):
        plan.generation_count += 1
        if not row.prompt.strip():
            plan.skipped_count += 1
            continue
        if row.prompt in seen:
            plan.duplicate_count += 1
            if skip_duplicates:
                continue
        else:
            plan.unique_prompt_count += 1
        seen.add(row.prompt)
        plan.prompts.append(row)
    return plan


def target_inputs(contract: Mapping[str, Any]) -> TargetInputs:
    inputs = _contract_inputs(contract)
    prompt_id = next(
        (
            str(item["id"])
            for item in inputs
            if item.get("semantic_role") == "positive_prompt" and isinstance(item.get("id"), str)
        ),
        None,
    )
    if prompt_id is None:
        raise AppError(
            "source_kind_invalid",
            "The chosen generation source has no prompt input, so prompts cannot be re-run.",
            status_code=422,
        )
    return TargetInputs(
        prompt_id=prompt_id,
        width=next((item for item in inputs if item.get("semantic_role") == "width"), None),
        height=next((item for item in inputs if item.get("semantic_role") == "height"), None),
        seed_id=next((str(item["id"]) for item in inputs if item.get("type") == "seed"), None),
    )


def _fits(declaration: Mapping[str, Any], value: int) -> bool:
    minimum = declaration.get("minimum")
    maximum = declaration.get("maximum")
    step = declaration.get("step")
    if isinstance(minimum, int | float) and value < minimum:
        return False
    if isinstance(maximum, int | float) and value > maximum:
        return False
    if isinstance(step, int) and not isinstance(step, bool) and step > 0:
        base = minimum if isinstance(minimum, int) and not isinstance(minimum, bool) else 0
        return (value - base) % step == 0
    return True


@dataclass
class BuiltRequests:
    requests: list[GenerationCreate]
    resolution_fallback_count: int = 0


def planned_count(payload: PromptRerunCreate, prompt_count: int) -> int:
    return prompt_count * len(payload.model_variants) * payload.quantity


def build_requests(
    contract: Mapping[str, Any],
    payload: PromptRerunCreate,
    plan: PromptPlan,
    collection_id: str | None,
) -> BuiltRequests:
    """Expand prompts by checkpoint variants by quantity into generation requests."""

    total = planned_count(payload, len(plan.prompts))
    if total > PROMPT_RERUN_MAX_ITEMS:
        raise AppError(
            "prompt_rerun_too_large",
            (
                f"Too many planned generations: {total} exceeds the "
                f"{PROMPT_RERUN_MAX_ITEMS}-item limit. Lower the count per prompt, "
                "choose fewer checkpoints, or select fewer images."
            ),
            status_code=422,
            details={"planned": total, "limit": PROMPT_RERUN_MAX_ITEMS},
        )
    target = target_inputs(contract)
    shared = {
        key: value
        for key, value in payload.parameters.items()
        if key != target.prompt_id and key != target.seed_id
    }
    built = BuiltRequests(requests=[])
    for source in plan.prompts:
        parameters = dict(shared)
        parameters[target.prompt_id] = source.prompt
        if payload.keep_original_resolution and target.width and target.height:
            if (
                source.width is not None
                and source.height is not None
                and _fits(target.width, source.width)
                and _fits(target.height, source.height)
            ):
                parameters[str(target.width["id"])] = source.width
                parameters[str(target.height["id"])] = source.height
            else:
                built.resolution_fallback_count += 1
        if target.seed_id is not None:
            reuse = payload.seed_mode == "original" and source.seed is not None
            seed = source.seed if reuse else "random"
        for variant in payload.model_variants:
            for _ in range(payload.quantity):
                item = {
                    **parameters,
                    **{key: value for key, value in variant.items() if key != target.prompt_id},
                }
                if target.seed_id is not None:
                    item[target.seed_id] = seed
                built.requests.append(
                    GenerationCreate(
                        source_key=payload.source_key,
                        revision=payload.revision,
                        parameters=item,
                        collection_id=collection_id,
                    )
                )
    return built


def collect_prompts(
    session: Session,
    generations: GenerationService,
    collections: CollectionService,
    owner_id: str,
    selection: GallerySelection,
    *,
    skip_duplicates: bool,
) -> PromptPlan:
    from .gallery import GalleryService

    resolved = GalleryService(generations, collections).selection(session, owner_id, selection)
    direct = [item.id for item in resolved.generations if not item.pending_delete]
    folder_ids = sorted(resolved.subtree_ids)
    columns = (
        Generation.id,
        Generation.final_prompt,
        Generation.accepted_at,
        Generation.resolved_contract_json,
        Generation.effective_controls_json,
        Generation.resolved_seeds_json,
    )
    rows: dict[str, SourcePrompt] = {}

    def add(statement: Any) -> None:
        for row in session.execute(statement):
            width, height = original_dimensions(
                row.resolved_contract_json, row.effective_controls_json
            )
            rows[row.id] = SourcePrompt(
                generation_id=row.id,
                prompt=row.final_prompt or "",
                accepted_at=row.accepted_at,
                width=width,
                height=height,
                seed=original_seed(row.resolved_contract_json, row.resolved_seeds_json),
            )

    base = select(*columns).where(
        Generation.owner_id == owner_id, Generation.pending_delete.is_(False)
    )
    for start in range(0, len(direct), BATCH_SIZE):
        add(base.where(Generation.id.in_(direct[start : start + BATCH_SIZE])))
    for start in range(0, len(folder_ids), BATCH_SIZE):
        add(base.where(Generation.collection_id.in_(folder_ids[start : start + BATCH_SIZE])))
    return plan_prompts(rows.values(), skip_duplicates=skip_duplicates)


def preview(
    session: Session,
    generations: GenerationService,
    collections: CollectionService,
    owner_id: str,
    selection: GallerySelection,
) -> PromptRerunPreview:
    plan = collect_prompts(
        session, generations, collections, owner_id, selection, skip_duplicates=True
    )
    return PromptRerunPreview(
        generation_count=plan.generation_count,
        prompt_count=len(plan.prompts) + plan.duplicate_count,
        duplicate_count=plan.duplicate_count,
        skipped_count=plan.skipped_count,
        unique_prompt_count=plan.unique_prompt_count,
        prompts=[
            PromptRerunPromptPreview(
                generation_id=item.generation_id,
                excerpt=_excerpt(item.prompt),
                width=item.width,
                height=item.height,
                has_seed=item.seed is not None,
            )
            for item in plan.prompts[:PREVIEW_LIMIT]
        ],
    )


def _excerpt(prompt: str) -> str:
    text = " ".join(prompt.split())
    return text if len(text) <= EXCERPT_LENGTH else text[: EXCERPT_LENGTH - 1].rstrip() + "…"


def _profile(service: GenerationService, session: Session, payload: PromptRerunCreate) -> Any:
    return service._profile_for_request(
        session,
        GenerationCreate(source_key=payload.source_key, revision=payload.revision),
    )


def project_receipt(
    service: GenerationService, session: Session, receipt: GenerationSubmission
) -> PromptRerunResult:
    from .collections import CollectionService

    header, *outcomes = receipt.outcomes
    meta = header.get("prompt_rerun", {}) if isinstance(header, Mapping) else {}
    projected = CollectionService.project(
        session, owner_id=receipt.owner_id, collection_ids=[str(meta.get("collection_id"))]
    )
    if not projected:
        raise AppError(
            "submission_result_unavailable",
            "This Prompt Re-run was accepted, but its folder has been deleted.",
            status_code=410,
        )
    return PromptRerunResult(
        collection=projected[0],
        items=project_items(service, session, receipt.owner_id, outcomes),
        prompt_count=int(meta.get("prompt_count", 0)),
        planned_count=int(meta.get("planned_count", 0)),
        resolution_fallback_count=int(meta.get("resolution_fallback_count", 0)),
    )


async def accept(
    service: GenerationService,
    collections: CollectionService,
    owner_id: str,
    payload: PromptRerunCreate,
    key: str,
) -> PromptRerunResult:
    digest = request_digest(ENDPOINT, payload.model_dump(mode="json"))

    def transaction() -> tuple[PromptRerunResult, list[dict[str, Any]]]:
        with service.session_factory() as session:
            lock_user_state(session)
            receipt = session.get(GenerationSubmission, (owner_id, key))
            if receipt is not None:
                if receipt.request_digest != digest or receipt.endpoint != ENDPOINT:
                    raise AppError(
                        "idempotency_conflict",
                        "This submission ID belongs to a different request.",
                        status_code=409,
                    )
                return project_receipt(service, session, receipt), []
            require_manual_generation(session, owner_id)
            user = session.get(User, owner_id)
            if user is None or user.state != UserState.ACTIVE:
                raise AppError("authentication_required", "Sign in is required.", status_code=401)
            profile: WorkflowProfile = _profile(service, session, payload)
            if payload.parent_collection_id is not None:
                collections.get_owned(session, owner_id, payload.parent_collection_id)
            plan = collect_prompts(
                session,
                service,
                collections,
                owner_id,
                payload,
                skip_duplicates=payload.skip_duplicates,
            )
            if not plan.prompts:
                raise AppError(
                    "prompt_rerun_empty",
                    "None of the selected images has a retained prompt to re-run.",
                    status_code=422,
                )
            # Validate the expansion before creating anything.
            build_requests(profile.resolved_contract_json, payload, plan, None)
            folder = collections.create_in_session(
                session,
                owner_id=owner_id,
                payload=CollectionCreate(
                    name=payload.folder_name, parent_id=payload.parent_collection_id
                ),
            )
            built = build_requests(profile.resolved_contract_json, payload, plan, folder.id)
            outcomes, events = accept_items(service, session, user, built.requests)
            accepted = [item for item in outcomes if "generation_id" in item]
            if not accepted:
                error = outcomes[0]["error"]
                raise AppError(
                    error["code"],
                    error["message"],
                    status_code=error["status"],
                    fields=error["fields"],
                    details=error["details"],
                )
            header = {
                "prompt_rerun": {
                    "collection_id": folder.id,
                    "prompt_count": len(plan.prompts),
                    "planned_count": len(built.requests),
                    "resolution_fallback_count": built.resolution_fallback_count,
                }
            }
            receipt = GenerationSubmission(
                owner_id=owner_id,
                key=key,
                endpoint=ENDPOINT,
                request_digest=digest,
                outcomes=[header, *outcomes],
            )
            session.add(receipt)
            session.flush()
            result = project_receipt(service, session, receipt)
            session.commit()
            return result, events

    result, events = await run_blocking(transaction)
    for event in events:
        try:
            await service.broker.publish(owner_id, event)
        except Exception:
            logger.exception("prompt_rerun_notification_failed")
    return result
