# Architecture

## Scope and invariants

This is one application container for a small trusted network, not a public multi-tenant platform. It uses one FastAPI process, one SQLite database, application-owned files, and in-process asynchronous workers. There is no broker, separate database, workflow editor, model installer, ComfyUI filesystem mount, or direct browser connection to ComfyUI, Ollama, or the speech-to-text service.

The principal invariants are:

1. A generation source exists only after a complete three-file ComfyUI publication passes strict validation.
2. The browser receives only an allowlisted public interface and submits only a stable source key/revision plus public parameters.
3. Cached frozen graphs are immutable; compilation deep-clones and patches only trusted manifest bindings per request.
4. Every accepted generation, its exact source revision, and its selected ComfyUI execution instance are durably recorded before dispatch.
5. Terminal `/history/{prompt_id}` is preserved as the source of truth; no native output node or batch member is discarded.
6. A gallery display image is a presentation aid, not an invented contract-declared final output.
7. Every content query and file response is scoped to the authenticated owner; administrator role is not a content bypass.
8. Accepted executions are immutable snapshots. Republishing or form edits affect only new requests.

## Process layout

`app.main.create_app` creates an `AppContainer` whose services divide responsibility:

- `Database`: SQLAlchemy sessions, SQLite pragmas, migration startup.
- `AuthService`: bootstrap, Argon2id credentials, throttling, revocable sessions, account operations.
- `AssetStore`: safe image decode, application paths, hashes, thumbnails, atomic file operations.
- `ComfyUIAdapter`: one configured instance's bounded HTTP/WebSocket transport, userdata route probing, `Comfy-User`, prompt/history/output operations.
- `ComfyUIInstances`: validated private instance configuration plus adapters keyed by stable execution ID; its default adapter is the publication-catalog boundary.
- `WorkflowRegistry`: publication listing, validation, immutable revision catalog, last-valid caching, diagnostics.
- `WorkflowCompiler`: public parameter validation/defaults, exact seed resolution, request-local graph clone/bindings/hash.
- `GenerationService`: owner-scoped API projection, acceptance transaction, recall, cancellation/deletion.
- `QueueWorker`: durable fair claim, submission, WebSocket/history monitoring, output normalization/archive, recovery.
- `OllamaAdapter`: router availability validation, model-free per-request thinking control, safe structured final-prompt extraction, bounded generated-token budget escalation with a single no-thinking fallback per candidate, and effective-model provenance.
- `SpeechToTextAdapter`: bounded authenticated forwarding to an OpenAI-compatible transcription endpoint without persisting recordings.
- `EventBroker`: low-latency owner-specific SSE fan-out; the database is the replay source.
- `UserDeletionService`: revocation, active-job reconciliation, row/file cleanup without content disclosure.

FastAPI serves the built frontend after `/api` routes. Public source details are constructed by allowlist; private values are never copied and then redacted.

The browser is the only microphone boundary. One `MediaRecorder` session may be active at a time; stopping it uploads the resulting audio through the authenticated, CSRF-protected application route. The browser never receives speech-service connection details or credentials. Transcribed text is inserted at the saved textarea selection and then follows the same editable state path as typed text. HTTPS is required for browser microphone capture outside localhost.

## Persistence boundaries

SQLite owns structured state and authorization. Binary data is beneath the configured application data directory:

```text
/data/app.db
/data/app.db-wal
/data/app.db-shm
/data/uploads/<owner shard>/<opaque id>.<ext>
/data/assets/<generation shard>/<opaque id>.<ext>
/data/assets/<generation shard>/<opaque id>.thumb.webp
```

Prompts and user/ComfyUI filenames are never storage paths. Application paths are generated, stored relative to the data root, and resolved beneath that root before open/delete. SQLite uses foreign keys, WAL, `synchronous=NORMAL`, and a busy timeout. Network work, hashing, decoding, and thumbnails occur outside long write transactions where practical.

Publication documents are durably snapshotted as JSON in `workflow_profiles`; the exact frozen-API and manifest SHA-256 values plus the manifest-recorded editable hash preserve identity even though current editable bytes and all source files remain externally owned by ComfyUI. Generations copy the source revision and result structures needed for historical display.

## Authentication and authorization

A random opaque token is stored in an `HttpOnly` cookie. SQLite stores only its HMAC-SHA-256 identity plus CSRF, session epoch, expiry, IP hash, and optional user-agent metadata. Passwords use Argon2id.

Anonymous login uses signed double-submit CSRF. Authenticated mutations require the session CSRF token in `X-CSRF-Token`. Password reset increments the user's session epoch and deletes sessions. Login throttling keys a username/IP tuple without logging credentials.

Content queries include both object ID and `owner_id`, returning not found for cross-user IDs even to administrators. Administrator APIs expose account records and safe workflow diagnostics only. Media is delivered through authenticated routes, not a public file mount.

## ComfyUI instance configuration

`CIF_COMFYUI_INSTANCES` is a server-side JSON array. An entry has required `id`, `label`, and credential-free `base_url` fields plus optional `description`, `ws_url`, `user`, and `concurrency`. IDs are stable routing identities; labels and descriptions are safe presentation text. HTTP/WebSocket URLs and the optional ComfyUI user selector stay private. `CIF_COMFYUI_DEFAULT_INSTANCE_ID` fixes image execution and its authoritative catalog; when omitted it resolves to the legacy default. `CIF_COMFYUI_TEXT_INSTANCE_ID` independently fixes prompt execution and its authoritative catalog. It must name a different configured instance; an unset text assignment disables prompt generation without affecting images. Per-entry concurrency falls back to `CIF_COMFYUI_CONCURRENCY`; both are restricted to 1 through 32. When the full list is absent, `CIF_COMFYUI_ADDITIONAL_INSTANCES` extends the primary assembled from the legacy single-instance fields. Supplying the additional list marks the configuration explicit, while an empty list is the deliberate single-runtime opt-out; a full list takes precedence when both are present.

The production Dockerfile records the non-secret display metadata, existing worker and CPU prompt service from `deployment/comfyui-instances.env` as image environment defaults, so the settings are independent of the launch working directory even when a custom Compose file or direct `docker run` bypasses `compose.example.yml`. Runtime process environment has higher precedence and remains authoritative. On an upgraded household deployment, additional instances are appended while retaining the existing primary identity and connection settings; this avoids rewriting private configuration or changing publication keys. A private full list bypasses the additional-instance layer and must include the configured CPU assignment before upgrade. Stage assignments come from the deployment environment, not Docker image ENV defaults. An explicit single-instance list without a text assignment remains image-only, including the installed release runner's isolated smoke environment. The private repository-root `.env` is excluded from the Docker build context.

When both JSON arrays are absent, settings synthesize one entry named **Primary** from the legacy `CIF_COMFYUI_BASE_URL`, `CIF_COMFYUI_WS_URL`, `CIF_COMFYUI_INSTANCE_ID`, `CIF_COMFYUI_USER`, and `CIF_COMFYUI_CONCURRENCY` fields. The safe instance-catalog response identifies this as `configuration_mode: legacy`, distinguishing its one-target compatibility configuration from an explicit list without exposing routing controls. Supplying `CIF_COMFYUI_ADDITIONAL_INSTANCES=[]` marks a deliberate one-target opt-out as explicit. This preserves existing source keys and single-instance deployment behavior. Once a full list is adopted, the primary entry must reuse the old stable instance ID rather than inventing a new catalog identity.

## Published-source discovery

At startup and administrator refresh, `WorkflowRegistry` discovers every configured instance through its own adapter and commits each result independently. An instance's offline-to-online health transition refreshes that catalog. Health monitoring does not refetch bundles periodically while an instance remains online. The pipeline for each catalog is:

1. Probe ComfyUI and retrieve bounded `/object_info` capability data.
2. Recursively list the configured userdata namespace, preferring `/v2/userdata?path=workflows` and falling back to `/userdata?dir=workflows&recurse=true&full_info=true`.
3. Filter safe normalized `.interface.json` paths.
4. Retrieve each manifest and adjacent `<stem>.json` / `<stem>.api.json`; nested paths are encoded whole as one route segment.
5. Parse strict UTF-8 JSON; compare recorded and observed workflow/API hashes for warning-only drift; and validate publication/interface schemas, path/stem/source agreement, observed API graph node count/shape, public IDs/types/defaults/ranges/steps, finite choice values/labels/hints, one positive prompt, trusted bindings, dependencies, warnings, and runtime flags.
6. Match all declared class types against `/object_info`.
7. Recognize additive generation-source/technical-inventory v1 metadata losslessly, checking inventory arithmetic diagnostically without converting metadata into executable selectors.
8. Atomically publish each complete accepted revision and safe diagnostic.

Each instance may configure its own optional `Comfy-User` value, which is applied consistently to that adapter's relevant HTTP and WebSocket traffic. Listing, manifest, workflow, API, object-info, history, and output responses have separate global byte caps.

`source_key` is stable for one catalog `instance_id + source_id`. Public listings use only the stage-assigned catalog, with one entry per logical source and output kind. Safe replica descriptors preserve alias keys. An old key resolves logical identity to the assigned catalog; its controls, dependencies and requested revision are validated there. Other copies never supply fallback health or execution. The immutable revision consists of the publication UUID, manifest-recorded workflow hash, exact observed/validated API hash, and exact manifest hash. Workflow/API hash drift produces `ready_with_warnings`, not rejection; an observed API change creates a distinct executable revision so it cannot alias an older frozen snapshot. A structurally invalid republish cannot replace its last accepted revision. One rejected candidate cannot remove independent valid sources. A transport/listing failure retains the last valid catalog as cached/offline; a successful authoritative listing retires disappeared sources and old embedded-contract profiles. Missing dependencies produce an unavailable catalog record.

Ordinary source APIs contain display name, stable key, instance identity, readiness/cached/availability, warnings, revision, recognized generation-source/technical-inventory metadata, and public interface inputs/outputs. They omit source path, graph, bindings, node IDs, instance UUIDs, and current missing-dependency details. Metadata artifact values are inert basenames; published reachable/orphan class inventories are descriptive only.

## Request acceptance and compilation

Canonical generation input is `{source_key, revision?, parameters, prompt_assistant_run_id?, comfyui_instance_id?}`. New clients omit execution IDs. Compatibility fields are assertions: supplied IDs must match the stage assignment or receive `runtime_assignment_conflict`. Accepted receipts are checked first and retain their original request digests and results. Acceptance requires:

- current source resolution and optional exact revision check;
- rejection of unknown public IDs and legacy graph/binding/path injection;
- required/default/type/range/step validation;
- finite-choice membership and deterministic companion-strength resolution;
- canonical decimal parsing and request-local random resolution for seeds;
- owner validation for any linked Prompt Assistant run;
- resolution of the configured stage assignment; a validated cached publication may queue during a transport outage and wait for that service;
- deep clone of the accepted frozen API graph;
- patching every private manifest binding for each effective public input;
- verification that the cached graph remained byte-for-byte/logically unchanged;
- positive-prompt extraction and compiled graph SHA-256.

Seed values remain decimal strings in public/effective state so values beyond JavaScript's safe integer range round-trip exactly; the cloned graph receives the validated integer. Choice values remain stable public IDs throughout application state. Only the trusted choice declaration's prompt-local `value` is patched; private `options_json` mappings and downstream model/file inputs remain frozen and server-side. Required image inputs remain owner-scoped application assets until dispatch. The worker uploads their validated bytes under a collision-resistant per-job ComfyUI input subfolder and replaces only the cloned graph's trusted `CIFImageParameter.image` placeholder.

One transaction inserts the generation, queue sequence, source revision snapshot, assigned execution ID and current safe label, requested/effective parameters, resolved seeds, compiled graph/hash, Prompt Assistant linkage, and initial durable event. Only then does the API return a card. The ID is the routing key; the label is a historical display snapshot and never selects an adapter.

Temporary legacy request/response aliases are isolated at the schema/service boundary. They resolve only to a current validated publication and cannot re-enable embedded-contract discovery.

## Scheduling and ComfyUI submission

The worker tracks active capacity independently for every configured instance. An entry's `concurrency` is its maximum number of active application jobs; omitted values inherit `CIF_COMFYUI_CONCURRENCY`. Queue claims filter by the persisted execution ID, and a separate hashed `scheduler_state` key preserves oldest-per-user/FIFO and round-robin fairness within each instance. Capacity becoming free on one runtime never claims another runtime's jobs or merges their native ComfyUI queues.

Before `/prompt`, the worker resolves the adapter from the generation's persisted execution ID and reuses the immutable compiled graph. When the accepted publication runtime flag requests it, the accepted editable snapshot is attached only as `extra_data.extra_pnginfo.workflow`; its observed hash is tracked separately when it differs from the publication record. Required input bytes are uploaded through that same adapter under a per-job namespace. Submission uses a request-specific client ID. The worker opens and begins consuming that adapter's client-specific WebSocket before submission, with a bounded readiness wait and history-only fallback. The returned native `prompt_id` is persisted alongside the execution identity before buffered events are applied.

The monitor combines a reconnecting WebSocket pump with bounded history polling/retry.
`progress_state` is preferred per node and legacy `progress` remains a fallback. Executing nodes
without valid numeric counters produce an indeterminate snapshot. Numeric callbacks are
coalesced before database/SSE work, while node transitions and final counters are retained as
sparse durable events. Safe labels come from frozen node metadata/editable titles, optional object
metadata, or the neutral `Processing` fallback; graph inputs and widget values are never used.
WebSocket events remain timely but incomplete: cached runs may omit them, and a terminal event may
precede history persistence. `/history/{prompt_id}` is therefore terminal/recovery truth and clears
the active snapshot on every terminal outcome. Polling, queue checks, interruption, cancellation,
and error interpretation continue through the pinned adapter even if the user changes the selector.

### Cached completion estimates

The estimator learns passively from successful, history-backed terminal generations. Timing
feature version 2 includes the execution instance, source revision, checkpoint, exact resolution,
performance controls, output selection, preset, and coarse prompt-length band. Prompt text, seeds,
user identity, upload content, graphs, and results are never stored in profiles. Changing the feature
version resets the bounded audit cursor and rebuilds statistics from retained successful generations;
no generation records or database schema are changed. Until rebuilt, missing evidence yields a
broader available estimate or no ETA.

Successful completions immediately enter a bounded in-memory observation window, including during
continuous generation. Durable profiles are maintained only while the queue is idle, with bounded
batches, time/lock budgets, independent total/landmark quotas, and recent sample windows. The audit's
completion-time/ID watermark removes already-incorporated live observations, so the same success
cannot train an estimate twice. Progress estimation never scans historical generation rows or
submits synthetic work.

The priority ladder is: matching same-run completions; exact historical node landmarks; exact
historical totals; checkpoint history; otherwise-compatible same-run completions from other
checkpoints; broader revision/resolution, revision, source, and instance history. One matching
sibling is usable immediately, and subsequent matching completions contribute to the robust median.
Cross-checkpoint sibling evidence is capped at low confidence and never overrides checkpoint
history. Failed, cancelled, and interrupted generations contribute no completion durations.

Run membership alone does not imply compatibility: an activity run can contain different settings
or runtimes. The worker retains feature-tagged durations, filters matching versus compatible
checkpoint evidence at lookup, and restores a bounded recent set using the same rules after restart.
Restoration fetches only timing-relevant fields, never compiled graphs, raw histories, or results.

Each generation attempt has a bounded cached completion deadline. Repeated updates in the same
node/decile age that deadline; a gap in landmark coverage cannot replace it with a broader estimate.
New matching successes or newly observed landmarks may revise it. Stale or repeated landmarks do
not restart it. Overruns retain the expired deadline instead of repeatedly allocating more time.
Saved progress preserves the deadline after restart; requeue starts a new attempt, and terminal
completion clears attempt state. Node fractions remain local to their nodes and are never treated
as whole-workflow percentages.

The existing `progress.eta` object carries remaining seconds and bounds as of `updated_at`, a
completion timestamp, confidence, and basis. The timestamp remains in the past after expiry while
remaining seconds clamp to zero. Cards and slideshow use the same countdown functions, displaying
“Taking longer than expected” until fresh evidence supports a revised deadline or completion arrives.
The slideshow selects the earliest estimate among the current gallery's active generations.
HTTP Date responses calibrate browser/server clock skew; each attempt freezes its clock mapping,
so delayed events and reconnect replay cannot move an unchanged server deadline. A fallback anchor
from the first ETA is used when HTTP clock calibration is unavailable. No timer-only server events
are needed; the browser updates the visible countdown every second.

## Result normalization and files

History normalization persists the complete bounded JSON-safe entry and raw ComfyUI status/error messages. The owner-facing API removes top-level submitted `prompt` and `extra_data` graph envelopes but leaves actual node results, arbitrary JSON-safe custom UI fields, publisher metadata, status/messages/errors, and execution metadata intact. For each native output node:

- A connected publisher's top-level list-shaped `comfyui_image_frontend` metadata is matched to its private manifest declaration. The namespaced `artifacts` list is authoritative for public ID, role, kind, cardinality, description, and batch order; mirrored `images` remain in raw history and are not counted again.
- Every nonpublisher result is copied untouched into the node-keyed `unmapped_outputs` map, whether or not its node appears in `interface.native_outputs`.
- Publisher metadata naming an undeclared ID or disagreeing with its frozen binding produces a retained result error.

Every valid logical file reference from declared and unmapped nodes remains represented in normalized history, including repeated locators and every batch member. Only the native `filename`, `subfolder`, and type (`input`, `output`, `temp`) tuple is used for retrieval. The job's pinned adapter fetches `/view` within its byte cap. `AssetStore` first writes an application-owned original/thumbnail; a later semantic stage then removes the older application files. Success retains only the authored final batch, while cancellation or failure retains one best available image. An optional retrieval failure leaves the logical reference in result data and records a warning. Public artifact routes use opaque IDs and owner authorization.

The detail API returns declared outputs in manifest order and joins the compact retained artifact set back to its logical `output_id`/`batch_index`. Pruned references remain metadata-only. A physical locator may be downloaded once as an optimization, but logical references are never deduplicated from normalized or raw result structures.

The gallery selects the authored final image for compact display when available. Earlier-stage binary files are pruned without rewriting raw history. Failure or interruption retains one useful partial image without marking it as a successful canonical final.

Once all successfully archived references for a terminal job are safe locally, the adapter batches their `output`/`temp` locators to the companion `POST /comfyui-image-frontend/artifacts/delete` route. The route resolves only beneath ComfyUI's configured output/temp roots and treats missing files idempotently. Cleanup failure never sacrifices the local result: it is recorded as a visible warning and retried during startup recovery. Historical terminal rows are compacted and offered for source cleanup when the new policy first starts.

Generation summary/detail returns the pinned execution ID and label together with source revision, prompt ID, requested/effective parameters, resolved seed strings, ordered declared outputs, untouched node-keyed unmapped outputs, graph-envelope-safe raw history, warnings/errors, ComfyUI status, artifacts, and durable events.

## Restart, refresh, and outage behavior

Startup migrations and local administrator bootstrap remain authoritative and complete before HTTP service begins. ComfyUI publication discovery is then retained as a managed background task: durable current source rows are immediately visible in a loading/cached state, while login, account routes, retained gallery history, and local health remain serviceable. A last-valid frozen graph is dispatchable once its recorded execution runtime is healthy; loading without a validated cached source is not. The task is coordinated with health-recovery refresh through the registry lock, observed for failures, and cancelled/joined during shutdown. Source readiness progresses through loading/online or cached-offline/unavailable state.

Queued rows resume after restart on their persisted execution instances. For dispatching/running/cancel-requested rows, recovery selects the same adapter before checking prompt ID, history, and that runtime's queue. Known active prompts resume monitoring; terminal history finalizes them; an irreconcilable outcome after the configured grace interval becomes explicit interrupted history. Existing artifacts, raw results, source identity, execution label, and recall data remain. Operators should keep an instance ID configured until its accepted jobs are terminal; removing it cannot safely redirect those jobs.

ComfyUI failure before submission returns a claimed item to that instance's queue; its independent health polling later resumes dispatch. An outage on one runtime does not make another runtime consume its queue. Browser disconnects never alter queue state. A selected revision that was republished fails with `source_republished` so the user reviews the new interface.

Health monitoring probes every configured adapter and stores one `comfyui_instance_health` row per ID. When the default catalog instance moves from offline to online, it reruns full source discovery before normal catalog operation continues. This recovers both a cached catalog and an empty catalog from an offline startup without requiring administrator action. A continuously online default changes its catalog only at startup or explicit refresh, avoiding periodic refetch/race churn. Secondary recovery changes only its execution availability.

## Responsiveness and diagnostic boundaries

The authenticated browser renders the gallery shell immediately after the authoritative session request. Preferences, cached service health, configured ComfyUI instance status, retained history, Prompt Assistant status, speech-to-text status, and source catalog/detail requests settle independently. Safe-method startup requests have explicit named deadlines covering both response headers and body consumption; mutation requests are never automatically retried or given a generic deadline because an ambiguous response could duplicate work. Generation stays disabled until the selected execution instance is available and the selected source revision is authoritative.

Normal bootstrap performs no live ComfyUI or Ollama probe. External probing belongs to the bounded background health/discovery loops. Prompt Assistant status reads a recent `service_health` row and treats an old success as stale; composition still performs authoritative runtime checks. Generate transport uses bounded retry only for transient HTTP statuses, connection failures, and malformed JSON. Separately, the composition layer recognizes a schema-incomplete `done_reason: length` response as generated-token exhaustion because Ollama shares `num_predict` between thinking and final output. It escalates `2048 → 4096 → 8192` without changing thinking, schema, instruction, temperature, or candidate seed. When thinking is enabled and all three escalations end in `length`, the candidate makes one extra attempt with `think: false` at the base `2048` allowance, reusing its seed and temperature; the fallback prompt still passes the same distinctness validation. A candidate that ends its thinking schedule and fallback with no usable prompt also advances to the next seed and higher temperature, so the terminal budget error fires only after every candidate is exhausted. Only a complete normalized unchanged Refine candidate, Create-direction echo, or excluded Create duplicate advances a distinctness candidate on the validation path; the next candidate receives a new seed and higher temperature. Safe structured logs and failed-run diagnostics retain the operation, failure class, attempts, thinking mode, model/status, output-field presence and lengths, done reason, validation stage, and output-budget metadata without prompt, Creative Direction, or raw reasoning content; non-retryable rejection and terminal timeout/transport/JSON/budget failures remain distinct.

Gallery pages use an explicit scalar projection rather than materializing `Generation` entities. Image counts, display-artifact precedence, favorites, exact-current revision availability, and dependency health are resolved in bounded batch queries. Compiled/submitted graphs, raw history, result diagnostics, and full workflow-profile documents are not selected or JSON-deserialized for cards. Gallery pages use a low constant number of SQL statements independent of page size. Owner collection lists use one aggregate for all direct generation counts and one windowed preview query for all newest direct thumbnails, plus one batched favorite-membership query, avoiding per-collection reads. A nonempty collection list uses four statements. Favorite membership is resolved in the same batched queries, so no separate feed statement budget exists.

Pillow decode/verification, decompressed pixel loading, thumbnail encoding, hashing, and durable filesystem writes run outside the asyncio event loop. Their database ownership transactions use fresh short-lived sessions and clean unowned files on failure or cancellation. Publication validation and catalog commits likewise run off the event loop; SQLite sessions are never passed into those worker threads.

Authenticated routes that wait on external services explicitly release their authentication transaction before the wait, and authenticated file routes close their metadata session before streaming begins. The SSE route authenticates inside a short local database scope before constructing `StreamingResponse`. Its iterator subscribes to the owner broker before materializing replay in a second short scope, then deduplicates queued durable events through the replay high-water mark. It retains only primitive owner/token/event data. Periodic session validation opens a fresh short-lived session, so an idle browser tab does not retain a pool checkout.

Every HTTP request emits one `http_request_completed` structured log with request ID, method, normalized route, status, monotonic total duration, and disconnect state. Query strings, cookies, CSRF values, request bodies, prompts, filenames, and other content are excluded. `X-Request-ID` correlates a response with its record, while `Server-Timing` exposes only a safe time-to-first-byte measurement. SSE produces one completion record per connection, not one record per event.

## Frontend architecture

The production frontend uses browser-native modules:

- `api.mjs`: same-origin JSON/multipart and CSRF handling.
- `lib.mjs`: source-input ordering/defaults/validation, finite-choice reconciliation, seed-safe serialization, recall/state and collection-tree helpers.
- `render.mjs`: escaped semantic HTML for source-driven controls, collection navigation/tiles, cards, detail, warnings and service states.
- `gallery-hover.mjs`: shared intentional-hover timing and hover/focus preservation across card redraws.
- `app.mjs`: state transitions, collection hash routing/CRUD, source selection/revision refresh, submission, pagination, SSE and administration.
- `styles.css`: design tokens, control geometry, responsive layout, focus and reduced-motion behavior.

The selected source's `interface.inputs` is the only workflow-control schema. Non-advanced controls render before a disclosed Advanced group. GPU image and CPU prompt services are assigned by the environment. `/api/comfyui-instances` reports these fixed assignments and health; the UI has no runtime selectors. Saved preferences and recall cannot change routing. Field errors map to public IDs. Warning-only and last-valid cached sources remain usable; loading without a validated cache, unavailable and empty catalogs disable submission with distinct explanations. Cached accepted publications can queue while their assigned service is offline. Automatic Prompt Assistant composition has one in-flight cycle and at most three frontend attempts with bounded exponential backoff. Prompt preparation may run while images remain pending; image submission retains the account-wide pending-generation gate. Full batch acceptance consumes the prepared composition and schedules the next one without awaiting the activity refresh. At most one prepared prompt is held in memory and shown in the editable Prompt field. Its retry key includes source/revision, a preparation version, and the Prompt Assistant fingerprint; input edits and automation/session changes invalidate stale timers and responses. Quantity and resolution changes reuse a valid prompt. Partial acceptance suppresses prefetch while accepted jobs remain pending. Terminal failure unchecks Auto-generate and exposes a persistent accessible paused state with an explicit retry action.

The gallery is a folder-style collection space. `#/` displays direct top-level collection tiles plus
unfiled generations; `#/c/<id>` displays direct children and generations of one collection. Each
generation is accepted with the collection ID captured at submission start, so later navigation
cannot misfile an in-flight request. Navigation clears the current page and restarts the same
newest-first keyset pagination under the selected scope. Collection ancestry and subtree displays
are derived from the owner-scoped flat list, while server checks remain authoritative for ownership,
cycles, and the five-level limit. Collection tiles follow the gallery scale with square preview
areas; each collection's preview switch optimistically hides its images. Folder captions always
show the name and direct generation count. Image cards have no caption or footer space.

Both card types reveal their bottom-right actions after the pointer settles for 450 ms, allowing
6 px of movement. Image checkpoint names and completed batch counts appear at top left and top
right. A dark gradient scrim fades in with the overlays over 120 ms; leaving for 150 ms hides
them. Scrolling, dragging, and leaving before the delay cancel pending hover intent. Keyboard
focus reveals controls immediately, while touch/non-hover devices keep them visible with larger
targets. Small image cards wrap their actions into two rows; very short frames reserve enough
height for the overlays while fitting the original image without cropping. Status pills sit 10 px
from the image frame's left and bottom edges, with actions stacked above them when present.
Active progress, errors, and cancel
remain visible, and an Info button opens details containing generation duration. Gallery redraws
preserve hover intent and keyboard focus for the same card.

The toolbar's generation activity indicator sits between Gallery scale and the account menu.
It uses resolved jobs / planned jobs, displays outcome counts on hover or focus, shows
completion for five seconds, and then hides. Auto-generation replaces the percentage
with active/preparing/waiting/retrying/paused state; its scheduling remains local to the
browser tab. Enabling the switch pins auto-generation to the collection currently on
screen (Home when enabled from Home), so the user can browse elsewhere while
new images keep landing in that folder; re-enabling or retrying recaptures the current
collection, and a pin to a deleted folder falls back to the collection on screen. Manual
generation always follows the collection on screen. The indicator's hover/focus tooltip
names the collection auto-generation is targeting. Folder count badges add animated
remaining counts including descendants, while the original count still describes direct
contents. Reduced-motion preferences stop the animation.

A compact owner-scoped activity snapshot supplies both indicators without reading card
or workflow JSON. The browser refreshes it on lifecycle events (coalesced), SSE connection,
folder navigation/mutations, and the ten-second service poll. Updates patch badges without
replacing folder cards or disrupting focus. Node-progress ticks do not refetch aggregates. Activity reads run one at a time; event-driven generation detail reads coalesce by ID with at most three in flight, preventing replay bursts from exhausting the database pool.
The snapshot also prevents auto-generation from overlooking jobs outside the loaded gallery.
`generation_runs` and `generation_run_members` preserve original totals and deleted outcomes.
Single submissions and atomic checkpoint batches acquire a database write lock before
selecting the current run, so overlapping tabs append to one run. Batch savepoints retain
per-item validation errors while committing the full plan in one transaction. The worker
continues to execute ordinary generations on their existing pinned runtimes.

The gallery keeps one object/card per generation and displays its snapshotted execution label in status/history. Changing the current selector never changes existing cards or active-job routing. SSE replaces only the affected durable state and drops a refreshed card when its `collection_id` does not match the open view. The same rule keeps auto-generated cards filed into their pinned collection invisible while the user browses a different one; they appear on return to that collection. Collection CRUD adds no SSE event in v1: the initiating tab refetches collections after its mutation, while another tab converges on navigation or reload. A terminal generation does not trigger a per-card collection-list refetch, so preview thumbnails may remain stale until that same navigation/reload boundary. Cursor pagination limits DOM growth; thumbnails are lazy while detail exposes every retained result and technical provenance. Favorites are a session-wide view filter, not a location: the toolbar button is an aria-pressed toggle whose filled heart marks the on state, and the open gallery shows only favorited generation cards and folder tiles among the view's direct contents, hiding prompt groups with no favorites. It uses the same scale, sentinel, image viewer, recall, move, and deletion controls; unfavoriting removes the item immediately while the filter is on. The filter persists across collection navigation and resets on reload and re-login; no route or synthetic collection exists, so new generations are never assigned a synthetic collection. Recall reports whether the historical runtime is still configured and available before restoring the selector; it never submits automatically.

## Compatibility and migration

Migration `b84f2d6a91c3_add_published_source_results.py` extends existing tables instead of rewriting history. Legacy embedded-contract profiles cease to be current after successful publication discovery, but their generation rows and files remain readable/deletable. New publication revisions coexist immutably so in-flight jobs and exact recall retain the revision they accepted.

Migration `b1e7c4a92d60_add_collections.py` is additive. Historical generations remain unfiled,
collection previews default on for existing preference rows, and no workflow publication,
compilation, scheduling, or result-normalization contract changes.

The retired two-file/node-embedded design is not a fallback discovery path. Compatibility fields have a bounded purpose: old stored data and a transitioning frontend, never acceptance of arbitrary or stale graphs. See [`published-workflows.md`](published-workflows.md) for the retirement policy.

## Graceful shutdown

Uvicorn gives open request tasks 10 seconds to finish, then cancels any that remain so an SSE or stalled external request cannot indefinitely delay FastAPI lifespan cleanup. FastAPI lifespan stops new claims, signals worker loops, waits for active monitors to finish/cancel, closes external clients, and disposes SQLite. The example Compose service provides a 30-second container grace period, and the update script defers to that value unless `CIF_UPDATE_STOP_TIMEOUT` explicitly overrides it. Already committed queue rows and prompt IDs remain recoverable at the next start.
