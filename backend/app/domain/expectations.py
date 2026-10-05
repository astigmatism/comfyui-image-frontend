"""Pure helpers for Creative Direction expectations checked with vision.

An expectation check composes a prompt, generates one probe image, asks a
vision-capable model to score every expectation from 0 to 100, and revises the
prompt until every expectation reaches the pass score or attempts run out.
Nothing here performs I/O; the service and adapter own persistence and calls.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_EXPECTATIONS = 12
MAX_EXPECTATION_LENGTH = 300
MAX_EXPECTATIONS_TEXT_LENGTH = 4000
MAX_OBSERVATION_LENGTH = 500
MAX_SUMMARY_LENGTH = 1000
DEFAULT_THRESHOLD = 80
DEFAULT_MAX_ATTEMPTS = 5
MAX_ATTEMPTS = 10

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")


def _strip_bullet(line: str) -> str:
    return _BULLET.sub("", line, count=1).strip()


def parse_expectations(text: str) -> list[str]:
    """One expectation per nonblank line; list bullets and numbering are dropped."""

    return [item for item in (_strip_bullet(line) for line in str(text or "").splitlines()) if item]


def normalize_expectations(items: Iterable[str]) -> list[str]:
    """Validate an explicit expectation list. Raises ``ValueError`` with a user message."""

    normalized = [_strip_bullet(str(item)) for item in items]
    normalized = [item for item in normalized if item]
    if not normalized:
        raise ValueError("Add at least one expectation.")
    if len(normalized) > MAX_EXPECTATIONS:
        raise ValueError(f"Use at most {MAX_EXPECTATIONS} expectations.")
    if any(len(item) > MAX_EXPECTATION_LENGTH for item in normalized):
        raise ValueError(f"Keep each expectation under {MAX_EXPECTATION_LENGTH + 1} characters.")
    return normalized


@dataclass(frozen=True)
class ExpectationScore:
    expectation: str
    score: int
    observation: str
    met: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "expectation": self.expectation,
            "score": self.score,
            "observation": self.observation,
            "met": self.met,
        }


@dataclass(frozen=True)
class Evaluation:
    results: tuple[ExpectationScore, ...]
    summary: str
    threshold: int

    @property
    def score(self) -> int:
        """The headline score is the weakest expectation."""

        return min(item.score for item in self.results)

    @property
    def passed(self) -> bool:
        return all(item.met for item in self.results)

    def as_json(self) -> dict[str, Any]:
        return {
            "results": [item.as_json() for item in self.results],
            "summary": self.summary,
            "threshold": self.threshold,
            "score": self.score,
            "passed": self.passed,
        }


def _truncate(value: str, maximum: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= maximum else text[: maximum - 1].rstrip() + "\u2026"


def score_results(
    expectations: Sequence[str], scores: Sequence[tuple[int, str]], threshold: int, summary: str
) -> Evaluation:
    if len(expectations) != len(scores):
        raise ValueError("Every expectation needs exactly one score.")
    return Evaluation(
        results=tuple(
            ExpectationScore(
                expectation=expectation,
                score=score,
                observation=_truncate(observation, MAX_OBSERVATION_LENGTH),
                met=score >= threshold,
            )
            for expectation, (score, observation) in zip(expectations, scores, strict=True)
        ),
        summary=_truncate(summary, MAX_SUMMARY_LENGTH),
        threshold=threshold,
    )


def validate_evaluation(raw: Any, expectations: Sequence[str], threshold: int) -> Evaluation:
    """Check a structured vision response and attach the pass rule.

    The response must score each numbered expectation exactly once with an
    integer from 0 to 100. Raises ``ValueError`` describing the structural fault.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("response is not an object")
    results = raw.get("results")
    if not isinstance(results, list):
        raise ValueError("results is not a list")
    count = len(expectations)
    by_index: dict[int, tuple[int, str]] = {}
    for item in results:
        if not isinstance(item, Mapping):
            raise ValueError("result is not an object")
        index = item.get("expectation")
        score = item.get("score")
        observation = item.get("observation", "")
        if isinstance(score, float) and score.is_integer():
            score = int(score)
        if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= count:
            raise ValueError("result references an unknown expectation")
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
            raise ValueError("score is not an integer from 0 to 100")
        if not isinstance(observation, str):
            raise ValueError("observation is not text")
        if index in by_index:
            raise ValueError("an expectation was scored more than once")
        by_index[index] = (score, observation.strip())
    if len(by_index) != count:
        raise ValueError("not every expectation was scored")
    summary = raw.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError("summary is not text")
    return score_results(
        expectations,
        [by_index[index] for index in range(1, count + 1)],
        threshold,
        summary.strip(),
    )


def evaluation_from_json(value: Mapping[str, Any] | None) -> Evaluation | None:
    """Rebuild a stored evaluation; ``None`` when the attempt was never scored."""

    if not isinstance(value, Mapping) or not isinstance(value.get("results"), list):
        return None
    threshold = int(value.get("threshold", DEFAULT_THRESHOLD))
    results = tuple(
        ExpectationScore(
            expectation=str(item.get("expectation", "")),
            score=int(item.get("score", 0)),
            observation=str(item.get("observation", "")),
            met=bool(item.get("met", False)),
        )
        for item in value["results"]
        if isinstance(item, Mapping)
    )
    if not results:
        return None
    return Evaluation(results=results, summary=str(value.get("summary", "")), threshold=threshold)


def _bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def first_attempt_direction(direction: str, expectations: Sequence[str]) -> str:
    """The first composition sees the user's direction plus every expectation."""

    block = f"The image must also satisfy these expectations:\n{_bullets(expectations)}"
    direction = direction.strip()
    return f"{direction}\n\n{block}" if direction else block


def build_revision_direction(direction: str, evaluation: Evaluation) -> str:
    """Turn a failed vision check into Creative Direction for the next revision."""

    unmet = [item for item in evaluation.results if not item.met]
    met = [item for item in evaluation.results if item.met]

    def described(item: ExpectationScore) -> str:
        observation = f": {item.observation}" if item.observation else ""
        return f"{item.expectation} (score {item.score}/100){observation}"

    parts = []
    if direction.strip():
        parts.append(direction.strip())
    parts.append(
        "An image generated from the current prompt was inspected. Revise the prompt so the "
        "next image satisfies every expectation below. Keep what already works and make the "
        "unmet expectations explicit, concrete, and visually prominent."
    )
    if unmet:
        parts.append(f"Unmet expectations:\n{_bullets(described(item) for item in unmet)}")
    if met:
        parts.append(
            "Expectations already met (keep them):\n"
            + _bullets(f"{item.expectation} (score {item.score}/100)" for item in met)
        )
    if evaluation.summary:
        parts.append(f"Reviewer summary: {evaluation.summary}")
    return "\n\n".join(parts)


def vision_instruction(instructions: str, expectations: Sequence[str]) -> str:
    numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(expectations, start=1))
    return f"{instructions.strip()}\n\nExpectations:\n{numbered}"


def evaluation_schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "expectation": {"type": "integer", "minimum": 1, "maximum": count},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "observation": {"type": "string"},
                    },
                    "required": ["expectation", "score", "observation"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["results", "summary"],
        "additionalProperties": False,
    }


def best_attempt(scores: Iterable[tuple[int, int]]) -> int | None:
    """Highest-scoring attempt number; a later attempt wins a tie."""

    best: tuple[int, int] | None = None
    for number, score in scores:
        if best is None or (score, number) >= (best[1], best[0]):
            best = (number, score)
    return best[0] if best else None
