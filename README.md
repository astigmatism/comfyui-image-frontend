# ComfyUI Image Front-End

A private image-generation appliance for a trusted home network. It discovers deliberately published ComfyUI workflows, renders their manifest-defined controls, keeps one durable gallery card per accepted generation, archives all returned image batches in application-owned storage, and supports private per-user favorites and exact request recall.

> **External prerequisites:** this repository does not publish workflows, install ComfyUI custom nodes, models, or other workflow dependencies. A separately maintained publisher/custom-node package must create valid three-file publication bundles in ComfyUI userdata.

## What is included

- FastAPI with server-managed sessions, Argon2id passwords, CSRF protection, login backoff, and narrow account administration.
- SQLite persistence with Alembic migrations, WAL mode, foreign keys, a durable fair queue, and restart reconciliation.
- Owner-scoped image/upload storage with MIME decoding, byte/pixel limits, SHA-256 hashes, and WebP thumbnails.
- Network-only ComfyUI integration with recursive userdata discovery, strict publication validation, request-local graph compilation, prompt submission, WebSocket/history reconciliation, and safe output retrieval.
- Optional server-side Ollama Prompt Assistant with deterministic model selection and persisted provenance.
- A dependency-free browser application with manifest-driven controls, precise seed handling, lazy cursor-paginated gallery, SSE updates, favorites, detail/recall, cancellation, and deletion.
- Deterministic fake services, backend/frontend/browser tests, production image, Compose example, validation scripts, and maintained API/architecture/schema documentation.

## Architecture at a glance

The browser talks only to the application API. It never receives an executable graph, ComfyUI URL, userdata path, or private input binding. Native output node keys remain visible only where required to preserve `unmapped_outputs` faithfully.

```text
Browser (public source interface + gallery)
                    |
             authenticated /api
                    |
       FastAPI application + queue worker
          |             |              |
       SQLite       app-owned       adapters
       records      files            |     |
                                  ComfyUI Ollama
```

The main stack is Python 3.12/3.13, FastAPI, SQLAlchemy/Alembic, SQLite, browser-native ES modules/CSS, authenticated Server-Sent Events, pytest, deterministic HTTP/WebSocket fakes, Node's test runner, and Playwright. See [`docs/architecture.md`](docs/architecture.md) for the component and trust boundaries.

## Requirements

Local development needs Python 3.12 or 3.13 and Node.js 22 or newer. Docker is needed only for the production image/startup smoke test. Automated tests do not require a household ComfyUI or Ollama server.

For production, Docker Compose is the recommended path.

## Production setup with Docker Compose

1. Copy the configuration template and generate a session secret:

   ```sh
   cp .env.example .env
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

   Put the value in `CIF_SESSION_SECRET`, replace the bootstrap password, configure ComfyUI, and never commit `.env`.

2. Give the ComfyUI server a stable logical identity with `CIF_COMFYUI_INSTANCE_ID`. If it is a multi-user installation, also set `CIF_COMFYUI_USER`.

3. Build and start:

   ```sh
   docker compose -f compose.example.yml up -d --build
   docker compose -f compose.example.yml logs -f comfyui-image-frontend
   ```

4. Open `http://<appliance-host>:8000` and sign in with the bootstrap administrator. The first sign-in requires a permanent password.

Bootstrap variables are read only when the database has no users. Replacing the container does not reset an existing password.

### Updating a Compose deployment

From a clean checkout with an upstream branch:

```sh
./update_and_restart
```

The script gracefully stops the service, performs a fast-forward-only pull, rebuilds, restarts, and waits for health. It tries to restart the prior image if pull/build fails after shutdown. Override the defaults with `CIF_COMPOSE_FILE`, `CIF_COMPOSE_SERVICE`, `CIF_UPDATE_STOP_TIMEOUT`, or `CIF_UPDATE_START_TIMEOUT`.

### Connecting to external services

The example uses `host.docker.internal` plus a Linux `host-gateway` mapping. For services on the same Docker network, use service DNS names:

```env
CIF_COMFYUI_BASE_URL=http://comfyui:8188
CIF_COMFYUI_WS_URL=ws://comfyui:8188/ws
CIF_COMFYUI_INSTANCE_ID=home
CIF_COMFYUI_WORKFLOW_DIRECTORY=workflows
CIF_OLLAMA_BASE_URL=http://ollama:11434
```

ComfyUI and Ollama may be unreachable during startup. Accounts and retained history remain available. A last-valid source catalog remains visible as cached/offline, but new dispatch waits for ComfyUI. Only Prompt Assistant depends on Ollama.

Health monitoring reruns complete source discovery once ComfyUI transitions from offline to online, including recovery from an empty startup catalog. While the server stays online, bundles are refreshed only at startup or by the administrator action; there is no periodic publication refetch.

## Local development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
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
CIF_COMFYUI_INSTANCE_ID=local
CIF_COMFYUI_WORKFLOW_DIRECTORY=workflows
CIF_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Run the source tree:

```sh
PYTHONPATH=backend python -m app
```

Startup upgrades the configured SQLite database before bootstrap initialization.

## Publishing compatible workflows

A normal ComfyUI save is not discoverable. The workflow author must choose **File → Save & Publish for Image Frontend**, producing one adjacent bundle:

```text
<name>.json
<name>.api.json
<name>.interface.json
```

The interface manifest is the publication commit marker and authoritative public surface. The app recursively finds only `.interface.json` candidates through the ComfyUI userdata API, fetches all three artifacts, strictly verifies the frozen API graph's raw-byte hash, checks safe paths/stems, schemas, node count, typed inputs, bindings, dependency coverage, and `/object_info`, then atomically accepts the revision. The editable workflow is still parsed and bounded, but a save-only byte change is reported as a nonfatal warning because it is runtime metadata rather than the executable graph. The app never reads a ComfyUI filesystem mount, infers controls from graph topology, or mutates publication files.

Current schemas are `comfyui-image-frontend.publication/v1` and `comfyui-image-frontend.interface/v1`. Supported public inputs are string, integer, number, boolean, seed, and finite choice; exactly one is the positive prompt. Choice controls expose only stable public values and labels—the trusted CIF declaration node keeps private model/file mappings inside the frozen graph. Every compatible source declares one or more connected `CIFPublishImage` outputs with `cardinality: many`, including exactly one authored `final`. Previews, comparisons, and auxiliary publishers coexist with every nonpublisher native history result under `unmapped_outputs`; `interface.native_outputs` is diagnostic inventory, never a runtime allowlist.

See [`docs/published-workflows.md`](docs/published-workflows.md) for the manifest contract, discovery/refresh states, configuration and size limits, revision identity, compilation rules, result semantics, diagnostics, security boundary, and migration policy.

## Accounts and privacy

- No self-registration, email recovery, SSO, or additional administrator creation.
- The bootstrap administrator can create, reset, and delete ordinary accounts.
- New/reset accounts use temporary passwords and must change them on next login.
- Password reset revokes existing sessions.
- Administrators can see account records and workflow diagnostics, but not another user's prompts, parameters, uploads, images, artifacts, or history.
- Every content lookup and media route is owner-scoped. Assets are never mounted as a public static directory.
- Downloaded ComfyUI artifacts may retain native prompt/workflow metadata and should be treated as sensitive when shared. The application does not expose that metadata in its source, generation-detail, or gallery UI projections.

The Docker/host administrator is outside this application privacy boundary because filesystem access bypasses application authorization.

## Queue, results, recovery, and cancellation

Every valid request resolves defaults, finite choices, companion-strength hints, and seeds; clones and compiles its accepted frozen graph; and commits an immutable generation plus queue entry before the browser receives it. Public choice IDs are patched only into their trusted declaration-node `value`; private mappings and downstream loader inputs are never caller-controlled. The worker preserves FIFO order per user and dispatches round-robin across users. `CIF_COMFYUI_CONCURRENCY` defaults to one.

When requested by the publication, the accepted editable snapshot is attached as `extra_data.extra_pnginfo.workflow`; its separately recorded observed hash may differ from the publication-time hash without changing the frozen executable revision. The native ComfyUI `prompt_id` is persisted. WebSocket events provide progress, while bounded `/history/{prompt_id}` reconciliation supplies terminal truth and recovery after cached or missed events.

The server retains complete bounded ComfyUI history. Generation detail removes only top-level submitted `prompt` and `extra_data` graph envelopes; actual outputs, arbitrary JSON-safe custom UI fields, publisher metadata, status/messages/errors, and execution metadata remain intact. It also returns requested/effective parameters, exact seed strings, immutable source revision, ordered publisher outputs with authoritative batch indices, untouched node-keyed unmapped outputs, and every archived image batch member. The gallery uses the authored final as its primary image, while detail groups previews, comparisons, auxiliary publishers, and additional native outputs without dropping any batch sibling.

Browser closure or sign-out does not cancel work. Queued jobs survive restarts. Running jobs reconcile from stored prompt ID, queue state, events, and history. Cancellation is asynchronous; already returned partial files remain available when safely archived.

## Recall and reproducibility

**Recall settings** replaces the selected source and public parameters with the historical effective values, including concrete seeds and final submitted prompt. It never queues automatically or invokes Ollama.

Recall is enabled only when the exact publication ID plus workflow/API/manifest hashes remain registered and compile to the original graph. A newer publication is never silently substituted. Historical generations remain viewable even when exact recall is unavailable.

The original retained image is authoritative. Repeating a request can reproduce a result only when the graph, models, custom nodes, runtime, hardware, source assets, effective parameters, and seeds remain deterministic and unchanged.

## Back up and restore

Back up SQLite and application-owned files as one consistency set.

```sh
docker compose -f compose.example.yml stop comfyui-image-frontend
docker run --rm \
  -v comfyui-image-frontend-data:/data:ro \
  -v "$PWD:/backup" \
  alpine:3.20 \
  tar -C /data -czf /backup/comfyui-image-frontend-backup-$(date +%Y%m%d).tar.gz .
docker compose -f compose.example.yml start comfyui-image-frontend
```

Restore into an empty named volume while the service is stopped. This removes the current volume, so keep the original archive until verification:

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

Do not restore only `app.db` or only media files; that can create dangling metadata or missing artifacts. Publication bundles live in ComfyUI and need their own external backup policy.

## Validation

After installing development dependencies, Playwright Chromium, and Docker:

```sh
make validate
```

This checks formatting/linting, types, generated traceability, backend/frontend tests, production builds, browser journeys, and the container smoke test. In constrained environments:

```sh
make validate-available
```

Focused commands and live integration guidance are in [`docs/testing.md`](docs/testing.md).

## Troubleshooting

### No generation sources appear

Open **Administration → Workflow diagnostics** and refresh. Confirm that publication used Save & Publish, all three adjacent files exist under `workflows/`, `CIF_COMFYUI_INSTANCE_ID` is stable, the optional `CIF_COMFYUI_USER` is correct, the frozen API hash matches its exact bytes, and every declared class exists in `/object_info`. A changed editable-workflow hash is a warning and does not remove an otherwise valid source. Orphaned `.json` / `.api.json` files are intentionally ignored.

### A source is ready with warnings

Warnings are nonfatal publication or runtime diagnostics. They include an editable workflow whose current bytes differ from the hash recorded at publication; the manifest value remains revision metadata and the frozen API graph remains authoritative for execution. An absent/disconnected publisher, frozen API hash mismatch, missing native-output inventory, invalid cardinality, or zero/multiple final declarations still rejects the source. Inspect accepted warnings before use; discovery never repairs a publication or promotes an arbitrary native image to authored final.

### Sources are cached/offline or Generate is disabled

Retained sources remain visible when discovery cannot contact/list ComfyUI, but submission is disabled. Verify `CIF_COMFYUI_BASE_URL`, optional WebSocket URL, `Comfy-User`, userdata endpoints, encoded nested-path routing, `/object_info`, and byte limits. A proxy that decodes `%2F` before routing can break nested artifact retrieval.

### A selected source was republished

The API returns `source_republished` when the selected revision no longer matches. Reload the current source descriptor, review its controls/warnings, and submit explicitly. In-flight and historical jobs keep their original revision.

### Startup configuration errors

With no users, set both bootstrap variables and use a temporary password of at least 8 characters. `CIF_SESSION_SECRET` must contain at least 32 random characters. A ComfyUI instance ID must contain 1–64 letters, digits, hyphens, or underscores. Response-size limits must be at least 1024 bytes.

### Prompt Assistant is unavailable

Verify `CIF_OLLAMA_BASE_URL` and that Ollama lists a model. Manual prompt entry and ComfyUI generation are unaffected.

### Browser receives 403 on a write

Refresh to obtain the current session CSRF token. Password reset or session revocation intentionally invalidates prior sessions.

### SQLite is busy

Store `/data` on a reliable local filesystem and run only one application instance against the database. The app enables WAL, foreign keys, and a busy timeout.

## Documentation

- [`docs/published-workflows.md`](docs/published-workflows.md) — publication, discovery, validation, compilation, result, security, and migration contract
- [`docs/architecture.md`](docs/architecture.md) — components, trust boundaries, request/result and recovery flows
- [`docs/api.md`](docs/api.md) — current source and generation API plus common application routes
- [`docs/database.md`](docs/database.md) — schema ownership, publication migration, and storage
- [`docs/migration-published-workflows.md`](docs/migration-published-workflows.md) — old discovery/client/data compatibility and operator steps
- [`docs/testing.md`](docs/testing.md) — validation commands and publication coverage
- [`docs/traceability.md`](docs/traceability.md) — generated product/publication acceptance mapping
- [`docs/normative-product-requirements-v1.0.md`](docs/normative-product-requirements-v1.0.md) — historical original product specification
- [`docs/normative-workflow-contract-v1.1.md`](docs/normative-workflow-contract-v1.1.md) — historical retired embedded-contract design
