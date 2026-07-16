from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..config import Settings
from ..errors import AppError


@dataclass(frozen=True)
class ComposeResult:
    prompt: str
    model: str
    raw_response: dict[str, Any]
    duration_ms: int


class OllamaAdapter:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.base_url = settings.ollama_base_url
        self._client = (
            httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=5.0),
                transport=transport,
            )
            if self.base_url
            else None
        )

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
        instruction = _instruction(mode=mode, prompt=prompt, direction=direction)
        options = (
            {"temperature": 0.1, "seed": 0, "num_predict": 512}
            if mode == "refine"
            else {"temperature": 0.5, "seed": 0, "num_predict": 512}
        )
        payload = {
            "prompt": instruction,
            "stream": False,
            "think": False,
            "format": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
                "additionalProperties": False,
            },
            "options": options,
        }
        started = time.monotonic()
        try:
            response = await self._client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "ollama_unavailable",
                "Prompt Assistant could not compose a prompt; manual prompting still works.",
                status_code=503,
            ) from exc
        raw_text = data.get("response") if isinstance(data, dict) else None
        if not isinstance(raw_text, str):
            raise AppError("ollama_invalid_response", "Prompt Assistant returned no usable prompt.")
        final = _extract_prompt(raw_text)
        if not final:
            raise AppError("ollama_invalid_response", "Prompt Assistant returned an empty prompt.")
        effective_model = data.get("model") if isinstance(data, dict) else None
        if not isinstance(effective_model, str) or not effective_model.strip():
            raise AppError(
                "ollama_invalid_response",
                "Prompt Assistant did not identify the Ollama model that produced its response.",
            )
        return ComposeResult(
            prompt=final,
            model=effective_model.strip(),
            raw_response=data if isinstance(data, dict) else {},
            duration_ms=int((time.monotonic() - started) * 1000),
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
    return text
