from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

import httpx

from ..config import Settings
from ..domain.expectations import (
    Evaluation,
    evaluation_schema,
    validate_evaluation,
    vision_instruction,
)
from ..domain.prompt_instructions import (
    DEFAULT_PROMPT_INSTRUCTIONS,
    DEFAULT_VISION_CHECK_INSTRUCTIONS,
)
from ..errors import AppError
from .llm_router import (
    CLIENT_NAME,
    CLIENT_NAME_HEADER,
    FALLBACK,
    FEATURES,
    RETRY,
    UNAVAILABLE,
    WAIT,
    ChangeListener,
    RouterWatch,
    accepting_requests,
    classify_error,
    error_code,
    model_for,
    pick_service,
    resolve,
    thinking_value,
)

CANDIDATE_SEED_MAXIMUM = 2**31 - 1
MAX_REFINE_ATTEMPTS = 3
MAX_CREATE_ATTEMPTS = 3
MAX_CREATE_EXCLUSIONS = 8
MAX_REFINE_CHAIN_EXCLUSIONS = 8
# A one-shot refinement starts conservatively so it stays faithful to the current prompt, then
# escalates when a candidate fails to apply the direction. Replaying a production refinement that
# returned its input verbatim stayed unchanged at temperatures 0.1 and 0.5 and changed at 1.0.
REFINE_TEMPERATURES = (0.1, 0.7, 1.0)
# A chained automatic refinement feeds each output back as the next input, so it starts warmer
# to keep the sequence moving instead of converging on a fixed point.
CHAINED_REFINE_TEMPERATURES = (0.7, 1.0, 1.0)
REFINE_RETRY_FEEDBACK = (
    "Your previous answer did not apply the creative direction: it repeated the current prompt "
    "or an earlier result. Apply the creative direction now and return a prompt that differs "
    "from them."
)
# Transient router failures (other 5xx, 408, 429, connection errors, malformed JSON) retry the
# same service. SERVICE_OFFLINE, BACKEND_UNAVAILABLE and MODEL_NOT_FOUND choose again at once and
# never consume these attempts (docs/llm-router-contract.md section 10).
MAX_GENERATE_ATTEMPTS = 3
GENERATE_RETRY_BASE_SECONDS = 0.25
RETRYABLE_GENERATE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
# BACKEND_DRAINING, MAINTENANCE_MODE and a draining capabilities document wait with this backoff
# for at least Settings.ollama_router_wait_seconds (ten minutes), then choose the model again.
ROUTER_WAIT_INITIAL_SECONDS = 2.0
ROUTER_WAIT_MAX_SECONDS = 30.0
# The effort requested when thinking is enabled. It is sent only to a model that lists it in
# metadata.reasoning.efforts; otherwise the closest listed effort, or true, is sent instead.
THINKING_EFFORT = "xhigh"
# Ollama's generated-token allowance is shared by thinking and final output. Creative prompt
# composition therefore starts with enough room for reasoning and escalates deterministically if
# the upstream response reports that it exhausted the allowance before completing the schema.
OUTPUT_TOKEN_BUDGETS = (2_048, 4_096, 8_192)
# Vision scoring is a judgement, not a creative draw: sample conservatively and redraw a
# structurally invalid score sheet with the next seed.
MAX_VISION_CANDIDATES = 3
VISION_TEMPERATURE = 0.1
# A non-NSFW model that declines a request, or answers with output that fails the JSON schema,
# is reported plainly and never retried in a loop (contract section 5).
DECLINE_CODES = frozenset({"MALFORMED_STRUCTURED_OUTPUT", "EMPTY_UPSTREAM_RESPONSE"})
_REFUSAL_OPENINGS = (
    "i can't",
    "i cannot",
    "i can not",
    "i won't",
    "i will not",
    "i'm sorry",
    "i am sorry",
    "sorry,",
    "sorry but",
    "i'm unable",
    "i am unable",
    "i'm not able",
    "i am not able",
    "i must decline",
    "i apologize",
    "i'm afraid i",
    "i am afraid i",
    "i refuse",
    "as an ai",
    "unfortunately, i",
    "i'm not comfortable",
    "i am not comfortable",
    "i don't feel comfortable",
    "i do not feel comfortable",
)
CandidateSeedResolver = Callable[[int, int], int]
GenerateRetrySleeper = Callable[[float], Awaitable[None]]
# Waits up to the given seconds while the router drains; returns the seconds actually waited,
# or None for the full interval.
RouterWaiter = Callable[[float], Awaitable[float | None]]
SelectionMode = Literal["selected", "wait", "unavailable"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouterSelection:
    """The service chosen for one request from the current capabilities document.

    ``fallback`` is true when the assistant is not on its preferred kind of model: a non-NSFW
    model under ``CIF_OLLAMA_NSFW=prefer``, or a later name under ``CIF_OLLAMA_SELECTION=named``.
    ``model`` is the document's entry for the service (limits and capabilities); it is never
    persisted, because it carries the canonical model ID.
    """

    outcome: SelectionMode
    service: str | None = None
    nsfw: bool | None = None
    fallback: bool = False
    reason: str = ""
    configuration_id: str | None = None
    model: dict[str, Any] | None = dataclass_field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "nsfw": self.nsfw,
            "fallback": self.fallback,
            "reason": self.reason,
            "configuration_id": self.configuration_id,
        }


@dataclass(frozen=True)
class ComposeResult:
    prompt: str
    model: str
    raw_response: dict[str, Any]
    duration_ms: int
    service: str | None = None
    fallback: bool = False
    nsfw: bool | None = None


@dataclass(frozen=True)
class VisionEvaluationResult:
    evaluation: Evaluation
    model: str
    diagnostics: dict[str, Any]
    duration_ms: int
    service: str | None = None
    fallback: bool = False


@dataclass(frozen=True)
class GenerateResult:
    data: Any
    status: int
    selection: RouterSelection | None = None
    think: bool | str = False
    # The router marked the answer incomplete because it reached the requested output limit
    # (x_router.status "incomplete", done_reason "length"). It is never accepted as complete.
    incomplete: bool = False


@dataclass(frozen=True)
class _CandidateCompose:
    """One candidate that produced a usable structured prompt.

    ``budget_diagnostics`` holds the metadata-only diagnostics of the attempts
    before the selected one (the thinking escalation levels that overflowed);
    the selected attempt is described by ``selected_budget_attempt`` and
    ``selected_budget`` so callers can reconstruct the exact allowance history.
    """

    final: str
    selected_field: str | None
    data: dict[str, Any]
    status: int
    budget_diagnostics: tuple[dict[str, Any], ...]
    selected_budget_attempt: int
    selected_budget: int
    used_no_thinking_fallback: bool
    generated: GenerateResult | None = None


@dataclass
class _RouterWaitBudget:
    """Shared by every wait of one request: a draining document and drain/maintenance codes."""

    limit: float
    waited: float = 0.0
    delay: float = ROUTER_WAIT_INITIAL_SECONDS

    @property
    def exhausted(self) -> bool:
        return self.waited >= self.limit


class _ServiceSlots:
    """Admit at most a service's published ``slots`` concurrent requests, first come first served.

    The router queues extra requests itself; holding them here keeps this client within the
    contract's concurrency guidance (section 7). The capacity is read from the current document
    on every check, so a configuration change applies immediately.
    """

    # Waiters also re-check periodically, so a larger slot count published by a configuration
    # change admits them without a release.
    RECHECK_SECONDS = 1.0

    def __init__(self) -> None:
        self._active: dict[str, int] = {}
        self._queue: dict[str, list[asyncio.Event]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop = loop
            self._active = {}
            self._queue = {}

    def active(self, service: str) -> int:
        return self._active.get(service, 0)

    def _wake_next(self, service: str) -> None:
        queue = self._queue.get(service)
        if queue:
            queue[0].set()

    @asynccontextmanager
    async def hold(self, service: str, capacity: Callable[[], int]) -> AsyncIterator[None]:
        self._bind()
        waiter = asyncio.Event()
        queue = self._queue.setdefault(service, [])
        queue.append(waiter)
        try:
            while queue[0] is not waiter or self._active.get(service, 0) >= max(1, capacity()):
                waiter.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(waiter.wait(), timeout=self.RECHECK_SECONDS)
        finally:
            queue.remove(waiter)
            self._wake_next(service)
        self._active[service] = self._active.get(service, 0) + 1
        try:
            yield
        finally:
            self._active[service] = max(0, self._active.get(service, 0) - 1)
            self._wake_next(service)


class OllamaAdapter:
    """Prompt Assistant client for the LLM Router (``docs/llm-router-contract.md``).

    The model is chosen for every request from the router's capabilities document, kept current
    by :meth:`watch_router` (a lifespan task) or, when no subscriber runs, read before each
    request. Requests use the router's native Ollama ``/api/chat`` route.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        seed_resolver: CandidateSeedResolver | None = None,
        retry_sleeper: GenerateRetrySleeper | None = None,
        router_waiter: RouterWaiter | None = None,
    ):
        self.settings = settings
        self.base_url = settings.ollama_base_url
        self.seed_resolver = seed_resolver or self._secure_seed
        self.retry_sleeper = retry_sleeper or asyncio.sleep
        self.router_waiter = router_waiter or self._wait_for_router
        headers = {CLIENT_NAME_HEADER: CLIENT_NAME}
        if settings.ollama_api_key and settings.ollama_api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {settings.ollama_api_key.get_secret_value()}"
        self._client = (
            httpx.AsyncClient(
                base_url=self.base_url,
                # A request may wait in the router's first-in, first-out queue behind long
                # generations, and the router has no queue deadline: keep the read timeout long
                # and never abandon and resubmit a queued request (contract section 7).
                timeout=httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=5.0),
                transport=transport,
                headers=headers,
            )
            if self.base_url
            else None
        )
        self.router: RouterWatch | None = RouterWatch(self._client) if self._client else None
        self._slots = _ServiceSlots()
        self._last_selection: dict[str, tuple[Any, ...]] = {}

    @staticmethod
    def _secure_seed(minimum: int, maximum: int) -> int:
        return minimum + secrets.randbelow(maximum - minimum + 1)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    # ---------- discovery and selection ----------

    def log_configuration(self) -> None:
        """Startup log: which selection mode and NSFW preference are active."""

        if not self._client:
            logger.info(
                "prompt_assistant_router_disabled",
                extra={"service": "llm_router", "operation": "configuration"},
            )
            return
        named = self.settings.ollama_selection == "named"
        logger.info(
            "prompt_assistant_router_selection",
            extra={
                "service": "llm_router",
                "operation": "configuration",
                "router_selection": self.settings.ollama_selection,
                "router_nsfw_preference": None if named else self.settings.ollama_nsfw,
                "router_named_models": list(self.settings.ollama_named_models) if named else None,
                "router_wait_seconds": self.settings.ollama_router_wait_seconds,
            },
        )
        if (
            not named
            and self.settings.ollama_model
            and "ollama_model" in self.settings.model_fields_set
        ):
            # Capability selection replaces the name: an existing CIF_OLLAMA_MODEL=nighttime
            # in a production .env no longer chooses the model.
            logger.info(
                "prompt_assistant_router_setting_ignored",
                extra={
                    "service": "llm_router",
                    "operation": "configuration",
                    "router_ignored_setting": "CIF_OLLAMA_MODEL",
                    "router_selection": self.settings.ollama_selection,
                },
            )
        key = self.settings.ollama_api_key
        if key and key.get_secret_value() and key.get_secret_value() != "local-only":
            logger.warning(
                "prompt_assistant_router_credential_configured",
                extra={"service": "llm_router", "operation": "configuration"},
            )

    async def watch_router(
        self, stop: asyncio.Event | None = None, on_change: ChangeListener | None = None
    ) -> None:
        """Lifespan task: read the capabilities document, then follow its change events."""

        if self.router is None:
            return
        if on_change is not None:
            self.router.add_listener(on_change)
        try:
            await self.router.run_forever(stop)
        finally:
            if on_change is not None:
                self.router.remove_listener(on_change)

    async def _ensure_document(self, *, for_request: bool = True) -> dict[str, Any] | None:
        """The current document; read it now when no subscriber keeps it current.

        While the subscriber runs it owns reads and polling, so a status check never waits on
        the network; a request that finds no document at all still tries one read.
        """

        router = self.router
        if router is None:
            return None
        if not router.running or (router.doc is None and for_request):
            await router.refresh()
        return router.doc

    def select_model(self, *, vision: bool = False, exclude: Iterable[str] = ()) -> RouterSelection:
        """Choose the service for one request from the current document (no network)."""

        doc = self.router.doc if self.router else None
        require = ("vision",) if vision else ()
        excluded = tuple(exclude)
        configuration = doc.get("configuration") if doc else None
        configuration_id = (
            configuration.get("id")
            if isinstance(configuration, Mapping) and isinstance(configuration.get("id"), str)
            else None
        )
        preference = self.settings.ollama_nsfw
        named = self.settings.ollama_selection == "named"
        names = self.settings.ollama_named_models
        if named:
            service = (
                resolve(doc, names[0], names[1:], require=require, exclude=excluded)
                if names
                else UNAVAILABLE
            )
        else:
            wanted = {"prefer": True, "require": True, "avoid": False, "any": None}[preference]
            service = pick_service(
                doc,
                nsfw=wanted,
                require=require,
                exclude=excluded,
                fallback_any=preference == "prefer",
            )
        if service == WAIT:
            router = doc.get("router") if doc else None
            maintenance = isinstance(router, Mapping) and router.get("maintenance") is True
            return RouterSelection(
                "wait",
                reason="router_maintenance" if maintenance else "router_draining",
                configuration_id=configuration_id,
            )
        if service == UNAVAILABLE:
            return RouterSelection(
                "unavailable",
                reason=self._unavailable_reason(doc, require),
                configuration_id=configuration_id,
            )
        model = model_for(doc, service)
        declared = model.get("nsfw") if model else None
        nsfw = declared if isinstance(declared, bool) else None
        if named:
            fallback = service != names[0]
            reason = "named_fallback" if fallback else "preferred"
        elif preference == "prefer":
            fallback = nsfw is not True
            reason = self._prefer_fallback_reason(doc, require) if fallback else "most_capable_nsfw"
        else:
            fallback = False
            reason = {
                "require": "most_capable_nsfw",
                "avoid": "most_capable_non_nsfw",
                "any": "most_capable",
            }[preference]
        return RouterSelection(
            "selected",
            service=service,
            nsfw=nsfw,
            fallback=fallback,
            reason=reason,
            configuration_id=configuration_id,
            model=model,
        )

    def _available_models(self, doc: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
        models = doc.get("models") if isinstance(doc, Mapping) else None
        return [
            model
            for model in (models if isinstance(models, list) else [])
            if isinstance(model, Mapping) and model.get("available")
        ]

    def _unavailable_reason(self, doc: Mapping[str, Any] | None, require: Sequence[str]) -> str:
        if doc is None:
            return "router_unreachable"
        available = self._available_models(doc)
        if not available:
            return "no_available_model"
        if require and not any(
            all(FEATURES[feature](model) for feature in require) for model in available
        ):
            return "no_vision_model"
        if self.settings.ollama_selection == "named":
            return "named_models_unavailable"
        if self.settings.ollama_nsfw == "require":
            return "no_nsfw_model"
        if self.settings.ollama_nsfw == "avoid":
            return "no_non_nsfw_model"
        return "no_usable_model"

    def _prefer_fallback_reason(self, doc: Mapping[str, Any] | None, require: Sequence[str]) -> str:
        nsfw_models = [model for model in self._available_models(doc) if model.get("nsfw") is True]
        if not nsfw_models:
            return "no_nsfw_model_available"
        if require and not any(
            all(FEATURES[feature](model) for feature in require) for model in nsfw_models
        ):
            return "no_nsfw_model_with_vision"
        return "nsfw_model_failed"

    def _note_selection(self, purpose: str, selection: RouterSelection) -> None:
        """Log every change of the chosen model with its reason."""

        key = (selection.outcome, selection.service, selection.fallback, selection.reason)
        previous = self._last_selection.get(purpose)
        if previous == key:
            return
        self._last_selection[purpose] = key
        degraded = selection.outcome != "selected" or selection.fallback
        (logger.warning if degraded else logger.info)(
            "llm_router_model_changed",
            extra={
                "service": "llm_router",
                "operation": "selection",
                "router_purpose": purpose,
                "router_outcome": selection.outcome,
                "router_service": selection.service,
                "router_previous_service": previous[1] if previous else None,
                "router_selection_reason": selection.reason,
                "router_fallback": selection.fallback,
                "router_nsfw": selection.nsfw,
                "router_configuration_id": selection.configuration_id,
                "router_selection": self.settings.ollama_selection,
                "router_nsfw_preference": self.settings.ollama_nsfw,
            },
        )

    def _service_slots(self, service: str) -> int:
        model = model_for(self.router.doc if self.router else None, service)
        slots = model.get("slots") if model else None
        return slots if isinstance(slots, int) and not isinstance(slots, bool) and slots > 0 else 1

    async def _wait_for_router(self, seconds: float) -> float | None:
        """Sleep up to ``seconds``, waking early when the capabilities document changes."""

        router = self.router
        revision = router.revision if router else None
        started = time.monotonic()
        deadline = started + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            await asyncio.sleep(min(remaining, 0.5))
            if router is not None and router.revision != revision:
                return time.monotonic() - started
        return None

    async def _pause_for_router(self, budget: _RouterWaitBudget) -> None:
        waited = await self.router_waiter(budget.delay)
        budget.waited += budget.delay if waited is None else max(0.0, waited)
        budget.delay = min(budget.delay * 2, ROUTER_WAIT_MAX_SECONDS)

    async def _choose(
        self, *, vision: bool, exclude: Iterable[str], budget: _RouterWaitBudget
    ) -> RouterSelection:
        """Choose a service, waiting (without switching) while the router drains."""

        excluded = tuple(exclude)
        while True:
            await self._ensure_document()
            selection = self.select_model(vision=vision, exclude=excluded)
            self._note_selection("vision" if vision else "text", selection)
            if selection.outcome != "wait" or budget.exhausted:
                return selection
            logger.info(
                "llm_router_waiting",
                extra={
                    "service": "llm_router",
                    "operation": "selection",
                    "router_selection_reason": selection.reason,
                    "backoff_seconds": budget.delay,
                },
            )
            await self._pause_for_router(budget)
            # The configuration may have changed while draining: exclusions no longer apply.
            excluded = ()

    def _selection_message(self, selection: RouterSelection, *, vision: bool = False) -> str:
        reason = selection.reason
        if selection.outcome == "wait":
            if reason == "router_maintenance":
                return "The LLM Router is in maintenance; Prompt Assistant resumes when it ends."
            return (
                "The LLM Router is switching configuration; Prompt Assistant resumes when it "
                "finishes."
            )
        messages = {
            "router_unreachable": (
                "Prompt Assistant is unavailable because the LLM Router's capabilities could "
                "not be read."
            ),
            "no_available_model": (
                "Prompt Assistant is unavailable because the LLM Router has no available model."
            ),
            "no_vision_model": "No available model on the LLM Router can inspect images.",
            "named_models_unavailable": (
                "Prompt Assistant's configured models ("
                + ", ".join(self.settings.ollama_named_models)
                + ") are unavailable on the LLM Router."
            ),
            "no_nsfw_model": (
                "Prompt Assistant requires an NSFW model, and none is available on the LLM "
                "Router right now."
            ),
            "no_non_nsfw_model": (
                "Prompt Assistant requires a non-NSFW model, and none is available on the LLM "
                "Router right now."
            ),
        }
        return messages.get(
            reason,
            "Prompt Assistant is temporarily unavailable because no suitable model is available "
            "on the LLM Router.",
        )

    def _fallback_notice(self, selection: RouterSelection, *, vision: bool = False) -> str | None:
        if selection.outcome != "selected" or not selection.fallback:
            return None
        name = _service_label(selection.service)
        if self.settings.ollama_selection == "named":
            preferred = _service_label(self.settings.ollama_named_models[0])
            return f"{preferred} is unavailable, so Prompt Assistant is using {name} instead."
        lacking = (
            "No NSFW model that can inspect images is available"
            if selection.reason == "no_nsfw_model_with_vision"
            else "No NSFW model is available"
        )
        purpose = "Image checks are" if vision else "Prompt Assistant is"
        return f"{lacking}, so {purpose} using {name}, which may decline some requests."

    async def status(self) -> tuple[bool, str | None]:
        if not self._client:
            return False, "Prompt Assistant is not configured."
        doc = await self._ensure_document(for_request=False)
        if doc is None:
            selection = RouterSelection("unavailable", reason="router_unreachable")
        else:
            selection = self.select_model()
        self._note_selection("text", selection)
        if selection.outcome == "selected":
            return True, None
        return False, self._selection_message(selection)

    async def capabilities(self) -> dict[str, Any]:
        """The chosen models and their capabilities, for the Prompt Assistant status row.

        Everything comes from the current capabilities document: image input is the chosen
        vision model's ``image`` modality, never inferred from a model name.
        """

        if not self._client:
            return {"vision": False, "capabilities": [], "router": None}
        doc = await self._ensure_document(for_request=False)
        text = (
            self.select_model()
            if doc is not None
            else RouterSelection("unavailable", reason="router_unreachable")
        )
        vision = (
            self.select_model(vision=True)
            if doc is not None
            else RouterSelection("unavailable", reason="router_unreachable")
        )
        self._note_selection("vision", vision)
        capabilities = text.model.get("capabilities") if text.model else None
        return {
            "vision": vision.outcome == "selected",
            "capabilities": sorted(
                {item for item in capabilities if isinstance(item, str)}
                if isinstance(capabilities, list)
                else set()
            ),
            "router": self.router_status(doc, text, vision),
        }

    def router_status(
        self,
        doc: Mapping[str, Any] | None,
        text: RouterSelection,
        vision: RouterSelection,
    ) -> dict[str, Any]:
        """Public description of the current choice: service IDs only, no canonical IDs."""

        configuration = doc.get("configuration") if isinstance(doc, Mapping) else None
        configuration = configuration if isinstance(configuration, Mapping) else {}
        state = {"selected": "ready", "wait": "waiting", "unavailable": "unavailable"}[text.outcome]
        return {
            "selection": self.settings.ollama_selection,
            "nsfw_preference": self.settings.ollama_nsfw,
            "state": state,
            "service": text.service,
            "nsfw": text.nsfw,
            "fallback": text.fallback,
            "reason": text.reason,
            "configuration_id": text.configuration_id,
            "exclusive": configuration.get("exclusive")
            if isinstance(configuration.get("exclusive"), bool)
            else None,
            "accepting_requests": accepting_requests(doc) if doc is not None else None,
            "vision_service": vision.service,
            "vision_nsfw": vision.nsfw,
            "vision_fallback": vision.fallback,
            "notice": (
                self._selection_message(text)
                if text.outcome == "wait"
                else self._fallback_notice(text)
                or (self._fallback_notice(vision, vision=True) if vision.service else None)
            ),
            "subscribed": bool(self.router and self.router.connected),
        }

    async def compose(
        self,
        *,
        mode: Literal["refine", "create"],
        prompt: str,
        direction: str,
        think: bool = True,
        excluded_prompts: Sequence[str] = (),
        instructions: str | None = None,
        chained: bool = False,
    ) -> ComposeResult:
        """Compose one prompt.

        ``excluded_prompts`` are earlier results the candidate must not repeat. In create mode
        they are past outputs for the same direction; in refine mode they are the recent prompts
        of a chained automatic sequence, which keeps the chain from alternating between two
        prompts. ``chained`` selects the warmer sampling schedule for that sequence.
        """
        if not self._client:
            raise AppError(
                "ollama_unavailable", "Prompt Assistant is not configured.", status_code=503
            )
        # The model is chosen per request inside _generate, from the current capabilities
        # document; an unavailable router fails there before any generation is sent.
        started = time.monotonic()
        response_diagnostics: list[dict[str, Any]] = []
        candidate_budget_failures: list[dict[str, Any]] = []
        excluded = (
            _create_excluded_prompts(prompt, direction, excluded_prompts)
            if mode == "create"
            else {}
        )
        # The input itself is compared separately so its rejection keeps the distinct
        # ``unchanged_prompt`` reason.
        refine_history = _distinct_prompts(excluded_prompts) if mode == "refine" else {}
        refine_rejections = 0
        maximum_attempts = MAX_CREATE_ATTEMPTS if mode == "create" else MAX_REFINE_ATTEMPTS
        direction_echo_attempts = 0
        candidate_seed = self.seed_resolver(
            0,
            CANDIDATE_SEED_MAXIMUM - (maximum_attempts - 1),
        )
        if (
            not isinstance(candidate_seed, int)
            or isinstance(candidate_seed, bool)
            or not 0 <= candidate_seed <= CANDIDATE_SEED_MAXIMUM - (maximum_attempts - 1)
        ):
            raise RuntimeError("candidate seed resolver returned an out-of-range value")
        for attempt in range(maximum_attempts):
            instruction = _instruction(
                mode=mode,
                prompt=prompt,
                direction=direction,
                instructions=instructions,
                # Tell the model why its previous candidate was rejected instead of only
                # redrawing the same request with another seed.
                feedback=REFINE_RETRY_FEEDBACK if refine_rejections else None,
            )
            candidate, budget_failure = await self._compose_candidate(
                mode=mode,
                instruction=instruction,
                think=think,
                attempt=attempt,
                seed=candidate_seed + attempt,
                chained=chained,
            )
            if candidate is None:
                # The candidate produced no usable structured prompt after its
                # bounded escalation (and, with thinking enabled, its no-thinking
                # fallback). Budget exhaustion is recoverable: advance to the
                # next candidate instead of terminating the composition.
                if budget_failure is None:
                    raise RuntimeError("Ollama candidate failure returned no budget diagnostics")
                candidate_budget_failures.append(budget_failure)
                continue
            final, selected_field, data, received_status = (
                candidate.final,
                candidate.selected_field,
                candidate.data,
                candidate.status,
            )
            effective_model = data.get("model")
            if not isinstance(effective_model, str) or not effective_model.strip():
                diagnostics = self._with_candidate_budget_diagnostics(
                    _response_diagnostics(
                        data,
                        status=received_status,
                        validation_stage="model_metadata",
                        selected_field=selected_field,
                    ),
                    candidate,
                )
                raise AppError(
                    "ollama_invalid_response",
                    "Prompt Assistant did not identify the Ollama model that produced its "
                    "response.",
                    details=diagnostics,
                )
            generated = candidate.generated
            if generated is not None and _may_decline(generated) and _looks_like_refusal(final):
                # A non-NSFW model answered with a refusal instead of a prompt. Report it
                # plainly; never redraw it, and never send the refusal to ComfyUI.
                raise self._declined_error(
                    generated,
                    details=self._with_candidate_budget_diagnostics(
                        _response_diagnostics(
                            data,
                            status=received_status,
                            validation_stage="refusal",
                            selected_field=selected_field,
                        ),
                        candidate,
                    ),
                    purpose="compose",
                )
            warnings = []
            if think and not candidate.used_no_thinking_fallback and not _has_thinking_output(data):
                warnings.append("thinking_output_missing")
            selected_stage = (
                "no_thinking_fallback"
                if candidate.used_no_thinking_fallback
                else "prompt_validation"
            )
            selected_diagnostic = {
                **_response_diagnostics(
                    data,
                    status=received_status,
                    validation_stage=selected_stage,
                    selected_field=selected_field,
                    warnings=warnings,
                ),
                "output_budget": candidate.selected_budget,
                "output_budget_attempt": candidate.selected_budget_attempt,
            }
            if candidate.used_no_thinking_fallback:
                selected_diagnostic["used_no_thinking_fallback"] = True
            diagnostics = self._with_candidate_budget_diagnostics(
                _response_diagnostics(
                    data,
                    status=received_status,
                    validation_stage="prompt_validation",
                    selected_field=selected_field,
                    warnings=warnings,
                ),
                candidate,
                selected_attempt=candidate.selected_budget_attempt,
                attempt_diagnostics=(
                    [*candidate.budget_diagnostics, selected_diagnostic]
                    if candidate.budget_diagnostics
                    else None
                ),
            )
            if candidate.used_no_thinking_fallback:
                diagnostics["used_no_thinking_fallback"] = True
            if warnings:
                logger.warning(
                    "ollama_thinking_output_missing",
                    extra={
                        "service": "ollama",
                        "operation": "generate",
                        "assistant_mode": mode,
                        "thinking_enabled": think,
                        **diagnostics,
                    },
                )
            normalized_final = _normalize_prompt(final)
            # Metadata only: the sampling temperature and a digest of the normalized candidate
            # let an operator tell an echoed input from a changed prompt without storing text.
            diagnostics["temperature"] = _candidate_temperature(mode, attempt, chained=chained)
            diagnostics["candidate_sha256"] = hashlib.sha256(
                normalized_final.encode("utf-8")
            ).hexdigest()
            if mode == "refine" and _same_prompt(final, prompt):
                diagnostics["validation_stage"] = "refinement_comparison"
                diagnostics["rejection_reason"] = "unchanged_prompt"
                response_diagnostics.append(diagnostics)
                refine_rejections += 1
                self._log_candidate_rejected(
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    maximum_attempts=maximum_attempts,
                    reason="unchanged_prompt",
                )
                continue
            if mode == "refine" and normalized_final in refine_history:
                # A chained sequence that returns an earlier prompt alternates between two
                # prompts and queues duplicate images; treat it like an unchanged result.
                diagnostics["validation_stage"] = "refinement_comparison"
                diagnostics["rejection_reason"] = "repeated_prompt"
                response_diagnostics.append(diagnostics)
                refine_rejections += 1
                self._log_candidate_rejected(
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    maximum_attempts=maximum_attempts,
                    reason="repeated_prompt",
                )
                continue
            if mode == "create" and _is_direction_echo(final, direction):
                # Create mode must expand the direction. A candidate that only repeats
                # (or truncates) it is a degenerate sample, not a new prompt: reject it
                # and redraw with the next seed instead of accepting it.
                diagnostics["validation_stage"] = "creation_comparison"
                direction_echo_attempts += 1
                response_diagnostics.append(diagnostics)
                self._log_candidate_rejected(
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    maximum_attempts=maximum_attempts,
                    reason="direction_echo",
                )
                excluded.setdefault(normalized_final, final.strip())
                continue
            diagnostics["validation_stage"] = "complete"
            response_diagnostics.append(diagnostics)
            if mode != "create" or normalized_final not in excluded:
                raw_response = (
                    dict(diagnostics)
                    if len(response_diagnostics) == 1
                    else {
                        "attempts": response_diagnostics,
                        "selected_attempt": len(response_diagnostics),
                    }
                )
                served = generated.selection if generated is not None else None
                if generated is not None:
                    raw_response["router"] = _router_diagnostics(generated)
                return ComposeResult(
                    prompt=final,
                    model=effective_model.strip(),
                    raw_response=raw_response,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    service=served.service if served else None,
                    fallback=bool(served and served.fallback),
                    nsfw=served.nsfw if served else None,
                )
            self._log_candidate_rejected(
                mode=mode,
                think=think,
                attempt=attempt,
                maximum_attempts=maximum_attempts,
                reason="excluded_prompt",
            )
            excluded.setdefault(normalized_final, final.strip())
        if len(candidate_budget_failures) == maximum_attempts:
            # Every candidate overflowed its thinking schedule (with thinking
            # enabled, each also failed its no-thinking fallback). Only a
            # genuinely broken router/model reaches this point, so the terminal
            # budget error is finally appropriate.
            last_failure = candidate_budget_failures[-1]
            exhausted = {
                **last_failure["output_budget_attempt_diagnostics"][-1],
                "validation_stage": "output_budget_exhausted",
                "output_budget_attempts": last_failure["output_budget_attempts"],
                "output_budgets": last_failure["output_budgets"],
                "attempted_no_thinking_fallback": last_failure["attempted_no_thinking_fallback"],
                "output_budget_attempt_diagnostics": (
                    last_failure["output_budget_attempt_diagnostics"]
                ),
                "candidate_budget_diagnostics": candidate_budget_failures,
            }
            raise AppError(
                "ollama_output_budget_exhausted",
                "Prompt Assistant exhausted its output-token budget before completing "
                "a structured prompt after bounded retries.",
                status_code=503,
                details=exhausted,
            )
        if mode == "refine":
            raise AppError(
                "prompt_refinement_unchanged",
                "Prompt Assistant repeated the current prompt instead of applying the Creative "
                "Direction after retrying. Check the Creative Direction for unfilled placeholders "
                "or rules that conflict with the prompt.",
                status_code=422,
                details={
                    **(response_diagnostics[-1] if response_diagnostics else {}),
                    "validation_stage": "refinement_distinctness",
                    "attempt_diagnostics": response_diagnostics,
                },
            )
        if mode == "create" and direction_echo_attempts == maximum_attempts:
            raise AppError(
                "prompt_creation_unchanged",
                "Prompt Assistant could not expand the creative direction into a new prompt. "
                "Add more detail to the Creative Direction or try again.",
                status_code=422,
                details={
                    **(response_diagnostics[-1] if response_diagnostics else {}),
                    "validation_stage": "create_distinctness",
                    "attempt_diagnostics": response_diagnostics,
                },
            )
        raise AppError(
            "ollama_invalid_response",
            "Prompt Assistant could not produce a distinct new prompt after retrying.",
            details={
                **(response_diagnostics[-1] if response_diagnostics else {}),
                "validation_stage": "create_distinctness",
                "attempt_diagnostics": response_diagnostics,
            },
        )

    async def evaluate_image(
        self,
        *,
        image_data_url: str,
        expectations: Sequence[str],
        threshold: int,
        think: bool = True,
        instructions: str | None = None,
    ) -> VisionEvaluationResult:
        """Score one image against numbered expectations with the vision model.

        The image travels as an inline ``data:`` URL in ``messages[].images``; the
        router forwards that form unchanged to llama.cpp. The evaluator never sees
        the prompt that produced the image. Output-budget escalation and the
        no-thinking fallback mirror composition; a structurally invalid score sheet
        is redrawn with the next seed.
        """

        if not self._client:
            raise AppError(
                "ollama_unavailable", "Prompt Assistant is not configured.", status_code=503
            )
        # The reviewer is chosen per request with the vision requirement: only a model with
        # "image" in input_modalities is a candidate (see _generate and select_model).
        started = time.monotonic()
        instruction = vision_instruction(
            instructions or DEFAULT_VISION_CHECK_INSTRUCTIONS, expectations
        )
        schema = evaluation_schema(len(expectations))
        seed = self.seed_resolver(0, CANDIDATE_SEED_MAXIMUM - (MAX_VISION_CANDIDATES - 1))
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not 0 <= seed <= CANDIDATE_SEED_MAXIMUM - (MAX_VISION_CANDIDATES - 1)
        ):
            raise RuntimeError("candidate seed resolver returned an out-of-range value")
        diagnostics: list[dict[str, Any]] = []
        last_generated: GenerateResult | None = None
        plan: list[tuple[bool, int]] = [(think, budget) for budget in OUTPUT_TOKEN_BUDGETS]
        if think:
            plan.append((False, OUTPUT_TOKEN_BUDGETS[0]))
        for candidate in range(MAX_VISION_CANDIDATES):
            for request_think, budget in plan:
                payload: dict[str, Any] = {
                    "messages": [
                        {"role": "user", "content": instruction, "images": [image_data_url]}
                    ],
                    "stream": False,
                    "think": THINKING_EFFORT if request_think else False,
                    "format": schema,
                    "options": {
                        "temperature": VISION_TEMPERATURE,
                        "seed": seed + candidate,
                        "num_predict": budget,
                    },
                    "seed": seed + candidate,
                }
                try:
                    received = await self._generate(
                        payload, mode="vision", think=request_think, vision=True
                    )
                except AppError as exc:
                    if exc.code == "ollama_generate_rejected":
                        raise AppError(
                            "vision_unavailable",
                            "The Creative Direction model rejected the image. Expectations need "
                            "a model with vision enabled on the LLM Router.",
                            status_code=502,
                            details=exc.details,
                        ) from exc
                    raise
                last_generated = received
                data = received.data if isinstance(received.data, dict) else {}
                diagnostic = _response_diagnostics(
                    data, status=received.status, validation_stage="vision_evaluation"
                )
                diagnostic.update(
                    candidate_attempt=candidate + 1,
                    output_budget=budget,
                    thinking_enabled=request_think,
                    image_bytes=len(image_data_url),
                )
                parsed, selected_field = (
                    (None, None) if received.incomplete else _response_object_with_source(data)
                )
                if (
                    parsed is None
                    and _may_decline(received)
                    and diagnostic["done_reason"] != "length"
                ):
                    # A non-NSFW reviewer that answers without a score sheet declined the image;
                    # report it instead of redrawing it.
                    diagnostics.append(diagnostic)
                    raise self._declined_error(
                        received,
                        details={"attempt_diagnostics": diagnostics[-12:]},
                        purpose="vision",
                    )
                if parsed is not None:
                    try:
                        evaluation = validate_evaluation(parsed, expectations, threshold)
                    except ValueError as exc:
                        # Metadata only: the fault class, never the reviewer's text.
                        diagnostic["rejection_reason"] = str(exc)
                        diagnostics.append(diagnostic)
                        logger.info(
                            "ollama_vision_candidate_rejected",
                            extra={
                                "service": "ollama",
                                "operation": "vision",
                                "candidate_attempt": candidate + 1,
                                "rejection_reason": str(exc),
                            },
                        )
                        break
                    effective_model = data.get("model")
                    diagnostic["selected_field"] = selected_field
                    if _may_decline(received) and _looks_like_refusal(evaluation.summary):
                        diagnostic["validation_stage"] = "refusal"
                        diagnostics.append(diagnostic)
                        raise self._declined_error(
                            received,
                            details={"attempt_diagnostics": diagnostics[-12:]},
                            purpose="vision",
                        )
                    diagnostic["validation_stage"] = "complete"
                    diagnostics.append(diagnostic)
                    served = received.selection
                    return VisionEvaluationResult(
                        evaluation=evaluation,
                        model=effective_model.strip()
                        if isinstance(effective_model, str) and effective_model.strip()
                        else (served.service if served and served.service else "unknown"),
                        diagnostics={
                            "attempts": diagnostics,
                            "selected_attempt": len(diagnostics),
                            "router": _router_diagnostics(received),
                        },
                        duration_ms=int((time.monotonic() - started) * 1000),
                        service=served.service if served else None,
                        fallback=bool(served and served.fallback),
                    )
                diagnostics.append(diagnostic)
                if diagnostic["done_reason"] != "length":
                    break
        details: dict[str, Any] = {"attempt_diagnostics": diagnostics[-12:]}
        if last_generated is not None:
            details["router"] = _router_diagnostics(last_generated)
        raise AppError(
            "vision_check_invalid_response",
            "The vision model did not return a usable score for every expectation.",
            status_code=502,
            details=details,
        )

    @staticmethod
    def _with_candidate_budget_diagnostics(
        diagnostics: dict[str, Any],
        candidate: _CandidateCompose,
        *,
        selected_attempt: int | None = None,
        attempt_diagnostics: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # The candidate's actual allowance history, including the reset to the
        # base allowance when the no-thinking fallback produced the prompt.
        return _with_output_budget_diagnostics(
            diagnostics,
            attempts=len(candidate.budget_diagnostics) + 1,
            allowances=[entry["output_budget"] for entry in candidate.budget_diagnostics]
            + [candidate.selected_budget],
            selected_attempt=selected_attempt,
            attempt_diagnostics=attempt_diagnostics,
        )

    async def _compose_candidate(
        self,
        *,
        mode: str,
        instruction: str,
        think: bool,
        attempt: int,
        seed: int,
        chained: bool = False,
    ) -> tuple[_CandidateCompose | None, dict[str, Any] | None]:
        """Run one candidate's bounded output-budget escalation.

        The user's thinking effort is preserved across the ``OUTPUT_TOKEN_BUDGETS``
        schedule. Thinking length is unbounded, so a thinking trace can always
        outgrow any fixed allowance; when thinking is enabled and every level
        ends in a schema-incomplete ``done_reason: "length"``, one extra attempt
        runs with thinking disabled at the base allowance, because the
        deliverable is the short structured prompt (the reasoning trace is not
        submitted to ComfyUI) and a composition without a trace fits far below
        the shared allowance. Returns ``(candidate, None)`` when a usable
        structured prompt was produced, otherwise ``(None,
        candidate_budget_diagnostics)`` so the caller can advance to the next
        candidate instead of terminating the composition.
        """
        plan: list[tuple[bool, int, str]] = [
            (think, budget, "structured_prompt") for budget in OUTPUT_TOKEN_BUDGETS
        ]
        if think:
            plan.append((False, OUTPUT_TOKEN_BUDGETS[0], "no_thinking_fallback"))
        budget_diagnostics: list[dict[str, Any]] = []
        attempted_no_thinking_fallback = False

        def _failure_diagnostics() -> dict[str, Any]:
            return {
                "candidate_attempt": attempt + 1,
                "attempted_no_thinking_fallback": attempted_no_thinking_fallback,
                "output_budget_attempts": len(budget_diagnostics),
                "output_budgets": [entry["output_budget"] for entry in budget_diagnostics],
                "output_budget_attempt_diagnostics": list(budget_diagnostics),
            }

        for plan_index, (request_think, output_budget, stage) in enumerate(plan):
            if stage == "no_thinking_fallback":
                attempted_no_thinking_fallback = True
            output_budget_attempt = plan_index + 1
            payload = _generate_payload(
                mode=mode,
                instruction=instruction,
                think=request_think,
                attempt=attempt,
                seed=seed,
                output_budget=output_budget,
                chained=chained,
            )
            received = await self._generate(payload, mode=mode, think=request_think)
            if not isinstance(received.data, dict):
                diagnostics = _with_output_budget_diagnostics(
                    _response_diagnostics(
                        {},
                        status=received.status,
                        validation_stage="response_envelope",
                    ),
                    attempts=output_budget_attempt,
                    allowances=OUTPUT_TOKEN_BUDGETS[:output_budget_attempt],
                )
                raise AppError(
                    "ollama_invalid_response",
                    "Prompt Assistant returned an invalid response envelope.",
                    details=diagnostics,
                )
            data = received.data
            # A router answer marked incomplete (it reached the output limit) is never a
            # completed prompt, even when its text happens to parse.
            final, selected_field = (
                ("", None) if received.incomplete else _response_prompt_with_source(data)
            )
            if final:
                return (
                    _CandidateCompose(
                        final=final,
                        selected_field=selected_field,
                        data=data,
                        status=received.status,
                        budget_diagnostics=tuple(budget_diagnostics),
                        selected_budget_attempt=output_budget_attempt,
                        selected_budget=output_budget,
                        used_no_thinking_fallback=stage == "no_thinking_fallback",
                        generated=received,
                    ),
                    None,
                )
            diagnostics = _response_diagnostics(
                data,
                status=received.status,
                validation_stage=stage,
            )
            diagnostics["output_budget"] = output_budget
            diagnostics["output_budget_attempt"] = output_budget_attempt
            budget_diagnostics.append(diagnostics)
            if stage == "no_thinking_fallback":
                # The single no-thinking attempt produced no usable prompt
                # (overflowed the base allowance or returned malformed output).
                # Thinking overflow plus a failed fallback exhausts the
                # candidate; the caller advances to the next candidate.
                return None, _failure_diagnostics()
            if diagnostics["done_reason"] == "length":
                if plan_index + 1 < len(plan):
                    if plan[plan_index + 1][2] == "no_thinking_fallback":
                        self._log_no_thinking_fallback(mode=mode, attempt=attempt)
                    else:
                        logger.info(
                            "ollama_output_budget_retry",
                            extra={
                                "service": "ollama",
                                "operation": "generate",
                                "assistant_mode": mode,
                                "thinking_enabled": think,
                                "candidate_attempt": attempt + 1,
                                "output_budget_attempt": output_budget_attempt,
                                "output_budget": output_budget,
                                "next_output_budget": plan[plan_index + 1][1],
                                "done_reason": "length",
                            },
                        )
                    continue
                # Thinking is disabled for this candidate, so the schedule was
                # the only recovery path; the candidate is exhausted.
                return None, _failure_diagnostics()
            has_output_text = any(
                isinstance(data.get(source), str) and bool(data[source].strip())
                for source in ("response", "thinking")
            )
            invalid = _with_output_budget_diagnostics(
                diagnostics,
                attempts=output_budget_attempt,
                allowances=OUTPUT_TOKEN_BUDGETS[:output_budget_attempt],
            )
            if _may_decline(received):
                # A non-NSFW model that answers without a structured prompt most likely
                # declined the direction; say so instead of a generic schema failure.
                raise self._declined_error(received, details=invalid, purpose="compose")
            raise AppError(
                "ollama_invalid_response",
                (
                    "Prompt Assistant returned malformed structured prompt output."
                    if has_output_text
                    else "Prompt Assistant returned no usable prompt."
                ),
                details=invalid,
            )
        raise RuntimeError("Ollama output-budget retry loop exited unexpectedly")

    @staticmethod
    def _log_no_thinking_fallback(*, mode: str, attempt: int) -> None:
        # Metadata only: which candidate drops thinking and at which allowance.
        # No prompt, Creative Direction, or reasoning text is ever logged.
        logger.info(
            "ollama_output_budget_no_thinking_fallback",
            extra={
                "service": "ollama",
                "operation": "generate",
                "assistant_mode": mode,
                "thinking_enabled": True,
                "fallback_thinking_enabled": False,
                "candidate_attempt": attempt + 1,
                "output_budget_attempt": len(OUTPUT_TOKEN_BUDGETS) + 1,
                "output_budget": OUTPUT_TOKEN_BUDGETS[0],
                "done_reason": "length",
            },
        )

    async def _generate(
        self,
        payload: dict[str, Any],
        *,
        mode: str,
        think: bool,
        vision: bool = False,
    ) -> GenerateResult:
        """Send one ``/api/chat`` request to the service chosen for it, per the contract.

        The model is chosen from the current capabilities document for this request and its
        ``think`` value adapted to that model's published efforts. Errors are classified by the
        router's ``error.code`` first (contract section 10): SERVICE_OFFLINE,
        BACKEND_UNAVAILABLE and MODEL_NOT_FOUND choose again at once without the failed service
        and consume no retries; BACKEND_DRAINING and MAINTENANCE_MODE wait with backoff, then
        choose again; other 5xx, 408, 429 and connection failures retry the same service; other
        4xx fail. A read timeout is not resubmitted, because that would lose the request's place
        in the router's queue.
        """

        if not self._client:
            raise RuntimeError("Ollama client is not configured")
        purpose = "vision" if vision else "compose"
        budget = _RouterWaitBudget(limit=self.settings.ollama_router_wait_seconds)
        exclude: set[str] = set()
        attempt = 1
        while True:
            selection = await self._choose(vision=vision, exclude=exclude, budget=budget)
            if selection.outcome != "selected" or selection.service is None:
                raise self._unavailable_error(selection, purpose=purpose, waited=budget.waited)
            service = selection.service
            request_payload = {
                **payload,
                "model": service,
                "think": thinking_value(selection.model, payload.get("think")),
            }
            fitted = _fit_output_limit(payload, selection.model)
            if fitted is not None:
                request_payload["options"] = fitted
            sent_think = request_payload["think"]
            router_code: str | None = None
            try:
                async with self._slots.hold(
                    service, functools.partial(self._service_slots, service)
                ):
                    response = await self._client.post("/api/chat", json=request_payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                upstream_status = exc.response.status_code
                response_data = _safe_json_object(exc.response)
                router_code = error_code(response_data)
                action = classify_error(upstream_status, response_data)
                if action == FALLBACK:
                    alternative = self.select_model(vision=vision, exclude={*exclude, service})
                    if alternative.outcome == "unavailable" and router_code == (
                        "BACKEND_UNAVAILABLE"
                    ):
                        # Nothing to fall back to: an unhealthy backend is retried with backoff.
                        action = RETRY
                retryable = action == RETRY and (
                    upstream_status in RETRYABLE_GENERATE_STATUS_CODES or upstream_status >= 500
                )
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="http_status",
                    retryable=retryable or action in {FALLBACK, WAIT},
                    upstream_status=upstream_status,
                    service=service,
                    router_code=router_code,
                    router_action=action,
                )
                if action == FALLBACK:
                    if router_code == "MODEL_NOT_FOUND":
                        logger.warning(
                            "llm_router_model_not_found",
                            extra={
                                "service": "llm_router",
                                "operation": "selection",
                                "router_service": service,
                                "router_code": router_code,
                            },
                        )
                    # The document lagged the router; refresh it, then choose again at once
                    # without the failed service. This consumes no retry attempt.
                    exclude.add(service)
                    await self._refresh_after_router_error()
                    continue
                if action == WAIT:
                    if budget.exhausted:
                        raise self._unavailable_error(
                            RouterSelection(
                                "wait",
                                reason="router_maintenance"
                                if router_code == "MAINTENANCE_MODE"
                                else "router_draining",
                                configuration_id=selection.configuration_id,
                            ),
                            purpose=purpose,
                            waited=budget.waited,
                        ) from exc
                    await self._pause_for_router(budget)
                    await self._refresh_after_router_error()
                    exclude.clear()
                    continue
                details = _generate_error_details(
                    attempt=attempt,
                    failure_kind="http_status",
                    think=think,
                    upstream_status=upstream_status,
                    response_data=response_data,
                    router_code=router_code,
                    selection=selection,
                    sent_think=sent_think,
                )
                if router_code in DECLINE_CODES and _selection_may_decline(selection):
                    raise self._declined_error(
                        GenerateResult(
                            data=response_data, status=upstream_status, selection=selection
                        ),
                        details=details,
                        purpose=purpose,
                    ) from exc
                if action == RETRY:
                    if attempt < MAX_GENERATE_ATTEMPTS:
                        await self._wait_before_generate_retry(attempt)
                        attempt += 1
                        continue
                    raise AppError(
                        "ollama_generate_unavailable",
                        "The Ollama router could not complete prompt composition after retrying.",
                        status_code=503,
                        details=details,
                    ) from exc
                if router_code == "context_length_exceeded":
                    raise AppError(
                        "ollama_context_exceeded",
                        "The request does not fit the context window of the model serving it on "
                        "the LLM Router. Shorten the instructions or Creative Direction.",
                        status_code=422,
                        details=details,
                    ) from exc
                raise AppError(
                    "ollama_generate_rejected",
                    "The Ollama router rejected the prompt composition request.",
                    status_code=502,
                    details=details,
                ) from exc
            except httpx.TimeoutException as exc:
                # Only a connection that never opened is retried. A read timeout means the
                # request may still be queued or running; resubmitting it would lose its place.
                retryable = isinstance(exc, httpx.ConnectTimeout)
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="timeout",
                    retryable=retryable,
                    service=service,
                )
                if retryable and attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    attempt += 1
                    continue
                raise AppError(
                    "ollama_generate_timeout",
                    "The Ollama router timed out while composing the prompt.",
                    status_code=504,
                    details=_generate_error_details(
                        attempt=attempt,
                        failure_kind="timeout",
                        think=think,
                        selection=selection,
                        sent_think=sent_think,
                    ),
                ) from exc
            except httpx.HTTPError as exc:
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="transport",
                    retryable=True,
                    service=service,
                )
                if attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    attempt += 1
                    continue
                raise AppError(
                    "ollama_generate_transport_error",
                    "Prompt Assistant lost its connection to the Ollama router after retrying.",
                    status_code=503,
                    details=_generate_error_details(
                        attempt=attempt,
                        failure_kind="transport",
                        think=think,
                        selection=selection,
                        sent_think=sent_think,
                    ),
                ) from exc
            try:
                raw = response.json()
            except ValueError as exc:
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="invalid_json",
                    retryable=True,
                    upstream_status=response.status_code,
                    service=service,
                )
                if attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    attempt += 1
                    continue
                raise AppError(
                    "ollama_generate_invalid_json",
                    "The Ollama router returned malformed JSON for prompt composition.",
                    status_code=502,
                    details=_generate_error_details(
                        attempt=attempt,
                        failure_kind="invalid_json",
                        think=think,
                        upstream_status=response.status_code,
                        selection=selection,
                        sent_think=sent_think,
                    ),
                ) from exc
            received: Any = _normalize_chat_response(raw)
            incomplete, incomplete_code = _router_incomplete(received)
            if incomplete and not (
                isinstance(received, dict) and received.get("done_reason") == "length"
            ):
                # Contract section 9: an incomplete answer is never a completed one, even
                # with text. Classify it like the transient upstream failure it reports.
                failure = AppError(
                    "ollama_generate_incomplete",
                    "The Ollama router ended prompt composition without a complete answer.",
                    status_code=502,
                    details=_generate_error_details(
                        attempt=attempt,
                        failure_kind="incomplete",
                        think=think,
                        upstream_status=response.status_code,
                        response_data=received if isinstance(received, dict) else None,
                        router_code=incomplete_code,
                        selection=selection,
                        sent_think=sent_think,
                    ),
                )
                self._log_generate_failure(
                    failure,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="incomplete",
                    retryable=True,
                    upstream_status=response.status_code,
                    service=service,
                    router_code=incomplete_code,
                    router_action=RETRY,
                )
                if incomplete_code in DECLINE_CODES and _selection_may_decline(selection):
                    raise self._declined_error(
                        GenerateResult(
                            data=received, status=response.status_code, selection=selection
                        ),
                        details=failure.details,
                        purpose=purpose,
                    )
                if attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    attempt += 1
                    continue
                raise failure
            if attempt > 1:
                logger.info(
                    "ollama_generate_recovered",
                    extra={
                        "service": "ollama",
                        "operation": "generate",
                        "assistant_mode": mode,
                        "thinking_enabled": think,
                        "attempt": attempt,
                        "max_attempts": MAX_GENERATE_ATTEMPTS,
                    },
                )
            return GenerateResult(
                data=received,
                status=response.status_code,
                selection=selection,
                think=sent_think,
                incomplete=incomplete,
            )

    async def _refresh_after_router_error(self) -> None:
        router = self.router
        if router is not None and not router.connected:
            await router.refresh()

    def _unavailable_error(
        self, selection: RouterSelection, *, purpose: str, waited: float = 0.0
    ) -> AppError:
        details: dict[str, Any] = {
            "operation": "generate",
            "failure_kind": "router_selection",
            "router": selection.public(),
        }
        if selection.outcome == "wait":
            details["router_wait_seconds"] = round(waited, 1)
            message = (
                "The LLM Router is still switching configuration after "
                f"{max(1, round(waited / 60))} minutes of waiting; try again shortly."
                if selection.reason != "router_maintenance"
                else "The LLM Router is still in maintenance; try again shortly."
            )
        else:
            message = self._selection_message(selection)
        if purpose == "vision" and selection.reason == "no_vision_model":
            return AppError(
                "vision_unavailable",
                "No available model on the LLM Router can inspect images, so expectations "
                "can't be verified. Apply Creative Direction still works without the check.",
                status_code=503,
                details=details,
            )
        suffix = " Manual prompting still works." if purpose == "compose" else ""
        return AppError(
            "ollama_unavailable", f"{message}{suffix}", status_code=503, details=details
        )

    def _declined_error(
        self, generated: GenerateResult, *, details: dict[str, Any], purpose: str
    ) -> AppError:
        """A non-NSFW model declined: say so plainly, with no retry (contract section 5)."""

        selection = generated.selection
        name = _service_label(selection.service if selection else None)
        if selection is not None and selection.fallback:
            if self.settings.ollama_selection == "named":
                preferred = _service_label(self.settings.ollama_named_models[0])
                prefix = f"{preferred} is unavailable; "
            else:
                prefix = "No NSFW model is available; "
        else:
            prefix = ""
        action = (
            "Edit the expectations or verify the image yourself."
            if purpose == "vision"
            else "Edit the Creative Direction or write the prompt manually."
        )
        logger.warning(
            "llm_router_model_declined",
            extra={
                "service": "llm_router",
                "operation": purpose,
                "router_service": selection.service if selection else None,
                "router_fallback": bool(selection and selection.fallback),
                "router_nsfw": selection.nsfw if selection else None,
            },
        )
        return AppError(
            "ollama_model_declined",
            f"{prefix}{name} declined this request. {action}",
            status_code=422,
            details={**details, "router": _router_diagnostics(generated)},
        )

    async def _wait_before_generate_retry(self, failed_attempt: int) -> None:
        await self.retry_sleeper(GENERATE_RETRY_BASE_SECONDS * (2 ** (failed_attempt - 1)))

    @staticmethod
    def _log_candidate_rejected(
        *,
        mode: str,
        think: bool,
        attempt: int,
        maximum_attempts: int,
        reason: str,
    ) -> None:
        # Candidate text is deliberately not logged; only the redrew-the-sample metadata
        # is recorded so operators can see distinctness retries without exposing prompts.
        logger.info(
            "ollama_candidate_rejected",
            extra={
                "service": "ollama",
                "operation": "generate",
                "assistant_mode": mode,
                "thinking_enabled": think,
                "candidate_attempt": attempt + 1,
                "max_candidate_attempts": maximum_attempts,
                "rejection_reason": reason,
            },
        )

    @staticmethod
    def _log_generate_failure(
        exc: Exception,
        *,
        mode: str,
        think: bool,
        attempt: int,
        failure_kind: str,
        retryable: bool,
        upstream_status: int | None = None,
        service: str | None = None,
        router_code: str | None = None,
        router_action: str | None = None,
    ) -> None:
        logger.warning(
            "ollama_generate_attempt_failed",
            extra={
                "service": "ollama",
                "operation": "generate",
                "assistant_mode": mode,
                "thinking_enabled": think,
                "attempt": attempt,
                "max_attempts": MAX_GENERATE_ATTEMPTS,
                "failure_kind": failure_kind,
                "retryable": retryable,
                "upstream_status": upstream_status,
                "exception_class": type(exc).__name__,
                "router_service": service,
                "router_code": router_code,
                "router_action": router_action,
            },
        )


def _service_label(service: str | None) -> str:
    """A service ID as a display name: ``daytime`` becomes ``Daytime``."""

    return service[:1].upper() + service[1:] if service else "The fallback model"


def _selection_may_decline(selection: RouterSelection | None) -> bool:
    # Only a model not declared NSFW is expected to refuse; an abliterated model is not.
    return selection is not None and selection.nsfw is not True


def _may_decline(generated: GenerateResult | None) -> bool:
    return generated is not None and _selection_may_decline(generated.selection)


def _looks_like_refusal(text: str) -> bool:
    """Whether a candidate opens like a refusal rather than an image prompt or review."""

    normalized = " ".join(text.replace("\u2019", "'").split()).casefold().lstrip("\"'*_ ")
    return normalized.startswith(_REFUSAL_OPENINGS)


def _estimated_input_tokens(payload: Mapping[str, Any]) -> int:
    """A deliberately high estimate of a request's formatted input tokens.

    Three UTF-8 bytes per token overestimates English text and JSON; each inline image is
    counted at 2048 tokens, above what a 1024 px JPEG costs a llama.cpp vision encoder.
    """

    total = 64
    messages = payload.get("messages")
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += -(-len(content.encode("utf-8")) // 3) + 8
        images = message.get("images")
        total += 2048 * (len(images) if isinstance(images, list) else 0)
    schema = payload.get("format")
    if schema is not None:
        total += -(-len(json.dumps(schema)) // 3)
    return total


def _fit_output_limit(
    payload: Mapping[str, Any], model: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Options with ``num_predict`` reduced to fit the serving model's context, or None.

    The router admits a request when formatted input + requested output +
    ``metadata.context_safety_reserve`` fits ``context_window`` (contract section 8). The
    limits always come from the model chosen for this request, never a hard-coded size; a
    request that cannot fit at all is sent unchanged so the router's exact
    ``context_length_exceeded`` arithmetic is reported.
    """

    options = payload.get("options")
    window = model.get("context_window") if isinstance(model, Mapping) else None
    if not isinstance(options, Mapping) or not isinstance(window, int) or window <= 0:
        return None
    requested = options.get("num_predict")
    if not isinstance(requested, int) or isinstance(requested, bool) or requested <= 0:
        return None
    metadata = model.get("metadata") if isinstance(model, Mapping) else None
    reserve = metadata.get("context_safety_reserve") if isinstance(metadata, Mapping) else None
    reserve = reserve if isinstance(reserve, int) and reserve >= 0 else 1024
    room = window - reserve - _estimated_input_tokens(payload)
    if room < 1 or requested <= room:
        return None
    logger.warning(
        "llm_router_output_limit_reduced",
        extra={
            "service": "llm_router",
            "operation": "generate",
            "output_budget": requested,
            "next_output_budget": room,
        },
    )
    return {**options, "num_predict": room}


def _router_incomplete(data: Any) -> tuple[bool, str | None]:
    """Whether the router marked an answer incomplete, and the code it gave (section 9)."""

    if not isinstance(data, dict):
        return False, None
    router = data.get("x_router")
    status = router.get("status") if isinstance(router, dict) else None
    if status != "incomplete" and data.get("done_reason") != "error":
        return False, None
    code = router.get("stop_reason") if isinstance(router, dict) else None
    if not isinstance(code, str) or not code:
        code = error_code(data)
    return True, code if isinstance(code, str) and code else None


def _router_diagnostics(generated: GenerateResult) -> dict[str, Any]:
    """Which service served a request and how: service IDs only, never canonical IDs."""

    selection = generated.selection
    diagnostics: dict[str, Any] = {
        **(selection.public() if selection else {}),
        "thinking_effort": generated.think,
    }
    data = generated.data if isinstance(generated.data, dict) else {}
    router = data.get("x_router")
    record_id = router.get("record_id") if isinstance(router, dict) else None
    if isinstance(record_id, str) and record_id:
        diagnostics["record_id"] = record_id[:100]
    return diagnostics


def _normalize_chat_response(data: Any) -> Any:
    if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
        return data
    # Keep the existing structured-output parser and bounded diagnostics shared across
    # final content and parser-compatible thinking, without retaining raw reasoning.
    normalized = {key: value for key, value in data.items() if key != "message"}
    message = data["message"]
    normalized["response"] = message.get("content", "")
    if "thinking" in message:
        normalized["thinking"] = message["thinking"]
    return normalized


def _json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _response_object_with_source(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    # Like prompt composition: final content first, then a schema object a thinking
    # parser left in ``thinking``. Unstructured reasoning is never accepted.
    for source in ("response", "thinking"):
        value = data.get(source)
        parsed = _json_object(value) if isinstance(value, str) else None
        if parsed is not None and "results" in parsed:
            return parsed, source
    return None, None


def _generate_error_details(
    *,
    attempt: int,
    failure_kind: str,
    think: bool,
    upstream_status: int | None = None,
    response_data: dict[str, Any] | None = None,
    router_code: str | None = None,
    selection: RouterSelection | None = None,
    sent_think: bool | str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "operation": "generate",
        "failure_kind": failure_kind,
        "attempts": attempt,
        "thinking_enabled": think,
    }
    if upstream_status is not None:
        details["upstream_status"] = upstream_status
    details.update(
        _response_diagnostics(
            response_data or {},
            status=upstream_status,
            validation_stage=failure_kind,
        )
    )
    if router_code is not None:
        details["router_code"] = router_code[:100]
    if selection is not None:
        details["router"] = {**selection.public(), "thinking_effort": sent_think}
    return details


def _safe_json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _safe_metadata_string(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped[:maximum] if stripped else None


def _response_diagnostics(
    data: dict[str, Any],
    *,
    status: int | None,
    validation_stage: str,
    selected_field: str | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    response_text = data.get("response")
    thinking_text = data.get("thinking")
    diagnostics: dict[str, Any] = {
        "model": _safe_metadata_string(data.get("model"), maximum=255),
        "status": status,
        "field_presence": {
            "response": "response" in data,
            "thinking": "thinking" in data,
        },
        "response_length": len(response_text) if isinstance(response_text, str) else 0,
        "thinking_length": len(thinking_text) if isinstance(thinking_text, str) else 0,
        "done_reason": _safe_metadata_string(data.get("done_reason"), maximum=100),
        "validation_stage": validation_stage,
    }
    if selected_field is not None:
        diagnostics["selected_field"] = selected_field
    if warnings:
        diagnostics["warnings"] = list(warnings)
    router = data.get("x_router")
    if isinstance(router, dict):
        # The router's own completion state and archive record, for tracing (section 9).
        for key, source in (
            ("router_status", "status"),
            ("router_stop_reason", "stop_reason"),
            ("router_record_id", "record_id"),
        ):
            value = _safe_metadata_string(router.get(source), maximum=100)
            if value is not None:
                diagnostics[key] = value
    return diagnostics


def _with_output_budget_diagnostics(
    diagnostics: dict[str, Any],
    *,
    attempts: int,
    allowances: Sequence[int],
    selected_attempt: int | None = None,
    attempt_diagnostics: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = {
        **diagnostics,
        "output_budget_attempts": attempts,
        "output_budgets": list(allowances),
    }
    if selected_attempt is not None:
        enriched["selected_output_budget_attempt"] = selected_attempt
        enriched["selected_output_budget"] = allowances[selected_attempt - 1]
    if attempt_diagnostics is not None:
        enriched["output_budget_attempt_diagnostics"] = list(attempt_diagnostics)
    return enriched


def _instruction(
    *,
    mode: str,
    prompt: str,
    direction: str,
    instructions: str | None = None,
    feedback: str | None = None,
) -> str:
    prefix = DEFAULT_PROMPT_INSTRUCTIONS[mode] if instructions is None else instructions.strip()
    if mode == "refine":
        instruction = f"{prefix}\n\nCurrent prompt:\n{prompt}\n\nCreative direction:\n{direction}"
        return f"{instruction}\n\nCorrection:\n{feedback}" if feedback else instruction
    return f"{prefix}\n\n{direction}"


def _extract_prompt(raw_text: str) -> str:
    text = raw_text.strip()
    structured = _extract_structured_prompt(text)
    return structured or text


def _same_prompt(first: str, second: str) -> bool:
    return _normalize_prompt(first) == _normalize_prompt(second)


def _normalize_prompt(value: str) -> str:
    return " ".join(value.split()).casefold()


def _distinct_prompts(prompts: Sequence[str]) -> dict[str, str]:
    distinct: dict[str, str] = {}
    for prompt in prompts:
        normalized = _normalize_prompt(prompt)
        if normalized:
            distinct.setdefault(normalized, prompt.strip())
    return distinct


def _create_excluded_prompts(
    current_prompt: str,
    direction: str,
    excluded_prompts: Sequence[str],
) -> dict[str, str]:
    # Create mode must return a prompt distinct from the current prompt and from past
    # outputs for the same direction, and it must expand the direction itself: a
    # candidate that only repeats the direction is a degenerate sample, not a new
    # prompt, so the normalized direction is part of the forbidden set.
    return _distinct_prompts((current_prompt, direction, *excluded_prompts))


def _is_direction_echo(candidate: str, direction: str) -> bool:
    # Deterministic create-mode rule: a candidate is a direction echo when its
    # normalized text is contained in the normalized direction, which covers
    # case/whitespace-variant verbatim echoes, truncated directions, and fragments.
    # A genuine expansion or paraphrase is always strictly longer than the direction
    # or not a substring of it, so it is never classified as an echo.
    normalized_direction = _normalize_prompt(direction)
    if not normalized_direction:
        return False
    return _normalize_prompt(candidate) in normalized_direction


def _candidate_temperature(mode: str, attempt: int, *, chained: bool = False) -> float:
    if mode == "refine":
        schedule = CHAINED_REFINE_TEMPERATURES if chained else REFINE_TEMPERATURES
        return schedule[min(attempt, len(schedule) - 1)]
    return min(0.9, 0.5 + (attempt * 0.2))


def _generate_payload(
    *,
    mode: str,
    instruction: str,
    think: bool = True,
    attempt: int = 0,
    seed: int | None = 0,
    output_budget: int = OUTPUT_TOKEN_BUDGETS[0],
    chained: bool = False,
) -> dict[str, Any]:
    if (
        not isinstance(seed, int)
        or isinstance(seed, bool)
        or not 0 <= seed <= CANDIDATE_SEED_MAXIMUM
    ):
        raise ValueError(f"{mode} sampling requires an in-range integer seed")
    options = {
        "temperature": _candidate_temperature(mode, attempt, chained=chained),
        "seed": seed,
        "num_predict": output_budget,
    }
    return {
        "messages": [{"role": "user", "content": instruction}],
        "stream": False,
        "think": THINKING_EFFORT if think else False,
        "format": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        },
        "options": options,
        # The llama.cpp router forwards only a top-level seed; Ollama reads options.seed and
        # ignores this field. Sending both keeps candidate sampling reproducible on either.
        "seed": seed,
    }


def _response_prompt(data: dict[str, Any]) -> str:
    return _response_prompt_with_source(data)[0]


def _response_prompt_with_source(data: dict[str, Any]) -> tuple[str, str | None]:
    raw_text = data.get("response")
    final = _extract_structured_prompt(raw_text) if isinstance(raw_text, str) else ""
    if final:
        return final, "response"
    # Thinking-capable Ollama parsers can place a schema-constrained final object in
    # `thinking` while leaving `response` empty. Only accept a structured prompt from
    # that field so internal reasoning can never become the visible image prompt.
    thinking_text = data.get("thinking")
    final = _extract_structured_prompt(thinking_text) if isinstance(thinking_text, str) else ""
    return (final, "thinking") if final else ("", None)


def _has_thinking_output(data: dict[str, Any]) -> bool:
    thinking_text = data.get("thinking")
    return isinstance(thinking_text, str) and bool(thinking_text.strip())


def _extract_structured_prompt(raw_text: str) -> str:
    text = raw_text.strip()
    try:
        parsed = json.loads(text)
        prompt = parsed.get("prompt") if isinstance(parsed, dict) else None
        if isinstance(prompt, str):
            return prompt.strip()
    except json.JSONDecodeError:
        pass
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
        try:
            parsed = json.loads(text)
            prompt = parsed.get("prompt") if isinstance(parsed, dict) else None
            if isinstance(prompt, str):
                return prompt.strip()
        except json.JSONDecodeError:
            pass
    return ""
