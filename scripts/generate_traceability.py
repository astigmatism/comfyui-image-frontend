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
    "COMFY": "`backend/app/services/comfyui.py`, `backend/app/services/workflow_registry.py`, `backend/app/services/queue_worker.py`",
    "WF": "`backend/app/domain/contract.py`, `backend/app/domain/compiler.py`, `backend/app/services/workflow_registry.py`, `backend/app/api/workflows.py`",
    "OLL": "`backend/app/services/ollama.py`, `backend/app/api/prompt_assistant.py`, `frontend/src/render.mjs`, `frontend/src/app.mjs`",
    "CTRL": "`frontend/src/render.mjs`, `frontend/src/lib.mjs`, `frontend/src/app.mjs`, `backend/app/domain/compiler.py`",
    "QUEUE": "`backend/app/models.py`, `backend/app/services/generations.py`, `backend/app/services/queue_worker.py`",
    "EXEC": "`backend/app/domain/compiler.py`, `backend/app/services/queue_worker.py`, `backend/app/api/events.py`, `backend/app/services/events.py`",
    "UI": "`frontend/src/styles.css`, `frontend/src/render.mjs`, `frontend/src/app.mjs`",
    "GAL": "`frontend/src/render.mjs`, `frontend/src/app.mjs`, `frontend/src/styles.css`, `backend/app/services/generations.py`",
    "RECALL": "`backend/app/services/generations.py`, `backend/app/schemas.py`, `frontend/src/lib.mjs`, `frontend/src/app.mjs`, `README.md`",
    "DEL": "`backend/app/services/generations.py`, `backend/app/services/user_deletion.py`, `backend/app/services/queue_worker.py`",
    "FAIL": "`backend/app/services/workflow_registry.py`, `backend/app/services/queue_worker.py`, `backend/app/services/ollama.py`, `frontend/src/render.mjs`",
    "SEC": "`backend/app/security.py`, `backend/app/dependencies.py`, `backend/app/services/assets.py`, `backend/app/domain/contract.py`, `backend/app/main.py`",
    "A11Y": "`frontend/src/render.mjs`, `frontend/src/styles.css`, `frontend/src/app.mjs`",
    "PERF": "`backend/app/models.py`, `backend/app/services/generations.py`, `backend/app/services/queue_worker.py`, `frontend/src/app.mjs`, `frontend/src/render.mjs`",
    "API": "`backend/app/api/`, `backend/app/schemas.py`, `backend/app/main.py`, `docs/api.md`",
    "TEST": "`backend/tests/`, `frontend/test/`, `frontend/e2e/`, `scripts/validate.sh`, `scripts/container-smoke.sh`, `Makefile`",
    "DOC": "`README.md`, `.env.example`, `docs/architecture.md`, `docs/api.md`, `docs/database.md`, `docs/testing.md`, `docs/traceability.md`",
}

TESTS = {
    "PROD": "`backend/tests/integration/test_generation_lifecycle.py`; `frontend/test/render.test.mjs`; Playwright principal journeys",
    "ENG": "complete pytest/frontend suites; production build; container smoke",
    "DEP": "`backend/tests/integration/test_migrations.py`; `scripts/container-smoke.sh`",
    "CFG": "`backend/tests/integration/test_auth_accounts.py`; queue/outage integration tests",
    "AUTH": "`backend/tests/integration/test_auth_accounts.py`; `backend/tests/integration/test_user_deletion.py`",
    "PRIV": "`backend/tests/integration/test_authorization_and_uploads.py`; `backend/tests/unit/test_events.py`",
    "DATA": "migration, lifecycle, queue/recovery, gallery/pagination, and deletion integration tests",
    "COMFY": "`backend/tests/integration/test_workflows_and_prompt_assistant.py`; lifecycle and queue/recovery tests using live fake HTTP/WS",
    "WF": "`backend/tests/unit/test_contract.py`; `backend/tests/unit/test_compiler.py`; discovery integration tests",
    "OLL": "`backend/tests/integration/test_workflows_and_prompt_assistant.py`; `frontend/test/render.test.mjs`",
    "CTRL": "compiler unit tests; discovery API integration; `frontend/test/lib.test.mjs`; `frontend/test/render.test.mjs`",
    "QUEUE": "`backend/tests/integration/test_queue_and_recovery.py`; rapid-submission lifecycle test",
    "EXEC": "lifecycle, queue/recovery, event broker/SSE, and artifact persistence integration tests",
    "UI": "`frontend/test/render.test.mjs`; `frontend/test/lib.test.mjs`; Playwright principal journeys",
    "GAL": "gallery/recall/preferences and lifecycle integration tests; frontend render tests; Playwright",
    "RECALL": "lifecycle exact recall; unavailable source; Prompt Assistant provenance; frontend overwrite test; Playwright",
    "DEL": "generation lifecycle deletion and user cascade deletion integration tests",
    "FAIL": "Ollama outage, ComfyUI outage/recovery, failure/cancel, persistence failure integration tests",
    "SEC": "authorization/uploads, authentication/CSRF/throttle, contract/compiler strictness, and deletion tests",
    "A11Y": "semantic markup/component assertions and keyboard-native controls in frontend tests; Playwright interaction journeys",
    "PERF": "cursor pagination/preference test; targeted card updates and thumbnail assertions",
    "API": "all backend integration tests exercise the same product API; OpenAPI model construction during startup",
    "TEST": "the referenced test suites and validation scripts are themselves the deliverable",
    "DOC": "`scripts/generate_traceability.py --check`; README command review; container/startup test documentation",
}

CONTRACT_ROWS = [
    ("1", "Exactly one valid contract node", "`domain/contract.py` extraction and duplicate checks", "contract unit + discovery invalid fixtures"),
    ("2", "Pinned UI and API hashes", "normalized UI/API hashing in `domain/contract.py`; registry snapshots", "contract unit + hash-mismatch fixture"),
    ("3", "Stable semantic control IDs", "contract schema/duplicate validation", "contract/compiler unit tests"),
    ("4", "Unique structural bindings", "`NodeIndex.resolve` assertions", "invalid-binding fixture and unit tests"),
    ("5", "Tested branch strategies", "compiler precompiled variants and graph transforms", "compiler unit tests"),
    ("6", "Runtime node/assets verified", "registry runtime resolution against object info/assets", "missing-dependency fixture"),
    ("7", "Render from resolved contract", "public workflow API + `render.mjs`", "workflow API + frontend render tests"),
    ("8", "Client has no node knowledge", "server-only compiler; public contract redaction", "private graph-key integration assertion"),
    ("9", "Enumerate/classify outputs", "queue worker declaration matching and artifact rows", "progressive/multiple output lifecycle tests"),
    ("10", "In-flight checkpoint delivery", "WebSocket `executed` processing", "progressive pre-terminal assertion"),
    ("11", "Ordered progression", "resolved sequence/supersedes/lineage", "timeline and progressive lifecycle tests"),
    ("12", "Canonical only after success", "terminal `_finalize` promotion", "success/cancel/failure tests"),
    ("13", "Best available after cancellation", "eligibility strategy in `_finalize`", "cancel-after-checkpoint test"),
    ("14", "Cancellation race reconciliation", "interrupt plus WS/history monitor", "fake cancellation/restart scenarios"),
    ("15", "Requested/effective persistence", "generation immutable JSON snapshots", "exact recall/lifecycle tests"),
    ("16", "Unsupported controls fail", "unknown/type/binding validation", "compiler and validation-rejection tests"),
    ("17", "Workflow/schema drift fails closed", "identity/hash validation and exact recall", "invalid fixtures + unavailable recall test"),
    ("18", "Failure/output/cancellation cases tested", "fake service modes and lifecycle suite", "lifecycle + queue/recovery suites"),
    ("19", "Pinned fixed-seed regression capability", "resolved seed and graph snapshots", "fixed/random seed compiler/lifecycle tests; optional live pinned run documented"),
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
        "This matrix is generated from the supplied product requirements by `scripts/generate_traceability.py`. Each normative ID maps to concrete implementation and one or more automated-test areas. Run `python3 scripts/generate_traceability.py --check` to detect an omitted or stale row.",
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
            "## Workflow Contract rev. 1.1 acceptance mapping",
            "",
            "The contract design uses numbered acceptance criteria rather than product-style IDs. They are mapped separately so strict graph/output semantics remain visible.",
            "",
            "| Contract criterion | Meaning | Implementation | Evidence |",
            "|---|---|---|---|",
        ]
    )
    for number, meaning, implementation, evidence in CONTRACT_ROWS:
        lines.append(f"| **WC-{number.zfill(2)}** | {meaning} | {implementation} | {evidence} |")
    lines.extend(
        [
            "",
            "## Normative boundary",
            "",
            "The `FrontendWorkflowContract` and optional `FrontendWorkflowArtifact` ComfyUI custom nodes, prepared workflows, models, and custom-node dependencies are external prerequisites. This repository discovers and validates them; it does not implement, install, or mutate them.",
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
