import { api } from "./api.mjs";
import { collectionSubtree, collectionTreeRows, escapeHtml } from "./lib.mjs";

const terminal = new Set(["succeeded", "cancelled_with_artifacts", "cancelled_without_artifacts", "failed_with_artifacts", "failed_without_artifacts", "interrupted"]);
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const keyFor = (card) => `${card.dataset.galleryCard}:${card.dataset.generationId || card.dataset.collectionId}`;

export function selectionPlan(keys, state) {
  const collections = state.collections || [];
  const generations = state.favoritesView
    ? (state.favorites?.items || []).flatMap((item) => item.generation ? [item.generation] : [])
    : state.generations || [];
  const chosenFolders = collections.filter((item) => keys.has(`collection:${item.id}`));
  const chosenCards = generations.filter((item) => keys.has(`generation:${item.id}`));
  const covered = new Set(chosenFolders.flatMap((item) => collectionSubtree(collections, item.id).map((child) => child.id)));
  const descendants = new Set(chosenFolders.flatMap((item) => collectionSubtree(collections, item.id).filter((child) => child.id !== item.id).map((child) => child.id)));
  const folders = chosenFolders.filter((item) => !descendants.has(item.id));
  const cards = chosenCards.filter((item) => !covered.has(item.collection_id));
  const folderContents = collections.filter((item) => covered.has(item.id));
  return {
    generation_ids: cards.map((item) => item.id),
    collection_ids: folders.map((item) => item.id),
    cards,
    folders,
    covered,
    favorites: {
      generation_ids: chosenCards.map((item) => item.id),
      collection_ids: chosenFolders.map((item) => item.id),
      count: chosenCards.length + chosenFolders.length,
      allFavorited: [...chosenCards, ...chosenFolders].every((item) => item.is_favorite),
    },
    downloadable: cards.some((item) => item.image_count > 0 || item.display_artifact?.content_url)
      || folderContents.some((item) => item.generation_count > 0),
    count: cards.length + folders.length,
    generationCount: cards.length + folderContents.reduce((sum, item) => sum + (Number(item.generation_count) || 0), 0),
    active: cards.some((item) => !terminal.has(item.status)) || folders.some((item) => Number(item.remaining_count) > 0),
    summary: [cards.length ? plural(cards.length, "image card") : "", folders.length ? plural(folders.length, "folder") : ""].filter(Boolean).join(" · "),
  };
}

export function transferDialogMarkup(plan, collections, currentCollectionId) {
  const chosen = plan.covered.has(currentCollectionId) ? null : currentCollectionId;
  const options = [{ collection: { id: "", name: "Home" }, depth: 0 }, ...collectionTreeRows(collections)];
  return `<div class="dialog-frame">
    <header class="dialog-header"><div><h2>Move or copy</h2><p>${escapeHtml(plan.summary)}</p></div><button type="button" class="icon-button" data-bulk-action="close" aria-label="Close">×</button></header>
    <div class="move-dialog-content">
      <p class="help-text">Choose a destination. Copy keeps the originals in their current location.</p>
      <div class="transfer-destinations" role="radiogroup" aria-label="Destination collection">${options.map(({ collection, depth }) => {
        const blocked = plan.covered.has(collection.id);
        return `<label class="move-option${blocked ? " destination-unavailable" : ""}" style="--collection-depth: ${depth}"><input type="radio" name="bulk_collection_id" value="${escapeHtml(collection.id)}" ${blocked ? "disabled" : ""} ${collection.id === (chosen || "") ? "checked" : ""} /><span>${escapeHtml(collection.name)}${blocked ? '<small>Inside the selection</small>' : collection.id === (currentCollectionId || "") ? '<small>Current location</small>' : ""}</span></label>`;
      }).join("")}</div>
      ${plan.folders.length ? '<p class="help-text">Folders include their images and nested folders.</p>' : ""}
      <p class="help-text" data-transfer-help></p>
      <p class="field-error" data-operation-error role="alert"></p>
    </div>
    <footer class="dialog-actions"><button type="button" class="button secondary" data-bulk-action="close">Cancel</button><div class="transfer-commit-actions"><button type="button" class="button secondary" data-bulk-action="copy">Copy here</button><button type="button" class="button primary" data-bulk-action="move">Move here</button></div></footer>
  </div>`;
}

export function deleteSelectionMarkup(plan) {
  return `<div class="dialog-frame">
    <header class="dialog-header"><div><h2>Delete ${plural(plan.count, "item")}?</h2><p>${escapeHtml(plan.summary)}</p></div><button type="button" class="icon-button" data-bulk-action="close" aria-label="Close">×</button></header>
    <div class="collection-dialog-content">
      <p>This permanently deletes <strong>${plural(plan.generationCount, "generation")}</strong> and all of their stored images. This cannot be undone.</p>
      ${plan.folders.length ? `<p>Includes everything inside ${plan.folders.map((item) => `<strong>“${escapeHtml(item.name)}”</strong>`).join(", ")}, including nested folders.</p>` : ""}
      ${plan.active ? '<p>Active generations in the selection will be cancelled and deleted.</p>' : ""}
      <p class="field-error" data-operation-error role="alert"></p>
    </div>
    <footer class="dialog-actions"><button type="button" class="button secondary" data-bulk-action="close">Cancel</button><button type="button" class="button destructive" data-bulk-action="confirm-delete">Delete ${plural(plan.count, "item")}</button></footer>
  </div>`;
}

export function bindGallerySelection(root, { getState, refresh, notify }) {
  let selected = new Set();
  let selecting = false;
  let anchor = null;
  let route = null;
  let busy = false;
  let operationPlan = null;
  let returnFocusKey = null;
  let syncQueued = false;
  const cards = () => [...root.querySelectorAll("#gallery [data-gallery-card]")];
  const dialogs = () => [...root.querySelectorAll(".gallery-bulk-dialog")];
  const activeDialog = () => dialogs().find((dialog) => dialog.open);
  const requestBody = () => ({ generation_ids: operationPlan.generation_ids, collection_ids: operationPlan.collection_ids });

  function sync() {
    const state = getState();
    const currentRoute = state.favoritesView ? "favorites" : state.currentCollectionId || "home";
    if (!state.session || route !== currentRoute) {
      selected.clear(); selecting = false; anchor = null; route = currentRoute;
      if (!busy) activeDialog()?.close();
    }
    const visibleCards = cards();
    const visibleKeys = new Set(visibleCards.map(keyFor));
    selected = new Set([...selected].filter((key) => visibleKeys.has(key)));
    if (!selected.size) selecting = false;
    for (const card of visibleCards) {
      const checked = selected.has(keyFor(card));
      card.classList.toggle("is-selected", checked);
      const control = card.querySelector(".card-select-button");
      control?.setAttribute("aria-checked", String(checked));
      control?.setAttribute("title", checked ? "Deselect card" : "Select card");
      card.querySelectorAll("[draggable]").forEach((image) => { image.draggable = !selecting; });
    }
    root.querySelector(".app-shell")?.classList.toggle("gallery-selection-mode", selecting);
    const host = root.querySelector("#gallery-selection-toolbar");
    if (!host) return;
    host.hidden = !selecting;
    host.setAttribute("aria-busy", String(busy));
    const focusedAction = host.contains(document.activeElement) ? document.activeElement.dataset.bulkAction : null;
    const plan = selectionPlan(selected, state);
    const all = visibleCards.length > 0 && selected.size === visibleCards.length;
    host.innerHTML = `<span class="selection-count" role="status" aria-label="${selected.size} selected" title="${escapeHtml(plan.summary)}">${selected.size}<span class="selection-count-label"> selected</span></span>
      <button type="button" class="button low selection-tool" data-bulk-action="all" aria-label="Select loaded (${visibleCards.length})" title="Select all ${visibleCards.length} loaded items" ${all || !visibleCards.length || busy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="7" y="7" width="14" height="14" rx="2" /><path d="M16 3H5a2 2 0 0 0-2 2v11m8-2 2 2 4-4" /></svg></button>
      <button type="button" class="button low selection-tool" data-bulk-action="favorite" aria-label="Add to Favorites" title="${plan.favorites.allFavorited ? "All selected items are already favorites" : "Add selected image and folder cards to Favorites"}" ${!plan.favorites.count || plan.favorites.allFavorited || busy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z" /></svg></button>
      <button type="button" class="button low selection-tool" data-bulk-action="download" aria-label="Download selection" title="Download all available images, including folder contents, as a ZIP" ${!plan.downloadable || busy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" /></svg></button>
      <button type="button" class="button low selection-tool" data-bulk-action="transfer" aria-label="Move / Copy…" title="Move or copy selected items" ${!plan.count || busy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 7h7l2 2h9v11H3Z" /><path d="M13 17v-5m-3 3 3-3 3 3" /></svg></button>
      <button type="button" class="button low selection-tool" data-bulk-action="delete" aria-label="Delete…" title="Delete selected items" ${!plan.count || busy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5" /></svg></button>
      <button type="button" class="button low selection-tool" data-bulk-action="clear" aria-label="Clear selection" title="Clear selection (Esc)" ${busy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M6 18 18 6" /></svg></button>`;
    if (focusedAction && selecting) {
      const replacement = host.querySelector(`[data-bulk-action="${focusedAction}"]`);
      (replacement && !replacement.disabled ? replacement : host.querySelector('[data-bulk-action="clear"]'))?.focus({ preventScroll: true });
    }
  }

  function choose(card, range) {
    if (busy) return;
    const key = keyFor(card);
    const order = cards().map(keyFor);
    if (range && selecting && order.includes(anchor)) {
      const [start, end] = [order.indexOf(anchor), order.indexOf(key)].sort((a, b) => a - b);
      order.slice(start, end + 1).forEach((item) => selected.add(item));
    } else {
      if (selected.has(key)) selected.delete(key);
      else selected.add(key);
      anchor = key;
    }
    selecting = selected.size > 0;
    sync();
    card.querySelector(".card-select-button")?.focus({ preventScroll: true });
  }

  function finish() {
    if (busy) return;
    selecting = false; selected.clear(); sync();
    const card = cards().find((item) => keyFor(item) === anchor) || cards()[0];
    card?.querySelector(".card-select-button")?.focus({ preventScroll: true });
  }

  function updateTransferControls() {
    const dialog = root.querySelector("#gallery-transfer-dialog");
    if (!dialog?.open || busy || !operationPlan) return;
    const selectedDestination = dialog.querySelector('[name="bulk_collection_id"]:checked');
    const destination = selectedDestination?.value || null;
    const sameLocation = operationPlan.cards.every((item) => (item.collection_id || null) === destination)
      && operationPlan.folders.every((item) => (item.parent_id || null) === destination);
    dialog.querySelector('[data-bulk-action="move"]').disabled = !selectedDestination || sameLocation;
    dialog.querySelector('[data-bulk-action="copy"]').disabled = !selectedDestination || operationPlan.active;
    dialog.querySelector("[data-transfer-help]").textContent = operationPlan.active
      ? "Copy is available when the selected generations finish. You can move them now."
      : sameLocation ? "Already in this location. Choose another destination to move, or copy here." : "";
  }

  function openDialog(kind) {
    operationPlan = selectionPlan(selected, getState());
    if (!operationPlan.count) return;
    returnFocusKey = document.activeElement?.dataset.bulkAction;
    const state = getState();
    const dialog = root.querySelector(kind === "transfer" ? "#gallery-transfer-dialog" : "#gallery-delete-dialog");
    dialog.innerHTML = kind === "transfer"
      ? transferDialogMarkup(operationPlan, state.collections, state.currentCollectionId)
      : deleteSelectionMarkup(operationPlan);
    dialog.oncancel = (event) => { if (busy) event.preventDefault(); };
    dialog.onclose = () => { root.querySelector(`#gallery-selection-toolbar [data-bulk-action="${returnFocusKey}"]`)?.focus({ preventScroll: true }); };
    dialog.showModal();
    updateTransferControls();
    (dialog.querySelector('input:checked') || dialog.querySelector('[data-bulk-action="close"]'))?.focus();
  }

  async function perform(operation) {
    const dialog = activeDialog();
    if (!dialog || busy || !operationPlan) return;
    busy = true;
    const destination = dialog.querySelector('[name="bulk_collection_id"]:checked')?.value || null;
    const controls = [...dialog.querySelectorAll("button, input")].map((node) => [node, node.disabled]);
    controls.forEach(([node]) => { node.disabled = true; });
    dialog.setAttribute("aria-busy", "true");
    const submit = dialog.querySelector(`[data-bulk-action="${operation}"]`);
    const oldLabel = submit.textContent;
    submit.textContent = operation === "copy" ? "Copying…" : operation === "move" ? "Moving…" : "Deleting…";
    dialog.querySelector("[data-operation-error]").textContent = "";
    let succeeded = false;
    let result = null;
    try {
      if (operation === "confirm-delete") {
        result = await api("/api/gallery/delete", { method: "POST", body: JSON.stringify(requestBody()) });
        const failures = result.items.filter((item) => item.status === "failed");
        const pending = result.items.filter((item) => item.status === "pending");
        selected = new Set(failures.map((item) => `${item.kind}:${item.id}`));
        selecting = failures.length > 0;
        notify(failures.length ? `${result.items.length - failures.length} items removed; ${failures.length} failed. ${failures[0].message}` : pending.length ? "Selection removed. Active generations are being cancelled and deleted." : "Selection deleted.", failures.length ? "error" : "success");
      } else {
        result = await api("/api/gallery/transfer", { method: "POST", body: JSON.stringify({ ...requestBody(), operation, collection_id: destination }) });
        const name = getState().collections.find((item) => item.id === destination)?.name || "Home";
        notify(`${operation === "copy" ? "Copied" : "Moved"} ${plural(operationPlan.count, "item")} to ${name}.`, "success");
        selected.clear(); selecting = false;
      }
      succeeded = true;
      dialog.close();
      await refresh({ operation, plan: operationPlan, result, destination });
    } catch (error) {
      if (succeeded) notify("The operation completed, but the gallery could not refresh. Reload to see the latest items.", "error");
      else dialog.querySelector("[data-operation-error]").textContent = error.message || "The operation could not be completed.";
    } finally {
      busy = false;
      dialog.removeAttribute("aria-busy");
      controls.forEach(([node, disabled]) => { node.disabled = disabled; });
      submit.textContent = oldLabel;
      updateTransferControls();
      sync();
    }
  }

  async function performToolbarAction(operation) {
    if (busy) return;
    const plan = selectionPlan(selected, getState());
    if (operation === "favorite" ? !plan.favorites.count || plan.favorites.allFavorited : !plan.downloadable) return;
    const selection = operation === "favorite" ? plan.favorites : plan;
    const body = JSON.stringify({ generation_ids: selection.generation_ids, collection_ids: selection.collection_ids });
    busy = true;
    sync();
    let succeeded = false;
    try {
      if (operation === "download") {
        const blob = await api("/api/gallery/download", { method: "POST", body, responseType: "blob" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "gallery-selection.zip";
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
        notify("Selection download started.", "success");
      } else {
        const result = await api("/api/gallery/favorite", { method: "POST", body });
        succeeded = true;
        await refresh({ operation, plan, result });
        notify(`Added ${plural(plan.favorites.count, "item")} to Favorites.`, "success");
      }
    } catch (error) {
      notify(succeeded ? "Favorites were saved, but the gallery could not refresh. Reload to see them." : error.message || "The operation could not be completed.", "error");
    } finally {
      busy = false;
      sync();
      if (selecting) {
        const button = root.querySelector(`#gallery-selection-toolbar [data-bulk-action="${operation}"]`);
        (button && !button.disabled ? button : root.querySelector('#gallery-selection-toolbar [data-bulk-action="clear"]'))?.focus({ preventScroll: true });
      }
    }
  }

  root.addEventListener("click", (event) => {
    const action = event.target.closest("[data-bulk-action]")?.dataset.bulkAction;
    const card = event.target.closest("#gallery [data-gallery-card]");
    if (card && (selecting || event.target.closest(".card-select-button"))) {
      event.preventDefault(); event.stopImmediatePropagation(); choose(card, event.shiftKey); return;
    }
    if (!action) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (busy) return;
    if (action === "all") { cards().forEach((item) => selected.add(keyFor(item))); sync(); }
    else if (action === "clear") finish();
    else if (action === "transfer" || action === "delete") openDialog(action);
    else if (action === "favorite" || action === "download") void performToolbarAction(action);
    else if (action === "close") activeDialog()?.close();
    else void perform(action);
  }, true);
  root.addEventListener("change", (event) => {
    if (event.target.name === "bulk_collection_id") updateTransferControls();
  });
  root.addEventListener("keydown", (event) => {
    if (!selecting || busy || activeDialog() || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); finish(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault(); cards().forEach((item) => selected.add(keyFor(item))); sync();
    }
  }, true);
  root.addEventListener("dragstart", (event) => { if (selecting && event.target.closest("#gallery")) event.preventDefault(); }, true);
  const observer = new MutationObserver((records) => {
    if (syncQueued || !records.some((record) => record.target === root || record.target.closest?.("#gallery"))) return;
    syncQueued = true;
    queueMicrotask(() => { syncQueued = false; sync(); });
  });
  observer.observe(root, { childList: true, subtree: true });
  sync();
  return { clear: finish, sync };
}
