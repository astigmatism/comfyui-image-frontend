# Testing and validation

## Complete validation

Install backend extras, frontend packages, Playwright Chromium, and Docker, then run:

```sh
python3 -m pip install -e '.[dev]'
cd frontend && npm install && npx playwright install chromium && cd ..
make validate
```

`make validate` executes:

1. Ruff formatting/lint for backend source/tests.
2. Frontend whitespace/static safety checks.
3. Strict mypy for `backend/app`.
4. Generated requirement/publication traceability check.
5. Complete pytest suite.
6. Node frontend unit/component tests.
7. Python bytecode compile, production frontend build, and Python wheel build.
8. Playwright principal journeys against deterministic network fake services.
9. Production Docker build/start/health smoke.

A constrained environment can run all available checks while printing explicit skips:

```sh
make validate-available
```

## Publication fixtures

`backend/tests/publication_fixtures.py` builds exact-byte three-file bundles with two different public interfaces. Hashes are calculated after deliberate fixture mutation so tests can distinguish a valid changed publication from raw-byte corruption. The fixtures include:

- the seven-input Krea-compatible shape with five Basic fields, an Advanced finite LoRA choice plus companion strength, a large random seed range, workflow metadata attachment, and three connected publishers (`base`, `second_pass`, `final`);
- a different generic source with independently bound publisher declarations;
- exact manifest/workflow/API paths and recorded/observed hashes, warning-only artifact drift, node count, dependencies, bindings, public metadata, warnings, and runtime policy;
- mutation hooks for invalid paths/schemas/IDs/bindings/hashes/count/dependencies and republish behavior.

Tests must remain general: they may prove the Krea compatibility target but cannot make its publication ID, hashes, node IDs, dependency count, or control set the catalog implementation.

## Unit and domain coverage

The publication/registry/adapter/compiler/result tests cover:

- strict JSON, schemas, size limits, safe `workflows/` paths, adjacent stems/source agreement, warning-only workflow/API byte drift, observed API revision identity, and fail-closed API graph node count/structure;
- typed/lossless generation-source and technical-inventory recognition, legacy absence, open-ended values/entries/warnings/fields, six distinct node counts and diagnostic arithmetic, fixed/public-choice LoRAs, and non-executable artifact basenames;
- recursive preferred/fallback userdata listing, `Comfy-User`, whole-path single-segment encoding, and bounded listing/object-info/artifact/history/output responses;
- multi-instance JSON parsing, unique IDs, explicit/default selection, optional instance fields, global concurrency inheritance, and legacy single-instance synthesis;
- adapter lookup by execution ID, refusal to fall back for an unknown pin, and public instance-status projection without private URLs/users/capacity;
- empty and multiple-source catalogs, independent candidate failures, safe diagnostics, warning readiness, missing dependencies, last-valid cache, bad republish retention, and revision retirement;
- all six v1 input types, finite choice membership/labels/default-strength hints, public IDs, defaults/ranges/steps, required/optional rules, one positive prompt, and trusted CIF binding/class matching;
- unknown/private-field rejection, choice-specific companion-strength precedence, canonical large seed strings, random seed bounds, exact effective values, multi-binding patching, cached-graph immutability, and compilation isolation;
- exact list-shaped publisher history normalization, authoritative `artifacts[].batch_index`, multiple declared roles and batches, untouched node-keyed nonpublisher results, runtime independence from `native_outputs`, publisher mismatch errors, status/error/warning preservation, and public removal of only top-level native prompt/extra-data graph envelopes;
- file-reference allowlists, asset path safety, status transitions, and owner-specific event serialization.

Estimator unit tests cover privacy-safe feature normalization, bounded robust timing profiles,
outlier resistance, successful-outcome filtering, exact-cohort selection and hierarchical fallback,
node-local refinement without workflow-fraction extrapolation, confidence/interval validation, and
cache serialization/reload. They also cover compatibility-capped confidence, separate profile
quotas, versioned cursor backfill and deletion survival, per-generation landmark windows, idle-race
rollback, and joining an in-flight audit during shutdown. Frontend unit/render tests cover source-driven control ordering and
defaults, Advanced disclosure, all input types, finite single-select choices, stale-option
reconciliation, absence of invented controls, BigInt-safe seed behavior, revision-aware request
payloads, runtime-selector placement/loading/default/availability/error states,
pinned runtime labels in status/history/detail, field errors,
loading/ready/warning/offline/unavailable/empty source states, multiple artifacts,
unmapped output provenance, recall, favorites, accessible markup, nested ETA rendering, and a local
absolute-timestamp countdown that does not require server timer ticks or restart on stale rerenders.

## Integration coverage

Integration tests run the real FastAPI lifespan against temporary SQLite/data directories and deterministic fake ComfyUI/Ollama HTTP/WebSocket services. Relevant scenarios include:

- startup/administrator discovery through publication bundles and precise diagnostics, including both current sources surviving editable-only drift as `ready_with_warnings` across refresh;
- preferred and fallback userdata route compatibility plus nested retrieval;
- exact source descriptor privacy (no bindings, graphs, node IDs, paths, or dependencies);
- revision mismatch and invalid republish behavior;
- validate/accept with dynamic parameters, random/fixed maximum seed, workflow `extra_pnginfo`, and native prompt ID;
- durable acceptance, rapid submissions, per-user FIFO and round-robin fairness;
- default-adapter-only publication discovery with independent execution selection, per-instance health and lanes, and unavailable/unconfigured target rejection;
- generation pinning across input upload, prompt submission, history monitoring, result retrieval, cancellation, and independent target outages;
- pre-submission WebSocket readiness, structured node-local progress with legacy fallback,
  coalescing, prompt/client isolation, and delayed/missing-event history reconciliation;
- passive successful-run ETA learning, profile persistence/reload, matching-cohort reuse,
  terminal clearing, and idle-only legacy audit behavior;
- complete multiple-node/multiple-publisher/multiple-batch archive, ordinary publisher-image mirror de-duplication, untouched unmapped outputs, optional retrieval warnings, and partial/failure/interruption result retention;
- restart/outage recovery and cached source availability;
- automatic full catalog refresh on offline-to-online recovery, including empty-cache startup, without continuous online refetch;
- last-valid cached catalog dispatch through a healthy selected runtime while an unavailable runtime remains blocked;
- exact recall and unavailable/republished source behavior;
- migration up/down/up with old rows, execution-ID/label backfill, per-instance health, and instance-queue indexes;
- authentication, CSRF, IDOR/admin content denial, uploads, favorites/preferences, deletion, and Ollama provenance regressions.
- progressive browser bootstrap with optional-service delay/failure, named safe-method deadlines, and mutation single-send behavior;
- cached Prompt Assistant status with no request-time Ollama probe, stale-success rejection, response-only and thinking-only structured output, unchanged-refinement rejection, transient generate recovery, thinking-enabled `done_reason: length` budget escalation for Create and Refine, stable seed/temperature/schema semantics across escalation, distinctness-attempt separation, bounded privacy-safe exhaustion, precise terminal failure diagnostics, authoritative final ComfyUI prompt replacement, and the server-side minor-safety boundary;
- constant-query gallery/favorites pages, forbidden detail-JSON SQL assertions, summary parity, artifact precedence, and owner isolation;
- event-loop responsiveness while artifact/upload filesystem or metadata operations are deliberately blocked;
- more live SSE subscriptions than the former pool capacity with zero retained pool checkouts;
- slow ComfyUI startup discovery and restart reconciliation while local session, health, and retained-history requests remain responsive;
- safe request-duration logs and timing headers without query/body/private content.

The fake ComfyUI service implements both userdata listings/retrieval, object info, prompt
accept/reject, queue/interrupt, realistic non-replayed WebSocket `progress_state`/legacy/terminal
events, history timing/status, `/view`, retrieval failure, and outage modes. Production code never
silently uses the fake service.

## Browser journeys

`frontend/e2e/principal-journeys.spec.mjs` starts `backend/tests/e2e_server.py` and exercises the built frontend against live deterministic fake network services. The suite covers bootstrap/account flow, manifest-driven source selection, Basic/Advanced fields, warning-enabled generation, progressive/complete card/detail behavior, favorites, Prompt Assistant, cursor-aware voice transcription in standard and focused editors, exact recall, scale persistence, cancellation/deletion, retained failures, backend field-error disclosure, submission-time source locking, and stale cross-source composition rejection. Auto-generate journeys also verify recoverable composition retry without parallel requests, pending-timer cancellation, stale-fingerprint invalidation, one generation after recovery, visible terminal pause, and explicit restart with reset backoff. Runtime-selector placement, unavailable-state blocking, and execution labels are covered by the frontend render suite; cross-runtime network routing is covered by the backend integration fake services.

Run browser tests alone:

```sh
cd frontend
npx playwright test
```

## Focused commands

```sh
PYTHONPATH=backend pytest -q backend/tests/unit/test_comfyui_instances.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_comfyui_adapter.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_workflow_registry.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_compiler.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_results.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_workflows_and_prompt_assistant.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_generation_lifecycle.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_comfyui_instance_routing.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_queue_and_recovery.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_gallery_query_performance.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_storage_and_sse_responsiveness.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_shutdown_observability.py
cd frontend && node --test test/*.test.mjs
cd frontend && node scripts/build.mjs
python3 scripts/generate_traceability.py --check
./scripts/container-smoke.sh
```

## Optional live ComfyUI verification

Automated tests never require household services. The container smoke test inspects settings inside the built image and fails unless an overridden stable primary identity is extended by `worker-2`; it also verifies that a full operator list remains authoritative and that an explicit empty additional list opts out cleanly. For this multi-runtime deployment, live verification is intentionally limited to `GET /system_stats` and `GET /object_info` from inside the frontend container. These calls verify HTTP reachability, device identity, and compatible node counts without uploading inputs, submitting a prompt, inspecting or changing either queue, interrupting work, reading history, or retrieving outputs:

```sh
docker compose -f compose.example.yml exec -T comfyui-image-frontend python - <<'PY'
import json
import socket
import urllib.request

targets = {
    "primary": ("local-ai-comfyui", "http://local-ai-comfyui:8188"),
    "worker-2": ("local-ai-comfyui-worker-2", "http://local-ai-comfyui-worker-2:8188"),
}
for label, (host, base_url) in targets.items():
    addresses = sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(host, 8188, type=socket.SOCK_STREAM)
        }
    )
    with urllib.request.urlopen(f"{base_url}/system_stats", timeout=5) as response:
        system_stats = json.load(response)
        assert response.status == 200
    with urllib.request.urlopen(f"{base_url}/object_info", timeout=20) as response:
        object_info = json.load(response)
        assert response.status == 200 and isinstance(object_info, dict)
    devices = [item.get("name", "unknown") for item in system_stats.get("devices", [])]
    print(label, "dns=", ",".join(addresses), "nodes=", len(object_info), "devices=", devices)
PY
```

Expect HTTP 200, 2,659 node types from each target, and device names identifying the RTX 3090 and RTX 3080. Report the exact command and result. A real generation requires separate explicit approval; do not use `/prompt` or any other generation, mutation, queue, history, or output endpoint as part of this verification.

## Optional live Ollama verification

The opt-in live suite exercises create, refine, and repeated-create behavior through the production `OllamaAdapter`. Successful cases require a schema-constrained final object in either `response` or `thinking`; create cases verify the requested concept without requiring the model to copy the Creative Direction verbatim. The same adapter uses the production `2048 → 4096 → 8192` output-budget policy, so a schema-incomplete length response may make bounded follow-up calls. It is excluded from ordinary deterministic validation. Run it only against the configured Ollama-compatible router:

```sh
CIF_RUN_LIVE_OLLAMA_TESTS=1 \
CIF_OLLAMA_BASE_URL=http://router-host:11434 \
PYTHONPATH=backend pytest -q backend/tests/live/test_ollama_integration.py
```

The repeated-create case deliberately submits the first generated prompt as the current prompt for the same Creative Direction and requires the adapter's duplicate-aware retry to return a distinct second prompt.
