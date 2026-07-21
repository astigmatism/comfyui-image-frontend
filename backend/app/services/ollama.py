from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..config import Settings
from ..errors import AppError

CREATE_SEED_MAXIMUM = 2**31 - 1
MAX_CREATE_ATTEMPTS = 3
MAX_CREATE_EXCLUSIONS = 8
CreateSeedResolver = Callable[[int, int], int]


@dataclass(frozen=True)
class ComposeResult:
    prompt: str
    model: str
    raw_response: dict[str, Any]
    duration_ms: int


class OllamaAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        seed_resolver: CreateSeedResolver | None = None,
    ):
        self.settings = settings
        self.base_url = settings.ollama_base_url
        self.seed_resolver = seed_resolver or self._secure_seed
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
        responses: list[dict[str, Any]] = []
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
            if attempt == 0:
                instruction = _instruction(mode=mode, prompt=prompt, direction=direction)
            else:
                instruction = _distinct_create_instruction(
                    direction=direction,
                    previous_prompts=list(excluded.values())[:MAX_CREATE_EXCLUSIONS],
                )
            payload = _generate_payload(
                mode=mode,
                instruction=instruction,
                attempt=attempt,
                seed=create_seed + attempt if create_seed is not None else None,
            )
            try:
                response = await self._client.post("/api/generate", json=payload)
                response.raise_for_status()
                received = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise AppError(
                    "ollama_unavailable",
                    "Prompt Assistant could not compose a prompt; manual prompting still works.",
                    status_code=503,
                ) from exc
            data = received if isinstance(received, dict) else {}
            responses.append(data)
            if not _has_thinking_output(data):
                raise AppError(
                    "ollama_invalid_response",
                    "Prompt Assistant did not return thinking output.",
                )
            final = _response_prompt(data)
            if not final:
                raise AppError(
                    "ollama_invalid_response", "Prompt Assistant returned no usable prompt."
                )
            effective_model = data.get("model")
            if not isinstance(effective_model, str) or not effective_model.strip():
                raise AppError(
                    "ollama_invalid_response",
                    "Prompt Assistant did not identify the Ollama model that produced its "
                    "response.",
                )
            normalized_final = _normalize_prompt(final)
            if mode != "create" or (
                _starts_with_creative_direction(final, direction)
                and normalized_final not in excluded
            ):
                raw_response = (
                    data
                    if len(responses) == 1
                    else {"attempts": responses, "selected_attempt": len(responses)}
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
            "Prompt Assistant could not produce a valid, distinct new prompt after retrying.",
        )


def _instruction(*, mode: str, prompt: str, direction: str) -> str:
    if mode == "refine":
        return (
            "You are a precise editor of prompts for modern text-to-image models. Revise the "
            "Current prompt only as requested by the Creative direction.\n\n"
            "Editing rules:\n"
            "- Treat the Current prompt as the source of truth.\n"
            "- Apply the smallest possible set of edits that satisfies the Creative direction.\n"
            "- Preserve every existing detail, constraint, relationship, count, negation, subject, "
            "action, setting, composition, camera detail, lighting choice, color, material, and "
            "style that the Creative direction does not explicitly change or necessarily imply "
            "changing.\n"
            "- Do not omit, generalize, contradict, embellish, or invent content that is unrelated "
            "to the Creative direction. Do not add unsolicited visual details.\n"
            "- If the Creative direction is empty or already satisfied, return the Current prompt "
            "unchanged.\n"
            "- Return the finalized prompt only: valid JSON with exactly one string field named "
            '"prompt" and no commentary, preface, markdown, or alternatives.\n\n'
            f"Current prompt:\n{prompt}\n\nCreative direction:\n{direction}"
        )
    return (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. Create "
        "one complete, polished, directly usable image prompt from the Creative direction. This "
        "mode is intentionally creative.\n\n"
        "Composition rules:\n"
        "- First copy the complete Creative direction exactly as the user wrote it into the start "
        "of the finalized prompt, without adding a label or wrapping it in quotation marks. Copy "
        "through its final character before generating any new words. Do not paraphrase, reorder, "
        "correct, or omit any part of that opening.\n"
        "- Always continue after that exact opening and expand it into a cohesive prompt of "
        "roughly 70 to 160 words. Never return the Creative direction alone, even when it already "
        "specifies many details. Add coherent visual specificity without contradicting or "
        "repeating the opening.\n"
        "- Never contradict the Creative direction. Treat every subject, attribute, action, "
        "setting, count, relationship, exclusion, exact quoted text, medium, named style or mood, "
        "palette, composition, and camera choice in it as mandatory.\n"
        "- Keep inline exclusions such as 'no people' explicit in the final prompt. The rule "
        "against a negative-prompt section does not permit dropping a user-specified exclusion.\n"
        "- Creatively invent coherent, visually specific missing details. In particular, when the "
        "user supplies only a subject or otherwise leaves gaps, invent an action or pose, a rich "
        "setting, a purposeful composition, and concrete camera details rather than leaving those "
        "parts unspecified.\n"
        "- Organize the prompt naturally in this order: subject and defining attributes; action or "
        "pose; setting and environment; composition and camera details such as shot type, "
        "viewpoint, lens, and depth of field; then lighting, color, atmosphere, and visual style.\n"
        "- Use fluent, descriptive natural language suited to a modern prompt-understanding model. "
        "Do not use legacy keyword spam, token weights, model parameters, a negative-prompt "
        "section, or empty quality claims.\n"
        "- Commit to one cohesive visual concept. Do not offer alternatives, explain your choices, "
        "or mention Krea 2, the Creative direction, or these rules in the result.\n"
        "- Return the finalized prompt only: valid JSON with exactly one string field named "
        '"prompt" and no commentary, preface, markdown, or alternatives.\n\n'
        f"Creative direction:\n{direction}"
    )


def _extract_prompt(raw_text: str) -> str:
    text = raw_text.strip()
    structured = _extract_structured_prompt(text)
    return structured or text


def _same_prompt(first: str, second: str) -> bool:
    return _normalize_prompt(first) == _normalize_prompt(second)


def _normalize_prompt(value: str) -> str:
    return " ".join(value.split()).casefold()


def _starts_with_creative_direction(prompt: str, direction: str) -> bool:
    required_opening = direction.strip()
    return not required_opening or prompt.startswith(required_opening)


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
        "think": True,
        "format": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        },
        "options": options,
    }


def _response_prompt(data: dict[str, Any]) -> str:
    raw_text = data.get("response")
    final = _extract_prompt(raw_text) if isinstance(raw_text, str) else ""
    if final:
        return final
    # Thinking-capable Ollama parsers can place a schema-constrained final object in
    # `thinking` while leaving `response` empty. Only accept a structured prompt from
    # that field so internal reasoning can never become the visible image prompt.
    thinking_text = data.get("thinking")
    return _extract_structured_prompt(thinking_text) if isinstance(thinking_text, str) else ""


def _has_thinking_output(data: dict[str, Any]) -> bool:
    thinking_text = data.get("thinking")
    return isinstance(thinking_text, str) and bool(thinking_text.strip())


def _distinct_create_instruction(*, direction: str, previous_prompts: Sequence[str]) -> str:
    exclusions = "\n\n".join(
        f"{index}. {prompt}" for index, prompt in enumerate(previous_prompts, start=1)
    )
    return (
        _instruction(mode="create", prompt="", direction=direction)
        + "\n\nDistinct-result requirement:\n"
        "Previous attempts produced the prompts below. Create a substantively different "
        "realization of the Creative direction. Do not repeat, paraphrase, or lightly edit that "
        "prior material; choose different invented details, composition, camera, lighting, and "
        "atmosphere while preserving every requirement in the Creative direction.\n\n"
        "Previous prompts that must not be returned:\n" + exclusions
    )


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
