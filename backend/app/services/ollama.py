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
from ..errors import AppError

CREATE_SEED_MAXIMUM = 2**31 - 1
MAX_CREATE_ATTEMPTS = 3
MAX_CREATE_EXCLUSIONS = 8
MAX_GENERATE_ATTEMPTS = 3
GENERATE_RETRY_BASE_SECONDS = 0.25
RETRYABLE_GENERATE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
CreateSeedResolver = Callable[[int, int], int]
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


class OllamaAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        seed_resolver: CreateSeedResolver | None = None,
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
        names = {
            str(item.get("name"))
            for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        return sorted(names, key=lambda item: (item.casefold(), item))

    async def status(self) -> tuple[bool, str | None]:
        if not self._client:
            return False, "Prompt Assistant is not configured."
        models = await self.available_models()
        if not models:
            return (
                False,
                "Prompt Assistant is unavailable because the Ollama router has no reachable model.",
            )
        return True, None

    async def compose(
        self,
        *,
        mode: Literal["refine", "create"],
        prompt: str,
        direction: str,
        think: bool = True,
        excluded_prompts: Sequence[str] = (),
    ) -> ComposeResult:
        if not self._client:
            raise AppError(
                "ollama_unavailable", "Prompt Assistant is not configured.", status_code=503
            )
        models = await self.available_models()
        if not models:
            raise AppError(
                "ollama_unavailable",
                "The Ollama router has no reachable model; manual prompting still works.",
                status_code=503,
            )
        started = time.monotonic()
        response_diagnostics: list[dict[str, Any]] = []
        excluded = _distinct_prompts((prompt, *excluded_prompts)) if mode == "create" else {}
        maximum_attempts = MAX_CREATE_ATTEMPTS if mode == "create" else 1
        create_seed = None
        if mode == "create":
            create_seed = self.seed_resolver(
                0,
                CREATE_SEED_MAXIMUM - (maximum_attempts - 1),
            )
            if (
                not isinstance(create_seed, int)
                or isinstance(create_seed, bool)
                or not 0 <= create_seed <= CREATE_SEED_MAXIMUM - (maximum_attempts - 1)
            ):
                raise RuntimeError("create seed resolver returned an out-of-range value")
        for attempt in range(maximum_attempts):
            instruction = _instruction(mode=mode, prompt=prompt, direction=direction)
            payload = _generate_payload(
                mode=mode,
                instruction=instruction,
                think=think,
                attempt=attempt,
                seed=create_seed + attempt if create_seed is not None else None,
            )
            received = await self._generate(payload, mode=mode, think=think)
            if not isinstance(received.data, dict):
                diagnostics = _response_diagnostics(
                    {},
                    status=received.status,
                    validation_stage="response_envelope",
                )
                raise AppError(
                    "ollama_invalid_response",
                    "Prompt Assistant returned an invalid response envelope.",
                    details=diagnostics,
                )
            data = received.data
            final, selected_field = _response_prompt_with_source(data)
            if not final:
                diagnostics = _response_diagnostics(
                    data,
                    status=received.status,
                    validation_stage="structured_prompt",
                )
                has_output_text = any(
                    isinstance(data.get(field), str) and bool(data[field].strip())
                    for field in ("response", "thinking")
                )
                raise AppError(
                    "ollama_invalid_response",
                    (
                        "Prompt Assistant returned malformed structured prompt output."
                        if has_output_text
                        else "Prompt Assistant returned no usable prompt."
                    ),
                    details=diagnostics,
                )
            effective_model = data.get("model")
            if not isinstance(effective_model, str) or not effective_model.strip():
                diagnostics = _response_diagnostics(
                    data,
                    status=received.status,
                    validation_stage="model_metadata",
                    selected_field=selected_field,
                )
                raise AppError(
                    "ollama_invalid_response",
                    "Prompt Assistant did not identify the Ollama model that produced its "
                    "response.",
                    details=diagnostics,
                )
            warnings = []
            if think and not _has_thinking_output(data):
                warnings.append("thinking_output_missing")
            diagnostics = _response_diagnostics(
                data,
                status=received.status,
                validation_stage="prompt_validation",
                selected_field=selected_field,
                warnings=warnings,
            )
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
                raise AppError(
                    "prompt_refinement_unchanged",
                    "Prompt Assistant returned the original prompt unchanged. Add a more "
                    "specific Creative Direction or try again.",
                    status_code=422,
                    details=diagnostics,
                )
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
            excluded.setdefault(normalized_final, final.strip())
        raise AppError(
            "ollama_invalid_response",
            "Prompt Assistant could not produce a distinct new prompt after retrying.",
            details={
                **(response_diagnostics[-1] if response_diagnostics else {}),
                "validation_stage": "create_distinctness",
                "attempt_diagnostics": response_diagnostics,
            },
        )

    async def _generate(self, payload: dict[str, Any], *, mode: str, think: bool) -> GenerateResult:
        if not self._client:
            raise RuntimeError("Ollama client is not configured")
        for attempt in range(1, MAX_GENERATE_ATTEMPTS + 1):
            try:
                response = await self._client.post("/api/generate", json=payload)
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
                received: Any = response.json()
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


def _instruction(*, mode: str, prompt: str, direction: str) -> str:
    if mode == "refine":
        return (
            "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
            "Refine the current prompt according to the creative direction.\n\n"
            f"Current prompt:\n{prompt}\n\nCreative direction:\n{direction}"
        )
    return (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. Create "
        "one complete, polished, directly usable image prompt from this creative direction:\n\n"
        f"{direction}"
    )


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


def _generate_payload(
    *,
    mode: str,
    instruction: str,
    think: bool = True,
    attempt: int = 0,
    seed: int | None = None,
) -> dict[str, Any]:
    if mode == "refine":
        options = {"temperature": 0.1, "seed": 0, "num_predict": 512}
    else:
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not 0 <= seed <= CREATE_SEED_MAXIMUM
        ):
            raise ValueError("create sampling requires an in-range integer seed")
        options = {
            "temperature": min(0.9, 0.5 + (attempt * 0.2)),
            "seed": seed,
            "num_predict": 512,
        }
    return {
        "prompt": instruction,
        "stream": False,
        "think": think,
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
