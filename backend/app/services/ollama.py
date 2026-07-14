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
            "options": {"temperature": 0.2, "seed": 0, "num_predict": 256},
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
            "You compose one polished image-generation prompt. Preserve the user's intent and do "
            "not add policy commentary. Return JSON with exactly one string field named prompt.\n\n"
            f"Current prompt:\n{prompt}\n\nCreative direction:\n{direction}"
        )
    return (
        "You compose one polished image-generation prompt from a creative direction. Return JSON "
        "with exactly one string field named prompt and no commentary.\n\n"
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
