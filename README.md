# ComfyUI Image Front-End

A private image-generation appliance for a trusted home network. It discovers deliberately published ComfyUI workflows, renders their manifest-defined controls, keeps one durable gallery card per accepted generation, retains only the final image batch (or one best available partial), and supports private per-user favorites and exact request recall.

> **External prerequisites:** this repository does not publish workflows, install ComfyUI custom nodes, models, or other workflow dependencies. A separately maintained publisher/custom-node package must create valid three-file publication bundles in ComfyUI userdata.

Install the included [`comfyui_extension/cif_artifact_cleanup`](comfyui_extension/cif_artifact_cleanup) companion in every execution runtime's `custom_nodes` directory. It lets the frontend remove its ComfyUI `output`/`temp` source files after their application-owned copy is durable. If the route is unavailable, generation still completes with an explicit cleanup warning and startup retries later.

## What is included

- FastAPI with server-managed sessions, Argon2id passwords, CSRF protection, login backoff, and narrow account administration.
- SQLite persistence with Alembic migrations, WAL mode, foreign keys, a durable fair queue, and restart reconciliation.
- Owner-scoped image/upload storage with MIME decoding, byte/pixel limits, SHA-256 hashes, and WebP thumbnails.
- Network-only ComfyUI integration with recursive userdata discovery, strict publication validation, request-local graph compilation, prompt submission, WebSocket/history reconciliation, and safe output retrieval.
- Optional server-side Prompt Assistant through an Ollama-compatible router with persisted effective-model provenance.
- Optional browser voice input through a server-side OpenAI-compatible speech-to-text proxy; service credentials never reach the browser.
- Persistent overall generation progress, animated folder activity including nested folders, and an auto-generation status indicator; server-owned auto generation stays pinned to its captured settings and folder across browsers, sign-out, and application restarts.
- A dependency-free browser application with manifest-driven controls, precise seed handling, lazy cursor-paginated gallery, SSE updates, private image and folder favorites with gold indicators and a per-view favorites filter, detail/recall, cancellation, and deletion.
- Image and folder multiselect with shared Move / Copy and Delete actions, recursive folder operations, and independent image copies. Try the [local gallery preview](docs/gallery-selection-preview.md).
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

2. The production image bundles the runtime defaults tracked in `deployment/comfyui-instances.env`, automatically adding the household secondary runtime to the existing primary regardless of whether the container is launched by the example Compose file, another Compose file, or `docker run`. For another topology, override `CIF_COMFYUI_INSTANCES` in the private environment; optionally set `CIF_COMFYUI_DEFAULT_INSTANCE_ID` when the first entry should not be the default. Reuse the primary server's existing stable `CIF_COMFYUI_INSTANCE_ID` value as that entry's `id`; changing it changes every published source key.

3. Build and start:

   ```sh
   docker compose -f compose.example.yml up -d --build
   docker compose -f compose.example.yml logs -f comfyui-image-frontend
   ```

4. Open `https://<appliance-hostname>:8443` (the TLS edge, where `<appliance-hostname>` is `CIF_TLS_HOSTNAME`, default `image-studio.lan`) and sign in with the bootstrap administrator. The first sign-in requires a permanent password. Browsers only expose the clipboard-paste and microphone APIs on a **secure origin** (`https://`, or loopback), which is why the appliance ships a committed local-CA TLS edge instead of plain HTTP. Complete the one-time per-client setup below before using clipboard paste or voice input.

Bootstrap variables are read only when the database has no users. Replacing the container does not reset an existing password.

### Browsers and the local TLS edge

The default `image-studio.lan` name uses a private certificate authority. `compose.example.yml` runs an unprivileged `caddy` edge (`cif-tls-edge`) that terminates TLS with a **locally issued** certificate and reverse-proxies to the application (plain HTTP on port 8000, which stays the container's internal listener). A LAN-only service could instead use a registered domain and ACME DNS validation, but that is a separate deployment configuration. The edge is what browsers connect to:

- **Primary URL:** `https://<CIF_TLS_HOSTNAME>:<CIF_TLS_HOST_PORT>` — default `https://image-studio.lan:8443`.
- **Plain HTTP (`http://<appliance-host>:8000`)** is now for health checks and tooling only. It is not a secure context (clipboard/mic are unavailable there) and, because the session cookie is flagged `Secure`, a plain-HTTP origin can no longer hold a login session.

**Why HTTPS is required:** `navigator.clipboard.readText` (paste) and `navigator.mediaDevices.getUserMedia` (microphone) require a *secure context*. Use HTTPS with a trusted certificate matching the URL hostname, then grant the browser's requested permissions. A LAN address over plain HTTP is not a secure context. Clicking through a certificate warning is not a substitute for installing the local root.

**One-time per-client setup** (do this on each phone/laptop that uses the app):

1. **Trust the local root CA.** Copy `data/certificates/ca.crt` (`CIF_TLS_CERT_DIR`) to the client and add it to the client's trusted root store:
   - **macOS:** double-click `ca.crt` → Keychain Access → add it to the `system` keychain → set the certificate to **Always Trust** for TLS.
   - **Windows:** `certutil -addstore -user Root ca.crt` (or run `certutil -addstore Root ca.crt` as administrator for all users).
   - **Linux:** `sudo cp ca.crt /usr/local/share/ca-certificates/home-image-studio.crt && sudo update-ca-certificates` (or import it into Firefox's *Security → View Certificates → Authorities*, which does not use the OS store).
   - **iOS:** Settings → General → VPN & Device Management → install the profile, then Settings → General → About → Certificate Trust Settings → enable **full trust** for it.
   - **Android:** Settings → Security → Install certificates → CA certificate, and import the same file into Chrome/Firefox (which use the Android system store).
2. **Resolve the hostname to the appliance's LAN IP** (`CIF_TLS_BIND_IP`, default `192.168.1.5`). Either use an internal DNS record for `CIF_TLS_HOSTNAME`, or add a hosts-file entry `<ip> <hostname>` (e.g. `192.168.1.5 image-studio.lan`). A `.lan` name is used because, unlike `.local`, it does not depend on mDNS resolving identically on every OS.

Once both steps are done, `https://<hostname>:8443` loads without a certificate warning on that client, and clipboard paste and voice input work.

**Remote access via SSH tunnel:** forward the plain-HTTP app to loopback with `ssh -N -L 18000:<appliance-ip>:8000 user@<appliance-ip>`, then open `http://localhost:18000`. SSH encrypts the network connection; browsers treat localhost as a potentially trustworthy origin. This is a temporary access option; verify login behavior with your browser's handling of Secure cookies on localhost. To tunnel the TLS edge instead, keep the URL hostname `image-studio.lan`, map that name to loopback on the tunnel client, and trust the same CA. `https://localhost:8443` does not match the default certificate.

**Service Portal:** the app's `io.service-portal.url` label advertises its HTTPS address;
`io.service-portal.hidden` keeps the TLS edge out of the launcher. Use a portal version
with explicit browser URL support and recreate the Compose services to apply labels.
The portal opens the address; each client still needs DNS resolution and CA trust.

The root and leaf are generated by `scripts/issue-local-cert.sh` (a stable root, a leaf for `CIF_TLS_HOSTNAME`, reissued only when the hostname changes or the leaf nears expiry). They live under `CIF_TLS_CERT_DIR` (outside git) and are revalidated/reused on every update and restart before the edge starts.

### Updating a Compose deployment

For an AI agent operating on the production host, follow
[`docs/production-deployment-agent.md`](docs/production-deployment-agent.md) for
preflight checks, backups, deployment, verification, and failure recovery.
On Samus (`192.168.1.5`), run the installed deployment-root `update_production`
command, or give the agent this [short update prompt](docs/production-update-prompt.md).
The command uses a pinned release runner, fetches application source in temporary
scratch, and retains release artifacts without a permanent checkout. It also works
from the agent's `/host` view without SSH setup.

The checkout updater below is for other installations. Samus uses one JSON-formatted
`compose.yaml` at `/home/astigmatism/deployments/comfyui-image-frontend`, external
credentials, and pinned local images. Its `update_production` command and
[production Portal integration](docs/production-service-portal.md) preserve that
topology; the single-file updater below does not. The current runbook covers recovery; the
[historical reference](docs/manual-deployment-reference.md) describes retired layouts.

For the single-file checkout layout, from a clean checkout with an upstream branch:

```sh
./update_and_restart
```

`./update_and_restart` is a thin wrapper around the single-file checkout entry point, `scripts/update-and-restart.sh` (also the script the example Service Portal control invokes; see below). From a clean checkout on the expected branch the script:

1. verifies its toolchain (Git, Docker CLI, Compose v2), Docker daemon access, and the validity of the current Compose configuration;
2. acquires an atomic per-checkout lock (removed again on every exit; a dead holder or a lock recorded by a different maintenance container is treated as stale, and `CIF_UPDATE_FORCE_UNLOCK=1` is the operator escape hatch);
3. verifies the expected branch (`main` by default), that `origin` points at the expected repository, that the branch upstream is `origin/<branch>`, and that the working tree is clean. A detached HEAD, an unexpected remote, a missing upstream, or a dirty tree aborts before any change;
4. fetches the expected upstream branch and merges **fast-forward only**, rejecting divergent or rewritten history;
5. re-validates the updated Compose configuration, then builds the replacement image while the current application remains available;
6. ensures the local TLS certificate for `CIF_TLS_HOSTNAME` is current — issuing it via `scripts/issue-local-cert.sh` when the leaf is missing or no longer matches the configured hostname — then reconciles the complete Compose project (including the `cif-tls-edge` service) with `docker compose up -d --wait --wait-timeout`, which recreates the services and health-checks them with a bounded wait. The old containers are only stopped as part of the recreation, so the current application stays up for as long as the architecture allows; and
7. verifies the running frontend resolved an explicit ComfyUI runtime configuration before reporting success.

The script never runs `docker compose down`, never prunes, and never touches named volumes or bind-mounted configuration, so user data survives every update. If the final reconcile fails, it makes one bounded recovery pass with the last built image and then exits nonzero with a concise `Error:`/`Refusing:` line. Uvicorn cancels lingering request tasks after `CIF_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` (10 seconds by default), leaving the rest of the container grace period for lifespan cleanup.

Override defaults with `CIF_COMPOSE_FILE`, `CIF_COMPOSE_SERVICE`, `CIF_UPDATE_START_TIMEOUT` (bounded health wait in seconds, default 120), `CIF_UPDATE_EXPECTED_BRANCH` (default `main`), or `CIF_UPDATE_EXPECTED_REMOTE` (default `https://github.com/astigmatism/comfyui-image-frontend.git`; set this if your host checkout uses a different remote URL, for example an SSH URL). The former `CIF_UPDATE_STOP_TIMEOUT` stop-deadline knob is no longer used because the script no longer stops the service explicitly; recreation honors the Compose `stop_grace_period` (30 seconds in the example) instead.

### Service Portal "Update and restart" control

**Samus production:** follow the
[production installation instructions](docs/production-service-portal.md) to enable
the button using `update_production_portal`. It waits for the existing production
deployer's verified result. The remaining instructions here apply to single-file
checkout installations.

`compose.example.yml` opts the single long-lived service into Service Portal's project-scoped **Update and restart** control through the `io.service-portal.update.*` labels: after operator confirmation, the portal runs `scripts/update-and-restart.sh` in a detached maintenance container built from an existing local runner image, as the numeric user of the host checkout, with the Docker socket mounted. No portal rebuild is required; the labels are read from live container metadata. To activate the control:

1. Build the runner image once on the Docker host (the portal never pulls or builds it):

   ```sh
   docker build -t comfyui-image-frontend-portal-runner:latest deployment/runner
   ```

   The image provides Bash, Python 3, Git, OpenSSL, Docker CLI and Compose, with no application code or secrets.
2. In the deployment's `.env`, set `HOST_UID` and `HOST_GID` to the numeric owner of the checkout (`id -u; id -g`) and keep `PROJECT_RUNNER_IMAGE` pointing at the image you built (defaults documented in `.env.example`).
3. Recreate the service so Docker records the new labels: `docker compose -f compose.example.yml up -d --force-recreate comfyui-image-frontend`.

For a custom **single-file checkout** deployment compatible with the updater,
copy the four labels from `compose.example.yml` and configure the verified update
environment. Do not enable the generic `scripts/update-and-restart.sh` for the
Samus deployment: use the production entrypoint linked above to preserve its
pinned images, external credentials, data bind, and app-only cutover. The
portal suppresses the control when more than one service opts in with different
effective settings. The remote is public, so no Git credentials are required; if
that ever changes, provision noninteractive least-privilege credentials inside the
runner image or the host checkout without committing them to source or exposing
them to the browser.

Application code, built browser assets, and the non-secret runtime defaults live in the frontend image. The Dockerfile records those defaults as image environment values so they do not depend on the launcher's working directory; process environment values supplied by Compose, `docker run --env-file`, or another launcher take precedence. Enabling or changing the instance list therefore requires rebuilding and recreating/restarting only `comfyui-image-frontend`. Never restart or recreate either ComfyUI container for this application update.

The updater intentionally never copies `.env.example` over the deployment's persistent, gitignored `.env`. The image-bundled defaults supply the safe **Primary** label and `CIF_COMFYUI_ADDITIONAL_INSTANCES` for the secondary runtime; the backend appends that worker to the primary ID, URL, user, and concurrency already supplied by the private environment. `compose.example.yml` also loads the same defaults explicitly, while the image environment makes custom Compose and direct-container launch paths behave identically. An existing deployment therefore receives both household runtimes automatically on its next image rebuild without changing its stable primary identity. A full private `CIF_COMFYUI_INSTANCES` list remains authoritative and suppresses the additional-instance layer; setting `CIF_COMFYUI_ADDITIONAL_INSTANCES=[]` explicitly disables the bundled worker. Before reporting success, `update_and_restart` verifies the running frontend resolved an explicit runtime configuration.

### Connecting to external services

`CIF_COMFYUI_INSTANCES` is a JSON array on one environment-variable line. Every entry requires a stable `id`, user-facing `label`, and credential-free HTTP(S) `base_url`. It may also include `description`, a credential-free `ws_url`, a ComfyUI multi-user `user`, and integer `concurrency` from 1 through 32. When `ws_url` is omitted, the application derives `/ws` from `base_url`; when per-instance `concurrency` is omitted, `CIF_COMFYUI_CONCURRENCY` supplies the same bounded fallback. IDs must be unique, start with an ASCII letter or digit, and contain 1–64 ASCII letters, digits, hyphens, or underscores. `CIF_COMFYUI_DEFAULT_INSTANCE_ID` must match one configured ID and defaults to the first array entry. When the full list is absent, `CIF_COMFYUI_ADDITIONAL_INSTANCES` appends entries after the legacy primary assembled from `CIF_COMFYUI_INSTANCE_ID`, `CIF_COMFYUI_BASE_URL`, `CIF_COMFYUI_WS_URL`, `CIF_COMFYUI_USER`, and `CIF_COMFYUI_CONCURRENCY`; `CIF_COMFYUI_LABEL` and `CIF_COMFYUI_DESCRIPTION` control that primary's safe display text.

For the two runtimes on the same Docker host, the explicit configuration below uses the supplied LAN endpoints. This deployment keeps the repository's legacy primary ID `home` stable while giving both targets concise user-facing names; the second container's internal `worker-2` ID is intentionally independent from its display label. During an automatic upgrade, the primary keeps its existing URL and the tracked worker uses `http://192.168.1.21:8189`:

```env
CIF_COMFYUI_INSTANCES=[{"id":"home","label":"Primary","base_url":"http://192.168.1.21:8188","concurrency":1},{"id":"worker-2","label":"Secondary","base_url":"http://192.168.1.21:8189","concurrency":1}]
CIF_COMFYUI_DEFAULT_INSTANCE_ID=home
CIF_COMFYUI_WORKFLOW_DIRECTORY=workflows
CIF_OLLAMA_BASE_URL=http://192.168.1.21:11434
CIF_OLLAMA_MODEL=nighttime
CIF_OLLAMA_API_KEY=local-only
CIF_SPEECH_TO_TEXT_URL=http://192.168.1.22:9000/v1/audio/transcriptions
CIF_SPEECH_TO_TEXT_API_KEY=replace-with-whisper-api-key
CIF_SPEECH_TO_TEXT_MODEL=whisper-1
```

The default instance is both the initial selector choice and the publication-catalog adapter. Source discovery, workflow diagnostics, and publication refresh use only that adapter. Every configured instance remains an independent execution target with its own availability, capacity, native queue, inputs, outputs, temporary files, history, and cancellation channel. This arrangement assumes the configured runtimes expose compatible nodes and the publications/models needed by the compiled graph; it does not merge their ComfyUI queues or storage.

When both instance arrays are absent—for example, in a source installation without the production image defaults—the application constructs one compatible entry named **Primary** from `CIF_COMFYUI_BASE_URL`, optional `CIF_COMFYUI_WS_URL`, `CIF_COMFYUI_INSTANCE_ID`, optional `CIF_COMFYUI_USER`, and `CIF_COMFYUI_CONCURRENCY`. The selector shows a configuration warning icon in this one-runtime legacy mode. An explicit `CIF_COMFYUI_ADDITIONAL_INSTANCES=[]` instead records a deliberate one-runtime configuration and suppresses that warning. This preserves intentional single-instance deployments without presenting the internal ID as a display name. Do not set a list entry URL containing credentials; connection URLs and optional user selectors remain server-side and are never returned to the browser.

The standard deployment uses the secondary runtime's LAN URL, so adding it does not require attaching the frontend to an unknown Docker network. If a custom deployment substitutes internal service names, first inspect the existing runtime networks without printing container environments or secrets:

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

The Prompt Assistant uses the router's native Ollama API (`/api/tags` and `/api/chat`), which the home router translates to its private LLM backend. `CIF_OLLAMA_BASE_URL` accepts either the router root or its advertised `/v1` base (the suffix is normalized away). Optional `CIF_OLLAMA_API_KEY` supplies a server-only bearer credential. Thinking defaults on; the **Thinking mode** checkbox is inside the collapsed **Prompt pre-processor** section in both the sidebar and focused editor. Enabled compositions explicitly send top-level `think: "xhigh"`; disabled compositions send `think: false`. Nighttime advertises `xhigh` as its highest effort and maps `max` to `xhigh`; boolean `true` would select the router default instead. Manual compositions and Auto-generate use the same effort mapping. The chat route supports thinking on the router’s llama.cpp backend; `/api/generate` rejects enabled thinking. The adapter normalizes `message.content` and `message.thinking` into its existing response/thinking diagnostics, accepts a schema-constrained final prompt from content first, and falls back to a schema-constrained object in thinking. Missing thinking is recorded as a capability warning when content is valid. Refine mode treats normalized unchanged output as a rejected candidate and redraws with a new seed and progressively stronger sampling up to three times; only exhaustion returns `prompt_refinement_unchanged` (HTTP 422). Create mode likewise rejects a candidate that only repeats or truncates the Creative Direction (case- and whitespace-insensitive) and redraws with the next seed; if every candidate fails to expand the direction the request returns `prompt_creation_unchanged` (HTTP 422) instead of storing the echo as a successful run. `CIF_OLLAMA_MODEL` defaults to `nighttime` and is sent explicitly on every generation attempt; the router resolves the alias and the application records the effective model returned in the response. Health checks require that selected alias to be advertised and not explicitly offline. An empty model setting deliberately restores router-default selection. Create sends only Creative Direction to the LLM; the existing prompt is used locally to reject duplicates. Refine sends both the current prompt and Creative Direction.

The **Prompt pre-processor** section starts collapsed in both the sidebar and focused editor. Expand it to edit the actual default instructions for the selected Create or Refine mode, with a separate **Reset to default** action for each mode. **Apply Creative Direction** stays visible, and custom instructions remain active while collapsed. Drafts are remembered for the signed-in account in this browser; the focused editor only saves its drafts when **Apply** is chosen, while **Cancel** discards them. The **Prompt pre-processor** sits above the Creative Direction field in both surfaces, mirroring the send order: the instructions are sent first, then the current prompt (Refine only), then the Creative Direction, all in one user message, and a hint inside the section explains that sequence. Custom instructions are passed to manual and automatic compositions, included in Auto-generate's input fingerprint, and saved with successful composition provenance for recall. Empty or oversized instructions are rejected before reaching the router. The existing distinctness and structured-output requirements still apply.

Ollama applies `num_predict` to thinking and final output together; it does not reserve a separate final-answer allowance. Prompt composition therefore uses the named output-budget schedule `2048 → 4096 → 8192`. When `done_reason` is `length` and neither `response` nor the parser-compatible `thinking` field contains a complete `{"prompt": <nonempty string>}` object, the adapter retries at the next allowance while preserving `think`, schema, instruction, temperature, and candidate seed. When thinking is enabled and all three thinking escalations end in `length`, the candidate makes one extra attempt with `think: false` at the base `2048` allowance: the no-thinking pass emits only the final object, so it fits the base allowance even when the reasoning overflows it. The fallback reuses the candidate's seed and temperature, and its prompt still passes the same distinctness validation. A complete structured prompt remains acceptable even if the terminal reason is `length`. Only after every candidate has exhausted its schedule and, with thinking enabled, its no-thinking fallback does the request return `ollama_output_budget_exhausted` with HTTP 503 and privacy-safe attempt/allowance diagnostics, including the fallback flag and the per-candidate budget history. Transient `/api/chat` responses (`408`, `425`, `429`, and `5xx`), connection failures, and malformed JSON retain their independent at-most-three-attempt transport policy. Candidate distinctness remains independent: only a complete unchanged Refine candidate, Create echo, or excluded Create duplicate advances its seed and temperature, and so does a candidate that ends its thinking schedule and fallback with no usable prompt. Pure budget exhaustion therefore stops the composition after at most twelve upstream `/api/chat` calls (three candidates × (three thinking attempts + one no-thinking fallback)), or at most 36 upstream requests when every transport attempt is also used.

A successful composition ID makes the stored assistant output authoritative for the positive prompt compiled and submitted to ComfyUI. Each composition starts with a fresh cryptographically random candidate seed. Refine retries an unchanged candidate with the next seed and higher temperature. Create rejects the current prompt, recent successful results for the same user and Creative Direction, and the Creative Direction itself, and retries a duplicate with the next seed while keeping the same minimal instruction. The application does not moderate or restrict prompt content. Auto-generation runs on the server and continues with the browser closed. With Prompt Generation enabled, each batch creates one prompt and, when enabled, one Creative Direction refinement; every image in the batch shares that text. Quantity repeats use separate random image seeds. A manual Generate with Prompt Generation enabled shares the same semantics: one generated prompt and at most one refinement cover the entire batch (all quantity and model selections), and a text or refinement failure fails the whole batch. Generation edits apply automatically to the next batch, while the folder remains pinned to where automation was enabled. The dropdown’s “Stop after … images queued” limit counts image jobs accepted since enabling; changing the limit preserves that count. Manual Generate is disabled while automation is on. Prompt validation failures include a short actionable explanation and block the batch until corrected or retried. Accepted images continue when automation is stopped. The operator-only router dashboard is `http://192.168.1.21:11435/`; it is not exposed in the application UI. Keep both `/api/tags` and `/api/chat` routed through the router, never its private upstream LLM service.

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
CIF_OLLAMA_MODEL=nighttime
CIF_OLLAMA_API_KEY=local-only
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

Every valid request resolves defaults, finite choices, companion-strength hints, seeds, and authorized image assets; clones and compiles its accepted frozen graph; and commits an immutable generation plus queue entry before the browser receives it. Public choice IDs are patched only into their trusted declaration-node `value`; image bytes are uploaded under an adapter-owned per-job ComfyUI input namespace; private mappings, paths, and downstream loader inputs are never caller-controlled. The worker gives manual jobs priority over waiting automatic jobs for the same user, preserves FIFO within each class, and dispatches round-robin across users within each runtime. The `CIF_COMFYUI_CONCURRENCY` per-entry fallback defaults to one.

The ComfyUI runtime selector appears in the left control panel beneath **Generate**, **Auto-generate**, and **Creative Direction**. It shows the safe configured label, description, and current availability. The default runtime is selected initially. Submission snapshots the selected runtime ID and label into the generation before it enters that runtime's independent fair queue. Changing the selector afterward affects only later submissions: input upload, `/prompt`, WebSocket progress, queue/history reconciliation, `/view` result retrieval, interruption, cancellation, and error reporting for an accepted job continue through its pinned adapter. Cards, detail/status, events, and recall retain the runtime label; removing an ID from configuration does not rewrite historical identity.

Each runtime admits up to its configured `concurrency` active application jobs and maintains a separate round-robin cursor. Capacity on one GPU never pulls from or combines the other ComfyUI queue. Keep an instance ID configured until all of its accepted work is terminal so startup recovery and cancellation can continue to reach the same runtime.

When requested by the publication, the accepted editable snapshot is attached as `extra_data.extra_pnginfo.workflow`; its separately recorded observed hash may differ from the publication-time hash without changing the frozen executable revision. The native ComfyUI `prompt_id` is persisted. WebSocket events provide progress, while bounded `/history/{prompt_id}` reconciliation supplies terminal truth and recovery after cached or missed events.

The server retains complete bounded ComfyUI history. Generation detail removes only top-level submitted `prompt` and `extra_data` graph envelopes; actual outputs, arbitrary JSON-safe custom UI fields, publisher metadata, status/messages/errors, and execution metadata remain intact. Binary retention is deliberately compact: while a job runs, a more advanced stage replaces older application files; success keeps the authored final batch; cancellation or failure keeps one best available image. Logical references to other returned results remain in history without duplicate binary storage. After all retrievable files have been processed, the frontend deletes its source `output`/`temp` files from ComfyUI through the included companion route.

Auto generation is a persistent per-user controller. Enabling it captures the workflow revision, parameters, checkpoint selection/order, quantity, seed policy, execution runtime, current destination folder, input uploads, and effective Creative Direction instructions. It keeps producing batches without a browser and resumes after server recovery. Valid edits synchronize automatically to future batches through the revision-checked API. Accepted images keep their settings; obsolete unaccepted preparation is discarded. The destination stays pinned until automation is turned off. Prompt Generation produces one shared prompt and optional refinement per batch; quantity repetitions receive separate random image seeds. Automatic Refine without Prompt Generation evolves from the last accepted prompt.

**Stop after [number] images queued** defaults to **200** image jobs. Quantity × selected checkpoints consumes the allowance, and a final batch is shortened to the exact remainder. Changing the limit preserves the number accepted since enabling; prompt jobs do not count. Blank means unlimited. Reaching or lowering the limit below the accepted count disables automation and lets accepted images finish. Switching the toggle off also preserves all accepted images. Both Generate buttons and new manual image submissions are blocked while automation is enabled; existing idempotency receipts remain recoverable.

Temporary runtime outages retain prepared prompts for recovery. Invalid configurations and failed prompt/image executions stop further production in a visible blocked state with Retry after correction. Prompt Assistant failures without Prompt Generation retain the existing bounded backoff. Deleted destination folders block generation; turn automation off and enable it again in another folder. Account deletion stops its automation before cleanup.

Generation controls and display preferences are saved per account through `/api/preferences`, with revision checks and conflict resolution across devices. Existing browser settings are imported only when shared settings have not been initialized; subsequent browsers use the saved server values. Temporary navigation, dialogs, playback, and unsaved typing remain local. The UI reports saving failures and never treats an unavailable automation status as off.

The authenticated automation API is `GET/PUT /api/auto-generation`, with `POST /apply`, `/retry`, and `/limit` beneath that path. Mutations require CSRF and `expected_revision`; stale revisions return HTTP 409. Manual generation submissions to `/api/generations` and `/api/generations/batch` require `X-CIF-Generation-Protocol: 3` and an account-scoped UUID `Idempotency-Key`.
Accepted submissions and ordered batch failures have durable receipts. Identical retries
reuse the original IDs; changed payloads conflict. The browser preserves unresolved
submissions across reloads and exposes a status-check/resume action. See
[concurrency and update reliability](docs/production-reliability.md). Update API clients and reload existing browser tabs when deploying this release; obsolete clients receive `client_reload_required` rather than continuing their browser-owned loops. The coordinator runs in the existing single application process, and `/api/health` includes its readiness.

Browser closure or sign-out does not cancel work. Queued jobs survive restarts, but cancelling one before dispatch deletes its generation record and removes its gallery card. Running jobs reconcile from stored prompt ID, queue state, events, and history. Running cancellation is asynchronous; already returned partial files remain available when safely archived.

## Recall and reproducibility

**Recall settings** replaces the selected source and public parameters with the historical effective values, including concrete seeds and final submitted prompt, and reports the historical execution runtime. It also restores the Creative Direction panel from the snapshot recorded when the generation was accepted — direction text, pre-processor mode and instructions, and thinking mode — so the composer is shown as it was when the image was created; rows predating the snapshot fall back to the linked composition run. When that runtime remains configured, the selector can be restored to it; an unavailable or removed runtime is shown as a warning. Recall never queues automatically or invokes Ollama.

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

This checks formatting/linting, types, generated traceability, backend/frontend tests, production builds, both browser suites (the loopback `e2e` journeys and the TLS-edge `e2e-tls` journeys over `https://`), and the container smoke test. The TLS-edge e2e runs against the real Compose stack when a Docker daemon is reachable and otherwise against a local Caddy in front of the in-process app. In constrained environments:

```sh
make validate-available
```

Run just the TLS-edge browser suite with `make e2e-tls`. Focused commands and live integration guidance are in [`docs/testing.md`](docs/testing.md).

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

Verify `CIF_OLLAMA_BASE_URL` points to the Ollama-compatible router, set `CIF_OLLAMA_MODEL=nighttime`, and confirm that alias is available through `/api/tags`. Configure `CIF_OLLAMA_API_KEY` if required. Use the router rather than its private upstream LLM service. Prompt-assistant failures distinguish an upstream rejection, exhausted transient status, timeout, transport error, malformed JSON, output-budget exhaustion after every candidate's thinking schedule and no-thinking fallback, and structurally invalid model output. The corresponding `prompt_assistant_runs` row retains bounded safe diagnostics and the requested thinking mode; structured retry logs expose no prompt, Creative Direction, or raw reasoning. Auto-generate retries recoverable composition failures and visibly pauses with an unchecked switch and retry action if recovery is exhausted. Manual prompt entry and ComfyUI generation are unaffected.

### Voice input is unavailable

Verify `CIF_SPEECH_TO_TEXT_URL` is the complete `/v1/audio/transcriptions` endpoint and `CIF_SPEECH_TO_TEXT_API_KEY` matches the Whisper service. Access the frontend over the TLS edge (`https://<hostname>:8443`) with the one-time per-client setup complete (trust the local root CA and resolve the hostname to the appliance IP — see "Browsers and the local TLS edge"), allow microphone permission in the browser, and confirm the application container can reach the voice host. A speech-to-text outage affects only microphone transcription; typing, Prompt Assistant, and generation remain available.

### Browser receives 403 on a write

Refresh to obtain the current session CSRF token. Password reset or session revocation intentionally invalidates prior sessions.

### SQLite is busy

Store `/data` on a reliable local filesystem and run only one application instance against the database. The app enables WAL, foreign keys, and a busy timeout.

### A refresh or API request is unexpectedly slow

Search the structured application logs for `http_request_completed`. Each record contains a safe request ID, method, normalized route template, status, monotonic duration, and disconnect flag; it does not contain query strings, cookies, request bodies, prompts, filenames, or credentials. Match the browser response's `X-Request-ID` header to the log record, then compare route durations to identify whether session resolution, gallery history, source state, or another endpoint was delayed. Responses also include a safe `Server-Timing` time-to-first-byte measurement for browser diagnostics.

Authenticated startup is progressive. Only `/api/auth/session` is an essential full-screen boundary; preferences, retained gallery history, cached service health, Prompt Assistant, speech-to-text, and source discovery have named browser deadlines and fail in their own UI regions. Browser developer-console messages prefixed with `[startup]` report only the logical operation, outcome, and duration. A message such as `Gallery history timed out after 15 seconds.` therefore identifies the affected operation without exposing an internal URL.

## Documentation

- [`docs/production-deployment-agent.md`](docs/production-deployment-agent.md) — production update and deployment instructions for AI agents
- [`docs/published-workflows.md`](docs/published-workflows.md) — publication, discovery, validation, compilation, result, security, and migration contract
- [`docs/architecture.md`](docs/architecture.md) — components, trust boundaries, request/result and recovery flows
- [`docs/api.md`](docs/api.md) — current source and generation API plus common application routes
- [`docs/database.md`](docs/database.md) — schema ownership, publication migration, and storage
- [`docs/migration-published-workflows.md`](docs/migration-published-workflows.md) — old discovery/client/data compatibility and operator steps
- [`docs/testing.md`](docs/testing.md) — validation commands and publication coverage
- [`docs/traceability.md`](docs/traceability.md) — generated product/publication acceptance mapping
- [`docs/normative-product-requirements-v1.0.md`](docs/normative-product-requirements-v1.0.md) — historical original product specification
- [`docs/normative-workflow-contract-v1.1.md`](docs/normative-workflow-contract-v1.1.md) — historical retired embedded-contract design

### Ordered LoRA controls

The complete ComfyUI interface/publisher extension is maintained in `comfyui_extension/comfyui-image-frontend-interface`, alongside the artifact-cleanup extension. Published `lora_stack` inputs support independent strengths and drag, keyboard, or touch ordering. See [ordered LoRA controls](docs/lora-controls.md) for the contract, package installation, workflow conversion, tests, and rollback procedure.
