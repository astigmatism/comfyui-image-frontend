import { escapeHtml } from "./lib.mjs";

const labels = { waiting: "Waiting", refining: "Refining", ready: "Waiting to queue images", finished: "Images queued", failed: "Failed", cancelled: "Stopped" };

export function promptRerunProgressMarkup(runs, { error = "", stopping = null } = {}) {
  return `${error ? `<p class="field-error" role="alert">${escapeHtml(error)} <button class="button low" type="button" data-rerun-refresh>Retry</button></p>` : ""}${runs.map((run) => {
    const counts = run.counts;
    const title = run.status === "stopped" ? "Prompt rerun stopped" : run.status === "completed" ? "All prompts processed" : "Refining saved prompts";
    return `<section class="rerun-progress" aria-label="Prompt rerun progress">
      <div class="rerun-progress-heading"><div><h2>${title}</h2>
        <p role="status">${counts.waiting} waiting · ${counts.refining} refining · ${counts.ready} ready · ${counts.finished} finished · ${counts.failed} failed${counts.cancelled ? ` · ${counts.cancelled} stopped` : ""}</p>
        <p>${run.queued_count} of ${run.planned_count} images queued. ${run.status === "processing" ? "Images queue as each refinement finishes." : "Queued images finish independently."}</p>
      </div>${run.status === "processing" ? `<button type="button" class="button secondary" data-rerun-stop="${escapeHtml(run.id)}" ${stopping === run.id ? "disabled" : ""}>${stopping === run.id ? "Stopping…" : "Stop remaining"}</button>` : ""}</div>
      <details data-rerun-detail="${escapeHtml(run.id)}"><summary>Prompt details (${run.prompt_count})</summary>
        <ol class="rerun-prompt-list">${run.items.map((item, index) => `<li><details data-rerun-detail="${escapeHtml(item.id)}"><summary>Prompt ${index + 1} · ${labels[item.status] || escapeHtml(item.status)}</summary>
          <h3>Original prompt</h3><pre>${escapeHtml(item.original_prompt)}</pre>
          ${item.prompt ? `<h3>Refined prompt</h3><pre>${escapeHtml(item.prompt)}</pre>` : ""}
          ${item.error ? `<p class="${item.status === "failed" ? "field-error" : "muted"}">${escapeHtml(item.error.message)}</p>` : ""}
        </details></li>`).join("")}</ol>
      </details>
    </section>`;
  }).join("")}`;
}

// Server-owned runs are discoverable by folder, even in a new browser session.
export function createPromptRerunProgress(host, { api, context, changed, notify, signal }) {
  let runs = [];
  let error = "";
  let stopping = null;
  let controller = new AbortController();
  let timer = null;
  let busy = false;
  let refreshAgain = false;
  let disposed = false;
  let location = null;
  let readEpoch = 0;
  const render = () => {
    const open = new Set([...host.querySelectorAll("details[open]")].map((node) => node.dataset.rerunDetail));
    const focus = host.contains(document.activeElement) ? document.activeElement?.dataset.rerunStop : null;
    const summaryFocus = host.contains(document.activeElement) && document.activeElement?.tagName === "SUMMARY"
      ? document.activeElement.parentElement?.dataset.rerunDetail : null;
    host.innerHTML = promptRerunProgressMarkup(runs, { error, stopping });
    host.hidden = !runs.length && !error;
    for (const detail of host.querySelectorAll("details")) detail.open = open.has(detail.dataset.rerunDetail);
    if (focus) host.querySelector(`[data-rerun-stop="${CSS.escape(focus)}"]`)?.focus({ preventScroll: true });
    if (summaryFocus) host.querySelector(`[data-rerun-detail="${CSS.escape(summaryFocus)}"] > summary`)?.focus({ preventScroll: true });
  };
  async function refresh() {
    if (disposed || !location?.collectionId) return;
    if (busy || stopping) { refreshAgain = true; return; }
    clearTimeout(timer);
    busy = true;
    const current = controller;
    const epoch = readEpoch;
    try {
      const next = await api(`/api/gallery/prompt-rerun?collection_id=${encodeURIComponent(location.collectionId)}`, { signal: current.signal, operation: "Prompt rerun progress" });
      if (current !== controller || disposed || epoch !== readEpoch) return;
      const changedRuns = JSON.stringify(runs) !== JSON.stringify(next);
      const hadError = Boolean(error);
      runs = next;
      error = "";
      if (changedRuns) changed();
      if (changedRuns || hadError) render();
    } catch (failure) {
      if (current !== controller || current.signal.aborted || disposed || epoch !== readEpoch) return;
      if (failure.status === 404) { runs = []; error = ""; }
      else error = failure.message || "Prompt rerun progress is temporarily unavailable.";
      render();
    } finally {
      if (current === controller && !disposed) {
        busy = false;
        const soon = refreshAgain;
        refreshAgain = false;
        if (soon || error || runs.some((run) => run.status === "processing")) timer = setTimeout(() => void refresh(), soon ? 100 : 3000);
      }
    }
  }
  const navigate = () => {
    controller.abort();
    controller = new AbortController();
    clearTimeout(timer);
    location = context();
    runs = []; error = ""; stopping = null; busy = false; refreshAgain = false;
    render();
    void refresh();
  };
  const click = async (event) => {
    if (event.target.closest("[data-rerun-refresh]")) { void refresh(); return; }
    const button = event.target.closest("[data-rerun-stop]");
    if (!button || stopping) return;
    const current = controller;
    readEpoch += 1;
    stopping = button.dataset.rerunStop;
    render();
    try {
      const run = await api(`/api/gallery/prompt-rerun/${encodeURIComponent(stopping)}/stop`, { method: "POST", signal: current.signal });
      if (current !== controller || disposed) return;
      runs = runs.map((item) => item.id === run.id ? run : item);
      changed();
    } catch (failure) {
      if (!current.signal.aborted && !disposed) notify(failure.message, "error");
    } finally {
      if (current === controller && !disposed) { stopping = null; render(); void refresh(); }
    }
  };
  host.addEventListener("click", click);
  const dispose = () => { disposed = true; controller.abort(); clearTimeout(timer); host.removeEventListener("click", click); };
  signal.addEventListener("abort", dispose, { once: true });
  navigate();
  return { navigate, refresh, dispose };
}
