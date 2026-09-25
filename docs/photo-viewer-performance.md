# Photo viewer performance and regression checks

The viewer keeps a displayed image separate from the requested destination. Its
download URL, favorite/delete targets, and metadata describe the displayed image
until the replacement original has decoded. Ordinary metadata/progress updates
preserve that image and the existing controls, including keyboard focus and pan.

Concurrent pagination callers share the pending page request. Repeated navigation
at a page boundary coalesces to the latest direction; close, folder navigation,
and subsequent navigation invalidate obsolete results. Empty pages can advance
to a new cursor, while repeated cursors and failures expose a retry action.

Closing or signing out clears viewer-owned images, requests, timers and frame
callbacks. The details dialog also clears its images on close. Thumbnail and
selection observers ignore changes that cannot affect their images/cards.

The retention gate found a Chromium-specific cleanup problem: clearing `src` on
an image that was never mounted can leave a viewport listener retaining it.
Shared image disposal now removes the cleared image from a detached fragment,
triggering the browser's removal cleanup. It applies to viewer images, thumbnail
decode probes, and thumbnails already removed from the gallery. The controlled
garbage-collection test failed with 18 surviving images before this fix and passes
with zero afterward. [Chromium removal implementation](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/html/html_image_element.cc).

## Preloading and rollback

`frontend/src/photo-viewer-preload.mjs` contains the independent speculative-load
policy. Set `PHOTO_VIEWER_PRELOAD_ENABLED = false` and rebuild to disable preloading
without reverting navigation, cleanup, or rendering fixes.

The policy selects one already-listed neighbor in Hold mode, following the last
navigation direction (older on opening). It waits for the current image and a
100 ms dwell, uses low network priority, and skips unknown dimensions or
`width × height × 4 > 32 MiB`.
It stops when the tab is hidden, the viewer closes, or playback becomes Slideshow.
The loader has only a displayed slot and one pending slot; navigation reuses a
matching preload rather than starting a duplicate request. A speculative failure
does not retry on every progress update; explicit navigation can retry it.

The budget estimates decoded pixel data, not total Chrome memory. GPU surfaces,
compressed HTTP cache entries, and browser-managed image caches have their own
lifetimes. Images retain original quality, and the backend/media APIs are unchanged.

## Regression coverage

`frontend/e2e/photo-viewer-performance.spec.mjs` covers page-boundary responsiveness,
shared pagination, reversal/close/folder races, empty/error/repeated cursors,
unchanged and replaced artifacts, atomic action targets, decode cancellation,
retry, sign-out, tab hiding, preloading, keyboard focus, and batched pan input.
It also checks actual Download/Delete request targets during loading, scroll/viewer
pagination sharing, pointer anchoring, detail cleanup, and collectability of
cancelled image objects. The image-loader unit tests control decode completion
and cancellation directly.

The full browser gate also exposed an existing move/live-update race: a detail
response started before a successful move could restore the old card. Moving now
invalidates that generation's outstanding refresh, as favorites already did. A
controlled late-response test failed before the fix and passes afterward.

Existing gallery, selection, grouped/classic layout, and principal-journey tests
remain required, including fullscreen/Escape, Fit/Fill/1:1, mouse dragging, wheel
zoom, slideshow, generation, countdowns, favorites, delete, and downloads.

Existing harness corrections include a Node 22-compatible crypto mock, explicit
thumbnail response gates, cancellation-aware pagination waits, the existing
automation-status element in the layout assertion, per-image thumbnail request
counts after scrolling into view, and an actionability trial during hover-control
preparation. The two-tab startup tests now prepare the producer before holding the
reader's gallery response, so setup cannot accidentally exhaust the request's
15-second deadline. Three real-generation journeys use a 60-second overall test
budget for login, catalog setup and generation; application deadlines are unchanged.
The serial two-image batch waits for the first completion before waiting for both,
and thumbnail setup keeps its target in view while late group metadata settles.
The exact image count and no-refetch assertions remain in place. These address
failures found while running the full gates.

From `frontend`, with Node 22+ and the Python development environment on PATH:

```sh
npm test
npm run lint
npm run format:check
npm run build
npx playwright test
npx playwright test -c playwright.tls.config.mjs
```

Run builds and browser suites sequentially: the build replaces `frontend/dist`,
which a running browser suite serves. The TLS suite can use local Caddy through
`CIF_E2E_TLS_MODE=local`, `CIF_E2E_CADDY_BIN`, and `PYTHON` on machines without Docker.

## Completed release gates

Validated on 24 September 2026 with Node 22.21.1:

| Gate | Result |
| --- | --- |
| Full frontend unit suite | 279 passed; no failures or skips |
| Standard Playwright suite | 142 passed in one complete run; no failures or retries |
| TLS-edge suite | 3 passed using local Caddy 2.11.4 and the committed edge configuration |
| Lint, formatting, production build | Passed |
| Headed Chrome profile | Completed with 2K PNG/JPEG, cold/warm caches, progress events, rapid navigation and 50 open/close cycles |

The new coverage includes 16 unit tests and 28 browser tests. Confirmed defects
were reproduced by failing tests before their fixes. Earlier full-suite failures
were investigated and addressed as described above; the final complete browser
run passed all 142 tests with the standard runner configuration.

## Repeatable headed profile

`frontend/scripts/profile-photo-viewer.mjs` starts a temporary loopback fixture
server and an isolated headed Chrome session. It never connects to an appliance.
Pillow creates identical 2048×1536 PNG/JPEG data for both source trees. The fixture
contains 500 gallery records and adds 100 ms to uncached original responses.

```sh
cd frontend
CIF_PERF_PYTHON=/path/to/python-with-pillow \
  node scripts/profile-photo-viewer.mjs /path/to/baseline /path/to/current
```

Each root must contain `frontend/src` and `frontend/index.html`. A baseline can be
made with `git archive` into a disposable directory. Output defaults to
`/tmp/cif-viewer-profile` and includes JSON measurements and fullscreen screenshots.

The probe records cold opening, sequential navigation, idle/progress main-thread
time, image replacement and gallery-scan counts, original requests, retained viewer
images, and heap/DOM/browser RSS samples over 50 open/close cycles. CPU and memory
measurements are diagnostic, not machine-independent test thresholds. Run the
comparison without other test suites competing for the CPU. Inspect the screenshots
and investigate repeated growth or latency regressions before accepting a change.
`CIF_PERF_CYCLES` extends the cycle count. `CIF_PERF_HEAP_SNAPSHOT=1` saves a Chrome
heap snapshot for diagnosing retained objects. Weak references track image-loader
objects without keeping them alive; additional idle/GC samples distinguish pending
browser work from persistent retention.

Navigation timings wait for decoding and a paint opportunity on both versions.
An assigned URL and nonzero `naturalWidth` alone do not establish that Chrome has
displayed the replacement. The original viewer committed the image before decode;
the revised viewer commits afterward.

## Recorded results — 24 September 2026

The baseline is `bff60db`; both versions used Chrome 153.0.8010.53. Full measurements
are in [photo-viewer-profile.json](photo-viewer-profile.json). Host load varied
substantially between runs, so elapsed times and absolute RSS are diagnostic.

| Check | Baseline | Final code |
| --- | ---: | ---: |
| Image replacements during 100 progress events | 100 | 0 |
| Full thumbnail scans during those events | 101 | 0 |
| Full selection scans during those events | 100 | 0 |
| Main-thread task time during those events | 5,456 ms | 1,470 ms |
| Median next-image readiness, including decode/paint | 892 ms | 102 ms |
| Median warm reverse navigation | 1,030 ms | 62 ms |
| Original requests across the whole probe | 39 | 53 |
| Images with sources left in the closed viewer | 1 | 0 |
| Surviving loader-created images after idle/GC | 8 thumbnail probes | 0 |

The final 50-cycle run retained zero loader-created images at every sample. DOM
counts stayed within 71,854–71,886 during cycling and settled at 71,853. Browser RSS
warmed from about 1,305 MiB to 1,465 MiB by cycle 20, then stayed around 1,458–1,468
MiB through cycle 50 and settled at 1,397 MiB. An earlier run with the disposal fix
settled at 899 MiB. A reduction in physical browser memory has **not** been
established; the verified result is bounded application/DOM retention. The
two-second quiet-window task time also varied and does not establish lower idle CPU.

The extra original requests reflect speculative work, including work cancelled
when closing. The one-image budget and cancellation remain enforced. Disabling the
independent preload policy removes that tradeoff while retaining the other fixes.

## Remaining limits

Preloading consumes one extra original request when the user stops before visiting
the neighbor. Its pixel budget depends on stored dimensions. A user who navigates
faster than a download/decode completes will still see the loading indicator.
The old photo and its actions remain available during that interval.

The profile uses synthetic images and controlled latency, not production traffic.
Results establish the reproduced defects and application retention behavior;
they do not predict speedups on every device, collection, or network connection.
