from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..config import Settings
from ..domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from ..errors import AppError

CANDIDATE_SEED_MAXIMUM = 2**31 - 1
MAX_REFINE_ATTEMPTS = 3
MAX_CREATE_ATTEMPTS = 3
MAX_CREATE_EXCLUSIONS = 8
MAX_GENERATE_ATTEMPTS = 3
GENERATE_RETRY_BASE_SECONDS = 0.25
RETRYABLE_GENERATE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
# Nighttime advertises xhigh as its highest effort; max is an alias for xhigh.
# Boolean true selects the router's default effort, so request the level explicitly.
THINKING_EFFORT = "xhigh"
# Ollama's generated-token allowance is shared by thinking and final output. Creative prompt
# composition therefore starts with enough room for reasoning and escalates deterministically if
# the upstream response reports that it exhausted the allowance before completing the schema.
OUTPUT_TOKEN_BUDGETS = (2_048, 4_096, 8_192)
CandidateSeedResolver = Callable[[int, int], int]
GenerateRetrySleeper = Callable[[float], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComposeResult:
    prompt: str
    model: str
    raw_response: dict[str, Any]
    duration_ms: int


@dataclass(frozen=True)
class GenerateResult:
    data: Any
    status: int


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


class OllamaAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        seed_resolver: CandidateSeedResolver | None = None,
        retry_sleeper: GenerateRetrySleeper | None = None,
    ):
        self.settings = settings
        self.base_url = settings.ollama_base_url
        self.seed_resolver = seed_resolver or self._secure_seed
        self.retry_sleeper = retry_sleeper or asyncio.sleep
        self._client = (
            httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=5.0),
                transport=transport,
                headers=(
                    {"Authorization": f"Bearer {settings.ollama_api_key.get_secret_value()}"}
                    if settings.ollama_api_key and settings.ollama_api_key.get_secret_value()
                    else {}
                ),
            )
            if self.base_url
            else None
        )

    @staticmethod
    def _secure_seed(minimum: int, maximum: int) -> int:
        return minimum + secrets.randbelow(maximum - minimum + 1)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def available_models(self) -> list[str]:
        if not self._client:
            return []
        try:
            response = await self._client.get("/api/tags", timeout=5)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        models = payload.get("models", []) if isinstance(payload, dict) else []
        if not isinstance(models, list):
            return []
        names = {
            item["name"].strip()
            for item in models
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item["name"].strip()
            and _router_model_is_available(item)
        }
        return sorted(names, key=lambda item: (item.casefold(), item))

    def _model_unavailable_message(self, models: Sequence[str]) -> str | None:
        if not models:
            return (
                "Prompt Assistant is unavailable because the Ollama router has no reachable model."
            )
        if self.settings.ollama_model and self.settings.ollama_model not in models:
            return "Prompt Assistant's configured model is unavailable on the Ollama router."
        return None

    async def status(self) -> tuple[bool, str | None]:
        if not self._client:
            return False, "Prompt Assistant is not configured."
        models = await self.available_models()
        message = self._model_unavailable_message(models)
        return message is None, message

    async def compose(
        self,
        *,
        mode: Literal["refine", "create"],
        prompt: str,
        direction: str,
        think: bool = True,
        excluded_prompts: Sequence[str] = (),
        instructions: str | None = None,
    ) -> ComposeResult:
        if not self._client:
            raise AppError(
                "ollama_unavailable", "Prompt Assistant is not configured.", status_code=503
            )
        models = await self.available_models()
        unavailable_message = self._model_unavailable_message(models)
        if unavailable_message:
            raise AppError(
                "ollama_unavailable",
                f"{unavailable_message} Manual prompting still works.",
                status_code=503,
            )
        started = time.monotonic()
        response_diagnostics: list[dict[str, Any]] = []
        candidate_budget_failures: list[dict[str, Any]] = []
        excluded = (
            _create_excluded_prompts(prompt, direction, excluded_prompts)
            if mode == "create"
            else {}
        )
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
                mode=mode, prompt=prompt, direction=direction, instructions=instructions
            )
            candidate, budget_failure = await self._compose_candidate(
                mode=mode,
                instruction=instruction,
                think=think,
                attempt=attempt,
                seed=candidate_seed + attempt,
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
            if mode == "refine" and _same_prompt(final, prompt):
                diagnostics["validation_stage"] = "refinement_comparison"
                response_diagnostics.append(diagnostics)
                self._log_candidate_rejected(
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    maximum_attempts=maximum_attempts,
                    reason="unchanged_prompt",
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
                    diagnostics
                    if len(response_diagnostics) == 1
                    else {
                        "attempts": response_diagnostics,
                        "selected_attempt": len(response_diagnostics),
                    }
                )
                return ComposeResult(
                    prompt=final,
                    model=effective_model.strip(),
                    raw_response=raw_response,
                    duration_ms=int((time.monotonic() - started) * 1000),
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
                "Prompt Assistant could not produce a changed prompt after retrying. "
                "Adjust the Creative Direction and try again.",
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
            )
            if self.settings.ollama_model:
                payload["model"] = self.settings.ollama_model
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
            final, selected_field = _response_prompt_with_source(data)
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
                isinstance(data.get(field), str) and bool(data[field].strip())
                for field in ("response", "thinking")
            )
            invalid = _with_output_budget_diagnostics(
                diagnostics,
                attempts=output_budget_attempt,
                allowances=OUTPUT_TOKEN_BUDGETS[:output_budget_attempt],
            )
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

    async def _generate(self, payload: dict[str, Any], *, mode: str, think: bool) -> GenerateResult:
        if not self._client:
            raise RuntimeError("Ollama client is not configured")
        for attempt in range(1, MAX_GENERATE_ATTEMPTS + 1):
            try:
                response = await self._client.post("/api/chat", json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                upstream_status = exc.response.status_code
                retryable = upstream_status in RETRYABLE_GENERATE_STATUS_CODES
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="http_status",
                    retryable=retryable,
                    upstream_status=upstream_status,
                )
                if retryable and attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    continue
                details = _generate_error_details(
                    attempt=attempt,
                    failure_kind="http_status",
                    think=think,
                    upstream_status=upstream_status,
                    response_data=_safe_json_object(exc.response),
                )
                if retryable:
                    raise AppError(
                        "ollama_generate_unavailable",
                        "The Ollama router could not complete prompt composition after retrying.",
                        status_code=503,
                        details=details,
                    ) from exc
                raise AppError(
                    "ollama_generate_rejected",
                    "The Ollama router rejected the prompt composition request.",
                    status_code=502,
                    details=details,
                ) from exc
            except httpx.TimeoutException as exc:
                retryable = isinstance(exc, httpx.ConnectTimeout)
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="timeout",
                    retryable=retryable,
                )
                if retryable and attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    continue
                raise AppError(
                    "ollama_generate_timeout",
                    "The Ollama router timed out while composing the prompt.",
                    status_code=504,
                    details=_generate_error_details(
                        attempt=attempt,
                        failure_kind="timeout",
                        think=think,
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
                )
                if attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
                    continue
                raise AppError(
                    "ollama_generate_transport_error",
                    "Prompt Assistant lost its connection to the Ollama router after retrying.",
                    status_code=503,
                    details=_generate_error_details(
                        attempt=attempt,
                        failure_kind="transport",
                        think=think,
                    ),
                ) from exc
            try:
                received: Any = _normalize_chat_response(response.json())
            except ValueError as exc:
                self._log_generate_failure(
                    exc,
                    mode=mode,
                    think=think,
                    attempt=attempt,
                    failure_kind="invalid_json",
                    retryable=True,
                    upstream_status=response.status_code,
                )
                if attempt < MAX_GENERATE_ATTEMPTS:
                    await self._wait_before_generate_retry(attempt)
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
                    ),
                ) from exc
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
            return GenerateResult(data=received, status=response.status_code)
        raise RuntimeError("Ollama generate retry loop exited unexpectedly")

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
            },
        )


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


def _router_model_is_available(item: dict[str, Any]) -> bool:
    # Ordinary Ollama tags have no router metadata. When the router supplies health,
    # an advertised alias with an offline backend must not enable the assistant.
    metadata = item.get("x_ollama_router")
    health = metadata.get("health") if isinstance(metadata, dict) else None
    return not (isinstance(health, dict) and health.get("available") is False)


def _generate_error_details(
    *,
    attempt: int,
    failure_kind: str,
    think: bool,
    upstream_status: int | None = None,
    response_data: dict[str, Any] | None = None,
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


def _instruction(*, mode: str, prompt: str, direction: str, instructions: str | None = None) -> str:
    prefix = DEFAULT_PROMPT_INSTRUCTIONS[mode] if instructions is None else instructions.strip()
    if mode == "refine":
        return f"{prefix}\n\nCurrent prompt:\n{prompt}\n\nCreative direction:\n{direction}"
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


def _generate_payload(
    *,
    mode: str,
    instruction: str,
    think: bool = True,
    attempt: int = 0,
    seed: int | None = 0,
    output_budget: int = OUTPUT_TOKEN_BUDGETS[0],
) -> dict[str, Any]:
    if (
        not isinstance(seed, int)
        or isinstance(seed, bool)
        or not 0 <= seed <= CANDIDATE_SEED_MAXIMUM
    ):
        raise ValueError(f"{mode} sampling requires an in-range integer seed")
    if mode == "refine":
        options = {
            "temperature": min(0.5, round(0.1 + (attempt * 0.2), 1)),
            "seed": seed,
            "num_predict": output_budget,
        }
    else:
        options = {
            "temperature": min(0.9, 0.5 + (attempt * 0.2)),
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
