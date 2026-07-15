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
- exact manifest/workflow/API paths and recorded hashes, strict frozen API integrity, editable-drift warnings, node count, dependencies, bindings, public metadata, warnings, and runtime policy;
- mutation hooks for invalid paths/schemas/IDs/bindings/hashes/count/dependencies and republish behavior.

Tests must remain general: they may prove the Krea compatibility target but cannot make its publication ID, hashes, node IDs, dependency count, or control set the catalog implementation.

## Unit and domain coverage

The publication/registry/adapter/compiler/result tests cover:

- strict JSON, schemas, size limits, safe `workflows/` paths, adjacent stems/source agreement, warning-only editable byte drift, fail-closed frozen API raw-byte hashes, and API graph node count;
- recursive preferred/fallback userdata listing, `Comfy-User`, whole-path single-segment encoding, and bounded listing/object-info/artifact/history/output responses;
- empty and multiple-source catalogs, independent candidate failures, safe diagnostics, warning readiness, missing dependencies, last-valid cache, bad republish retention, and revision retirement;
- all six v1 input types, finite choice membership/labels/default-strength hints, public IDs, defaults/ranges/steps, required/optional rules, one positive prompt, and trusted CIF binding/class matching;
- unknown/private-field rejection, choice-specific companion-strength precedence, canonical large seed strings, random seed bounds, exact effective values, multi-binding patching, cached-graph immutability, and compilation isolation;
- exact list-shaped publisher history normalization, authoritative `artifacts[].batch_index`, multiple declared roles and batches, untouched node-keyed nonpublisher results, runtime independence from `native_outputs`, publisher mismatch errors, status/error/warning preservation, and public removal of only top-level native prompt/extra-data graph envelopes;
- file-reference allowlists, asset path safety, status transitions, and owner-specific event serialization.

Frontend unit/render tests cover source-driven control ordering and defaults, Advanced disclosure, all input types, finite single-select choices, stale-option reconciliation, absence of invented controls, BigInt-safe seed behavior, revision-aware request payloads, field errors, loading/ready/warning/offline/unavailable/empty states, multiple artifacts, unmapped output provenance, recall, favorites, and accessible markup.

## Integration coverage

Integration tests run the real FastAPI lifespan against temporary SQLite/data directories and deterministic fake ComfyUI/Ollama HTTP/WebSocket services. Relevant scenarios include:

- startup/administrator discovery through publication bundles and precise diagnostics, including both current sources surviving editable-only drift as `ready_with_warnings` across refresh;
- preferred and fallback userdata route compatibility plus nested retrieval;
- exact source descriptor privacy (no bindings, graphs, node IDs, paths, or dependencies);
- revision mismatch and invalid republish behavior;
- validate/accept with dynamic parameters, random/fixed maximum seed, workflow `extra_pnginfo`, and native prompt ID;
- durable acceptance, rapid submissions, per-user FIFO and round-robin fairness;
- WebSocket progress plus delayed/missing-event history reconciliation;
- complete multiple-node/multiple-publisher/multiple-batch archive, ordinary publisher-image mirror de-duplication, untouched unmapped outputs, optional retrieval warnings, and partial/failure/interruption result retention;
- restart/outage recovery and cached source availability;
- automatic full catalog refresh on offline-to-online recovery, including empty-cache startup, without continuous online refetch;
- exact recall and unavailable/republished source behavior;
- migration up/down/up with old rows/default result fields;
- authentication, CSRF, IDOR/admin content denial, uploads, favorites/preferences, deletion, and Ollama provenance regressions.
- progressive browser bootstrap with optional-service delay/failure, named safe-method deadlines, and mutation single-send behavior;
- cached Prompt Assistant status with no request-time Ollama probe, stale-success rejection, and authoritative composition failure;
- constant-query gallery/favorites pages, forbidden detail-JSON SQL assertions, summary parity, artifact precedence, and owner isolation;
- event-loop responsiveness while artifact/upload filesystem or metadata operations are deliberately blocked;
- more live SSE subscriptions than the former pool capacity with zero retained pool checkouts;
- slow ComfyUI startup discovery and restart reconciliation while local session, health, and retained-history requests remain responsive;
- safe request-duration logs and timing headers without query/body/private content.

The fake ComfyUI service implements both userdata listings/retrieval, object info, prompt accept/reject, queue/interrupt, WebSocket progress/terminal events, history timing/status, `/view`, retrieval failure, and outage modes. Production code never silently uses the fake service.

## Browser journeys

`frontend/e2e/principal-journeys.spec.mjs` starts `backend/tests/e2e_server.py` and exercises the built frontend against live deterministic fake network services. The suite covers bootstrap/account flow, manifest-driven source selection, Basic/Advanced fields, warning-enabled generation, progressive/complete card/detail behavior, favorites, Prompt Assistant, cursor-aware voice transcription in standard and focused editors, exact recall, scale persistence, cancellation/deletion, retained failures, backend field-error disclosure, submission-time source locking, and stale cross-source composition rejection.

Run browser tests alone:

```sh
cd frontend
npx playwright test
```

## Focused commands

```sh
PYTHONPATH=backend pytest -q backend/tests/unit/test_comfyui_adapter.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_workflow_registry.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_compiler.py
PYTHONPATH=backend pytest -q backend/tests/unit/test_results.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_workflows_and_prompt_assistant.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_generation_lifecycle.py
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

Automated tests never require household services. For a live check, configure `.env`, start the app, and use only ComfyUI network APIs—not the server filesystem:

1. Refresh **Administration → Workflow diagnostics** and record the accepted publication ID/hashes and warnings. If editable bytes changed after publication, confirm the source remains accepted as `ready_with_warnings`; an API hash mismatch must still be rejected.
2. Confirm the source's public interface contains only manifest inputs and no bindings/node data.
3. Queue a low-cost/base-path request with expensive optional branches disabled.
4. Record the native `prompt_id`; wait for bounded history reconciliation.
5. Inspect effective parameters/concrete seed, graph-envelope-safe raw history, raw ComfyUI status/error details, ordered declared output list, untouched unmapped output map, and every retained batch artifact; confirm top-level submitted prompt/extra-data graphs are absent but custom result fields remain.
6. Replay the concrete seed only after the base path succeeds; compare within the limits of the pinned graph/models/runtime/hardware.
7. After Krea has been republished, confirm `lora` exposes only the published option values/labels and that `knpv4_1_strength` is absent.
8. With a concrete seed pinned, run the saved default choice and one nondefault choice; confirm effective public IDs, choice-specific/default or explicit `lora_strength`, and unchanged exhaustive output behavior.
9. Run the Krea publication with `enable_seedvr2_upscale=true`; confirm the effective Boolean, native branch behavior, complete history outputs, and retained artifacts.
10. Submit two deliberately different concurrent requests and confirm choices, strengths, graphs, prompt IDs, status, and files do not cross.

Do not claim live end-to-end completion unless the exact latest publication was discovered, queued through `/prompt`, reconciled through `/history/{prompt_id}`, and its `/view` assets were retained. Report the exact prompt ID and result. If the live server is unavailable, state that and report deterministic commands/results plus this remaining procedure.
