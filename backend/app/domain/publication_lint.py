"""Publish-time lint over a frozen API graph.

This module exists because of one specific class of defect: a sampling parameter with
no control and no visibility. A hard-coded re-draw amount in the middle of a published
graph passes every structural rule in :mod:`backend.app.domain.publication` — the graph
is valid, the interface is valid, generation stays pinned and reproducible — and the only
person who discovers that the amount is invisible is the person who notices the artefact
three weeks later. A warning raised at publication time names the node while the author
still has the workflow open, and it reaches the same Workflow health surface as any other
publication warning.

Every finding is a warning or a diagnostic, never a rejection, so a published bundle keeps
working while its author catches up. An intentional constant is recorded in
:data:`SAMPLER_CONSTANT_EXEMPTIONS` — checked in, with a reason — so an accepted constant
stays in the record instead of silently disappearing from it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# A re-draw amount above this and below a full pass is a nudge nobody can see the effect of
# from the control panel, which is exactly where the measured skin/jewellery faceting
# defect lived. The threshold is a decision, not a claim: at or below it a pass is a
# resample, and a full pass (1.0) is the normal shape of a generation graph, not a nudge.
REDAW_DENOISE_WARNING_THRESHOLD = 0.25
# Below this many steps a partial re-draw has almost no room to settle, which turns the
# same amount into visible patching. Measured: raising the step count while keeping the
# amount does not remove the defect, so this half of the rule stays advisory.
REDAW_STEPS_WARNING_FLOOR = 4
# Findings are advisory, so they must never crowd out the contract warnings a bundle
# author published itself (the interface contract allows 256 warnings in total).
MAX_LINT_FINDINGS = 24

# Nodes that can re-draw a latent they receive, mapped to the widget inputs that decide
# how much. A ``None`` amount field means the node is walked for the node feeding it
# instead of carrying the amount itself: ``SamplerCustomAdvanced`` takes its re-draw
# amount from the scheduler producing ``sigmas``, and ``KSamplerSelect`` only names one.
REDAW_NODE_FIELDS: dict[str, tuple[str | None, str | None]] = {
    "KSampler": ("denoise", "steps"),
    "KSamplerAdvanced": ("denoise", "steps"),
    "UltimateSDUpscale": ("denoise", "steps"),
    "UltimateSDUpscaleCustomSample": ("denoise", "steps"),
    "BasicScheduler": ("denoise", "steps"),
    "SamplerCustomAdvanced": (None, "steps"),
    "KSamplerSelect": (None, None),
}
SIGMA_PRODUCER_FIELDS: dict[str, str] = {"SamplerCustomAdvanced": "sigmas"}

# Control vocabulary that names a stage of the pipeline. A control may say which pass it
# belongs to; it must not say a pass that its own reachability contradicts. The
# vocabulary is deliberately tiny, and every entry must name a ComfyUI class family whose
# membership is unambiguous in a graph.
STAGE_KEYWORD_CLASSES: dict[str, frozenset[str]] = {
    "upscale": frozenset(
        {
            "UltimateSDUpscale",
            "UltimateSDUpscaleCustomSample",
            "UpscaleModelLoader",
            "LatentUpscale",
            "LatentUpscaleBy",
            "LatentUpscaleTiled",
            "ImageUpscaleWithModel",
            "ImageUpscale",
        }
    ),
    "refine": frozenset({"KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"}),
}

# Where a control's value stops being "the amount for that stage". Any node that can
# re-draw a latent is a stage, so a label naming a stage is judged against the first such
# node the value arrives at and not against everything downstream of it.
STAGE_BOUNDARY_CLASSES: frozenset[str] = frozenset(
    set(REDAW_NODE_FIELDS) | set().union(*STAGE_KEYWORD_CLASSES.values())
)

# Interface-declared control classes, mirrored from the publication contract. A node of
# one of these classes that no interface input binds is a control the browser never sees.
CONTROL_PARAMETER_CLASSES = frozenset(
    {
        "CIFTextParameter",
        "CIFIntegerParameter",
        "CIFDecimalParameter",
        "CIFBooleanParameter",
        "CIFSeedParameter",
        "CIFChoiceParameter",
        "CIFLoraStack",
    }
)


@dataclass(frozen=True)
class PublicationLintExemption:
    """A recorded exception to one lint rule.

    ``target`` is an API node ID for the sampler and control rules and a public control ID
    for the label rule. ``field`` is the widget input for the sampler rules, ``"label"``
    for the label rule and ``"control"`` for an unpublished parameter node. ``source_id``
    narrows the exception to one published source; ``"*"`` applies to every source and is
    only right for a rule that is known to be wrong about a shape this project wants.
    """

    source_id: str
    target: str
    field: str
    reason: str


# Checked-in exemptions. An intentional constant belongs here with its reason, so the next
# reader learns that the constant was decided rather than missed. Nothing in this project's
# own fixtures needs one, and adding one should say why a literal beats a control.
SAMPLER_CONSTANT_EXEMPTIONS: tuple[PublicationLintExemption, ...] = ()


def _linked(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _number(value: Any) -> float | None:
    """The value as a finite float, or ``None`` when it is not a number at all.

    Returning the number instead of testing for it is deliberate: a ComfyUI widget is a
    bare ``Any`` from JSON, and every caller below needs the float itself, so narrowing by
    return value keeps the call sites free of ``cast``.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _node(api_document: Mapping[str, Any], node_id: str) -> Mapping[str, Any] | None:
    node = api_document.get(node_id)
    if isinstance(node, Mapping):
        return node
    found = next((value for key, value in api_document.items() if str(key) == node_id), None)
    return found if isinstance(found, Mapping) else None


def _node_title(node: Mapping[str, Any], node_id: str) -> str:
    meta = node.get("_meta")
    if isinstance(meta, Mapping):
        title = meta.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return node_id


def _class_type(node: Mapping[str, Any]) -> str | None:
    class_type = node.get("class_type")
    return class_type if isinstance(class_type, str) and class_type else None


def _consumer_edges(api_document: Mapping[str, Any]) -> dict[str, set[str]]:
    """Map each producer node ID to the nodes that consume one of its outputs."""

    edges: dict[str, set[str]] = {}
    for node_id, node in api_document.items():
        if not isinstance(node, Mapping):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        for value in inputs.values():
            if _linked(value):
                edges.setdefault(str(value[0]), set()).add(str(node_id))
    return edges


def _reachable_classes(
    api_document: Mapping[str, Any], edges: Mapping[str, set[str]], start: str
) -> set[str]:
    """Class types a control's value flows into, stopping at each stage boundary.

    The walk passes through helper nodes (a math expression, a gate) and terminates at the
    first node whose class is a stage: a control that feeds a refine sampler also feeds
    everything downstream of it, so an unbounded walk would call every later node "reached"
    and the label rule would never contradict anything.
    """

    classes: set[str] = set()
    seen: set[str] = set()
    queue = [start]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        node = _node(api_document, current)
        if node is None:
            continue
        class_type = _class_type(node)
        if class_type is not None:
            classes.add(class_type)
            if class_type in STAGE_BOUNDARY_CLASSES:
                continue
        queue.extend(node_id for node_id in edges.get(current, ()) if node_id not in seen)
    return classes


def _exemption(
    exemptions: Sequence[PublicationLintExemption],
    source_id: str,
    target: str,
    field: str,
) -> PublicationLintExemption | None:
    for exemption in exemptions:
        if (
            exemption.target == target
            and exemption.field == field
            and exemption.source_id in {source_id, "*"}
        ):
            return exemption
    return None


def _amount_owner(
    api_document: Mapping[str, Any], node_id: str, node: Mapping[str, Any], class_type: str
) -> tuple[str, Mapping[str, Any]] | None:
    """The node that actually holds this graph's re-draw amount, and its ID.

    A custom advanced sampler holds none of its own, so the amount is attributed to the
    scheduler producing its ``sigmas``; an exemption then names the widget a person would
    really edit.
    """

    producer_field = SIGMA_PRODUCER_FIELDS.get(class_type)
    if producer_field is None:
        return node_id, node
    inputs = node.get("inputs")
    if not isinstance(inputs, Mapping):
        return None
    producer = inputs.get(producer_field)
    if not isinstance(producer, (list, tuple)) or len(producer) < 1:
        return None
    producer_id = str(producer[0])
    owner = _node(api_document, producer_id)
    return None if owner is None else (producer_id, owner)


def lint_publication_graph(
    *,
    source_id: str,
    api_document: Mapping[str, Any],
    private_inputs: Sequence[Mapping[str, Any]],
    exemptions: Sequence[PublicationLintExemption] = SAMPLER_CONSTANT_EXEMPTIONS,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(warnings, diagnostics)`` for one frozen API graph.

    ``private_inputs`` are the validated interface declarations, which is what makes "no
    control is bound to this" provable rather than guessed: a linked widget counts as
    controlled whether the link lands on a published parameter node or on a math node that
    derives the amount from one.
    """

    warnings: list[str] = []
    accepted: list[str] = []
    suppressed = 0

    def report(message: str) -> None:
        nonlocal suppressed
        if len(warnings) < MAX_LINT_FINDINGS:
            warnings.append(message)
        else:
            suppressed += 1

    bound_nodes: dict[str, list[Mapping[str, Any]]] = {}
    for declaration in private_inputs:
        bindings = declaration.get("bindings")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if isinstance(binding, Mapping) and binding.get("node_id") is not None:
                bound_nodes.setdefault(str(binding["node_id"]), []).append(declaration)

    edges = _consumer_edges(api_document)
    # One finding per (node, widget): a custom advanced sampler and the scheduler that
    # holds its amount are the same widget seen twice.
    decided: set[tuple[str, str]] = set()
    graph_classes: set[str] = set()
    for raw_node in api_document.values():
        if isinstance(raw_node, Mapping):
            node_class = _class_type(raw_node)
            if node_class is not None:
                graph_classes.add(node_class)

    for raw_node_id, raw_node in api_document.items():
        if not isinstance(raw_node, Mapping):
            continue
        node_class = _class_type(raw_node)
        if node_class is None or node_class not in REDAW_NODE_FIELDS:
            continue
        denoise_field, steps_field = REDAW_NODE_FIELDS[node_class]
        owner = _amount_owner(api_document, str(raw_node_id), raw_node, node_class)
        if owner is None:
            continue
        node_id, node = owner
        owner_class = _class_type(node) or node_class
        if denoise_field is None:
            denoise_field = "denoise"
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        denoise = inputs.get(denoise_field)
        amount = _number(denoise)
        if amount is None:
            continue
        if not REDAW_DENOISE_WARNING_THRESHOLD < amount < 1.0:
            continue
        if node_id in bound_nodes:
            continue
        if (node_id, denoise_field) in decided:
            continue
        decided.add((node_id, denoise_field))
        title = _node_title(node, node_id)
        amount_text = f"{amount:g}"
        exemption = _exemption(exemptions, source_id, node_id, denoise_field)
        if exemption is not None:
            accepted.append(
                f'Node {node_id} ({owner_class}, "{title}") keeps a constant '
                f"{denoise_field} {amount_text}: {exemption.reason}"
            )
        else:
            report(
                f'Hidden re-draw: node {node_id} ({owner_class}, "{title}") re-draws '
                f"{amount_text} of the latent it receives, and no published control is bound "
                f"to its {denoise_field} input, so the amount cannot be changed or audited "
                f"from the app. Bind a CIFDecimalParameter to it or record an exemption. "
                f"Amounts at or below {REDAW_DENOISE_WARNING_THRESHOLD:g} are treated as a "
                f"resample and a full pass of 1.0 as a first pass, so neither is reported."
            )
        if steps_field is None:
            continue
        steps = _number(inputs.get(steps_field))
        if steps is None or steps >= REDAW_STEPS_WARNING_FLOOR:
            continue
        if (node_id, steps_field) in decided:
            continue
        decided.add((node_id, steps_field))
        steps_exemption = _exemption(exemptions, source_id, node_id, steps_field)
        if steps_exemption is not None:
            accepted.append(
                f'Node {node_id} ({owner_class}, "{title}") keeps a constant '
                f"{steps_field} {steps:g}: {steps_exemption.reason}"
            )
        else:
            report(
                f'Short re-draw: node {node_id} ({owner_class}, "{title}") spends only '
                f"{steps:g} steps on the same {amount_text} re-draw. The measured defect "
                f"moved with the re-draw amount and not with the step count, so publish the "
                f"amount before raising this number."
            )

    for declaration in private_inputs:
        control_id = declaration.get("id")
        if not isinstance(control_id, str):
            continue
        label = declaration.get("label") if isinstance(declaration.get("label"), str) else ""
        group = declaration.get("group") if isinstance(declaration.get("group"), str) else ""
        naming = f"{group} {label}".lower()
        keywords = sorted(keyword for keyword in STAGE_KEYWORD_CLASSES if keyword in naming)
        if not keywords:
            continue
        wanted: set[str] = set()
        for keyword in keywords:
            wanted |= STAGE_KEYWORD_CLASSES[keyword]
        if not graph_classes & wanted:
            # The stage the label names is not in this graph at all, which is normal for a
            # control that toggles an optional stage. Only a label that contradicts a
            # stage the graph actually contains is worth a warning.
            continue
        reachable: set[str] = set()
        bindings = declaration.get("bindings")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if isinstance(binding, Mapping) and binding.get("node_id") is not None:
                reachable |= _reachable_classes(api_document, edges, str(binding["node_id"]))
        if not reachable:
            # No binding resolves to a node in this graph, so nothing here proves or
            # contradicts the label. The contract validator owns that failure.
            continue
        if reachable & wanted:
            continue
        stage_exemption = _exemption(exemptions, source_id, control_id, "label")
        if stage_exemption is not None:
            accepted.append(
                f"Control {control_id} is labelled {label!r} without reaching the stage it "
                f"names: {stage_exemption.reason}"
            )
            continue
        stage_node_classes: dict[str, str] = {}
        for raw_node_id, raw_node in api_document.items():
            if not isinstance(raw_node, Mapping):
                continue
            node_class = _class_type(raw_node)
            if node_class is not None and node_class in wanted:
                stage_node_classes[str(raw_node_id)] = node_class
        stage_nodes = ", ".join(
            f"{node_id} ({node_class})"
            for node_id, node_class in sorted(stage_node_classes.items())[:3]
        )
        report(
            f"Stage mismatch: control {control_id} is labelled {label!r} in group {group!r}, "
            f"which names the {'/'.join(keywords)} stage, but its value reaches "
            f"{', '.join(sorted(reachable)) or 'nothing'}. "
            f"The stage it names is {stage_nodes or 'not in this graph'}. A control describing "
            f"a stage it does not touch is how an invisible constant stays invisible."
        )

    for raw_node_id, raw_node in api_document.items():
        if not isinstance(raw_node, Mapping):
            continue
        class_type = _class_type(raw_node)
        if class_type is None or class_type not in CONTROL_PARAMETER_CLASSES:
            continue
        node_id = str(raw_node_id)
        if node_id in bound_nodes:
            continue
        inputs = raw_node.get("inputs")
        parameter_id = inputs.get("parameter_id") if isinstance(inputs, Mapping) else None
        label = inputs.get("label") if isinstance(inputs, Mapping) else None
        if isinstance(parameter_id, str) and parameter_id:
            name = repr(parameter_id)
        elif isinstance(label, str) and label:
            name = f'"{label}"'
        else:
            name = f"node {node_id}"
        exemption = _exemption(exemptions, source_id, node_id, "control")
        if exemption is not None:
            accepted.append(f"Parameter node {node_id} stays unpublished: {exemption.reason}")
            continue
        report(
            f"Unpublished control: {name} is a {class_type} node that no published interface "
            f"input binds, so the app cannot see or change it. Declare the input or set the "
            f"value as a graph literal."
        )

    if suppressed:
        warnings.append(f"{suppressed} further publication lint findings were suppressed.")
    return tuple(warnings), tuple(accepted)
