# Database and storage

## Migration policy

Alembic migrations live in `backend/alembic/versions`. Startup runs `upgrade head` under a process migration lock before bootstrap or worker startup. Migration tests exercise `base → head → base → head` against a temporary SQLite database.

Publication integration is additive. Migration `b84f2d6a91c3_add_published_source_results.py` does not rewrite or delete historical generations:

- `workflow_profiles` gains ComfyUI instance/source identity, publication UUID/schema/time, manifest hash, warnings, readiness, and source/revision indexes.
- `generations` gains source identity plus complete raw/normalized result JSON.
- New JSON columns use empty object/array defaults so old rows remain readable.
- Legacy profile identity/graph/contract columns remain because generations reference immutable profile rows and historical APIs still project them.

A successful authoritative publication refresh marks embedded-contract profiles non-current/stale; it does not delete their rows or application assets. Old generations remain viewable and deletable. Exact recall is available only if a matching current published revision exists and recompiles identically.

## Main tables

| Table | Ownership and purpose |
|---|---|
| `users` | Local account, role, forced-change state, session epoch |
| `sessions` | HMAC token ID, CSRF, expiry/revocation, privacy-safe client metadata |
| `login_throttles` | Username/IP-keyed attempt windows and temporary blocks |
| `user_preferences` | Owner gallery scale |
| `workflow_profiles` | Immutable accepted publication revisions plus retained legacy snapshots |
| `workflow_diagnostics` | Safe latest transport/candidate discovery diagnostics |
| `service_health` | Last known ComfyUI/Ollama state and catalog capability summary |
| `uploads` | Owner-scoped application source/mask metadata |
| `generations` | Immutable accepted request/source/graph plus lifecycle and complete results |
| `favorites` | Owner bookmark linking one owned generation |
| `generation_uploads` | Historical parameter-to-upload/hash links |
| `prompt_assistant_runs` | Owner-scoped Ollama input/output/provenance, optionally linked to a generation |
| `artifacts` | Every retained image/file batch member with owner-mediated URLs and presentation state |
| `generation_events` | Durable owner event timeline and SSE replay source |
| `scheduler_state` / `app_locks` | Fair queue cursor/sequence and SQLite coordination |
| `audit_logs` | Non-content actor/target/action records for account/destructive operations |

## Published source rows

For publication-based rows, `workflow_profiles` stores:

- logical identity: `instance_id`, opaque `source_key`, private `source_id`;
- revision identity: `publication_id`, the manifest-recorded editable-workflow SHA-256, exact verified API/manifest SHA-256 values, schema and publication time;
- immutable parsed snapshots: the accepted editable document plus its separately observed hash, frozen API graph, full manifest, and private resolved interface/runtime data;
- readiness, warning list, state/current flag, validation/last-seen times.

`identity_key` distinguishes immutable published revisions. Republishing the same logical source creates or reactivates the exact matching revision and marks a different prior current revision stale only after complete validation. Current editable-byte drift does not change that identity: it refreshes readiness/warning metadata while the stored frozen API and manifest snapshot remain immutable. A rejected replacement does not partially update the accepted row.

Private `source_id`, manifest, graph, bindings, and dependencies never appear in the ordinary public source API. Keeping them in the trusted database allows compilation without refetching a mutable server artifact for each request.

## Generation snapshots and rich results

At acceptance, a generation stores its profile foreign key, display/compatibility identity fields, resolved interface, requested/effective parameter maps, seed map, final prompt, compiled graph/hash, and a compact `generation_source_json` with:

```text
source_key, instance_id, publication_id,
workflow_sha256, api_sha256, manifest_sha256
```

The source snapshot prevents later republishing from changing an in-flight or historical record. Seed values are stored as decimal strings in the public/effective maps to preserve integers beyond JavaScript's safe range.

After ComfyUI history reconciliation, these columns retain the result without flattening it into one image:

| Column | Contents |
|---|---|
| `raw_history_json` | Complete bounded JSON-safe server-side history entry; API projection removes top-level prompt/extra-data graphs |
| `declared_outputs_json` | CIF publisher declarations keyed internally by public output ID, with cardinality and authoritative ordered logical batch references |
| `unmapped_outputs_json` | Every nonpublisher native result copied untouched and keyed by node ID |
| `result_warnings_json` | Publication and normalization warnings |
| `result_errors_json` | Safe execution/publisher/normalization errors |
| `comfyui_status_json` | Native bounded status/error metadata |

The existing `comfyui_prompt_id` stores the native prompt ID. `artifacts` remains the retrievable binary index: every successfully archived image reference in declared and unmapped results has its own row, including batch siblings. Logical publisher references remain in `declared_outputs_json` even when `/view` retrieval fails, so normalization is not reduced to the set of locally stored binaries. `canonical` / `best_available` and generation artifact pointers remain presentation/legacy lifecycle aids; they do not rewrite the declared/unmapped/raw result structures.

## Files and deletion

Uploads, original artifacts, and thumbnails are normal files, not database blobs. Paths are relative to the configured data root and filenames are opaque. Every open/delete resolves the target and rejects paths outside the root.

Removing a favorite deletes only its bookmark. Generation deletion removes exclusive generation rows/files and deletes an upload only when no retained generation references it. User deletion revokes sessions, reconciles active jobs, collects paths, deletes all owner rows, commits, then deletes application files. Neither operation purges ComfyUI userdata/history/storage or changes published workflows.

## Time, indexes, and operations

UTC timestamps are returned as timezone-aware ISO values. Indexes cover owner/newest pagination, queue status/order, native prompt ID recovery, artifact timelines, events, sessions, and publication instance/source/revision lookup.

Gallery and favorites reads use explicit scalar projections and batched auxiliary queries. Detail-only JSON columns such as compiled/submitted graphs, raw history, diagnostics, and normalized result documents are not transferred to or deserialized by Python for gallery cards. Expected dimensions and source identifiers are extracted in SQLite, while display artifacts, image counts, favorite membership, exact-current revision availability, and dependency status are resolved once per page rather than once per generation.

Long-lived SSE iterators never own a SQLAlchemy session. Authentication finishes in one short scope, then the iterator subscribes before loading replay in another short scope so connection setup cannot lose a durable event; queued events through the replay high-water mark are deduplicated. Periodic authorization checks likewise create and close a fresh session. CPU-heavy image work and durable filesystem writes run in worker threads; their short metadata transactions open thread-confined sessions only after the file operation completes.

Run one application instance against one SQLite file. Keep database and files on a reliable local persistent volume. Back up the entire data directory while the service is stopped; restoring only `app.db` or only media can create dangling metadata. ComfyUI publication bundles are external and require a separate server backup policy.
