# Database and storage

## Migration policy

Alembic migrations live in `backend/alembic/versions`. Startup runs `upgrade head` under a process migration lock before bootstrap or worker startup. Migration tests exercise `base → head → base → head` against a temporary SQLite database.

Publication integration is additive. Migration `b84f2d6a91c3_add_published_source_results.py` does not rewrite or delete historical generations:

- `workflow_profiles` gains ComfyUI instance/source identity, publication UUID/schema/time, manifest hash, warnings, readiness, and source/revision indexes.
- `generations` gains source identity plus complete raw/normalized result JSON.
- New JSON columns use empty object/array defaults so old rows remain readable.
- Legacy profile identity/graph/contract columns remain because generations reference immutable profile rows and historical APIs still project them.

A successful authoritative publication refresh marks embedded-contract profiles non-current/stale; it does not delete their rows or application assets. Old generations remain viewable and deletable. Exact recall is available only if a matching current published revision exists and recompiles identically.

Migration `4f2a8c1d9e70_add_generation_progress_snapshot.py` adds nullable
`generations.progress_json`. Existing and terminal generations remain null. While a request is
active, the column contains only the latest safe node-local progress snapshot and, when historical
evidence is sufficient, its nested cached ETA. It is cleared when the request is requeued or reaches
its history-backed terminal state.

Migration `a8d4e6f2c901_add_generation_timing_profiles.py` adds bounded timing profiles, a small
versioned audit cursor, and the successful-run audit index. It performs no synchronous history
backfill; the estimator incorporates eligible legacy rows later in idle-only batches.

Migration `d72f6a8c9e10_add_comfyui_execution_instances.py` adds independent execution routing without rewriting publication identity. It creates `comfyui_instance_health`, adds non-null `generations.comfyui_instance_id` and `generations.comfyui_instance_label`, and adds the instance/status/queue index. Existing rows derive the ID from the retained generation source, then workflow profile, then the legacy `default` fallback; their initial display label is that ID. The migration does not contact, restart, or mutate ComfyUI.

Migration `82bc14d6e9a0_record_prompt_instructions.py` adds nullable `prompt_assistant_runs.instructions`. Successful compositions store the effective pre-processor instructions and expose them on recall. Historical and failed rows retain `null`; raw custom instructions are excluded from failure diagnostics. Per-mode editing drafts are stored in the browser under the signed-in user's ID, independently of saved composition history.

Migration `f3a91c7d2b64_record_prompt_assistant_thinking.py` adds non-null `prompt_assistant_runs.thinking_enabled`. Historical rows are backfilled to `true`, matching the only behavior available before the focused-editor toggle. New failed runs retain bounded model/status, field-presence, response/thinking-length, done-reason, validation-stage, and output-budget attempt/allowance details in `raw_response_json`. Their prompt and creative-direction columns are left empty, and raw reasoning is never copied into diagnostics, so failed composition content is not retained by default.

Migration `b1e7c4a92d60_add_collections.py` adds the owner-scoped `collections` tree,
nullable `generations.collection_id`, and the per-owner collection-preview preference. Existing
generation rows are not rewritten and therefore remain unfiled at the gallery root. The generation
foreign key uses `SET NULL` as a safety net; normal recursive collection deletion explicitly routes
every contained generation through the established generation deletion lifecycle before removing
collection rows deepest-first.

Migration `2f8d6a1c4b90_add_checkpoint_tiers.py` adds the JSON preference that stores ordered
checkpoint values under each source and public selector ID. Existing users receive an empty object;
the browser reconciles it with the currently published choices and initially places every choice in
Unsorted.

## Main tables

| Table | Ownership and purpose |
|---|---|
| `users` | Local account, role, forced-change state, session epoch |
| `sessions` | HMAC token ID, CSRF, expiry/revocation, privacy-safe client metadata |
| `login_throttles` | Username/IP-keyed attempt windows and temporary blocks |
| `user_preferences` | Owner gallery scale, persisted checkpoint tier/order layout, and retained legacy source rating/color data |
| `workflow_profiles` | Immutable accepted publication revisions plus retained legacy snapshots |
| `workflow_diagnostics` | Safe latest transport/candidate discovery diagnostics |
| `service_health` | Last known ComfyUI/Ollama state and catalog capability summary |
| `comfyui_instance_health` | Last bounded availability result for each configured ComfyUI execution ID |
| `uploads` | Owner-scoped application source/mask metadata |
| `collections` | Owner-scoped, self-referencing gallery collection tree (maximum depth 5) |
| `generations` | Immutable accepted request/source/graph plus lifecycle and complete results |
| `favorites` | Owner bookmark linking one owned generation |
| `collection_favorites` | Owner bookmark linking one owned collection; unique owner/collection pair |
| `generation_uploads` | Historical parameter-to-upload/hash links |
| `prompt_assistant_runs` | Owner-scoped Ollama input/output, thinking mode, safe failure diagnostics, and provenance, optionally linked to a generation |
| `artifacts` | Every retained image/file batch member with owner-mediated URLs and presentation state |
| `generation_events` | Durable owner event timeline and SSE replay source |
| `scheduler_state` / `app_locks` | Fair queue cursor/sequence and SQLite coordination |
| `audit_logs` | Non-content actor/target/action records for account/destructive operations |

## Published source rows

For publication-based rows discovered through the configured default catalog adapter, `workflow_profiles` stores:

- logical identity: `instance_id`, opaque `source_key`, private `source_id`;
- revision identity: `publication_id`, the manifest-recorded editable-workflow SHA-256, exact observed API/manifest SHA-256 values, schema and publication time;
- immutable parsed snapshots: the accepted editable document plus its separately observed hash, observed validated API graph, full manifest (including lossless optional source metadata), and private resolved interface/runtime data;
- readiness, warning list, state/current flag, validation/last-seen times.

`identity_key` distinguishes immutable published revisions. Republishing the same logical source creates or reactivates the exact matching revision and marks a different prior current revision stale only after complete validation. Current editable-byte drift does not change that identity: it refreshes readiness/warning metadata while the stored API and manifest snapshot remain immutable. Observed API-byte drift does change identity because execution uses that exact graph. A rejected replacement does not partially update the accepted row.

Private `source_id`, the raw manifest, graph, bindings, and runtime dependency state never appear wholesale in the ordinary public source API. Only recognized safe generation-source/technical-inventory sections are projected from the retained manifest. Keeping the snapshots in the trusted database allows compilation without refetching a mutable server artifact for each request.

## Generation snapshots and rich results

At acceptance, a generation stores its profile foreign key, nullable owner-validated
`collection_id`, display/compatibility identity fields, resolved interface, requested/effective
parameter maps, seed map, final prompt, compiled graph/hash, selected `comfyui_instance_id`, and a
snapshot of that target's safe `comfyui_instance_label`. It also stores a compact publication
`generation_source_json` with:

```text
source_key, instance_id, publication_id,
workflow_sha256, api_sha256, manifest_sha256
```

The publication `instance_id` inside `generation_source_json` identifies where the source catalog was discovered; it need not equal the selected execution ID. The separate execution columns are the durable adapter-routing key and historical display label. Together with `comfyui_prompt_id`, they ensure recovery, polling, retrieval, and cancellation return to the same independent ComfyUI queue even if a user changes the current selector. Removing an execution ID from deployment configuration never rewrites or redirects old rows. On startup, a never-submitted queued job pinned to a removed ID fails explicitly; an in-flight prompt without its exact adapter is marked interrupted because it cannot be safely recovered, but the application does not send a remote cancellation. The source snapshot prevents later republishing from changing an in-flight or historical record. Seed values are stored as decimal strings in the public/effective maps to preserve integers beyond JavaScript's safe integer range.

After ComfyUI history reconciliation, these columns retain the result without flattening it into one image:

| Column | Contents |
|---|---|
| `raw_history_json` | Complete bounded JSON-safe server-side history entry; API projection removes top-level prompt/extra-data graphs |
| `declared_outputs_json` | CIF publisher declarations keyed internally by public output ID, with cardinality and authoritative ordered logical batch references |
| `unmapped_outputs_json` | Every nonpublisher native result copied untouched and keyed by node ID |
| `result_warnings_json` | Publication and normalization warnings |
| `result_errors_json` | Safe execution/publisher/normalization errors |
| `comfyui_status_json` | Native bounded status/error metadata |
| `progress_json` | Coalesced active current-operation label, safe node identity, counter/fraction, timestamp, and optional nested ETA; never a workflow-wide percentage |

`comfyui_prompt_id` is meaningful only together with `comfyui_instance_id`; native prompt IDs and queues are not merged across instances. `artifacts` is the compact retrievable binary index: advancing stages replace older image rows/files, success keeps the final batch, and cancellation/failure keeps one best image. Logical publisher references remain in `declared_outputs_json` even when their binary was pruned or `/view` retrieval failed, so normalization is not reduced to the locally retained set. `canonical` / `best_available` and generation artifact pointers select that set without rewriting declared/unmapped/raw result structures. `internal_diagnostics_json` durably records transferred ComfyUI source locators and whether their terminal cleanup completed, allowing failed cleanup to retry after restart.

## Completion timing profiles

Completion estimates are learned passively from successful generations after their history-backed
terminal commit. Training uses the already retained source revision, requested/effective technical
controls, resolution, and `started_at`/`completed_at` interval. Cancelled, failed, and interrupted
runs do not become successful-completion samples. Prompt text, seeds, user identity, upload names or
contents, executable graphs, and result payloads are never copied into timing profiles.

The estimator retains bounded, privacy-safe robust profiles and loads them into an in-process lookup
cache. Profiles contain only normalized technical cohort keys, capped timing observations or
statistics, confidence inputs, and maintenance timestamps. Point estimates and intervals are based
on robust distributions so one unusually cold or stalled run cannot dominate later estimates. The
cache may back off from an exact source revision and control cohort to broader compatible cohorts
when evidence is sparse; that fallback is reflected by lower confidence and a safe `basis` string.
Total-duration and progress-landmark rows have independent fixed quotas so high-cardinality node
history cannot evict all useful source fallbacks.

Total-duration profiles exist in several coarsening scopes so sparse cohorts can fall back to
broader ones. Besides the exact source/revision/resolution/control cohort, the estimator folds
successful runs into a checkpoint cohort keyed by instance, source, checkpoint (model-selector)
value, a coarse prompt-size band, and normalized resolution. The prompt contributes only its length
band to that key — never its text — so the cohort is content-free like every other scope, and an
API republish does not invalidate it (revision sensitivity is carried by the broader
revision/resolution scope instead). This checkpoint scope is an additive key namespace: existing
scope keys are byte-identical, so it requires no feature-version bump, no schema migration, and no
re-audit of historical rows.

Normal request acceptance and generation start never scan historical generations or aggregate
timings. They perform only a lookup against the prepared cache. Fresh same-run sibling durations
used to estimate a checkpoint batch are kept in worker memory only — never in the database — so
the progress path stays database-free and the run's evidence disappears with the run. A bounded
legacy audit can seed or repair profiles from older successful rows, but runs only while generation
work is idle and uses scalar lifecycle/source/control projections rather than compiled graphs, raw
history, or results. One versioned completion-time/ID cursor advances transactionally with each
batch; the database does not accumulate a per-generation training marker. Progress-event reads are
capped independently for each generation, and maintenance uses a short time/lock budget plus
cooperative shutdown. Completing a generation clears `progress_json` and its ETA; the successful
lifecycle interval can still be incorporated into a later profile update.

## Files and deletion

Uploads, original artifacts, and thumbnails are normal files, not database blobs. Paths are relative to the configured data root and filenames are opaque. Every open/delete resolves the target and rejects paths outside the root.

Migration `6e4b9c2a7d15_add_collection_favorites.py` revises `2f8d6a1c4b90` and adds UUID bookmarks with `owner_id`, `collection_id`, and `created_at`. Both foreign keys use `ON DELETE CASCADE`. The unique owner/collection constraint enforces binary favorites; `(owner_id, created_at, id)` supports the mixed feed, and a collection index supports target deletion. Collection and user deletion remove their collection bookmarks automatically; generation favorites likewise cascade on generation or user deletion.

Removing a favorite deletes only its bookmark. Generation deletion removes exclusive generation rows/files and deletes an upload only when no retained generation references it. User deletion revokes sessions, reconciles active jobs, collects paths, deletes all owner rows, commits, then deletes application files. Normal terminal reconciliation—not later gallery/user deletion—removes frontend-generated `output`/`temp` files from ComfyUI. Userdata publications and ComfyUI history are unchanged.

Deleting a collection collects its bounded-depth subtree, invokes the same generation deletion
service for every direct or descendant generation, and records one content-free audit row per
collection. Terminal contents and their owned files are removed immediately. Active contents are
marked pending deletion and cancelled; the collection rows can then disappear through the nullable
foreign key without making those pending rows visible at the root, and normal worker reconciliation
finishes their deletion.

## Time, indexes, and operations

UTC timestamps are returned as timezone-aware ISO values. Indexes cover owner/newest pagination,
queue status/order per execution instance, owner/collection/newest gallery pagination,
owner/parent collection traversal, native prompt ID recovery, artifact timelines, events, sessions,
publication instance/source/revision lookup, and bounded successful-run timing maintenance.

Gallery and favorites reads use explicit scalar projections and batched auxiliary queries. Detail-only JSON columns such as compiled/submitted graphs, raw history, diagnostics, and normalized result documents are not transferred to or deserialized by Python for gallery cards. Expected dimensions and source identifiers are extracted in SQLite, while display artifacts, image counts, favorite membership, exact-current revision availability, and dependency status are resolved once per page rather than once per generation. Collection listing likewise resolves all direct generation counts in one aggregate and all preview quadrants in one windowed batch query, independent of collection count. One additional membership query supplies `is_favorite`. The mixed favorites feed pages a tagged `UNION ALL` first, then loads only the selected generation and collection projections in batches.

Long-lived SSE iterators never own a SQLAlchemy session. Authentication finishes in one short scope, then the iterator subscribes before loading replay in another short scope so connection setup cannot lose a durable event; queued events through the replay high-water mark are deduplicated. Periodic authorization checks likewise create and close a fresh session. CPU-heavy image work and durable filesystem writes run in worker threads; their short metadata transactions open thread-confined sessions only after the file operation completes.

Run one application instance against one SQLite file. Keep database and files on a reliable local persistent volume. Back up the entire data directory while the service is stopped; restoring only `app.db` or only media can create dangling metadata. ComfyUI publication bundles are external and require a separate server backup policy.

## Generation runs

`generation_runs` stores an owner-scoped run ID, original total, submission-failure
count, deleted success/failure/cancellation counters, and created/updated timestamps.
`generation_run_members` maps each generation to its run with indexed `run_id`.
Deleting a generation cascades its membership after preserving the terminal outcome;
deleting the owner cascades both run history and memberships. Ordinary completion
is aggregated from durable generation statuses, so worker recovery needs no additional
progress state transitions. Migration `3ab76df901e2` adopts existing active work into
one run per owner. Historical completed generations remain outside new runs.
