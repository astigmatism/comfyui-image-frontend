import { api } from "./api.mjs";
import { escapeHtml, sortGenerationsNewestFirst } from "./lib.mjs";

export function promptRuns(generations, metadata = new Map()) {
  const groups = [];
  for (const generation of sortGenerationsNewestFirst(generations)) {
    const info = metadata.get(generation.id);
    const last = groups.at(-1);
    const same = info && last?.info ? info.id === last.info.id
      : generation.prompt_fingerprint && generation.prompt_fingerprint === last?.fingerprint;
    if (same) {
      last.items.push(generation);
      // A new arrival can precede cached members of a collapsed run.
      if (!last.info && info) { last.info = info; last.id = info.id; }
    }
    else groups.push({ id: info?.id || generation.id, info, fingerprint: generation.prompt_fingerprint, items: [generation] });
  }
  return groups;
}

export function promptGroupsMarkup(generations, cardMarkup, { metadata, collapsed, changes }) {
  return promptRuns(generations, metadata).map((group) => {
    const id = escapeHtml(group.id);
    const closed = collapsed.has(group.id);
    const count = Math.max(group.info?.generation_count || 0, group.items.length);
    const change = changes.get(group.id);
    const label = group.info?.previous_generation_id === null ? "First prompt"
      : change ? `${change.edit_count} prompt ${change.edit_count === 1 ? "change" : "changes"}` : "Prompt changes";
    return `<section class="prompt-group" data-prompt-group="${id}" data-group-count="${count}" aria-label="Prompt group, ${count} generations">
      <header class="prompt-group-header">
        <button type="button" class="prompt-group-collapse" data-group-toggle="${id}" aria-expanded="${!closed}" aria-controls="prompt-cards-${id}" aria-label="${closed ? "Expand" : "Collapse"} group of ${count} generations"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg></button>
        <button type="button" class="prompt-group-changes" data-group-changes="${id}" aria-expanded="false" aria-controls="prompt-group-preview">${label}</button>
        <span class="prompt-group-rule" aria-hidden="true"></span>
        <span class="prompt-group-count">${count} ${count === 1 ? "generation" : "generations"}${group.info ? "" : "+"}</span>
        <button type="button" class="prompt-group-select" data-prompt-group-select="${id}" role="checkbox" aria-checked="false" aria-label="Select group of ${count} generations" ${group.info ? "" : "disabled"}><span class="prompt-group-check" aria-hidden="true"></span><span>Select group</span></button>
      </header>
      <div class="prompt-group-grid" id="prompt-cards-${id}" ${closed ? "hidden" : ""}>${group.items.map(cardMarkup).join("")}</div>
      ${count > group.items.length ? `<button type="button" class="button low prompt-group-more" data-group-more="${id}" ${closed ? "hidden" : ""}>Load more in group · ${group.items.length} of ${count} loaded</button>` : ""}
    </section>`;
  }).join("");
}

export function promptChangesMarkup(change) {
  if (change.first_prompt) return '<p class="prompt-diff-note">First prompt in this folder. There is no earlier group to compare.</p>';
  const snippets = change.snippets.map((parts) => `<p class="prompt-diff-snippet">${parts.map((part) => {
    const tag = part.kind === "removed" ? "del" : part.kind === "added" ? "ins" : "span";
    return `<${tag}>${escapeHtml(part.text)}</${tag}>`;
  }).join("")}</p>`).join("");
  return `<p class="prompt-diff-note">Compared with the previous prompt group · ${change.edit_count} ${change.edit_count === 1 ? "edit" : "edits"}</p>${snippets}${change.omitted_edits ? `<p class="prompt-diff-note">${change.omitted_edits} more edits. Full prompt in generation details.</p>` : ""}<div class="prompt-diff-legend"><del>Removed</del><ins>Added</ins></div>`;
}

export function bindGalleryGroups(root, { getState, render, appendMembers, notify }) {
  let route = null;
  let routeRevision = 0;
  let signature = null;
  let controller = null;
  let metadata = new Map();
  const collapsed = new Set();
  const changes = new Map();
  const diffCache = new Map();
  let preview = null;
  let active = null;
  let pinned = false;
  let openTimer, closeTimer;
  let previewRequest = 0;
  let retryTimer;
  let retryCount = 0;
  let suppressFocus = false;
  const pending = new Set();
  const stateRoute = () => {
    const state = getState();
    return `${state.session?.user?.id || state.session?.user?.username || ""}:${state.favoritesView ? "favorites" : state.currentCollectionId || "home"}`;
  };
  const url = (id, suffix, extras = {}) => `/api/gallery/prompt-groups/${encodeURIComponent(id)}/${suffix}?${new URLSearchParams({ collection_id: getState().currentCollectionId || "", ...extras })}`;

  function closePreview() {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    previewRequest += 1;
    active?.setAttribute("aria-expanded", "false");
    active = null; pinned = false;
    if (preview) preview.hidden = true;
  }

  function closeAndFocus() {
    const target = active;
    closePreview();
    suppressFocus = true;
    target?.focus({ preventScroll: true });
    suppressFocus = false;
  }

  function options() {
    if (route !== stateRoute()) {
      controller?.abort(); closePreview(); clearTimeout(retryTimer);
      route = stateRoute(); signature = null; metadata = new Map();
      routeRevision += 1;
      collapsed.clear(); changes.clear(); diffCache.clear(); retryCount = 0;
    }
    return { metadata, collapsed, changes };
  }

  function positionPreview() {
    if (!active?.isConnected || preview?.hidden) { closePreview(); return; }
    const bounds = active.getBoundingClientRect();
    const width = preview.offsetWidth;
    preview.style.left = `${Math.max(12, Math.min(bounds.left, window.innerWidth - width - 12))}px`;
    const below = bounds.bottom + 9;
    const top = below + preview.offsetHeight < window.innerHeight - 12 ? below : bounds.top - preview.offsetHeight - 9;
    preview.style.top = `${Math.max(12, top)}px`;
  }

  async function showPreview(button, pin = false) {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    if (active !== button) closePreview();
    active = button; pinned = pin || pinned;
    if (!preview?.isConnected) {
      preview = document.createElement("section");
      preview.id = "prompt-group-preview"; preview.className = "prompt-group-preview";
      preview.setAttribute("role", "dialog"); preview.setAttribute("aria-label", "Prompt changes");
      root.append(preview);
      preview.addEventListener("pointerenter", () => clearTimeout(closeTimer));
      preview.addEventListener("pointerleave", scheduleClose);
      preview.addEventListener("focusin", () => clearTimeout(closeTimer));
      preview.addEventListener("focusout", scheduleClose);
    }
    const request = ++previewRequest;
    const id = button.dataset.groupChanges;
    const group = metadata.get(id) || [...metadata.values()].find((item) => item.id === id);
    const cacheKey = `${id}:${group?.previous_generation_id || ""}`;
    preview.hidden = false;
    button.setAttribute("aria-expanded", "true");
    preview.innerHTML = '<header><strong>Prompt changes</strong><button type="button" class="icon-button" data-close-prompt-preview aria-label="Close prompt changes">×</button></header><div data-prompt-diff-body role="status">Loading changes…</div>';
    positionPreview();
    try {
      const change = diffCache.get(cacheKey) || await api(url(id, "changes"));
      if (request !== previewRequest || active !== button) return;
      diffCache.set(cacheKey, change); changes.set(id, change);
      button.textContent = change.first_prompt ? "First prompt" : `${change.edit_count} prompt ${change.edit_count === 1 ? "change" : "changes"}`;
      preview.querySelector("[data-prompt-diff-body]").innerHTML = promptChangesMarkup(change);
      positionPreview();
    } catch (error) {
      if (request !== previewRequest) return;
      preview.querySelector("[data-prompt-diff-body]").textContent = error.message || "Could not load prompt changes. Reopen to retry.";
      positionPreview();
    }
  }

  function scheduleClose() {
    if (!pinned) closeTimer = setTimeout(() => {
      if (!preview?.contains(document.activeElement) && document.activeElement !== active) closePreview();
    }, 200);
  }

  function afterRender() {
    options();
    if (active && !active.isConnected) closePreview();
    const state = getState();
    if (state.favoritesView || !state.session?.authenticated) return;
    for (const button of root.querySelectorAll("[data-prompt-group-select], [data-group-more]")) {
      if (pending.has(button.dataset.promptGroupSelect || button.dataset.groupMore)) button.disabled = true;
    }
    const next = state.generations.map((item) => `${item.id}:${item.prompt_fingerprint}`).join("|");
    if (next === signature) return;
    signature = next;
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    const requestedRoute = route;
    const ids = state.generations.map((item) => item.id);
    if (!ids.length) return;
    void (async () => {
      const found = new Map();
      try {
        for (let offset = 0; offset < ids.length; offset += 500) {
          const rows = await api("/api/gallery/prompt-groups/lookup", { method: "POST", signal, body: JSON.stringify({ collection_id: state.currentCollectionId, generation_ids: ids.slice(offset, offset + 500) }) });
          for (const row of rows) found.set(row.generation_id, row.group);
        }
        if (signal.aborted || requestedRoute !== stateRoute() || signature !== next) return;
        for (const [id, info] of found) if (collapsed.has(id) || collapsed.has(metadata.get(id)?.id)) collapsed.add(info.id);
        for (const [id, info] of found) {
          const previous = metadata.get(id);
          if (previous && previous.previous_generation_id !== info.previous_generation_id) changes.delete(info.id);
        }
        metadata = found;
        retryCount = 0;
        render();
      } catch (error) {
        if (signal.aborted || requestedRoute !== stateRoute()) return;
        if (retryCount++ < 2) retryTimer = setTimeout(() => { signature = null; afterRender(); }, 1500 * retryCount);
        else notify(error.message || "Could not load prompt groups. Refresh to retry.", "error");
      }
    })();
  }

  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-group-toggle], [data-prompt-group-select], [data-group-changes], [data-close-prompt-preview], [data-group-more]");
    if (!button) return;
    event.preventDefault();
    if (button.hasAttribute("data-close-prompt-preview")) { closeAndFocus(); return; }
    if (button.dataset.groupChanges) {
      if (active === button && pinned) closePreview();
      else await showPreview(button, true);
      return;
    }
    const id = button.dataset.groupToggle || button.dataset.promptGroupSelect || button.dataset.groupMore;
    if (button.dataset.groupToggle) {
      if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
      render();
      root.querySelector(`[data-group-toggle="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
      return;
    }
    const requestRoute = stateRoute();
    const requestRevision = routeRevision;
    if (pending.has(id)) return;
    pending.add(id);
    const selection = Boolean(button.dataset.promptGroupSelect);
    button.disabled = true; button.setAttribute("aria-busy", "true");
    try {
      const group = promptRuns(getState().generations, metadata).find((item) => item.id === id);
      const oldest = group?.items.at(-1);
      const cursor = oldest ? btoa(JSON.stringify({ accepted_at: oldest.accepted_at, id: oldest.id })).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "") : "";
      const page = await api(url(id, "members", selection ? { selection: "true" } : { cursor }));
      if (requestRoute !== stateRoute() || requestRevision !== routeRevision) return;
      if (selection) root.dispatchEvent(new CustomEvent("gallery-select-group", { detail: { id, generations: page.items } }));
      else appendMembers(page.items);
    } catch (error) { if (requestRoute === stateRoute()) notify(error.message, "error"); }
    finally {
      pending.delete(id); button.disabled = false; button.removeAttribute("aria-busy");
      if (requestRoute === stateRoute()) for (const control of root.querySelectorAll("[data-prompt-group-select], [data-group-more]")) {
        if ((control.dataset.promptGroupSelect || control.dataset.groupMore) === id) {
          control.disabled = false;
          if (selection && control.dataset.promptGroupSelect && (document.activeElement === document.body || document.activeElement === root)) control.focus({ preventScroll: true });
        }
      }
    }
  });
  root.addEventListener("pointerover", (event) => {
    const button = event.target.closest("[data-group-changes]");
    if (!button || button.contains(event.relatedTarget) || pinned) return;
    clearTimeout(closeTimer); clearTimeout(openTimer);
    openTimer = setTimeout(() => void showPreview(button), 200);
  });
  root.addEventListener("pointerout", (event) => {
    if (event.target.closest("[data-group-changes]") && !event.target.closest("[data-group-changes]").contains(event.relatedTarget)) { clearTimeout(openTimer); scheduleClose(); }
  });
  root.addEventListener("focusin", (event) => { if (!suppressFocus && event.target.matches("[data-group-changes]")) void showPreview(event.target); });
  root.addEventListener("focusout", (event) => { if (event.target.matches("[data-group-changes]")) scheduleClose(); });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && active) { event.preventDefault(); event.stopImmediatePropagation(); closeAndFocus(); }
  }, true);
  document.addEventListener("pointerdown", (event) => { if (active && !active.contains(event.target) && !preview?.contains(event.target)) closePreview(); }, true);
  document.addEventListener("scroll", (event) => { if (active && !preview?.contains(event.target)) closePreview(); }, true);
  window.addEventListener("resize", () => { if (active) positionPreview(); });
  return {
    options, afterRender,
    invalidate() { signature = null; },
    paginationCursor(cursor) {
      const last = promptRuns(getState().generations, metadata).at(-1);
      return cursor && last?.info && collapsed.has(last.id) ? last.info.after_cursor : cursor;
    },
  };
}
