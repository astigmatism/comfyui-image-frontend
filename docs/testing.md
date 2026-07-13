# Testing and validation

## Complete validation

Install backend development extras, frontend packages, Playwright Chromium, and Docker, then run:

```sh
python3 -m pip install -e '.[dev]'
cd frontend && npm install && npx playwright install chromium && cd ..
make validate
```

`make validate` executes:

1. Ruff format check and lint over backend source/tests.
2. Frontend whitespace/static safety checks.
3. Strict mypy over `backend/app`.
4. Requirement-traceability regeneration check.
5. Complete pytest suite.
6. Node frontend unit/component tests.
7. Python bytecode compile, frontend production build, and Python wheel build.
8. Playwright critical journeys against deterministic network fake services.
9. Production Docker build/start/health smoke.

A constrained environment can run all available checks and list missing tools without hiding them:

```sh
make validate-available
```

## Test pyramid

### Unit/domain

- Contract extraction, normalized graph hashes, duplicate/missing nodes, selectors, bindings, dependencies, stages, outputs, dynamic options.
- Semantic compiler types/constraints/conditions/presets, seed resolution, transforms/variants/uploads, compiled immutability/hash.
- Status transitions, artifact selection/storage/path safety.
- Owner-specific event broker and SSE serialization.
- Frontend pure state/control validation, conditions/capabilities, recall overwrite, gallery scale/date formatting.
- Frontend markup invariants: Generate/source order, collapsed advanced/assistant, one-card update, exact footer.

### Database/repository and API integration

Tests start the real FastAPI lifespan against temporary SQLite/data directories and deterministic fake ComfyUI/Ollama HTTP/WebSocket services. They cover:

- bootstrap and forced password changes;
- account create/reset/session revocation/delete;
- case-insensitive usernames, CSRF, login backoff;
- cross-user IDOR and administrator content denial;
- image decode/MIME/traversal limits;
- startup discovery and invalid fixtures;
- explicit Ollama behavior and persisted provenance;
- durable acceptance, rapid submissions, FIFO and round-robin fairness;
- progressive artifacts, multiple finals, canonical timing, cancellation/failure best available;
- artifact persistence failure;
- exact recall and unavailable source behavior;
- cursor pagination and preference persistence;
- generation/user cascade deletion including files;
- queued/running restart recovery, interrupted state, and ComfyUI outage;
- migration up/down/up.

The fake service supports workflow listing/retrieval, object info, prompt accept/reject, queue, interrupt, WebSocket stages/progress/executed/terminal events, progressive and multiple output files, history, cancellation races, disconnects, retrieval failure, uploads, and Ollama structured responses/outage.

### Browser journeys

`frontend/e2e/principal-journeys.spec.mjs` starts `backend/tests/e2e_server.py`, which runs the production frontend against live deterministic fake HTTP/WebSocket services. The tests exercise bootstrap, user creation, forced changes, generation/progressive card behavior, exact footer, explicit Prompt Assistant, recall overwrite, scale persistence, and retained failed history.

Run only browser tests:

```sh
cd frontend
npx playwright test
```

## Useful focused commands

```sh
PYTHONPATH=backend pytest -q backend/tests/unit
PYTHONPATH=backend pytest -q backend/tests/integration/test_auth_accounts.py
PYTHONPATH=backend pytest -q backend/tests/integration/test_generation_lifecycle.py
cd frontend && node --test test/*.test.mjs
cd frontend && node scripts/build.mjs
./scripts/container-smoke.sh
```

## Optional live integration

The automated suite never requires household services. To manually exercise a pinned live environment, configure `.env`, run the app, refresh workflow discovery in Administration, and submit a fixed-seed request. Compare requested/effective controls, graph hashes, stage timeline, and retained outputs. Pixel regression is meaningful only when models, custom nodes, runtime, hardware, and graph are pinned.
