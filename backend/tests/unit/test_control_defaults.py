from typing import Any

import pytest
from app.domain.control_defaults import control_declarations, validate_control_defaults
from app.errors import AppError


def _contract(*declarations: dict[str, Any]) -> dict[str, Any]:
    return {"inputs": [dict(declaration) for declaration in declarations]}


def _number(control_id: str, minimum: float, maximum: float, step: float) -> dict[str, Any]:
    return {
        "id": control_id,
        "type": "number",
        "minimum": minimum,
        "maximum": maximum,
        "step": step,
    }


def _declarations(*contracts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return control_declarations(contracts)


def test_a_default_for_a_published_control_is_stored_unchanged() -> None:
    declarations = _declarations(
        _contract(
            _number("refine_denoise", 0, 0.5, 0.01), {"id": "enable_upscale", "type": "boolean"}
        )
    )

    stored = validate_control_defaults(
        {"intorealism_krea2_flash_v3_int8": {"refine_denoise": 0.0, "enable_upscale": False}},
        declarations,
    )

    assert stored == {
        "intorealism_krea2_flash_v3_int8": {"refine_denoise": 0.0, "enable_upscale": False}
    }


def test_an_unpublished_control_id_is_rejected_because_no_bundle_could_apply_it() -> None:
    declarations = _declarations(_contract(_number("refine_denoise", 0, 1, 0.01)))

    with pytest.raises(AppError) as error:
        validate_control_defaults({"cp": {"steps_that_never_shipped": 2}}, declarations)

    assert error.value.code == "control_defaults_unknown_control"
    assert error.value.status_code == 422
    assert "steps_that_never_shipped" in error.value.message
    assert error.value.fields == {
        "control_defaults.cp.steps_that_never_shipped": "unknown control ID"
    }


def test_a_control_that_is_not_a_value_carries_no_default() -> None:
    declarations = _declarations(
        _contract({"id": "seed", "type": "seed", "minimum": 0, "maximum": 10, "step": 1})
    )

    with pytest.raises(AppError) as error:
        validate_control_defaults({"cp": {"seed": 3}}, declarations)

    assert error.value.code == "control_defaults_unknown_control"
    assert "control type cannot carry a default" in str(error.value.fields)


def test_a_value_outside_the_published_bounds_is_clamped_not_refused() -> None:
    # The owner meant "as little re-draw as this bundle allows"; the floor is the answer,
    # and refusing the write would lose the intent.
    declarations = _declarations(_contract(_number("refine_denoise", 0.05, 0.5, 0.05)))

    stored = validate_control_defaults({"cp": {"refine_denoise": 0.0}}, declarations)

    assert stored == {"cp": {"refine_denoise": 0.05}}


def test_a_value_between_steps_is_snapped_to_the_grid_without_float_drift() -> None:
    declarations = _declarations(_contract(_number("refine_denoise", 0, 1, 0.1)))

    stored = validate_control_defaults({"cp": {"refine_denoise": 0.33}}, declarations)

    assert stored == {"cp": {"refine_denoise": 0.3}}
    assert repr(stored["cp"]["refine_denoise"]) == "0.3"


def test_a_value_already_on_one_bundles_grid_is_left_alone() -> None:
    # Two bundles publish the same control with different grids; a value either of them
    # accepts verbatim is the owner's value, not something to snap to the wider contract.
    declarations = _declarations(
        _contract(_number("refine_denoise", 0, 0.5, 0.01)),
        _contract(_number("refine_denoise", 0, 1, 0.25)),
    )

    stored = validate_control_defaults({"cp": {"refine_denoise": 0.39}}, declarations)

    assert stored == {"cp": {"refine_denoise": 0.39}}


def test_an_integer_control_rejects_a_string_and_a_number_control_rejects_a_choice() -> None:
    declarations = _declarations(
        _contract({"id": "steps", "type": "integer", "minimum": 1, "maximum": 60, "step": 1})
    )

    with pytest.raises(AppError) as error:
        validate_control_defaults({"cp": {"steps": "twelve"}}, declarations)

    assert error.value.code == "control_defaults_invalid"
    assert "must be a number" in str(error.value.fields)


def test_a_choice_default_must_be_one_of_the_published_options() -> None:
    declarations = _declarations(
        _contract(
            {
                "id": "sampler_name",
                "type": "choice",
                "choices": [{"value": "euler_ancestral"}, {"value": "dpmpp_2m"}],
            }
        )
    )

    assert validate_control_defaults({"cp": {"sampler_name": "dpmpp_2m"}}, declarations) == {
        "cp": {"sampler_name": "dpmpp_2m"}
    }

    with pytest.raises(AppError) as error:
        validate_control_defaults({"cp": {"sampler_name": "lcm"}}, declarations)

    assert error.value.code == "control_defaults_invalid"
    assert "unknown choice" in str(error.value.fields)
    assert "dpmpp_2m" in error.value.message


def test_a_boolean_default_on_a_number_control_is_rejected() -> None:
    declarations = _declarations(_contract(_number("refine_denoise", 0, 1, 0.01)))

    with pytest.raises(AppError) as error:
        validate_control_defaults({"cp": {"refine_denoise": True}}, declarations)

    assert error.value.code == "control_defaults_invalid"


def test_one_checkpoints_default_says_nothing_about_another() -> None:
    declarations = _declarations(_contract(_number("refine_denoise", 0, 0.5, 0.01)))

    stored = validate_control_defaults(
        {"soft_checkpoint": {"refine_denoise": 0.39}, "int8_quant": {"refine_denoise": 0.0}},
        declarations,
    )

    assert stored["soft_checkpoint"] == {"refine_denoise": 0.39}
    assert stored["int8_quant"] == {"refine_denoise": 0.0}


def test_an_undersized_map_and_an_oversized_map_are_both_refused_early() -> None:
    declarations = _declarations(_contract(_number("refine_denoise", 0, 1, 0.1)))
    too_many = {f"cp{index}": {"refine_denoise": 0.1} for index in range(201)}

    with pytest.raises(AppError) as error:
        validate_control_defaults(too_many, declarations)

    assert error.value.code == "control_defaults_too_large"


def test_an_empty_catalog_judges_nothing_and_keeps_the_shape() -> None:
    # ComfyUI offline with nothing cached: the map is still the owner's to keep.
    assert validate_control_defaults({"cp": {"refine_denoise": 0.1}}, {}) == {
        "cp": {"refine_denoise": 0.1}
    }


def test_a_checkpoint_key_must_look_like_a_published_option_id() -> None:
    declarations = _declarations(_contract(_number("refine_denoise", 0, 1, 0.1)))

    with pytest.raises(AppError) as error:
        validate_control_defaults({"../../etc/passwd": {"refine_denoise": 0.1}}, declarations)

    assert error.value.code == "control_defaults_invalid"
