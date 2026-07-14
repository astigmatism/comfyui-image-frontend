#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "normative-product-requirements-v1.0.md"
TARGET = ROOT / "docs" / "traceability.md"

IMPLEMENTATION = {
    "PROD": "`frontend/src/app.mjs`, `frontend/src/render.mjs`, `backend/app/services/generations.py`, `backend/app/services/queue_worker.py`",
    "ENG": "`backend/app/`, `frontend/src/`, `Dockerfile`, `backend/alembic/`",
    "DEP": "`Dockerfile`, `compose.example.yml`, `backend/app/main.py`, `backend/app/db.py`, `backend/app/services/queue_worker.py`",
    "CFG": "`backend/app/config.py`, `.env.example`, `backend/app/services/auth.py`, `backend/app/services/workflow_registry.py`",
    "AUTH": "`backend/app/security.py`, `backend/app/services/auth.py`, `backend/app/api/auth.py`, `backend/app/api/admin.py`",
    "PRIV": "`backend/app/dependencies.py`, `backend/app/api/generations.py`, `backend/app/api/uploads.py`, `backend/app/services/generations.py`",
    "DATA": "`backend/app/models.py`, `backend/alembic/versions/19a5fe877349_initial_schema.py`, `backend/app/services/assets.py`, `backend/app/services/generations.py`, `backend/app/services/queue_worker.py`",
    "COMFY": "`backend/app/services/comfyui.py`, `backend/app/services/workflow_registry.py`, `backend/app/services/queue_worker.py`, `backend/app/domain/results.py`",
    "WF": "`backend/app/domain/publication.py`, `backend/app/domain/compiler.py`, `backend/app/domain/results.py`, `backend/app/services/workflow_registry.py`, `backend/app/api/workflows.py`",
    "OLL": "`backend/app/services/ollama.py`, `backend/app/api/prompt_assistant.py`, `frontend/src/render.mjs`, `frontend/src/app.mjs`",
    "CTRL": "`frontend/src/render.mjs`, `frontend/src/lib.mjs`, `frontend/src/app.mjs`, `backend/app/domain/compiler.py`",
    "QUEUE": "`backend/app/models.py`, `backend/app/services/generations.py`, `backend/app/services/queue_worker.py`",
    "EXEC": "`backend/app/domain/compiler.py`, `backend/app/domain/results.py`, `backend/app/services/queue_worker.py`, `backend/app/api/events.py`, `backend/app/services/events.py`",
    "UI": "`frontend/src/styles.css`, `frontend/src/render.mjs`, `frontend/src/app.mjs`",
    "GAL": "`frontend/src/render.mjs`, `frontend/src/app.mjs`, `frontend/src/styles.css`, `backend/app/services/generations.py`",
    "RECALL": "`backend/app/services/generations.py`, `backend/app/schemas.py`, `frontend/src/lib.mjs`, `frontend/src/app.mjs`, `README.md`",
    "DEL": "`backend/app/services/generations.py`, `backend/app/services/user_deletion.py`, `backend/app/services/queue_worker.py`",
    "FAIL": "`backend/app/services/workflow_registry.py`, `backend/app/services/comfyui.py`, `backend/app/services/queue_worker.py`, `backend/app/services/ollama.py`, `frontend/src/render.mjs`",
    "SEC": "`backend/app/security.py`, `backend/app/dependencies.py`, `backend/app/services/assets.py`, `backend/app/domain/publication.py`, `backend/app/services/comfyui.py`, `backend/app/main.py`",
    "A11Y": "`frontend/src/render.mjs`, `frontend/src/styles.css`, `frontend/src/app.mjs`",
    "PERF": "`backend/app/models.py`, `backend/app/services/generations.py`, `backend/app/services/queue_worker.py`, `frontend/src/app.mjs`, `frontend/src/render.mjs`",
    "API": "`backend/app/api/`, `backend/app/schemas.py`, `backend/app/main.py`, `docs/api.md`",
    "TEST": "`backend/tests/`, `frontend/test/`, `frontend/e2e/`, `scripts/validate.sh`, `scripts/container-smoke.sh`, `Makefile`",
    "DOC": "`README.md`, `.env.example`, `docs/published-workflows.md`, `docs/migration-published-workflows.md`, `docs/architecture.md`, `docs/api.md`, `docs/database.md`, `docs/testing.md`, `docs/traceability.md`",
}

TESTS = {
    "PROD": "`backend/tests/integration/test_generation_lifecycle.py`; `frontend/test/render.test.mjs`; Playwright principal journeys",
    "ENG": "complete pytest/frontend suites; production build; container smoke",
    "DEP": "`backend/tests/integration/test_migrations.py`; `scripts/container-smoke.sh`",
    "CFG": "`backend/tests/integration/test_auth_accounts.py`; queue/outage integration tests",
    "AUTH": "`backend/tests/integration/test_auth_accounts.py`; `backend/tests/integration/test_user_deletion.py`",
    "PRIV": "`backend/tests/integration/test_authorization_and_uploads.py`; `backend/tests/unit/test_events.py`",
    "DATA": "migration, lifecycle, queue/recovery, gallery/pagination, and deletion integration tests",
    "COMFY": "adapter/registry/result unit tests; lifecycle and queue/recovery integration tests using deterministic fake HTTP/WS",
    "WF": "publication fixtures; registry/compiler/result unit tests; source discovery and lifecycle integration tests",
    "OLL": "`backend/tests/integration/test_workflows_and_prompt_assistant.py`; `frontend/test/render.test.mjs`",
    "CTRL": "compiler unit tests; discovery API integration; `frontend/test/lib.test.mjs`; `frontend/test/render.test.mjs`",
    "QUEUE": "`backend/tests/integration/test_queue_and_recovery.py`; rapid-submission lifecycle test",
    "EXEC": "compiler/result unit tests; lifecycle, queue/recovery, event broker/SSE, and artifact persistence integration tests",
    "UI": "`frontend/test/render.test.mjs`; `frontend/test/lib.test.mjs`; Playwright principal journeys",
    "GAL": "gallery/recall/preferences and lifecycle integration tests; frontend render tests; Playwright",
    "RECALL": "lifecycle exact recall; unavailable source; Prompt Assistant provenance; frontend overwrite test; Playwright",
    "DEL": "generation lifecycle deletion and user cascade deletion integration tests",
    "FAIL": "Ollama outage, ComfyUI outage/recovery, failure/cancel, persistence failure integration tests",
    "SEC": "authorization/uploads, authentication/CSRF/throttle, publication/compiler strictness, adapter path/size checks, and deletion tests",
    "A11Y": "semantic markup/component assertions and keyboard-native controls in frontend tests; Playwright interaction journeys",
    "PERF": "cursor pagination/preference test; targeted card updates and thumbnail assertions",
    "API": "all backend integration tests exercise the same product API; OpenAPI model construction during startup",
    "TEST": "the referenced test suites and validation scripts are themselves the deliverable",
    "DOC": "`scripts/generate_traceability.py --check`; README command review; container/startup test documentation",
}

PUBLICATION_ROWS = [
    ("01", "Only deliberate three-file publications are candidates", "`.interface.json` filtering and adjacent artifact resolution in `services/workflow_registry.py`", "empty/orphan/multiple publication fixtures and registry tests"),
    ("02", "Preferred and fallback recursive userdata listing", "bounded route probing in `services/comfyui.py`", "adapter tests for `/v2/userdata` and compatibility `/userdata`"),
    ("03", "ComfyUI multi-user identity is preserved", "`Comfy-User` HTTP/WebSocket headers in `services/comfyui.py`", "adapter request/header assertions"),
    ("04", "Nested userdata paths are encoded as one segment", "whole-value `quote(..., safe='')` encoding in `services/comfyui.py`", "nested-path and encoded-slash adapter tests"),
    ("05", "Every response class is conservatively bounded", "listing/object-info/manifest/workflow/API/history/output limits in config and adapter", "over-limit adapter/publication fixtures"),
    ("06", "Paths, schemas, stems, source ID, strict JSON and node count fail closed", "`domain/publication.py`", "adversarial publication validation tests"),
    ("07", "Frozen API integrity uses the hash of exact raw bytes", "fail-closed API raw-byte SHA-256 validation in `domain/publication.py`", "API corruption and manifest revision fixtures"),
    ("08", "Public inputs and private bindings are strictly validated", "interface/type/default/range/step/role/binding checks in `domain/publication.py`", "all-type, duplicate-ID, prompt-role, missing-target and class-mismatch tests"),
    ("09", "Declared dependencies must cover and exist in object info", "dependency coverage and readiness in `domain/publication.py` / `services/workflow_registry.py`", "omitted/missing dependency fixtures"),
    ("10", "Candidate acceptance and refresh are atomic", "immutable revision publication in `services/workflow_registry.py`", "bad republish retains prior accepted revision; independent candidate failure tests"),
    ("11", "Transport failure retains the last valid catalog and recovery refreshes it", "service-health/cache state plus offline-to-online discovery in registry/worker services", "startup/outage, empty-cache recovery and cached-offline tests"),
    ("12", "Logical source and immutable revision identities are stable", "instance/source key plus publication and three hashes", "republish, new-job, in-flight, and recall identity tests"),
    ("13", "Public source descriptors are allowlisted", "projection in `api/workflows.py`", "API assertions excluding paths, graphs, bindings, node IDs and dependencies"),
    ("14", "Dynamic controls support all interface v1 types", "`frontend/src/lib.mjs`, `render.mjs`, `app.mjs`", "frontend ordering/default/type/Advanced rendering tests"),
    ("15", "Unknown fields and graph/binding injection are rejected", "`domain/compiler.py` and forbidden-extra request schemas", "compiler/API rejection tests"),
    ("16", "Large fixed and random seeds round-trip exactly", "decimal-string seed semantics in compiler/schema/frontend", "maximum-range, omitted/null/random, effective-value and recall tests"),
    ("17", "Compilation is request-local and patches trusted bindings only", "deep clone, binding patch and cache mutation assertion in `domain/compiler.py`", "multi-binding, graph immutability and isolated compilation tests"),
    ("18", "Accepted editable workflow metadata is attached when declared without becoming the executable boundary", "publication runtime snapshot and `queue_worker.py` prompt `extra_data`", "submission payload tests plus separate observed/published hash diagnostics"),
    ("19", "Native prompt ID and accepted source/parameters are durable", "generation acceptance and prompt transition in generation/queue services", "lifecycle, restart and recall integration tests"),
    ("20", "History is terminal truth with bounded reconciliation", "WebSocket plus history monitor/recovery in `queue_worker.py`", "delayed history, cached execution, restart and outage tests"),
    ("21", "Connected multi-publisher declarations have unique IDs/UUIDs/nodes, cardinality many, and exactly one final", "`domain/publication.py`", "strict publication/output/native-inventory fixtures and contract tests"),
    ("22", "Exact publisher metadata and every untouched nonpublisher result coexist independently of native inventory", "`domain/results.py`", "list-shaped multi-role publisher, inventory-independence, and arbitrary custom-field tests"),
    ("23", "Complete native result/status/error/execution metadata is retained with only submitted graph envelopes removed publicly", "generation result JSON fields plus top-level prompt/extra-data removal in detail projection", "success/error/interruption/partial-output and public-boundary tests"),
    ("24", "ComfyUI file references cannot become arbitrary paths", "tuple/type allowlist and bounded `/view` in publication/adapter code", "path/type/size adversarial tests"),
    ("25", "Authoritative publisher batches remain ordered, archived, inspectable, and downloadable without double-counting mirrored images", "result normalization, queue/asset services, ordered detail API and role-grouped renderer", "multiple-publisher/multiple-batch lifecycle and render tests"),
    ("26", "Legacy rows remain readable while legacy discovery is retired", "additive Alembic migration and compatibility projections", "migration and historical generation/recall tests"),
    ("27", "Publication behavior is documented operationally", "`.env.example`, `docs/published-workflows.md`, API/architecture/database/testing docs", "link checks, generated traceability check and documented live procedure"),
    ("28", "Finite choices expose only stable public values while private mappings remain frozen", "choice validation/projection/compiler paths in `domain/publication.py`, `api/workflows.py`, and `domain/compiler.py`", "choice manifest, API privacy, invalid request, and graph-isolation tests"),
    ("29", "Choice companion strengths resolve deterministically without weakening exhaustive outputs", "choice/default resolution in `domain/compiler.py` and selector state in frontend modules", "default-strength precedence, explicit override, concurrency, lifecycle, and output regression tests"),
    ("30", "Mutable editable-workflow drift warns without hiding a valid frozen runtime", "warning-only editable hash comparison in `domain/publication.py` plus refresh metadata in `services/workflow_registry.py`", "contract and registry tests for initial discovery, repeated refresh, two-source availability, and strict API mismatch rejection"),
]


def requirements() -> list[tuple[str, str]]:
    text = SOURCE.read_text(encoding="utf-8")
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = re.search(r"\*\*([A-Z]+-\d{3}):\*\*\s*(.+)", line)
        if match:
            summary = re.sub(r"\s+", " ", match.group(2).strip())
            found.append((match.group(1), summary))
    if not found:
        raise RuntimeError(f"No requirement IDs found in {SOURCE}")
    return found


def render() -> str:
    rows = requirements()
    lines = [
        "# Requirement traceability",
        "",
        "This matrix is generated by `scripts/generate_traceability.py`. The first table retains every ID from the historical product v1.0 baseline and maps it to the current repository. Workflow-integration language in that baseline is superseded by the publication acceptance matrix that follows. Run `python3 scripts/generate_traceability.py --check` to detect an omitted or stale row.",
        "",
        f"**Coverage:** {len(rows)} of {len(rows)} product requirement IDs mapped.",
        "",
        "| Requirement | Requirement summary | Implementation | Automated evidence |",
        "|---|---|---|---|",
    ]
    for requirement_id, summary in rows:
        prefix = requirement_id.split("-", 1)[0]
        implementation = IMPLEMENTATION[prefix]
        tests = TESTS[prefix]
        safe_summary = summary.replace("|", "\\|")
        lines.append(f"| **{requirement_id}** | {safe_summary} | {implementation} | {tests} |")
    lines.extend(
        [
            "",
            "## Published-workflow acceptance mapping",
            "",
            "The current integration accepts only deliberate three-file publications using `comfyui-image-frontend.publication/v1` and `comfyui-image-frontend.interface/v1`. These criteria replace the retired embedded-node/two-file contract mapping.",
            "",
            "| Publication criterion | Meaning | Implementation | Evidence |",
            "|---|---|---|---|",
        ]
    )
    for number, meaning, implementation, evidence in PUBLICATION_ROWS:
        lines.append(f"| **PUB-{number}** | {meaning} | {implementation} | {evidence} |")
    lines.extend(
        [
            "",
            "## Normative boundary",
            "",
            "The ComfyUI publisher/custom-node package, prepared workflows, models, and custom-node dependencies are external prerequisites. This repository discovers and validates committed `<stem>.json` / `<stem>.api.json` / `<stem>.interface.json` publications through network APIs; it does not implement the publisher, install dependencies, mutate artifacts, or revive the historical embedded `FrontendWorkflowContract` design.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        actual = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if actual != expected:
            print("docs/traceability.md is stale; run scripts/generate_traceability.py", file=sys.stderr)
            return 1
        print(f"Traceability is current ({len(requirements())} requirement IDs).")
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)} with {len(requirements())} requirement IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
