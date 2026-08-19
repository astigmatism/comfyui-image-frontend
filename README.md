# ComfyUI Image Front-End

A private image-generation appliance for a trusted home network. It discovers deliberately published ComfyUI workflows, renders their manifest-defined controls, keeps one durable gallery card per accepted generation, archives all returned image batches in application-owned storage, and supports private per-user favorites and exact request recall.

> **External prerequisites:** this repository does not publish workflows, install ComfyUI custom nodes, models, or other workflow dependencies. A separately maintained publisher/custom-node package must create valid three-file publication bundles in ComfyUI userdata.

## What is included

- FastAPI with server-managed sessions, Argon2id passwords, CSRF protection, login backoff, and narrow account administration.
- SQLite persistence with Alembic migrations, WAL mode, foreign keys, a durable fair queue, and restart reconciliation.
- Owner-scoped image/upload storage with MIME decoding, byte/pixel limits, SHA-256 hashes, and WebP thumbnails.
- Network-only ComfyUI integration with recursive userdata discovery, strict publication validation, request-local graph compilation, prompt submission, WebSocket/history reconciliation, and safe output retrieval.
- Optional server-side Prompt Assistant through an Ollama-compatible router with persisted effective-model provenance.
- Optional browser voice input through a server-side OpenAI-compatible speech-to-text proxy; service credentials never reach the browser.
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
       records      files            |    |    |
                                  ComfyUI Ollama STT
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

2. The production image bundles the runtime defaults tracked in `deployment/comfyui-instances.env`, automatically adding the household RTX 3080 worker to the existing RTX 3090 primary regardless of whether the container is launched by the example Compose file, another Compose file, or `docker run`. For another topology, override `CIF_COMFYUI_INSTANCES` in the private environment; optionally set `CIF_COMFYUI_DEFAULT_INSTANCE_ID` when the first entry should not be the default. Reuse the primary server's existing stable `CIF_COMFYUI_INSTANCE_ID` value as that entry's `id`; changing it changes every published source key.

3. Build and start:

   ```sh
   docker compose -f compose.example.yml up -d --build
   docker compose -f compose.example.yml logs -f comfyui-image-frontend
   ```

4. Open `http://<appliance-host>:8000` and sign in with the bootstrap administrator. The first sign-in requires a permanent password. Put the app behind HTTPS before using browser microphone input from a LAN hostname or IP.

Bootstrap variables are read only when the database has no users. Replacing the container does not reset an existing password.

### Updating a Compose deployment

From a clean checkout with an upstream branch:

```sh
./update_and_restart
```

The script gracefully stops the service, performs a fast-forward-only pull, rebuilds, restarts, and waits for health. It tries to restart the prior image if pull/build fails after shutdown. By default, Compose's `stop_grace_period` controls the stop deadline (30 seconds in the example); set `CIF_UPDATE_STOP_TIMEOUT` only for an exceptional explicit override. Override other defaults with `CIF_COMPOSE_FILE`, `CIF_COMPOSE_SERVICE`, or `CIF_UPDATE_START_TIMEOUT`. Uvicorn cancels lingering request tasks after `CIF_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` (10 seconds by default), leaving the rest of the container grace period for lifespan cleanup.

Application code, built browser assets, and the non-secret runtime defaults live in the frontend image. The Dockerfile records those defaults as image environment values so they do not depend on the launcher's working directory; process environment values supplied by Compose, `docker run --env-file`, or another launcher take precedence. Enabling or changing the instance list therefore requires rebuilding and recreating/restarting only `comfyui-image-frontend`. Never restart or recreate either ComfyUI container for this application update.

The updater intentionally never copies `.env.example` over the deployment's persistent, gitignored `.env`. The image-bundled defaults supply safe RTX 3090 display text and `CIF_COMFYUI_ADDITIONAL_INSTANCES` for Worker 1; the backend appends that worker to the primary ID, URL, user, and concurrency already supplied by the private environment. `compose.example.yml` also loads the same defaults explicitly, while the image environment makes custom Compose and direct-container launch paths behave identically. An existing deployment therefore receives both household runtimes automatically on its next image rebuild without changing its stable primary identity. A full private `CIF_COMFYUI_INSTANCES` list remains authoritative and suppresses the additional-instance layer; setting `CIF_COMFYUI_ADDITIONAL_INSTANCES=[]` explicitly disables the bundled worker. Before reporting success, `update_and_restart` verifies the running frontend resolved an explicit runtime configuration.

### Connecting to external services

`CIF_COMFYUI_INSTANCES` is a JSON array on one environment-variable line. Every entry requires a stable `id`, user-facing `label`, and credential-free HTTP(S) `base_url`. It may also include `description`, a credential-free `ws_url`, a ComfyUI multi-user `user`, and integer `concurrency` from 1 through 32. When `ws_url` is omitted, the application derives `/ws` from `base_url`; when per-instance `concurrency` is omitted, `CIF_COMFYUI_CONCURRENCY` supplies the same bounded fallback. IDs must be unique, start with an ASCII letter or digit, and contain 1–64 ASCII letters, digits, hyphens, or underscores. `CIF_COMFYUI_DEFAULT_INSTANCE_ID` must match one configured ID and defaults to the first array entry. When the full list is absent, `CIF_COMFYUI_ADDITIONAL_INSTANCES` appends entries after the legacy primary assembled from `CIF_COMFYUI_INSTANCE_ID`, `CIF_COMFYUI_BASE_URL`, `CIF_COMFYUI_WS_URL`, `CIF_COMFYUI_USER`, and `CIF_COMFYUI_CONCURRENCY`; `CIF_COMFYUI_LABEL` and `CIF_COMFYUI_DESCRIPTION` control that primary's safe display text.

For the two runtimes on the same Docker host, the explicit configuration below uses the supplied LAN endpoints. This deployment keeps the repository's legacy primary ID `home` stable while giving both targets concise user-facing names; the second container's internal `worker-2` ID is intentionally independent from its sequential display name. During an automatic upgrade, the primary keeps its existing URL and the tracked worker uses `http://192.168.1.21:8189`:

```env
CIF_COMFYUI_INSTANCES=[{"id":"home","label":"Original · RTX 3090","description":"24 GB VRAM","base_url":"http://192.168.1.21:8188","concurrency":1},{"id":"worker-2","label":"Worker 1 · RTX 3080","description":"10 GB VRAM","base_url":"http://192.168.1.21:8189","concurrency":1}]
CIF_COMFYUI_DEFAULT_INSTANCE_ID=home
CIF_COMFYUI_WORKFLOW_DIRECTORY=workflows
CIF_OLLAMA_BASE_URL=http://192.168.1.21:11434
CIF_SPEECH_TO_TEXT_URL=http://192.168.1.22:9000/v1/audio/transcriptions
CIF_SPEECH_TO_TEXT_API_KEY=replace-with-whisper-api-key
CIF_SPEECH_TO_TEXT_MODEL=whisper-1
```

The default instance is both the initial selector choice and the publication-catalog adapter. Source discovery, workflow diagnostics, and publication refresh use only that adapter. Every configured instance remains an independent execution target with its own availability, capacity, native queue, inputs, outputs, temporary files, history, and cancellation channel. This arrangement assumes the configured runtimes expose compatible nodes and the publications/models needed by the compiled graph; it does not merge their ComfyUI queues or storage.

When both instance arrays are absent—for example, in a source installation without the production image defaults—the application constructs one compatible entry named **Original** from `CIF_COMFYUI_BASE_URL`, optional `CIF_COMFYUI_WS_URL`, `CIF_COMFYUI_INSTANCE_ID`, optional `CIF_COMFYUI_USER`, and `CIF_COMFYUI_CONCURRENCY`. The selector shows a configuration warning icon in this one-runtime legacy mode. An explicit `CIF_COMFYUI_ADDITIONAL_INSTANCES=[]` instead records a deliberate one-runtime configuration and suppresses that warning. This preserves intentional single-instance deployments without presenting the internal ID as a display name. Do not set a list entry URL containing credentials; connection URLs and optional user selectors remain server-side and are never returned to the browser.

The standard deployment uses Worker 1's LAN URL, so adding it does not require attaching the frontend to an unknown Docker network. If a custom deployment substitutes internal service names, first inspect the existing runtime networks without printing container environments or secrets:

```sh
docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
  local-ai-comfyui local-ai-comfyui-worker-2
```

If the frontend is not already on a common network, add the existing network to its Compose service and declare it external; do not create replacement ComfyUI services:

```yaml
services:
  comfyui-image-frontend:
    networks:
      - default
      - comfyui-runtime-network

networks:
  comfyui-runtime-network:
    external: true
    name: replace-with-existing-network-name
```

After the frontend container has joined that network, verify both DNS names and the two read-only ComfyUI endpoints from inside it. This does not submit, cancel, or otherwise start generation work:

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

The expected result is HTTP 200 from both endpoints, 2,659 node types from each runtime, and device names identifying the RTX 3090 and RTX 3080 respectively. Do not perform a live `/prompt`, upload, queue mutation, interrupt, cancellation, history retrieval, or output retrieval merely to verify the deployment.

The Prompt Assistant uses the router's native Ollama API with no authentication and does not use the OpenAI-compatible `/v1` API. Thinking defaults on; the focused prompt editor exposes a **Thinking mode** checkbox beside the Creative Direction mode choices and forwards its value as top-level `think`. The compact sidebar and Auto-generate paths continue to use thinking. The adapter accepts a schema-constrained final prompt from `response` first and falls back to a schema-constrained object in `thinking`; a missing `thinking` field is recorded as a capability warning when `response` is valid. Refine mode rejects normalized unchanged output with an actionable error. Do not set `CIF_OLLAMA_MODEL`: the backend omits the model from `/api/generate`, allowing this active-only router to select `hauhau-qwen3.6-35b-a3b-aggressive-q4-k-m:qwen35-parser`, and records the effective model returned in the response. Transient `/api/generate` responses (`408`, `425`, `429`, and `5xx`), connection failures, and malformed JSON are retried at most twice with short backoff; terminal failures retain model/status, field-presence, output-length, done-reason, and validation-stage diagnostics without storing failed prompt text. A successful composition ID makes the stored assistant output authoritative for the positive prompt compiled and submitted to ComfyUI. Create mode starts each composition with a fresh cryptographically random sampling seed, rejects recent results for the same user and Creative Direction, and retries a duplicate with a different seed while keeping the same minimal instruction. Server-side checks reject sexual content involving minors before Prompt Assistant processing, after model composition, and before direct ComfyUI generation. With Auto-generate enabled, every generation cycle with a nonempty Creative Direction waits for a fresh Prompt Assistant composition before queueing; a manual application may prepare the imminent cycle, and that preparation is consumed when its generation is accepted. The operator-only router dashboard is `http://192.168.1.21:11435/`; it is not exposed in the application UI. Keep both `/api/tags` and `/api/generate` routed through the router, never its private upstream Ollama container.

Voice input records in the browser until the microphone button is pressed again, then sends the bounded audio upload to the application. The application adds `CIF_SPEECH_TO_TEXT_API_KEY` and forwards it to the configured OpenAI-compatible transcription endpoint with `model=whisper-1` and `response_format=json`. The recording is not retained. Browser microphone capture requires a secure context: use HTTPS for access by LAN hostname/IP (localhost is the browser-development exception).

ComfyUI and the Ollama router may be unreachable during startup. Accounts and retained history remain available. A last-valid source catalog remains visible as cached/offline, but new dispatch waits for its job's selected ComfyUI instance. Only Prompt Assistant depends on the Ollama router.

Health monitoring probes every configured ComfyUI instance independently. It reruns complete source discovery when the default catalog instance transitions from offline to online, including recovery from an empty startup catalog. While that instance stays online, bundles are refreshed only at startup or by the administrator action; there is no periodic publication refetch.

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
CIF_OLLAMA_BASE_URL=http://192.168.1.21:11434
CIF_SPEECH_TO_TEXT_URL=http://192.168.1.22:9000/v1/audio/transcriptions
CIF_SPEECH_TO_TEXT_API_KEY=replace-with-whisper-api-key
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

The interface manifest is the publication commit marker and authoritative public surface. The app recursively finds only `.interface.json` candidates through the ComfyUI userdata API, fetches all three artifacts, records both manifest-declared and observed artifact hashes, checks safe paths/stems, schemas, node count, typed inputs, bindings, dependency coverage, and `/object_info`, then atomically accepts the revision. Workflow or API byte drift is reported as a nonfatal warning; execution remains pinned to the exact observed, validated API graph. The app never reads a ComfyUI filesystem mount, infers controls or source classifications from graph topology, or mutates publication files.

Publication v1 may also include additive `generation_source` and `technical_inventory` sections. Recognized v1 metadata is returned losslessly on source summary/detail responses for future catalog and dropdown behavior, while older manifests expose both fields as unavailable. `generation_source.base_model.timeline` may describe a provenance-backed architecture introduction month separately from fixed/default and selectable model release months; these dates remain inert metadata and do not change discovery or execution. Unknown values, entries, warnings, and extra fields are retained; metadata never becomes a caller-controlled model selector or queue input.

Current schemas are `comfyui-image-frontend.publication/v1` and `comfyui-image-frontend.interface/v1`. Supported public inputs are string, integer, number, boolean, seed, finite choice, and required static reference image; exactly one text input is the positive prompt. Choice controls expose only stable public values and labels—the trusted CIF declaration node keeps private model/file mappings inside the frozen graph. Image controls accept owner-scoped PNG, JPEG, or WebP assets and patch only `CIFImageParameter.image`; callers never provide ComfyUI paths or locators. Every compatible source declares one or more connected `CIFPublishImage` outputs with `cardinality: many`, including exactly one authored `final`. Previews, comparisons, and auxiliary publishers coexist with every nonpublisher native history result under `unmapped_outputs`; `interface.native_outputs` is diagnostic inventory, never a runtime allowlist.

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

Every valid request resolves defaults, finite choices, companion-strength hints, seeds, and authorized image assets; clones and compiles its accepted frozen graph; and commits an immutable generation plus queue entry before the browser receives it. Public choice IDs are patched only into their trusted declaration-node `value`; image bytes are uploaded under an adapter-owned per-job ComfyUI input namespace; private mappings, paths, and downstream loader inputs are never caller-controlled. The worker preserves FIFO order per user and dispatches round-robin across users within each runtime. The `CIF_COMFYUI_CONCURRENCY` per-entry fallback defaults to one.

The ComfyUI runtime selector appears in the left control panel beneath **Generate**, **Auto-generate**, and **Creative Direction**. It shows the safe configured label, description, and current availability. The default runtime is selected initially. Submission snapshots the selected runtime ID and label into the generation before it enters that runtime's independent fair queue. Changing the selector afterward affects only later submissions: input upload, `/prompt`, WebSocket progress, queue/history reconciliation, `/view` result retrieval, interruption, cancellation, and error reporting for an accepted job continue through its pinned adapter. Cards, detail/status, events, and recall retain the runtime label; removing an ID from configuration does not rewrite historical identity.

Each runtime admits up to its configured `concurrency` active application jobs and maintains a separate round-robin cursor. Capacity on one GPU never pulls from or combines the other ComfyUI queue. Keep an instance ID configured until all of its accepted work is terminal so startup recovery and cancellation can continue to reach the same runtime.

When requested by the publication, the accepted editable snapshot is attached as `extra_data.extra_pnginfo.workflow`; its separately recorded observed hash may differ from the publication-time hash without changing the frozen executable revision. The native ComfyUI `prompt_id` is persisted. WebSocket events provide progress, while bounded `/history/{prompt_id}` reconciliation supplies terminal truth and recovery after cached or missed events.

The server retains complete bounded ComfyUI history. Generation detail removes only top-level submitted `prompt` and `extra_data` graph envelopes; actual outputs, arbitrary JSON-safe custom UI fields, publisher metadata, status/messages/errors, and execution metadata remain intact. It also returns requested/effective parameters, exact seed strings, immutable source revision, ordered publisher outputs with authoritative batch indices, untouched node-keyed unmapped outputs, and every archived image batch member. The gallery uses the authored final as its primary image, while detail groups previews, comparisons, auxiliary publishers, and additional native outputs without dropping any batch sibling.

Browser closure or sign-out does not cancel work. Queued jobs survive restarts, but cancelling one before dispatch deletes its generation record and removes its gallery card. Running jobs reconcile from stored prompt ID, queue state, events, and history. Running cancellation is asynchronous; already returned partial files remain available when safely archived.

## Recall and reproducibility

**Recall settings** replaces the selected source and public parameters with the historical effective values, including concrete seeds and final submitted prompt, and reports the historical execution runtime. When that runtime remains configured, the selector can be restored to it; an unavailable or removed runtime is shown as a warning. Recall never queues automatically or invokes Ollama.

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

Open **Administration → Workflow diagnostics** and refresh. Confirm that publication used Save & Publish, all three adjacent files exist under `workflows/`, the default `CIF_COMFYUI_INSTANCES` entry reuses the stable primary instance ID, its optional `user` is correct, and every declared class exists in `/object_info`. Legacy deployments should check `CIF_COMFYUI_INSTANCE_ID` and optional `CIF_COMFYUI_USER`. Recorded/observed workflow or API hash differences are warnings and do not remove an otherwise valid source. Orphaned `.json` / `.api.json` files are intentionally ignored.

### A source is ready with warnings

Warnings are nonfatal publication or runtime diagnostics. They include workflow/API bytes that differ from hashes recorded at publication and inconsistent optional metadata counts. The manifest values remain publication diagnostics; the observed validated API graph is the executable revision. An absent/disconnected publisher, wrong API node count, missing native-output inventory, invalid cardinality, or zero/multiple final declarations still rejects the source. Inspect accepted warnings before use; discovery never repairs a publication or promotes an arbitrary native image to authored final.

### Sources are cached/offline or Generate is disabled

Retained, fully validated sources remain usable from their frozen API graphs when discovery cannot contact/list the default catalog instance. Dispatch still requires the runtime selected beneath the auto-generation controls to be healthy; an unavailable selected runtime disables submission without affecting a different runtime's queue. Verify the selected entry in `CIF_COMFYUI_INSTANCES`, Docker network/DNS reachability, its optional WebSocket URL and `user`, userdata endpoints, encoded nested-path routing, `/object_info`, and byte limits. `GET /api/comfyui-instances` distinguishes the selected execution runtime's availability from catalog health. A proxy that decodes `%2F` before routing can break nested artifact retrieval.

### A selected source was republished

The API returns `source_republished` when the selected revision no longer matches. Reload the current source descriptor, review its controls/warnings, and submit explicitly. In-flight and historical jobs keep their original revision.

### Startup configuration errors

With no users, set both bootstrap variables and use a temporary password of at least 8 characters. `CIF_SESSION_SECRET` must contain at least 32 random characters. `CIF_COMFYUI_INSTANCES` must be a nonempty JSON array with unique valid IDs, labels, credential-free URLs, and concurrency values from 1 through 32; `CIF_COMFYUI_DEFAULT_INSTANCE_ID` must name an entry. A ComfyUI instance ID must start with an ASCII letter or digit and contain 1–64 ASCII letters, digits, hyphens, or underscores. Response-size limits must be at least 1024 bytes.

### Prompt Assistant is unavailable

Verify `CIF_OLLAMA_BASE_URL` points to the Ollama-compatible router, omit `CIF_OLLAMA_MODEL`, and confirm the router exposes at least one model through `/api/tags`. Use the router rather than its private upstream Ollama container. Prompt-assistant failures distinguish an upstream rejection, exhausted transient status, timeout, transport error, malformed JSON, and structurally invalid model output. The corresponding `prompt_assistant_runs` row retains bounded safe diagnostics and the requested thinking mode; structured `ollama_generate_attempt_failed` logs expose no prompt content. Manual prompt entry and ComfyUI generation are unaffected.

### Voice input is unavailable

Verify `CIF_SPEECH_TO_TEXT_URL` is the complete `/v1/audio/transcriptions` endpoint and `CIF_SPEECH_TO_TEXT_API_KEY` matches the Whisper service. Access the frontend over HTTPS when using a LAN hostname or IP, allow microphone permission in the browser, and confirm the application container can reach the voice host. A speech-to-text outage affects only microphone transcription; typing, Prompt Assistant, and generation remain available.

### Browser receives 403 on a write

Refresh to obtain the current session CSRF token. Password reset or session revocation intentionally invalidates prior sessions.

### SQLite is busy

Store `/data` on a reliable local filesystem and run only one application instance against the database. The app enables WAL, foreign keys, and a busy timeout.

### A refresh or API request is unexpectedly slow

Search the structured application logs for `http_request_completed`. Each record contains a safe request ID, method, normalized route template, status, monotonic duration, and disconnect flag; it does not contain query strings, cookies, request bodies, prompts, filenames, or credentials. Match the browser response's `X-Request-ID` header to the log record, then compare route durations to identify whether session resolution, gallery history, source state, or another endpoint was delayed. Responses also include a safe `Server-Timing` time-to-first-byte measurement for browser diagnostics.

Authenticated startup is progressive. Only `/api/auth/session` is an essential full-screen boundary; preferences, retained gallery history, cached service health, Prompt Assistant, speech-to-text, and source discovery have named browser deadlines and fail in their own UI regions. Browser developer-console messages prefixed with `[startup]` report only the logical operation, outcome, and duration. A message such as `Gallery history timed out after 15 seconds.` therefore identifies the affected operation without exposing an internal URL.

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
