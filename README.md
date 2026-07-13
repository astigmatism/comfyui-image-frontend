# ComfyUI Image Front-End

A private, contract-driven image-generation appliance for a trusted home network. It presents prepared ComfyUI workflow profiles as semantic controls, keeps one durable gallery card per accepted generation, archives progressive and final artifacts in application-owned storage, and supports private per-user favorites and exact request recall.

The implementation follows the supplied **Product and Implementation Requirements v1.0** and **ComfyUI Front-End Workflow Contract Design rev. 1.1**. The product requirements govern behavior, security, persistence, and UX; the workflow contract governs discovery, strict validation, graph compilation, stages, outputs, and cancellation semantics.

> **External prerequisites:** this repository does **not** implement or install the ComfyUI `FrontendWorkflowContract` or `FrontendWorkflowArtifact` custom nodes. A separately maintained custom-node package and prepared `.workflow.json` / `.api.json` workflow pairs must already exist in ComfyUI.

## What is included

- FastAPI application with server-managed sessions, Argon2id passwords, CSRF protection, login backoff, and narrow account administration.
- SQLite persistence with Alembic migrations, WAL mode, foreign keys, a durable application queue, and restart reconciliation.
- Application-owned image/upload storage with MIME decoding, byte/pixel limits, opaque paths, SHA-256 hashes, and WebP thumbnails.
- Network-only ComfyUI integration with capability-probed workflow user-data routes, strict contract validation, semantic compilation, progressive artifact retrieval, history reconciliation, and cancellation.
- Optional server-side Ollama Prompt Assistant with deterministic model selection and complete provenance.
- Dependency-free browser application built from ES modules and CSS: dark responsive shell, contract-defined controls, lazy cursor-paginated gallery, SSE updates, private Favorites modal, detail timeline, recall, cancellation, and deletion.
- Deterministic fake ComfyUI/Ollama services, unit/integration/security/migration tests, frontend tests, and Playwright journeys.
- One production image, Compose example, validation scripts, API/architecture/schema documentation, and requirement traceability.

## Architecture at a glance

The browser talks only to the application API. The backend owns authentication, authorization, workflow discovery, graph compilation, the queue, ComfyUI/Ollama calls, event normalization, persistence, and media delivery.

```text
Browser (semantic controls + gallery)
              |
       authenticated /api
              |
FastAPI application + queue worker
   |             |             |
SQLite       app-owned       adapters
records      files            |       |
                           ComfyUI   Ollama
```

Technology choices are intentionally proportionate to a single home-network appliance:

- **Backend:** Python 3.12/3.13, FastAPI, SQLAlchemy 2, Alembic, Pydantic, HTTPX, websockets, Pillow.
- **Frontend:** browser-native ES modules, semantic HTML, and CSS, built by a small deterministic Node script.
- **Persistence:** SQLite plus one application-owned data directory.
- **Live updates:** authenticated Server-Sent Events backed by durable generation events.
- **Tests:** pytest, deterministic HTTP/WebSocket fakes, Node's test runner, and optional Playwright browser tests.

More detail is in [`docs/architecture.md`](docs/architecture.md).

## Requirements

For local development:

- Python 3.12 or 3.13
- Node.js 22 or newer
- Docker only for image/startup smoke validation
- A live ComfyUI and optionally Ollama only for optional live use; automated tests use fakes

For production, Docker is the recommended path.

## Production setup with Docker Compose

1. Copy the configuration template and set real secrets:

   ```sh
   cp .env.example .env
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

   Put the generated value in `CIF_SESSION_SECRET`. Replace the temporary administrator password. Never commit `.env`.

2. Confirm external service addresses and the ComfyUI workflow namespace in `.env`.

3. Build and start:

   ```sh
   docker compose -f compose.example.yml up -d --build
   docker compose -f compose.example.yml logs -f comfyui-image-frontend
   ```

4. Open `http://<appliance-host>:8000`.

5. Sign in with `CIF_BOOTSTRAP_ADMIN_USERNAME` and `CIF_BOOTSTRAP_ADMIN_TEMPORARY_PASSWORD`. The bootstrap administrator is forced to choose a permanent password before entering the application.

The bootstrap variables are read only when the database has no users. Restarting or replacing the container does not reset an existing password.

### Updating a Compose deployment

After the project is checked out on the production server, update and restart it with:

```sh
./update_and_restart
```

The script requires a clean working tree and a configured upstream branch. It gracefully stops the application service, performs a fast-forward-only pull, rebuilds the image, starts the service, and waits for it to become healthy. If the pull or build fails after shutdown, it attempts to restart the last available image automatically.

It defaults to `compose.example.yml` and the `comfyui-image-frontend` service. Custom deployments can set `CIF_COMPOSE_FILE`, `CIF_COMPOSE_SERVICE`, `CIF_UPDATE_STOP_TIMEOUT`, or `CIF_UPDATE_START_TIMEOUT` before invoking it.

### Connecting to external services

The example uses `host.docker.internal` and a Linux `host-gateway` mapping for ComfyUI/Ollama running directly on the Docker host. When services share a user-defined Docker network, use their service DNS names instead, for example:

```env
CIF_COMFYUI_BASE_URL=http://comfyui:8188
CIF_COMFYUI_WS_URL=ws://comfyui:8188/ws
CIF_OLLAMA_BASE_URL=http://ollama:11434
```

ComfyUI and Ollama may be unreachable at application startup. Login, account management, and retained history still work. New dispatch is paused while ComfyUI is unavailable; only Prompt Assistant is disabled when Ollama is unavailable.

## Local development

Create an environment and install backend development dependencies:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Install the optional browser test dependency and build the frontend:

```sh
cd frontend
npm install
node scripts/build.mjs
cd ..
```

Create a local `.env` with at least:

```env
CIF_SESSION_SECRET=a-unique-random-value-with-at-least-32-characters
CIF_BOOTSTRAP_ADMIN_USERNAME=admin
CIF_BOOTSTRAP_ADMIN_TEMPORARY_PASSWORD=replace-this-temporary-password
CIF_DATA_DIR=./backend/data
CIF_COMFYUI_BASE_URL=http://127.0.0.1:8188
CIF_COMFYUI_WORKFLOW_DIRECTORY=workflows/front-end
CIF_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Run the source tree:

```sh
PYTHONPATH=backend python -m app
```

The application automatically upgrades the configured SQLite database before bootstrap initialization.

## Publishing compatible workflows

The application never reads a ComfyUI filesystem mount and never derives an executable graph from an arbitrary UI workflow. Through ComfyUI's user-data/workflow API it lists and retrieves exact pairs under `CIF_COMFYUI_WORKFLOW_DIRECTORY`:

```text
<profile-basename>.workflow.json
<profile-basename>.api.json
```

The UI-format document must contain exactly one `FrontendWorkflowContract` node. The manifest must declare exact normalized UI and API graph hashes, one valid `prompt.text` control, safe bindings, runtime requirements, stages, outputs, and one deterministic canonical-on-success output. Invalid or incomplete pairs are excluded from ordinary users and shown only as concise administrator diagnostics.

The application stores validated immutable source documents and clones the approved API graph for each request. It applies only contract-authorized bindings and graph transforms. Graph drift, ambiguous selectors, unsupported required capabilities, and missing dependencies fail closed.

## Accounts and privacy

- No self-registration, email, external recovery, SSO, or additional administrator creation.
- The bootstrap administrator can create, reset, and delete ordinary accounts.
- New and reset accounts use temporary passwords and must change them on next login.
- Resetting a password revokes existing sessions.
- The administrator can see account records and workflow diagnostics, but cannot inspect another user's prompts, controls, uploads, images, artifacts, or generation history.
- Every content lookup and media route is owner-scoped in the backend. Application assets are never mounted as a public static directory.

The Docker/host administrator remains outside this application-level privacy boundary because host filesystem access naturally bypasses application authorization.

## Queue, recovery, and cancellation

Every valid request resolves seeds, compiles the graph, and commits an immutable generation plus queue entry before it is returned to the browser. The worker preserves FIFO order within each user's backlog and dispatches round-robin across users. Concurrency defaults to one and is configured with `CIF_COMFYUI_CONCURRENCY`.

Browser closure or sign-out does not cancel work. Queued jobs survive restarts. Dispatched jobs are reconciled by stored ComfyUI `prompt_id`, queue state, events, and history. An unrecoverable outcome becomes explicit `interrupted` history without losing recall data.

Cancellation is asynchronous. A queued request becomes cancelled without dispatch. A running request enters `cancel_requested`; already emitted contract checkpoints are archived and the highest eligible one may become `best_available`, never canonical. Only successful terminal completion promotes declared final siblings to `final`.

## Recall and reproducibility

**Recall settings** immediately replaces the current source and all semantic controls, including concrete effective seeds, uploads, branches, and the exact final submitted prompt. It never queues automatically and never invokes Ollama.

Generate remains enabled only when the exact historical workflow ID, version, UI hash, API hash, contract hash, and compilation result are available. The application never silently substitutes a newer graph.

Re-submitting the same request ordinarily reproduces the same result only when the workflow, graph, models, custom nodes, ComfyUI/runtime versions, hardware behavior, source assets, effective controls, and seeds remain deterministic and unchanged. The retained original image is the authoritative record; this product does not promise universal pixel-for-pixel reproduction.

## Back up and restore

SQLite and all application-owned files must be backed up as one consistency set.

For the Compose example:

```sh
docker compose -f compose.example.yml stop comfyui-image-frontend
docker run --rm \
  -v comfyui-image-frontend-data:/data:ro \
  -v "$PWD:/backup" \
  alpine:3.20 \
  tar -C /data -czf /backup/comfyui-image-frontend-backup-$(date +%Y%m%d).tar.gz .
docker compose -f compose.example.yml start comfyui-image-frontend
```

Restore into an empty named volume while the container is stopped. The command
below intentionally removes the existing volume, so preserve the original
archive until the restored instance is verified:

```sh
docker compose -f compose.example.yml stop comfyui-image-frontend
docker volume rm comfyui-image-frontend-data
docker volume create comfyui-image-frontend-data
docker run --rm \
  -v comfyui-image-frontend-data:/data \
  -v "$PWD:/backup:ro" \
  alpine:3.20 \
  tar -C /data -xzf /backup/comfyui-image-frontend-backup-YYYYMMDD.tar.gz
docker compose -f compose.example.yml start comfyui-image-frontend
```

Do not restore only `app.db` or only media files; that can create dangling metadata or missing artifacts.

## Validation

After installing all development dependencies, Playwright Chromium, and Docker, run the complete gate:

```sh
make validate
```

That command checks Python and frontend formatting/linting, strict Python types, traceability, backend and frontend tests, production builds, Playwright journeys, and the container startup smoke test.

For constrained environments, run every available check and print explicit skips:

```sh
make validate-available
```

Individual commands and optional live integration guidance are in [`docs/testing.md`](docs/testing.md).

## Troubleshooting

### Startup says bootstrap configuration is missing

The database has no users and one or both bootstrap variables are absent. Set `CIF_BOOTSTRAP_ADMIN_USERNAME` and a temporary password of at least 12 characters, then restart. Once initialized, changing these variables does not alter the existing account.

### Startup rejects the session secret

Set `CIF_SESSION_SECRET` to at least 32 random characters. This key protects stored session-token hashes and login CSRF signatures; changing it invalidates existing browser sessions.

### No generation sources appear

Open **Administration → Workflow diagnostics** and refresh discovery. Typical causes are an incomplete pair, malformed JSON, duplicate/missing contract node, hash mismatch, missing `prompt.text`, unresolved binding, missing runtime node class/asset, or invalid output declaration. Source files are never modified by the application.

### History works but Generate is disabled

The application currently considers ComfyUI unavailable or no valid workflow is registered. Verify `CIF_COMFYUI_BASE_URL`, optional WebSocket URL, user-data routes, `/object_info`, and the configured workflow directory. Queued records already accepted before an outage remain durable.

### Prompt Assistant is unavailable

Verify `CIF_OLLAMA_BASE_URL` and ensure Ollama lists at least one model. The backend picks the lexicographically first model deterministically. Manual prompt entry and ComfyUI generation are unaffected.

### Browser receives 403 on a write

Refresh the page to obtain the current server-session CSRF token. Password reset or session revocation intentionally invalidates older sessions.

### SQLite is busy

The app enables WAL, foreign keys, and a 15-second busy timeout. Store `/data` on a local filesystem rather than an unreliable network filesystem, and run only one application instance against a database.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — component boundaries, request and recovery flows, security model
- [`docs/api.md`](docs/api.md) — application API and error/SSE formats
- [`docs/database.md`](docs/database.md) — schema ownership and migration notes
- [`docs/testing.md`](docs/testing.md) — test pyramid and exact validation commands
- [`docs/traceability.md`](docs/traceability.md) — every product requirement ID mapped to implementation and tests
- [`docs/normative-product-requirements-v1.0.md`](docs/normative-product-requirements-v1.0.md) — supplied normative product specification
- [`docs/normative-workflow-contract-v1.1.md`](docs/normative-workflow-contract-v1.1.md) — supplied normative contract design
