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
8. Playwright loopback principal journeys against deterministic network fake services.
9. Playwright TLS-edge browser journeys over `https://` — the real Compose stack when a Docker daemon is reachable, otherwise a local Caddy edge in front of the in-process app (`make e2e-tls`).
10. Production Docker build/start/health smoke.

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
Collection unit/render coverage includes ancestry, depth, subtree and display-tree helpers; escaped
fixed-slot tiles and four-image previews; breadcrumbs and the persisted switch; required-name,
recursive-delete, and move-dialog markup; and collection-specific empty states.

## Integration coverage

Integration tests run the real FastAPI lifespan against temporary SQLite/data directories and deterministic fake ComfyUI/Ollama HTTP/WebSocket services. Relevant scenarios include:

- startup/administrator discovery through publication bundles and precise diagnostics, including both current sources surviving editable-only drift as `ready_with_warnings` across refresh;
- preferred and fallback userdata route compatibility plus nested retrieval;
- exact source descriptor privacy (no bindings, graphs, node IDs, paths, or dependencies);
- revision mismatch and invalid republish behavior;
- validate/accept with dynamic parameters, random/fixed maximum seed, workflow `extra_pnginfo`, and native prompt ID;
- durable acceptance, rapid submissions, per-user FIFO and round-robin fairness;
- per-instance publication discovery and diagnostics, deduplicated catalogs, exact-copy text routing with independent image selection, per-instance health and lanes, and unavailable/unconfigured target rejection;
- generation pinning across input upload, prompt submission, history monitoring, result retrieval, cancellation, and independent target outages;
- pre-submission WebSocket readiness, structured node-local progress with legacy fallback,
  coalescing, prompt/client isolation, and delayed/missing-event history reconciliation;
- passive successful-run ETA learning, profile persistence/reload, matching-cohort reuse,
  terminal clearing, and idle-only legacy audit behavior;
- complete multiple-node/multiple-publisher history with compact final-batch retention, ordinary publisher-image mirror de-duplication, untouched unmapped outputs, optional retrieval warnings, one-best partial/failure/interruption retention, and terminal ComfyUI source cleanup/retry;
- restart/outage recovery and cached source availability;
- automatic full catalog refresh on offline-to-online recovery, including empty-cache startup, without continuous online refetch;
- last-valid cached catalogs queue only on their assigned service during outages and resume on recovery;
- exact recall and unavailable/republished source behavior;
- per-generation Prompt Assistant snapshots: run-derived values for linked runs, submitted drafts for manual rows, blank-instruction normalization to `null`, recall preference with the linked-run fallback (gaining the run's thinking mode) for legacy `null` rows, batch-item snapshot coverage without linked runs, and schema mode/instruction-boundary validation;
- migration up/down/up with old rows, execution-ID/label backfill, per-instance health, and instance-queue indexes;
- authentication, CSRF, IDOR/admin content denial, uploads, favorites/preferences, deletion, and Ollama provenance regressions.
- collection CRUD/name/depth/cycle/subtree rules, owner isolation, direct counts/previews, scoped
  cursor pagination, generation filing/moves, preview preference migration/defaults, terminal and
  active recursive deletion, content-free audits, file cleanup, and bounded scoped/list queries;
- progressive browser bootstrap with optional-service delay/failure, named safe-method deadlines, and mutation single-send behavior;
- cached Prompt Assistant status with no request-time Ollama probe, stale-success rejection, response-only and thinking-only structured output, unchanged-refinement redraw and bounded exhaustion, transient generate recovery, thinking-enabled `done_reason: length` budget escalation for Create and Refine, the single no-thinking fallback after thinking overflow and its distinctness rejection advancing to the next candidate, stable seed/temperature/schema semantics across escalation, distinctness-attempt separation, bounded privacy-safe exhaustion after every candidate is exhausted, precise terminal failure diagnostics, authoritative final ComfyUI prompt replacement, and the server-side minor-safety boundary;
- constant-query gallery pages, forbidden detail-JSON SQL assertions, summary parity, artifact precedence, and owner isolation;
- event-loop responsiveness while artifact/upload filesystem or metadata operations are deliberately blocked;
- more live SSE subscriptions than the former pool capacity with zero retained pool checkouts;
- slow ComfyUI startup discovery and restart reconciliation while local session, health, and retained-history requests remain responsive;
- safe request-duration logs and timing headers without query/body/private content.

The fake ComfyUI service implements both userdata listings/retrieval, object info, prompt
accept/reject, queue/interrupt, realistic non-replayed WebSocket `progress_state`/legacy/terminal
events, history timing/status, `/view`, bounded output/temp deletion, retrieval/cleanup failure,
and outage modes. Production code never
silently uses the fake service.

## Browser journeys

`frontend/e2e/gallery-hover.spec.mjs` exercises production card markup, styles, and the shared
hover controller with a controlled clock. It covers dwell/movement/exit timing, scrim visibility,
stable geometry, redraw and focus continuity, keyboard-to-pointer transitions, scroll cancellation,
small-card action placement, active progress/cancel visibility, touch targets, and reduced motion.

`frontend/e2e/gallery-selection.spec.mjs` covers starting selection from the hover toolkit,
range selection, incoming cards, clearing selection, shared Move / Copy controls, rejected
operations, and toolbar/dialog bounds at 320–1024px. The principal journeys also exercise a real
mixed folder/image copy, bulk move, and bulk delete, verifying that deleting copies preserves
the originals and their image files. Backend gallery-selection integration tests cover ownership,
overlapping subtrees, independent artifacts and recall, copy rollback, and folder depth limits.

`frontend/e2e/principal-journeys.spec.mjs` starts `backend/tests/e2e_server.py` and exercises the built frontend against live deterministic fake network services. The suite covers bootstrap/account flow, manifest-driven source selection, Basic/Advanced fields, warning-enabled generation, progressive/complete card/detail behavior, favorites, Prompt Assistant, cursor-aware voice transcription in standard and focused editors, exact recall, scale persistence, cancellation/deletion, retained failures, backend field-error disclosure, submission-time source locking, and stale cross-source composition rejection. It also covers collection creation/rename/navigation, in-collection generation, preview preference persistence, moving a completed card, recursive collection deletion, and recall restoring the Creative Direction panel (direction, mode, instructions, thinking mode) from the generation snapshot while preserving the current thinking mode when a snapshot carries none. Auto-generate journeys verify recoverable composition retry without parallel requests, pending-timer cancellation, stale-fingerprint invalidation, one generation after recovery, visible terminal pause, and explicit restart with reset backoff. Runtime-selector placement, unavailable-state blocking, and execution labels are covered by the frontend render suite; cross-runtime network routing is covered by the backend integration fake services.

`frontend/e2e/auto-generation-prefetch.spec.mjs` independently controls composition responses, queue acceptance, image completion events, and activity reads. It verifies one-prompt lookahead for single images and quantity × checkpoint batches, both completion orders, edits and restored values, off/on versions, source and session changes, retries, partial acceptance, and single-use composition provenance.

`frontend/e2e/tls-edge.spec.mjs` is a second, standalone Playwright project that runs the principal journeys against a **real TLS origin**. Its global setup (`scripts/e2e-tls-stack.sh up`) starts the real Compose stack (`cif-tls-edge` + app) when a Docker daemon is reachable, otherwise a local Caddy edge in front of the in-process app, then waits for the edge to answer a 200 over `https://` with a valid, hostname-matching leaf. The suite uses an untrusted-CA-tolerant browser context (equivalent to an operator's one-time trust-store import) and verifies: the edge terminates TLS and proxies to the app over plaintext (the app receives no client-address / `X-Forwarded-For` header from the browser); the session cookie is `Secure` and is actually sent by a real browser over `https://`; and the clipboard-paste and microphone APIs are present in the secure context (the `mediaDevices` guard mirrors the app's own availability check, so the suite stays honest on browser builds without a media pipeline).

Run the loopback browser journeys alone:

```sh
cd frontend
npx playwright test
```

Run the TLS-edge journeys alone (the runner auto-selects the Compose stack or a local Caddy, and cleans the stack on exit):

```sh
cd frontend
npx playwright test -c playwright.tls.config.mjs
```

## Focused commands

```sh
PYTHONPATH=backend pytest -q backend/tests/unit/test_comfyui_instances.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_comfyui_adapter.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_workflow_registry.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_compiler.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_results.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_collections.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_workflows_and_prompt_assistant.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_generation_lifecycle.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_comfyui_instance_routing.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_queue_and_recovery.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_gallery_query_performance.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_collections.py
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

The opt-in live suite exercises create and refine with thinking both enabled and disabled, plus repeated-create behavior through the production `OllamaAdapter`. Successful cases require a schema-constrained final object in chat `message.content` or `message.thinking` (normalized into response/thinking diagnostics); create cases verify the requested concept without requiring the model to copy the Creative Direction verbatim. The same adapter uses the production `2048 → 4096 → 8192` output-budget policy, so a schema-incomplete length response may make bounded follow-up calls. It is excluded from ordinary deterministic validation. Run it only against the configured Ollama-compatible router:

```sh
CIF_RUN_LIVE_OLLAMA_TESTS=1 \
CIF_OLLAMA_BASE_URL=http://router-host:11434 \
CIF_OLLAMA_MODEL=nighttime \
CIF_OLLAMA_API_KEY=local-only \
PYTHONPATH=backend pytest -q backend/tests/live/test_ollama_integration.py
```

The repeated-create case carries each generated prompt forward as the current prompt and requires four distinct results for the same Creative Direction. Two additional live cases exercise the authenticated application composition API, persistence, and generation preparation with a fake ComfyUI service and temporary storage.

### Production release tooling

`make test-deployment` (also included in `make test` and validation) runs the
standard-library tests in `scripts/tests`. They use temporary deployment fixtures,
a local Git repository, and real SQLite archives to cover installation, restored
image tags, external TLS paths, source ancestry, app-only Compose operations,
preparation failures, stopped-backup ordering, database rollback, lock contention,
and the portal's verified-result/exit-code contract. No production host is contacted.

With a local Docker daemon, opt in to the runner/entrypoint and isolated image
startup regressions:

```sh
CIF_RUN_DOCKER_TESTS=1 python3 -m unittest discover -s scripts/tests -q
```

The Docker fixtures use unique image/container names and disposable tmpfs data.
They verify both the installed entrypoint under the portal's mount contract and
application startup as non-root numeric users. Live Samus installation, button
visibility, trusted HTTPS, and the 16-LoRA generation acceptance are separate
operator checks in [the installation guide](production-service-portal.md).

Fixed-stage routing coverage verifies that both runtime selectors are absent, stale
preferences and recalled records cannot select a runtime, and conflicting API targets
are rejected. Catalog tests use independent GPU/CPU copies with missing dependencies,
drift, retirement, outages and recovery. Automation tests normalize both old pins before
new cycles while preserving accepted work and receipt replay. Browser journeys verify
CPU prompt generation, GPU image generation, saved controls and stable synchronization.
