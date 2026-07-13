# Database and storage

## Migration policy

Alembic migrations live in `backend/alembic`. Application startup runs `upgrade head` under a process migration lock before bootstrap initialization or worker startup. SQLite's Alembic revision is committed explicitly so replacement/restart does not replay the initial schema.

The migration integration test performs `base → head → base → head` against a temporary database and verifies the revision and core tables.

## Main tables

| Table | Ownership and purpose |
|---|---|
| `users` | Local account, role, forced-change state, session epoch |
| `sessions` | HMAC token ID, CSRF, expiry/revocation, privacy-safe client metadata |
| `login_throttles` | Username/IP-keyed attempt windows and temporary blocks |
| `user_preferences` | Per-user gallery scale |
| `workflow_profiles` | Exact immutable validated UI/API/contract/runtime snapshots |
| `workflow_diagnostics` | Safe administrator discovery results |
| `service_health` | Last known ComfyUI/Ollama state |
| `uploads` | Owner-scoped application-owned source/mask metadata |
| `generations` | Immutable accepted request plus mutable lifecycle/reconciliation fields |
| `generation_uploads` | Exact control-to-upload/hash links for an execution |
| `prompt_assistant_runs` | Owner-scoped Ollama request/output/provenance, optionally linked to generation |
| `artifacts` | Retained declared output metadata, state, hash, lineage, canonical/best flags |
| `generation_events` | Durable owner-specific event timeline and SSE replay source |
| `scheduler_state` / `app_locks` | Fair round-robin cursor, queue sequence, and SQLite coordination |
| `audit_logs` | Non-content actor/target/action records for account/destructive operations |

The `generations` row stores workflow ID/display/version/schema/adapter and exact UI/API/contract hashes, resolved contract, requested/effective controls, all resolved seeds, selected preset/outputs, exact final prompt, immutable compiled graph and hash, submitted graph/prompt IDs, queue/state timestamps, current semantic stage, errors, and artifact pointers.

## Files

Uploads, original artifacts, and thumbnails are normal files, never database blobs. The database stores paths relative to the configured data root. File names are opaque UUIDs. Opens and deletes resolve the path and reject anything outside the root.

Generation deletion removes exclusive generation rows/files and deletes an upload only when no retained generation references it. User deletion collects paths, revokes sessions, cancels/reconciles active jobs, removes all owner rows, commits, then deletes application-owned files. Neither operation purges ComfyUI history/storage or contacts Ollama beyond normal active-job cancellation.

## Time and indexing

Timestamps are generated in UTC and returned as timezone-aware ISO values; browser rendering localizes submission dates. Indices cover owner/newest gallery pagination, queue status/order, prompt ID recovery, artifact timelines, events, sessions, and workflow identities.

## Operational constraints

Run only one application instance against one SQLite file. The worker provides internal concurrency; multiple containers are intentionally out of scope. Keep database and files on a reliable local persistent volume and back them up together while the application is stopped.
