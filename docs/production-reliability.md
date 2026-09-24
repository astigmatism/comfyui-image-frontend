# Concurrency and production-update reliability

The application remains one process with SQLite and the existing external runtimes.
Pool exhaustion is handled by bounding incoming work and shortening connection
lifetimes, rather than by allowing an unbounded thread or request backlog.

| Resource | Initial limit |
| --- | ---: |
| Active ordinary API metadata requests | 8 |
| Active media metadata requests within that total | 4 |
| Requests awaiting admission | 64 |
| Admission wait | 5 seconds |
| Background database operations | 4 |
| Application database connections | 15 |
| Independent coalesced read-only health probe | 1 |

`admission.py` queues before authentication and selects the oldest eligible request.
Media cannot consume the four places reserved for other API work. Overload returns
`503 service_busy` and `Retry-After: 1`. Waiters do not occupy worker threads or
connections. Request-body buffering is limited to the initial ASGI chunk; a larger
upload keeps transport backpressure until admission. Disconnects after complete
request bodies and ASGI cancellations remove waiters immediately; an incomplete
body remains bounded by the five-second admission deadline.

`blocking.py` owns the execution limits and waits for an in-flight operation on
cancellation. Each database operation owns its session in its worker thread and
returns materialized values. Authentication returns frozen context objects from a
separate short transaction. Streaming, external HTTP, broker notifications, and
worker cache updates happen outside transactions. Polling awaits its prior work;
accepted generation work stays in the durable database queue.

Only reconciliation of active generations gates dispatcher startup. Historical
artifact retention and remote source cleanup run in a separate supervised task,
use narrow database projections, and never compact active generations. Slow or
failed housekeeping cannot prevent queued work from running. Shutdown cancels
housekeeping while allowing its current database operation to finish.

The browser shares four thumbnail fetches across galleries and collections, prioritizes
visible images over the 600px preload margin, deduplicates URLs, and cancels obsolete
work. Images remain behind static placeholders until decoded; gallery updates retain
unchanged cards and image elements. Unused successful thumbnails use an LRU cache
bounded to 96 entries, 16 MiB of blobs, and 60 seconds idle. Active consumers are never
evicted; expired entries and shell disposal revoke their object URLs. Logout clears
the cache and observers. Safe reads retry transient failures at most four times. Manual submission
protocol 3 adds durable, account-scoped idempotency receipts and a status endpoint.
Five attempts fit within a 60-second submission deadline; an unresolved submission
retains its key and frozen payload through reloads. Logout clears pending browser
storage. This protects application acceptance, not external ComfyUI submission;
existing ambiguous-native-submission safeguards remain in place.

Production safety checks and operational recovery are separate. Configuration,
identity, storage, image provenance, and certificates must be valid before recovery.
The updater holds its deployment lock through a 60-second grace period and one
permitted recovery restart. `--restart` restarts an unchanged release using the
existing container; recovery consumes that restart request. New releases retain
isolated candidate startup, consistent backup, and migration safeguards. No failure
after cutover automatically restores an old database or starts old code.

Every deployment phase and terminal result is retained in restricted records,
including preflight failures under a valid deployment root. The public error names
the cause, phase, recovery outcome, and retained job. The waiting launcher requires
a successful child exit and verified terminal result. The tracked Portal entrypoint
uses `--wait --restart`; Service Portal itself needs no deployment.

## Local acceptance evidence

On 2026-09-21, a disposable checkout of baseline `9d1fb466ad9f48f350ef72619d2de30bc7385aa6`
reproduced pool exhaustion: 45 of 60 concurrent authenticated thumbnail/gallery reads
failed with pool timeouts while background workers ran. The pool retained its original
15-connection maximum; the reproducer reduced its timeout to 0.5 seconds and injected
15 ms of local query latency to bound the experiment. A separate regression demonstrates
that a synchronous checkout blocks the event loop from releasing a held connection.

The revised application's 60-read mixed workload completes without pool failures,
keeps health below two seconds, and lets workers finish. Additional tests cover
bounded sustained overload, cancellation, disconnects, health-probe coalescing,
stream cleanup, submission races and conflicting payloads, transaction rollback,
committed-result recovery, account isolation/deletion, migrations, and deployment
recovery. Browser fault injection verifies lost replies, reload reconciliation,
thumbnail retries, and the four-request limit. All synthetic traffic and injected
failures stay local; production validation uses normal reads and authorized restarts.

Release validation covered 659 backend cases (nine optional live-Ollama cases were
skipped), 168 frontend unit cases, 79 browser journeys, and 49 deployment/backup
cases including four real Docker-runner tests. The full backend and browser runs
needed focused reruns: local host contention exposed timing-sensitive fake-runtime
cancellation, and browser journeys inherited saved account preferences. Explicit
cancellation gates and fresh test accounts made those cases deterministic; all
affected cases passed on rerun. The final admission/contention subset passed ten
cases with the same 15 ms query delay used in the baseline reproducer.

Three trusted-HTTPS browser tests passed against real local Caddy 2.11.4. This
Apple Silicon host used the native Caddy executable because the pinned production
Caddy image is AMD64-only and cannot run with its production restrictions under
local emulation. Production image selection and container restrictions are unchanged.
The Docker image smoke test passed startup, migrations, readiness, immutable asset
delivery (including both new browser modules), and runtime configuration checks
with external networking disabled.

The first production rollout and unchanged-release restart both passed, but startup
housekeeping over roughly 2,000 historical generations took about 100 seconds.
That observation prompted the separate maintenance task above. Four additional
local cases cover blocked database/remote cleanup, cleanup failure, and retention
of active-generation artifacts; the affected worker, recovery, lifecycle, and
shutdown suites were rerun before publishing the follow-up release.
