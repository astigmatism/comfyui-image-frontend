from __future__ import annotations

from typing import Any

from app.api.workflows import _model_selectors


def _choice(
    parameter_id: str,
    semantic_role: str,
    *,
    default: str = "first",
) -> dict[str, Any]:
    return {
        "id": parameter_id,
        "type": "choice",
        "label": f"{parameter_id} label",
        "description": f"{parameter_id} description",
        "semantic_role": semantic_role,
        "default": default,
        "choices": [
            {
                "value": "first",
                "label": "Authoritative first",
                "binding": "/private/first.safetensors",
            },
            {
                "value": "second",
                "label": "Authoritative second",
                "options_json": "private",
            },
        ],
        "bindings": [{"node_id": "private", "input": "value"}],
    }


def _dump_selectors(contract: dict[str, Any], generation_source: Any) -> list[dict[str, Any]]:
    return [selector.model_dump() for selector in _model_selectors(contract, generation_source)]


def test_model_selector_projection_uses_canonical_and_compatibility_signals() -> None:
    contract = {
        "inputs": [
            _choice("canonical_model", "model"),
            _choice("role_checkpoint", "checkpoint"),
            _choice("checkpoint", "custom"),
            _choice("timeline_model", "custom"),
            _choice("ordinary_lora", "lora"),
            {**_choice("timeline_number", "custom"), "type": "number"},
        ]
    }
    generation_source = {
        "base_model": {
            "timeline": {
                "model_variants": [
                    {
                        "parameter_id": "timeline_model",
                        "value": "second",
                        "label": "Timeline label must not replace the interface label",
                        "released_month": "2026-07",
                        "path": "/private/value-must-not-be-projected.safetensors",
                    },
                    {
                        "parameter_id": "timeline_number",
                        "value": "second",
                        "released_month": "2026-08",
                    },
                ]
            }
        }
    }

    selectors = _dump_selectors(contract, generation_source)

    assert [selector["parameter_id"] for selector in selectors] == [
        "canonical_model",
        "role_checkpoint",
        "checkpoint",
        "timeline_model",
    ]
    timeline_selector = selectors[-1]
    assert timeline_selector["default"] == "first"
    assert timeline_selector["choices"] == [
        {
            "value": "first",
            "label": "Authoritative first",
            "released_month": None,
        },
        {
            "value": "second",
            "label": "Authoritative second",
            "released_month": "2026-07",
        },
    ]
    assert "private" not in str(selectors).lower()
    assert "safetensors" not in str(selectors).lower()


def test_model_selector_projection_ignores_partial_or_nonmatching_timeline_variants() -> None:
    contract = {"inputs": [_choice("custom_choice", "custom")]}

    assert (
        _dump_selectors(
            contract,
            {
                "base_model": {
                    "timeline": {
                        "model_variants": [
                            {"parameter_id": "custom_choice", "released_month": "2026-07"},
                            {"value": "first", "released_month": "2026-07"},
                            {
                                "parameter_id": "other_choice",
                                "value": "first",
                                "released_month": "2026-07",
                            },
                            {
                                "parameter_id": "custom_choice",
                                "value": "not_published",
                                "released_month": "2026-07",
                            },
                        ]
                    }
                }
            },
        )
        == []
    )
