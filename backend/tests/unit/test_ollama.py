from __future__ import annotations

import json

from app.services.ollama import (
    _extract_prompt,
    _generate_payload,
    _has_thinking_output,
    _instruction,
)


def test_refine_instruction_only_defines_the_outcome_and_inputs() -> None:
    current = (
        "Two scarlet macaws on a rain-dark branch, one facing left, 85mm lens, "
        "shallow depth of field, no flowers"
    )
    direction = "Change the rain to snow."

    instruction = _instruction(mode="refine", prompt=current, direction=direction)

    assert instruction == (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Refine the current prompt according to the creative direction.\n\n"
        f"Current prompt:\n{current}\n\nCreative direction:\n{direction}"
    )


def test_create_instruction_only_defines_the_outcome_and_direction() -> None:
    instruction = _instruction(
        mode="create",
        prompt="this existing prompt is deliberately irrelevant",
        direction="a ceramic robot",
    )

    assert instruction == (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Create one complete, polished, directly usable image prompt from this creative "
        "direction:\n\na ceramic robot"
    )


def test_extract_prompt_supports_structured_and_plain_text_responses() -> None:
    assert _extract_prompt(json.dumps({"prompt": "  a detailed scene  "})) == "a detailed scene"
    assert _extract_prompt('```json\n{"prompt": "a fenced response"}\n```') == ("a fenced response")
    assert _extract_prompt("  a plain response  ") == "a plain response"


def test_thinking_output_must_be_a_nonempty_string() -> None:
    assert _has_thinking_output({"thinking": "considered the constraints"}) is True
    assert _has_thinking_output({"thinking": "  "}) is False
    assert _has_thinking_output({"response": '{"prompt":"portrait"}'}) is False


def test_duplicate_create_retry_changes_sampling_without_adding_instructions() -> None:
    instruction = _instruction(mode="create", prompt="", direction="a ceramic robot")
    payload = _generate_payload(mode="create", instruction=instruction, attempt=1, seed=90210)

    assert payload["prompt"] == instruction
    assert payload["options"] == {"temperature": 0.7, "seed": 90210, "num_predict": 512}
    assert payload["think"] is True


def test_refine_sampling_remains_deterministic() -> None:
    payload = _generate_payload(mode="refine", instruction="refine this", attempt=2)

    assert payload["options"] == {"temperature": 0.1, "seed": 0, "num_predict": 512}
