# Architecture

## Scope and invariants

This is one application container for a small trusted network, not a public multi-tenant platform. It uses one FastAPI process, one SQLite database, application-owned files, and in-process asynchronous workers. There is no broker, separate database, workflow editor, model installer, ComfyUI filesystem mount, or direct browser connection to ComfyUI, Ollama, or the speech-to-text service.

The principal invariants are:

1. A generation source exists only after a complete three-file ComfyUI publication passes strict validation.
2. The browser receives only an allowlisted public interface and submits only a stable source key/revision plus public parameters.
3. Cached frozen graphs are immutable; compilation deep-clones and patches only trusted manifest bindings per request.
4. Every accepted generation and its exact source revision are durably recorded before dispatch.
5. Terminal `/history/{prompt_id}` is preserved as the source of truth; no native output node or batch member is discarded.
6. A gallery display image is a presentation aid, not an invented contract-declared final output.
7. Every content query and file response is scoped to the authenticated owner; administrator role is not a content bypass.
8. Accepted executions are immutable snapshots. Republishing or form edits affect only new requests.

## Process layout

`app.main.create_app` creates an `AppContainer` whose services divide responsibility:

- `Database`: SQLAlchemy sessions, SQLite pragmas, migration startup.
- `AuthService`: bootstrap, Argon2id credentials, throttling, revocable sessions, account operations.
- `AssetStore`: safe image decode, application paths, hashes, thumbnails, atomic file operations.
- `ComfyUIAdapter`: bounded HTTP/WebSocket transport, userdata route probing, `Comfy-User`, prompt/history/output operations.
- `WorkflowRegistry`: publication listing, validation, immutable revision catalog, last-valid caching, diagnostics.
- `WorkflowCompiler`: public parameter validation/defaults, exact seed resolution, request-local graph clone/bindings/hash.
- `GenerationService`: owner-scoped API projection, acceptance transaction, recall, cancellation/deletion.
- `QueueWorker`: durable fair claim, submission, WebSocket/history monitoring, output normalization/archive, recovery.
- `OllamaAdapter`: router availability validation, model-free non-thinking structured composition, and effective-model provenance.
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

## Published-source discovery

At startup, administrator refresh, and an offline-to-online health transition, `WorkflowRegistry` performs this network-only pipeline. Health monitoring does not refetch bundles periodically while ComfyUI remains online:

1. Probe ComfyUI and retrieve bounded `/object_info` capability data.
2. Recursively list the configured userdata namespace, preferring `/v2/userdata?path=workflows` and falling back to `/userdata?dir=workflows&recurse=true&full_info=true`.
3. Filter safe normalized `.interface.json` paths.
4. Retrieve each manifest and adjacent `<stem>.json` / `<stem>.api.json`; nested paths are encoded whole as one route segment.
5. Parse strict UTF-8 JSON; strictly verify the frozen API raw-byte hash; compare editable bytes for warning-only drift; and validate publication/interface schemas, path/stem/source agreement, API graph node count/shape, public IDs/types/defaults/ranges/steps, finite choice values/labels/hints, one positive prompt, trusted bindings, dependencies, warnings, and runtime flags.
6. Match all declared class types against `/object_info`.
7. Atomically publish each complete accepted revision and safe diagnostic.

The optional `Comfy-User` header is applied consistently to the relevant HTTP and WebSocket traffic. Listing, manifest, workflow, API, object-info, history, and output responses have separate byte caps.

`source_key` is stable for one configured `instance_id + source_id`. The immutable revision consists of the publication UUID and manifest-recorded workflow hash plus exact verified API and manifest hashes. Current editable-workflow drift is liveness metadata: it produces `ready_with_warnings`, not rejection, and does not alter the frozen executable snapshot or revision identity. A bad frozen republish cannot replace its last accepted revision. One rejected candidate cannot remove independent valid sources. A transport/listing failure retains the last valid catalog as cached/offline; a successful authoritative listing retires disappeared sources and old embedded-contract profiles. Missing dependencies produce an unavailable catalog record.

Ordinary source APIs contain display name, stable key, instance identity, readiness/cached/availability, warnings, revision, and public interface inputs/outputs. They omit source path, graph, bindings, node IDs, instance UUIDs, and dependencies.

## Request acceptance and compilation

Canonical generation input is `{source_key, revision?, parameters, prompt_assistant_run_id?}`. Acceptance requires:

- current source resolution and optional exact revision check;
- rejection of unknown public IDs and legacy graph/binding/path injection;
- required/default/type/range/step validation;
- finite-choice membership and deterministic companion-strength resolution;
- canonical decimal parsing and request-local random resolution for seeds;
- owner validation for any linked Prompt Assistant run;
- deep clone of the accepted frozen API graph;
- patching every private manifest binding for each effective public input;
- verification that the cached graph remained byte-for-byte/logically unchanged;
- positive-prompt extraction and compiled graph SHA-256.

Seed values remain decimal strings in public/effective state so values beyond JavaScript's safe integer range round-trip exactly; the cloned graph receives the validated integer. Choice values remain stable public IDs throughout application state. Only the trusted choice declaration's prompt-local `value` is patched; private `options_json` mappings and downstream model/file inputs remain frozen and server-side.

One transaction inserts the generation, queue sequence, source revision snapshot, requested/effective parameters, resolved seeds, compiled graph/hash, Prompt Assistant linkage, and initial durable event. Only then does the API return a card.

Temporary legacy request/response aliases are isolated at the schema/service boundary. They resolve only to a current validated publication and cannot re-enable embedded-contract discovery.

## Scheduling and ComfyUI submission

The worker maintains up to `CIF_COMFYUI_CONCURRENCY` active jobs. A SQLite lock row serializes fair selection: oldest queued item per user, round-robin across owners, preserving each owner's FIFO order.

Before `/prompt`, the worker reuses the generation's immutable compiled graph. When the accepted publication runtime flag requests it, the accepted editable snapshot is attached only as `extra_data.extra_pnginfo.workflow`; its observed hash is tracked separately when it differs from the publication record. Submission uses a request-specific client ID. The returned native `prompt_id` is persisted before monitoring.

The monitor combines WebSocket progress with bounded history polling/retry. WebSocket events are timely but incomplete: cached runs may omit them, and a terminal event may precede history persistence. `/history/{prompt_id}` is therefore terminal/recovery truth.

## Result normalization and files

History normalization persists the complete bounded JSON-safe entry and raw ComfyUI status/error messages. The owner-facing API removes top-level submitted `prompt` and `extra_data` graph envelopes but leaves actual node results, arbitrary JSON-safe custom UI fields, publisher metadata, status/messages/errors, and execution metadata intact. For each native output node:

- A connected publisher's top-level list-shaped `comfyui_image_frontend` metadata is matched to its private manifest declaration. The namespaced `artifacts` list is authoritative for public ID, role, kind, cardinality, description, and batch order; mirrored `images` remain in raw history and are not counted again.
- Every nonpublisher result is copied untouched into the node-keyed `unmapped_outputs` map, whether or not its node appears in `interface.native_outputs`.
- Publisher metadata naming an undeclared ID or disagreeing with its frozen binding produces a retained result error.

Every valid logical file reference from declared and unmapped nodes is retained, including repeated locators and every batch member. Only the native `filename`, `subfolder`, and type (`input`, `output`, `temp`) tuple is used for retrieval. The adapter fetches `/view` within its byte cap; `AssetStore` archives application-owned originals/thumbnails. An optional retrieval failure leaves the logical reference in result data and records a warning. Public artifact routes use opaque IDs and owner authorization.

The detail API returns declared outputs in manifest order and joins archived artifact summaries back to their logical `output_id`/`batch_index`. The visual hierarchy is final, previews/prototypes, comparisons, auxiliary publishers, then additional native output. A physical locator may be downloaded once as an optimization, but logical references are never deduplicated from normalized or raw result structures.

The gallery selects the authored final image for compact display when available. That selection does not remove siblings, rewrite raw history, or discard earlier/native outputs. Failure or interruption retains useful partial images without marking them as a successful canonical final.

Generation detail returns source revision, prompt ID, requested/effective parameters, resolved seed strings, ordered declared outputs, untouched node-keyed unmapped outputs, graph-envelope-safe raw history, warnings/errors, ComfyUI status, artifacts, and durable events.

## Restart, refresh, and outage behavior

Startup migrations and bootstrap precede workers. Durable current source rows load before network discovery completes. Source readiness progresses through loading/online or cached-offline/unavailable state.

Queued rows resume after restart. For dispatching/running/cancel-requested rows, recovery checks prompt ID, history, and queue state. Known active prompts resume monitoring; terminal history finalizes them; an irreconcilable outcome after the configured grace interval becomes explicit interrupted history. Existing artifacts, raw results, source identity, and recall data remain.

ComfyUI failure before submission returns a claimed item to queued; health polling later resumes dispatch. Browser disconnects never alter queue state. A selected revision that was republished fails with `source_republished` so the user reviews the new interface.

When health monitoring sees ComfyUI move from offline to online, it reruns full source discovery before normal operation continues. This recovers both a cached catalog and an empty catalog from an offline startup without requiring administrator action. A continuously online instance changes its catalog only at startup or explicit refresh, avoiding periodic refetch/race churn.

## Frontend architecture

The production frontend uses browser-native modules:

- `api.mjs`: same-origin JSON/multipart and CSRF handling.
- `lib.mjs`: source-input ordering/defaults/validation, finite-choice reconciliation, seed-safe serialization, recall/state helpers.
- `render.mjs`: escaped semantic HTML for source-driven controls, cards, detail, warnings and service states.
- `app.mjs`: state transitions, source selection/revision refresh, submission, pagination, SSE and administration.
- `styles.css`: design tokens, control geometry, responsive layout, focus and reduced-motion behavior.

The selected source's `interface.inputs` is the only control schema. Non-advanced controls render before a disclosed Advanced group. Field errors map to public IDs. Warning-only sources remain usable; loading, cached/offline, unavailable and empty catalogs disable submission with distinct explanations.

The gallery keeps one object/card per generation. SSE replaces only the affected durable state. Cursor pagination limits DOM growth; thumbnails are lazy while detail exposes every retained result and technical provenance.

## Compatibility and migration

Migration `b84f2d6a91c3_add_published_source_results.py` extends existing tables instead of rewriting history. Legacy embedded-contract profiles cease to be current after successful publication discovery, but their generation rows and files remain readable/deletable. New publication revisions coexist immutably so in-flight jobs and exact recall retain the revision they accepted.

The retired two-file/node-embedded design is not a fallback discovery path. Compatibility fields have a bounded purpose: old stored data and a transitioning frontend, never acceptance of arbitrary or stale graphs. See [`published-workflows.md`](published-workflows.md) for the retirement policy.

## Graceful shutdown

FastAPI lifespan stops new claims, signals worker loops, waits for active monitors to finish/cancel, closes external clients, and disposes SQLite. Already committed queue rows and prompt IDs remain recoverable at the next start.
