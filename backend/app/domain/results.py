from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, TypeGuard

from .publication import find_file_references

INTERRUPTION_MESSAGE_TYPES = {
    "execution_interrupted",
    "execution_cancelled",
    "execution_canceled",
}

IMAGE_FILE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

_REDACTED_VALUE = "[redacted]"
_MAX_PUBLIC_RESULT_DEPTH = 128
_PRIVATE_RESULT_KEYS = {
    "api_graph",
    "binding",
    "bindings",
    "class_type",
    "client_id",
    "comfyui_image_frontend",
    "current_inputs",
    "current_outputs",
    "dependencies",
    "dependency_class_types",
    "executed",
    "extra_data",
    "extra_pnginfo",
    "graph",
    "instance_uuid",
    "links",
    "meta",
    "node",
    "node_errors",
    "node_id",
    "node_ids",
    "node_type",
    "nodes",
    "object_info",
    "prompt",
    "prompt_graph",
    "publisher_instance",
    "selector",
    "source_id",
    "traceback",
    "userdata",
    "workflow",
}
_SECRET_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/])(?:/(?:[^\s\"'<>]+)|~/(?:[^\s\"'<>]+)|"
    r"[A-Za-z]:[\\/](?:[^\s\"'<>]+)|\\\\(?:[^\s\"'<>]+))"
)


@dataclass(frozen=True)
class NativeFileOutput:
    node_id: str
    output_id: str
    role: str
    kind: str
    batch_index: int
    reference: dict[str, str]
    declared: bool
    sequence: int = 0


@dataclass(frozen=True)
class NormalizedHistory:
    declared_outputs: dict[str, Any]
    unmapped_outputs: dict[str, Any]
    files: tuple[NativeFileOutput, ...]
    status: dict[str, Any]
    errors: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


def project_public_result(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-safe result projection with private runtime diagnostics removed.

    Native output-node mapping keys and safe output payloads remain intact. Fields capable of
    carrying the submitted graph, bindings, server paths, publisher identity, tracebacks, current
    node inputs/outputs, or credentials are removed recursively.
    """

    if _depth > _MAX_PUBLIC_RESULT_DEPTH:
        return _REDACTED_VALUE
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if -(2**63) <= value <= 2**64 - 1 else _REDACTED_VALUE
    if isinstance(value, float):
        return value if math.isfinite(value) else _REDACTED_VALUE
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return _REDACTED_VALUE
        return _ABSOLUTE_PATH_RE.sub("[redacted path]", value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, nested in value.items():
            if not isinstance(raw_key, (str, int)) or isinstance(raw_key, bool):
                continue
            key = str(raw_key)
            try:
                encoded_key = key.encode("utf-8")
            except UnicodeEncodeError:
                continue
            if not encoded_key or len(encoded_key) > 1_024:
                continue
            if _private_result_key(key):
                continue
            if key == "filename" and not _safe_public_filename(nested):
                result[key] = _REDACTED_VALUE
                continue
            if key == "subfolder" and not _safe_public_subfolder(nested):
                result[key] = _REDACTED_VALUE
                continue
            result[key] = project_public_result(nested, _depth=_depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [project_public_result(nested, _depth=_depth + 1) for nested in value]
    return _REDACTED_VALUE


def project_public_declared_outputs(
    value: Any,
    *,
    output_order: Sequence[str] = (),
    artifacts: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return declarations as an ordered semantic view with logical artifact references.

    Normalized declarations deliberately do not duplicate their publisher node's ordinary output
    payload. That payload remains authoritative and exhaustive in ``raw_history``. Each logical
    publisher artifact reference is retained here and, when available, joined to the corresponding
    application-owned artifact summary without replacing the native reference.
    """

    entries: dict[str, Mapping[str, Any]] = {}
    if isinstance(value, Mapping):
        for raw_output_id, raw_output in value.items():
            if (
                isinstance(raw_output_id, (str, int))
                and not isinstance(raw_output_id, bool)
                and isinstance(raw_output, Mapping)
            ):
                entries[str(raw_output_id)] = raw_output
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for raw_output in value:
            if not isinstance(raw_output, Mapping):
                continue
            raw_output_id = raw_output.get("output_id", raw_output.get("id"))
            if isinstance(raw_output_id, str):
                entries[raw_output_id] = raw_output

    ordered_ids: list[str] = []
    for output_id in output_order:
        if output_id in entries and output_id not in ordered_ids:
            ordered_ids.append(output_id)
    ordered_ids.extend(output_id for output_id in entries if output_id not in ordered_ids)

    archived: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for artifact in artifacts:
        raw_output_id = artifact.get("output_id")
        batch_index = artifact.get("batch_index")
        if not isinstance(raw_output_id, str) or not _is_batch_index(batch_index):
            continue
        archived.setdefault((raw_output_id, batch_index), []).append(copy.deepcopy(dict(artifact)))

    result: list[dict[str, Any]] = []
    for output_id in ordered_ids:
        raw_output = entries[output_id]
        public_output: dict[str, Any] = {"id": output_id, "output_id": output_id}
        for key in (
            "schema_version",
            "role",
            "kind",
            "label",
            "cardinality",
            "description",
        ):
            if key in raw_output:
                public_output[key] = copy.deepcopy(raw_output[key])
        public_output.setdefault("role", "auxiliary")
        public_output.setdefault("kind", "file")
        public_output.setdefault("cardinality", "many")
        public_output.setdefault("description", "")

        public_references: list[dict[str, Any]] = []
        raw_references = raw_output.get("artifacts", [])
        if isinstance(raw_references, Sequence) and not isinstance(
            raw_references, (str, bytes, bytearray)
        ):
            for raw_reference in raw_references:
                if not isinstance(raw_reference, Mapping):
                    continue
                batch_index = raw_reference.get("batch_index")
                if not _is_batch_index(batch_index):
                    continue
                reference = {
                    key: copy.deepcopy(raw_reference[key])
                    for key in ("batch_index", "filename", "subfolder", "type")
                    if key in raw_reference
                }
                matches = archived.get((output_id, batch_index), [])
                reference["artifact"] = matches.pop(0) if matches else None
                public_references.append(reference)

        # Historical declarations did not retain logical references. Keep their archived files
        # visible without manufacturing native filename/subfolder values.
        for (artifact_output_id, batch_index), summaries in archived.items():
            if artifact_output_id != output_id:
                continue
            while summaries:
                public_references.append({"batch_index": batch_index, "artifact": summaries.pop(0)})
        public_output["artifacts"] = public_references
        result.append(public_output)
    return result


def _private_result_key(value: str) -> bool:
    key = value.casefold().replace("-", "_")
    if key in _PRIVATE_RESULT_KEYS or key == "path" or key.endswith("_path"):
        return True
    key_tokens = set(key.split("_"))
    if key_tokens.intersection(
        {"binding", "bindings", "dependency", "dependencies", "graph", "userdata"}
    ):
        return True
    if key.startswith("node_") or key.endswith("_node_id") or key.endswith("_node_ids"):
        return True
    return any(fragment in key for fragment in _SECRET_KEY_FRAGMENTS)


def _safe_public_filename(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= 500
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
        and PurePosixPath(value).name == value
    )


def _safe_public_subfolder(value: Any) -> bool:
    if value == "":
        return True
    if not isinstance(value, str) or len(value) > 500:
        return False
    path = PurePosixPath(value)
    return bool(
        not value.startswith("/")
        and "\\" not in value
        and "\x00" not in value
        and "//" not in value
        and path.parts
        and all(part not in {".", ".."} for part in path.parts)
        and str(path) == value
    )


def history_status_indicates_interruption(status: Any) -> bool:
    """Recognize ComfyUI's native interruption history shape.

    ComfyUI records an interrupted executor as ``status_str=error`` with
    ``completed=false``. The distinguishing source-of-truth signal is the
    ``execution_interrupted`` message retained in the status message list.
    """

    if not isinstance(status, Mapping):
        return False
    messages = status.get("messages", [])
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        return False
    for message in messages:
        message_type: Any = None
        if isinstance(message, Mapping):
            message_type = message.get("type", message.get("event"))
        elif isinstance(message, Sequence) and not isinstance(message, (str, bytes, bytearray)):
            message_type = message[0] if message else None
        elif isinstance(message, str):
            message_type = message
        if isinstance(message_type, str) and message_type.casefold() in INTERRUPTION_MESSAGE_TYPES:
            return True
    return False


def normalize_history(
    history: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    warnings: Sequence[str] = (),
) -> NormalizedHistory:
    raw_outputs = history.get("outputs", {})
    outputs = raw_outputs if isinstance(raw_outputs, Mapping) else {}
    raw_declarations = contract.get("outputs", [])
    declaration_values = (
        raw_declarations
        if isinstance(raw_declarations, Sequence)
        and not isinstance(raw_declarations, (str, bytes, bytearray))
        else []
    )
    declarations: dict[str, Mapping[str, Any]] = {}
    declaration_order: list[str] = []
    declaration_sequence: dict[str, int] = {}
    publisher_node_ids: set[str] = set()
    for raw_declaration in declaration_values:
        if not isinstance(raw_declaration, Mapping):
            continue
        raw_output_id = raw_declaration.get("id", raw_declaration.get("output_id"))
        if not isinstance(raw_output_id, str) or raw_output_id in declarations:
            continue
        declarations[raw_output_id] = raw_declaration
        declaration_sequence[raw_output_id] = len(declaration_order)
        declaration_order.append(raw_output_id)
        raw_node_id = raw_declaration.get("node_id")
        if isinstance(raw_node_id, (str, int)) and not isinstance(raw_node_id, bool):
            publisher_node_ids.add(str(raw_node_id))

    matched_outputs: dict[str, dict[str, Any]] = {}
    declared_files: dict[str, list[NativeFileOutput]] = {}
    unmapped_outputs: dict[str, Any] = {}
    native_files: list[NativeFileOutput] = []
    errors: list[dict[str, Any]] = []
    contract_schema = str(contract.get("schema", "comfyui-image-frontend.interface/v1"))

    for raw_node_id, raw_output in outputs.items():
        node_id = str(raw_node_id)
        if not isinstance(raw_output, Mapping):
            if node_id not in publisher_node_ids:
                unmapped_outputs[node_id] = copy.deepcopy(raw_output)
            continue
        metadata = _publisher_metadata(raw_output)
        valid_publisher_artifact_count = 0
        for item in metadata:
            if not isinstance(item, Mapping):
                errors.append(
                    {
                        "code": "publisher_metadata_invalid",
                        "message": "ComfyUI returned a malformed publisher declaration.",
                    }
                )
                continue
            raw_output_id = item.get("output_id")
            if not isinstance(raw_output_id, str) or not raw_output_id:
                errors.append(
                    {
                        "code": "publisher_metadata_invalid",
                        "message": "ComfyUI returned a malformed publisher declaration.",
                        "field": "output_id",
                    }
                )
                continue
            output_id = raw_output_id
            declaration = declarations.get(output_id)
            if declaration is None:
                errors.append(
                    {
                        "code": "undeclared_publisher_output",
                        "message": "ComfyUI returned publisher metadata for an undeclared output.",
                    }
                )
                continue
            if str(declaration.get("node_id")) != node_id:
                errors.append(
                    {
                        "code": "publisher_binding_mismatch",
                        "message": (
                            "ComfyUI returned declared publisher metadata from an unexpected "
                            "output node."
                        ),
                        "output_id": output_id,
                    }
                )
                continue
            mismatch = _publisher_metadata_mismatch(
                item,
                declaration=declaration,
                contract_schema=contract_schema,
            )
            if mismatch is not None:
                errors.append(
                    {
                        "code": "publisher_metadata_mismatch",
                        "message": (
                            "ComfyUI publisher metadata did not match the frozen declaration."
                        ),
                        "output_id": output_id,
                        "field": mismatch,
                    }
                )
                continue
            if output_id in matched_outputs:
                errors.append(
                    {
                        "code": "duplicate_publisher_output",
                        "message": "ComfyUI returned a publisher output more than once.",
                        "output_id": output_id,
                    }
                )
                continue

            references, reference_errors = _publisher_artifact_references(
                item.get("artifacts"), output_id=output_id
            )
            errors.extend(reference_errors)
            valid_publisher_artifact_count += len(references)
            matched_outputs[output_id] = {
                "schema_version": item["schema_version"],
                "output_id": output_id,
                "role": item["role"],
                "kind": item["kind"],
                "label": declaration.get("label", output_id),
                "cardinality": item["cardinality"],
                "description": item["description"],
                "artifacts": copy.deepcopy(references),
            }
            declared_files[output_id] = [
                NativeFileOutput(
                    node_id=node_id,
                    output_id=output_id,
                    role=str(item["role"]),
                    kind=str(item["kind"]),
                    sequence=declaration_sequence[output_id],
                    batch_index=reference["batch_index"],
                    reference={
                        "filename": reference["filename"],
                        "subfolder": reference["subfolder"],
                        "type": reference["type"],
                    },
                    declared=True,
                )
                for reference in references
            ]

        # A manifest-bound publisher node is represented by its semantic declaration and by the
        # complete raw history record. Its ordinary images/custom UI payload is intentionally not
        # duplicated into the nonpublisher map or recursively reclassified as declared files.
        if node_id not in publisher_node_ids:
            unmapped_outputs[node_id] = copy.deepcopy(dict(raw_output))
            for batch_index, reference in enumerate(find_file_references(raw_output)):
                native_files.append(
                    NativeFileOutput(
                        node_id=node_id,
                        output_id=f"native:{node_id}"[:255],
                        role="unmapped",
                        kind=_native_file_kind(reference),
                        sequence=0,
                        batch_index=batch_index,
                        reference=reference,
                        declared=False,
                    )
                )
        elif valid_publisher_artifact_count == 0:
            # Malformed, mismatched, absent, or empty namespaced artifacts must not cause a useful
            # partial image to disappear. Keep the publisher node out of ``unmapped_outputs`` but
            # archive its ordinary locators under a nonsemantic native ID. Exact publisher
            # payloads take the namespaced branch above and therefore never double-scan mirrors.
            for batch_index, reference in enumerate(find_file_references(raw_output)):
                native_files.append(
                    NativeFileOutput(
                        node_id=node_id,
                        output_id=f"native:{node_id}"[:255],
                        role="unmapped",
                        kind=_native_file_kind(reference),
                        sequence=0,
                        batch_index=batch_index,
                        reference=reference,
                        declared=False,
                    )
                )

    for output_id in declaration_order:
        if output_id not in matched_outputs:
            errors.append(
                {
                    "code": "missing_publisher_output",
                    "message": "ComfyUI history did not contain an expected publisher output.",
                    "output_id": output_id,
                }
            )

    declared_outputs = {
        output_id: matched_outputs[output_id]
        for output_id in declaration_order
        if output_id in matched_outputs
    }
    files = [
        file_output
        for output_id in declaration_order
        for file_output in declared_files.get(output_id, [])
    ]
    files.extend(native_files)

    raw_status = history.get("status", {})
    status = (
        copy.deepcopy(dict(raw_status))
        if isinstance(raw_status, Mapping)
        else {"status": raw_status}
    )
    status_text = str(status.get("status_str", status.get("status", ""))).casefold()
    if history_status_indicates_interruption(status):
        errors.append(
            {
                "code": "comfyui_execution_interrupted",
                "message": "ComfyUI reported an interrupted execution.",
            }
        )
    elif status_text in {"error", "failed"}:
        errors.append(
            {
                "code": "comfyui_execution_failed",
                "message": "ComfyUI reported an execution failure.",
            }
        )
    elif status_text in {"cancelled", "canceled", "interrupted"}:
        errors.append(
            {
                "code": "comfyui_execution_interrupted",
                "message": "ComfyUI reported an interrupted execution.",
            }
        )
    return NormalizedHistory(
        declared_outputs=declared_outputs,
        unmapped_outputs=unmapped_outputs,
        files=tuple(files),
        status=status,
        errors=tuple(errors),
        warnings=tuple(str(value) for value in warnings),
    )


def _native_file_kind(reference: Mapping[str, Any]) -> str:
    """Classify only well-known image filenames as images.

    Native ComfyUI output objects may contain arbitrary allowlisted file locators. Treating
    every locator as an image would unnecessarily run generic files through Pillow and lose
    otherwise safe, downloadable output data.
    """

    filename = reference.get("filename")
    if not isinstance(filename, str):
        return "file"
    suffix = "." + filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    return "image" if suffix in IMAGE_FILE_SUFFIXES else "file"


def _is_batch_index(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _publisher_metadata_mismatch(
    item: Mapping[str, Any],
    *,
    declaration: Mapping[str, Any],
    contract_schema: str,
) -> str | None:
    expected = {
        "schema_version": contract_schema,
        "output_id": declaration.get("id", declaration.get("output_id")),
        "instance_uuid": declaration.get("instance_uuid"),
        "role": declaration.get("role"),
        "kind": declaration.get("kind"),
        "cardinality": declaration.get("cardinality", "many"),
        "description": declaration.get("description", ""),
    }
    for field, expected_value in expected.items():
        if item.get(field) != expected_value:
            return field
    return None


def _publisher_artifact_references(
    value: Any, *, output_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    references: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return (
            references,
            [
                {
                    "code": "publisher_artifacts_invalid",
                    "message": "ComfyUI returned a malformed publisher artifact list.",
                    "output_id": output_id,
                }
            ],
        )
    seen_batch_indices: set[int] = set()
    for artifact_index, raw_reference in enumerate(value):
        valid = isinstance(raw_reference, Mapping)
        batch_index = raw_reference.get("batch_index") if valid else None
        filename = raw_reference.get("filename") if valid else None
        subfolder = raw_reference.get("subfolder", "") if valid else None
        storage_type = raw_reference.get("type", "output") if valid else None
        if not (
            _is_batch_index(batch_index)
            and isinstance(filename, str)
            and filename
            and isinstance(subfolder, str)
            and isinstance(storage_type, str)
            and storage_type in {"input", "output", "temp"}
        ):
            errors.append(
                {
                    "code": "publisher_artifact_invalid",
                    "message": "ComfyUI returned a malformed publisher artifact reference.",
                    "output_id": output_id,
                    "artifact_index": artifact_index,
                }
            )
            continue
        if batch_index in seen_batch_indices:
            errors.append(
                {
                    "code": "publisher_artifact_batch_duplicate",
                    "message": "ComfyUI returned duplicate publisher artifact batch indices.",
                    "output_id": output_id,
                    "artifact_index": artifact_index,
                }
            )
        seen_batch_indices.add(batch_index)
        references.append(
            {
                "batch_index": batch_index,
                "filename": filename,
                "subfolder": subfolder,
                "type": storage_type,
            }
        )
    return references, errors


def _publisher_metadata(output: Mapping[str, Any]) -> list[Any]:
    # Handoff 2's top-level list is authoritative. Retain the older nested envelope only as a
    # read/runtime compatibility fallback when the canonical key is absent.
    if "comfyui_image_frontend" in output:
        candidates: list[Any] = [output.get("comfyui_image_frontend")]
    else:
        candidates = []
    ui = output.get("ui")
    if not candidates and isinstance(ui, Mapping):
        candidates.append(ui.get("comfyui_image_frontend"))
    result: list[Any] = []
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            nested = candidate.get("outputs")
            if isinstance(nested, list):
                result.extend(nested)
            else:
                result.append(candidate)
        elif isinstance(candidate, list):
            result.extend(candidate)
        elif candidate is not None:
            result.append(candidate)
    return result
