from __future__ import annotations

import json

from app.services.ollama import (
    _distinct_create_instruction,
    _extract_prompt,
    _generate_payload,
    _instruction,
)


def test_refine_instruction_requires_lossless_minimal_editing() -> None:
    current = (
        "Two scarlet macaws on a rain-dark branch, one facing left, 85mm lens, "
        "shallow depth of field, no flowers"
    )
    direction = "Change the rain to snow."

    instruction = _instruction(mode="refine", prompt=current, direction=direction)

    assert "Treat the Current prompt as the source of truth" in instruction
    assert "Apply the smallest possible set of edits" in instruction
    assert "Preserve every existing detail" in instruction
    assert "does not explicitly change or necessarily imply changing" in instruction
    assert "Do not add unsolicited visual details" in instruction
    assert "return the Current prompt unchanged" in instruction
    assert f"Current prompt:\n{current}" in instruction
    assert f"Creative direction:\n{direction}" in instruction
    assert "intentionally creative" not in instruction


def test_create_instruction_requires_missing_action_setting_and_camera_details() -> None:
    instruction = _instruction(
        mode="create",
        prompt="this existing prompt is deliberately irrelevant",
        direction="a ceramic robot",
    )

    assert "expert prompt writer for Krea 2" in instruction
    assert "This mode is intentionally creative" in instruction
    assert "copy the complete Creative direction exactly as the user wrote it" in instruction
    assert "Copy through its final character before generating any new words" in instruction
    assert "Do not paraphrase, reorder, correct, or omit" in instruction
    assert "Never return the Creative direction alone" in instruction
    assert "exact quoted text" in instruction
    assert "named style or mood" in instruction
    assert "Keep inline exclusions such as 'no people' explicit" in instruction
    assert "Creatively invent coherent, visually specific missing details" in instruction
    assert "invent an action or pose" in instruction
    assert "a rich setting" in instruction
    assert "concrete camera details" in instruction
    assert (
        "subject and defining attributes; action or pose; setting and environment; composition "
        "and camera details"
    ) in instruction
    assert "legacy keyword spam" in instruction
    assert "this existing prompt is deliberately irrelevant" not in instruction
    assert "Creative direction:\na ceramic robot" in instruction


def test_both_instructions_require_one_prompt_only() -> None:
    for mode in ("refine", "create"):
        instruction = _instruction(mode=mode, prompt="portrait", direction="warmer light")
        assert 'valid JSON with exactly one string field named "prompt"' in instruction
        assert "no commentary, preface, markdown, or alternatives" in instruction


def test_extract_prompt_supports_structured_and_plain_text_responses() -> None:
    assert _extract_prompt(json.dumps({"prompt": "  a detailed scene  "})) == "a detailed scene"
    assert _extract_prompt('```json\n{"prompt": "a fenced response"}\n```') == ("a fenced response")
    assert _extract_prompt("  a plain response  ") == "a plain response"


def test_duplicate_create_retry_requires_a_distinct_result_and_changes_sampling() -> None:
    previous = [
        "A ceramic robot waving in a bright studio.",
        "A ceramic robot painting beside a rainy window.",
    ]
    instruction = _distinct_create_instruction(
        direction="a ceramic robot",
        previous_prompts=previous,
    )
    payload = _generate_payload(mode="create", instruction=instruction, attempt=1, seed=90210)

    assert "Distinct-result requirement" in instruction
    assert "Do not repeat, paraphrase, or lightly edit" in instruction
    assert "Previous prompts that must not be returned" in instruction
    assert f"1. {previous[0]}" in instruction
    assert f"2. {previous[1]}" in instruction
    assert payload["prompt"] == instruction
    assert payload["options"] == {"temperature": 0.7, "seed": 90210, "num_predict": 512}
    assert payload["think"] is True


def test_refine_sampling_remains_deterministic() -> None:
    payload = _generate_payload(mode="refine", instruction="refine this", attempt=2)

    assert payload["options"] == {"temperature": 0.1, "seed": 0, "num_predict": 512}
