"""Publish-time lint tests, including the incident that motivated it.

The fixture bundles have no sampler, so the graph shapes here are written out by hand:
they mirror the published Moody Krea2 Advanced graph (base pass -> latent upscale ->
refine ``KSampler`` -> colour match -> ``UltimateSDUpscale``) because that is the shape the
lint has to recognise and stay quiet about once the re-draw amount is published.
"""

from typing import Any

from app.domain.publication import validate_publication
from app.domain.publication_lint import (
    MAX_LINT_FINDINGS,
    PublicationLintExemption,
    lint_publication_graph,
)
from tests.publication_fixtures import (
    PublicationBundle,
    build_publication_bundle,
    object_info_fixture,
)

JsonObject = dict[str, Any]


def _parameter(node_id: str, value: Any, parameter_id: str) -> JsonObject:
    return {
        "inputs": {
            "value": value,
            "minimum": 0,
            "maximum": 1,
            "step": 0.01,
            "parameter_id": parameter_id,
            "instance_uuid": f"00000000-0000-4000-8000-{node_id:>04}00000000"[:36],
            "label": parameter_id.replace("_", " ").title(),
            "description": "",
            "semantic_role": "denoise",
            "required": False,
            "advanced": False,
            "group": "Advanced",
            "order": 50,
        },
        "class_type": "CIFDecimalParameter",
        "_meta": {"title": "Image Frontend Decimal Parameter"},
    }


def _declaration(
    control_id: str,
    node_id: str,
    *,
    label: str,
    group: str = "Advanced",
    input_type: str = "number",
    value_field: str = "value",
) -> JsonObject:
    return {
        "id": control_id,
        "type": input_type,
        "label": label,
        "description": "",
        "semantic_role": "denoise",
        "required": False,
        "advanced": False,
        "group": group,
        "order": 50,
        "bindings": [
            {"node_id": node_id, "input": value_field, "class_type": "CIFDecimalParameter"}
        ],
        "default": 0.1,
        "minimum": 0,
        "maximum": 1,
        "step": 0.01,
    }


def _refine_graph(*, denoise: Any, steps: Any = 4) -> JsonObject:
    """The published shape: latent upscale -> refine KSampler -> colour match -> upscaler."""

    return {
        "647": {
            "inputs": {"samples": ["599", 0], "upscale_method": "bicubic", "multiples": 1.5},
            "class_type": "LatentUpscaleBy",
            "_meta": {"title": "Upscale Latent By"},
        },
        "842": {
            "inputs": {
                "seed": ["771", 0],
                "steps": steps,
                "cfg": 1,
                "sampler_name": "euler_ancestral",
                "scheduler": "beta",
                "denoise": denoise,
                "model": ["862", 0],
                "positive": ["627", 0],
                "negative": ["627", 0],
                "latent_image": ["647", 0],
            },
            "class_type": "KSampler",
            "_meta": {"title": "KSampler"},
        },
        "610": {
            "inputs": {"samples": ["842", 0]},
            "class_type": "VAEDecode",
            "_meta": {"title": "VAE Decode"},
        },
        "663": {
            "inputs": {
                "image": ["610", 0],
                "upscale_model": ["662", 0],
                "steps": 4,
                "cfg": 1,
                "denoise": ["915", 0],
            },
            "class_type": "UltimateSDUpscale",
            "_meta": {"title": "Ultimate SD Upscale"},
        },
    }


def test_constant_refine_denoise_names_the_node_and_cannot_be_reached_from_a_control() -> None:
    warnings, diagnostics = lint_publication_graph(
        source_id="moody-krea2-advanced-v1",
        api_document=_refine_graph(denoise=0.39),
        private_inputs=[],
    )

    assert len(warnings) == 1
    assert "Hidden re-draw" in warnings[0]
    # The message has to name the node a person can find in the editor.
    assert "node 842" in warnings[0]
    assert "KSampler" in warnings[0]
    assert "0.39" in warnings[0]
    assert diagnostics == ()


def test_linking_the_amount_to_a_published_control_silences_the_lint() -> None:
    graph = _refine_graph(denoise=["916", 0])
    graph["915"] = _parameter("915", 0.2, "upscale_denoise")
    graph["916"] = _parameter("916", 0.1, "refine_denoise")

    warnings, diagnostics = lint_publication_graph(
        source_id="moody-krea2-advanced-v1",
        api_document=graph,
        private_inputs=[
            _declaration("refine_denoise", "916", label="Refine denoise"),
            _declaration("upscale_denoise", "915", label="Upscale denoise"),
        ],
    )

    assert warnings == ()
    assert diagnostics == ()


def test_a_derived_amount_counts_as_controlled_even_though_the_link_is_not_a_control() -> None:
    # i2i 4KHD publishes `creativity` and derives the sampler's denoise with a math node.
    graph = _refine_graph(denoise=["923", 0])
    graph["923"] = {
        "inputs": {"a": ["953", 0], "b": 1},
        "class_type": "ComfyMathExpression",
        "_meta": {"title": "Refine denoise"},
    }

    warnings, _ = lint_publication_graph(
        source_id="moody-krea2-i2i-4khd-v1",
        api_document=graph,
        private_inputs=[_declaration("creativity", "953", label="Creativity")],
    )

    assert [warning for warning in warnings if "Hidden re-draw" in warning] == []


def test_a_full_pass_and_a_resample_are_not_reported() -> None:
    for denoise in (1, 1.0, 0.25, 0.0, 0.1):
        warnings, _ = lint_publication_graph(
            source_id="bundle",
            api_document=_refine_graph(denoise=denoise),
            private_inputs=[],
        )
        assert [warning for warning in warnings if "Hidden re-draw" in warning] == [], denoise


def test_a_short_re_draw_is_reported_apart_from_the_amount() -> None:
    warnings, _ = lint_publication_graph(
        source_id="bundle",
        api_document=_refine_graph(denoise=0.39, steps=3),
        private_inputs=[],
    )

    assert [warning.split(":")[0] for warning in warnings] == ["Hidden re-draw", "Short re-draw"]
    assert "only 3 steps" in warnings[1]


def test_a_recorded_exemption_becomes_a_diagnostic_instead_of_vanishing() -> None:
    exemptions = [
        PublicationLintExemption(
            source_id="bundle",
            target="842",
            field="denoise",
            reason="the author keeps one deliberate look constant",
        )
    ]

    warnings, diagnostics = lint_publication_graph(
        source_id="bundle",
        api_document=_refine_graph(denoise=0.39),
        private_inputs=[],
        exemptions=exemptions,
    )

    assert [warning for warning in warnings if "Hidden re-draw" in warning] == []
    assert len(diagnostics) == 1
    assert "842" in diagnostics[0]
    assert "the author keeps one deliberate look constant" in diagnostics[0]


def test_an_exemption_written_for_another_bundle_does_not_apply() -> None:
    warnings, _ = lint_publication_graph(
        source_id="bundle",
        api_document=_refine_graph(denoise=0.39),
        private_inputs=[],
        exemptions=[
            PublicationLintExemption(
                source_id="other-bundle", target="842", field="denoise", reason="not this one"
            )
        ],
    )

    assert len(warnings) == 1


def test_a_custom_advanced_sampler_is_charged_to_the_scheduler_holding_the_amount() -> None:
    graph: JsonObject = {
        "600": {
            "inputs": {"noise": ["601", 0], "guider": ["602", 0], "sigmas": ["603", 0]},
            "class_type": "SamplerCustomAdvanced",
            "_meta": {"title": "Sampler Custom Advanced"},
        },
        "603": {
            "inputs": {"model": ["604", 0], "scheduler": "simple", "steps": 8, "denoise": 0.6},
            "class_type": "BasicScheduler",
            "_meta": {"title": "Basic Scheduler"},
        },
    }

    warnings, _ = lint_publication_graph(source_id="bundle", api_document=graph, private_inputs=[])

    assert len(warnings) == 1
    assert "node 603" in warnings[0]
    assert "BasicScheduler" in warnings[0]
    assert "0.6" in warnings[0]


def test_a_control_naming_a_stage_the_graph_reaches_is_silent() -> None:
    graph = _refine_graph(denoise=["916", 0])
    graph["915"] = _parameter("915", 0.2, "upscale_denoise")
    graph["916"] = _parameter("916", 0.1, "refine_denoise")

    warnings, _ = lint_publication_graph(
        source_id="bundle",
        api_document=graph,
        private_inputs=[
            _declaration("upscale_denoise", "915", label="Upscale denoise"),
            _declaration("refine_denoise", "916", label="Refine denoise"),
        ],
    )

    assert warnings == ()


def test_a_denoise_bound_to_the_refine_but_labelled_for_the_upscaler_is_reported() -> None:
    # Exactly the confusion that made the defect unreadable: the only visible denoise
    # control talks about the upscaler while feeding the refine sampler.
    graph = _refine_graph(denoise=["915", 0])
    graph["915"] = _parameter("915", 0.2, "upscale_denoise")
    del graph["663"]["inputs"]["denoise"]

    warnings, _ = lint_publication_graph(
        source_id="bundle",
        api_document=graph,
        private_inputs=[_declaration("upscale_denoise", "915", label="Upscale denoise")],
    )

    stage_warnings = [warning for warning in warnings if warning.startswith("Stage mismatch")]
    assert len(stage_warnings) == 1
    assert "upscale_denoise" in stage_warnings[0]
    assert "UltimateSDUpscale" in stage_warnings[0]


def test_a_stage_word_for_a_stage_the_graph_does_not_contain_is_not_a_contradiction() -> None:
    graph: JsonObject = {
        "915": _parameter("915", False, "enable_upscale"),
        "999": {
            "inputs": {"value": ["915", 0]},
            "class_type": "PreviewBridge",
            "_meta": {"title": "Preview"},
        },
    }

    warnings, _ = lint_publication_graph(
        source_id="bundle",
        api_document=graph,
        private_inputs=[
            _declaration("enable_upscale", "915", label="Enable SeedVR2 upscale", group="Basic")
        ],
    )

    assert warnings == ()


def test_a_parameter_node_the_interface_never_binds_is_reported() -> None:
    graph = _refine_graph(denoise=["916", 0])
    graph["916"] = _parameter("916", 0.1, "refine_denoise")

    warnings, _ = lint_publication_graph(source_id="bundle", api_document=graph, private_inputs=[])

    unpublished = [warning for warning in warnings if warning.startswith("Unpublished control")]
    assert len(unpublished) == 1
    assert "'refine_denoise'" in unpublished[0]
    assert "CIFDecimalParameter" in unpublished[0]


def test_a_bounded_number_of_findings_is_reported_and_the_rest_are_counted() -> None:
    graph: JsonObject = {}
    for index in range(MAX_LINT_FINDINGS + 5):
        graph[str(800 + index)] = {
            "inputs": {"steps": 4, "cfg": 1, "denoise": 0.4, "latent_image": ["1", 0]},
            "class_type": "KSampler",
            "_meta": {"title": f"KSampler {index}"},
        }

    warnings, _ = lint_publication_graph(source_id="bundle", api_document=graph, private_inputs=[])

    assert len(warnings) == MAX_LINT_FINDINGS + 1
    assert "suppressed" in warnings[-1]
    assert "5 further publication lint findings were suppressed." in warnings[-1]


def _add_hidden_refine_sampler(manifest: JsonObject, workflow: JsonObject, api: JsonObject) -> None:
    """The published defect, grafted onto an otherwise valid bundle."""

    api["842"] = {
        "inputs": {
            "seed": ["13", 0],
            "steps": 4,
            "cfg": 1,
            "sampler_name": "euler_ancestral",
            "scheduler": "beta",
            "denoise": 0.39,
            "model": ["1", 0],
            "positive": ["10", 0],
            "negative": ["10", 0],
            "latent_image": ["11", 0],
        },
        "class_type": "KSampler",
        "_meta": {"title": "Refine"},
    }
    manifest["dependencies"]["class_types"].append("KSampler")
    workflow.setdefault("nodes", []).append(
        {
            "id": 842,
            "type": "KSampler",
            "widgets_values": [0, "randomize", 4, 1, "euler_ancestral", "beta", 0.39],
        }
    )


def _validate(bundle: PublicationBundle) -> Any:
    return validate_publication(
        instance_id="test-instance",
        manifest_path=bundle.manifest_path,
        manifest_bytes=bundle.manifest_bytes,
        workflow_bytes=bundle.workflow_bytes,
        api_bytes=bundle.api_bytes,
        object_info={
            **object_info_fixture(),
            "KSampler": {
                "input": {
                    "required": {
                        "steps": ["INT"],
                        "cfg": ["FLOAT"],
                        "denoise": ["FLOAT"],
                    }
                }
            },
        },
        manifest_max_bytes=1024 * 1024,
        workflow_max_bytes=1024 * 1024,
        api_max_bytes=1024 * 1024,
    )


def test_a_published_bundle_with_a_hidden_refine_is_accepted_with_a_warning_that_names_it() -> None:
    publication = _validate(
        build_publication_bundle("krea", mutate_artifacts=_add_hidden_refine_sampler)
    )

    # Warned, never rejected: the bundle still serves generations while its author catches up.
    assert publication.readiness == "ready_with_warnings"
    findings = [warning for warning in publication.warnings if warning.startswith("Hidden re-draw")]
    assert len(findings) == 1
    assert "node 842" in findings[0]
    assert "0.39" in findings[0]
