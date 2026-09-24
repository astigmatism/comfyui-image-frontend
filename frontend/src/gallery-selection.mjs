import { api } from "./api.mjs";
import { collectionSubtree, collectionTreeRows, escapeHtml } from "./lib.mjs";
import { galleryViewChecked, galleryViewKeys, galleryViewScope } from "./gallery-view.mjs";

const terminal = new Set(["succeeded", "cancelled_with_artifacts", "cancelled_without_artifacts", "failed_with_artifacts", "failed_without_artifacts", "interrupted"]);
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const keyFor = (card) => `${card.dataset.galleryCard}:${card.dataset.generationId || card.dataset.collectionId}`;

export function selectionPlan(keys, state) {
  const collections = state.collections || [];
  const visibleGenerations = state.favoritesFilter
    ? (state.generations || []).filter((item) => item.is_favorite)
    : (state.generations || []);
  const generations = [...new Map([...(state.selectionGenerations || []), ...visibleGenerations].map((item) => [item.id, item])).values()];
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
  let selectedScope = null;
  let inventory = null;
  let inventorySignature = null;
  let inventoryController = null;
  let inventoryError = false;
  let viewBusy = false;
  let viewRequest = 0;
  const extraGenerations = new Map();
  const groupMembers = new Map();
  const selectionState = () => ({ ...getState(), selectionGenerations: [...extraGenerations.values()] });
  const cards = () => [...root.querySelectorAll("#gallery [data-gallery-card]")];
  const dialogs = () => [...root.querySelectorAll(".gallery-bulk-dialog")];
  const activeDialog = () => dialogs().find((dialog) => dialog.open);
  const requestBody = (plan = operationPlan) => ({ generation_ids: plan.generation_ids, collection_ids: plan.collection_ids, ...(selectedScope ? { scope: selectedScope } : {}) });
  const viewRoute = () => JSON.stringify([getState().session?.user?.id || getState().session?.user?.username, galleryViewScope(getState())]);

  function viewSignature() {
    const state = getState();
    return JSON.stringify([viewRoute(), state.generations.map((item) => [item.id, item.status, item.is_favorite, item.image_count]), state.collections.map((item) => [item.id, item.parent_id, item.is_favorite, item.generation_count])]);
  }

  async function loadInventory() {
    const requestedRoute = viewRoute();
    inventorySignature = viewSignature();
    inventoryController?.abort();
    const controller = new AbortController();
    inventoryController = controller;
    inventoryError = false;
    const scope = galleryViewScope(getState());
    try {
      const result = await api(`/api/gallery/items?${new URLSearchParams({ collection_id: scope.collection_id || "", favorites_only: String(scope.favorites_only) })}`, { signal: controller.signal });
      if (controller.signal.aborted || requestedRoute !== viewRoute()) return null;
      inventory = result;
      if (selectedScope) {
        const keys = new Set(galleryViewKeys(result));
        selected = new Set([...selected].filter((key) => keys.has(key)));
        for (const item of result.generations) if (selected.has(`generation:${item.id}`)) extraGenerations.set(item.id, item);
      }
      return result;
    } catch (error) {
      if (!controller.signal.aborted && requestedRoute === viewRoute()) inventoryError = true;
      return null;
    } finally {
      if (inventoryController === controller) {
        inventoryController = null;
        sync();
      }
    }
  }

  function syncView() {
    const classic = getState().galleryLayout === "classic";
    if ((classic || selectedScope) && getState().session && inventorySignature !== viewSignature()) void loadInventory();
    const header = root.querySelector(".classic-gallery-header");
    if (!header) return;
    const count = header.querySelector(".classic-gallery-count");
    const label = inventoryError ? "Count unavailable" : inventory ? [plural(inventory.generations.length, "generation"), inventory.collection_ids.length ? plural(inventory.collection_ids.length, "folder") : ""].filter(Boolean).join(" · ") : "Loading count…";
    if (count.textContent !== label) count.textContent = label;
    const control = header.querySelector("[data-select-view]");
    control.setAttribute("aria-checked", galleryViewChecked(inventory, selected));
    control.setAttribute("aria-busy", String(viewBusy));
    control.disabled = busy || viewBusy || (!inventoryError && inventory && !galleryViewKeys(inventory).length);
  }

  async function selectView(toggle = true) {
    if (busy || viewBusy) return;
    if (toggle && galleryViewChecked(inventory, selected) === "true") { finish(); return; }
    const focusHeader = document.activeElement?.matches("[data-select-view]");
    const restoreFocus = () => { if (focusHeader) root.querySelector("[data-select-view]")?.focus({ preventScroll: true }); };
    const request = ++viewRequest;
    const requestedRoute = viewRoute();
    viewBusy = true;
    sync();
    const result = await loadInventory();
    if (request !== viewRequest || requestedRoute !== viewRoute()) return;
    viewBusy = false;
    if (!result) { notify("Could not select all items. Try again.", "error"); sync(); restoreFocus(); return; }
    selectedScope = galleryViewScope(getState());
    for (const item of result.generations) extraGenerations.set(item.id, item);
    selected = new Set(galleryViewKeys(result));
    selecting = selected.size > 0;
    sync();
    restoreFocus();
  }

  function sync() {
    const state = getState();
    const currentRoute = viewRoute();
    if (!state.session || route !== currentRoute) {
      selected.clear(); extraGenerations.clear(); groupMembers.clear(); selecting = false; anchor = null; route = currentRoute;
      inventoryController?.abort(); inventoryController = null;
      inventory = null; inventorySignature = null; selectedScope = null; inventoryError = false;
      viewBusy = false; viewRequest += 1;
      if (!busy) activeDialog()?.close();
    }
    const visibleCards = cards();
    const visibleKeys = new Set(visibleCards.map(keyFor));
    for (const id of extraGenerations.keys()) if (!selectedScope && visibleKeys.has(`generation:${id}`)) extraGenerations.delete(id);
    selected = new Set([...selected].filter((key) => visibleKeys.has(key) || extraGenerations.has(key.slice("generation:".length))));
    for (const [id] of extraGenerations) if (!selected.has(`generation:${id}`)) extraGenerations.delete(id);
    if (state.favoritesFilter && !selectedScope) {
      for (const id of [...extraGenerations.keys()]) {
        if (!visibleKeys.has(`generation:${id}`)) {
          extraGenerations.delete(id);
          selected.delete(`generation:${id}`);
        }
      }
      for (const members of groupMembers.values()) {
        for (const id of [...members]) if (!visibleKeys.has(`generation:${id}`)) members.delete(id);
      }
      for (const [id, members] of [...groupMembers]) if (!members.size) groupMembers.delete(id);
    }
    selecting = selected.size > 0;
    for (const card of visibleCards) {
      const checked = selected.has(keyFor(card));
      card.classList.toggle("is-selected", checked);
      const control = card.querySelector(".card-select-button");
      control?.setAttribute("aria-checked", String(checked));
      control?.setAttribute("title", checked ? "Deselect card" : "Select card");
      card.querySelectorAll("[draggable]").forEach((image) => { image.draggable = !selecting; });
    }
    for (const group of root.querySelectorAll("[data-prompt-group]")) {
      const ids = new Set([...(groupMembers.get(group.dataset.promptGroup) || []), ...[...group.querySelectorAll('[data-gallery-card="generation"]')].map((card) => card.dataset.generationId)]);
      const count = [...ids].filter((id) => selected.has(`generation:${id}`)).length;
      group.querySelector("[data-prompt-group-select]")?.setAttribute("aria-checked", count === 0 ? "false" : count === Number(group.dataset.groupCount) ? "true" : "mixed");
    }
    syncView();
    root.querySelector(".app-shell")?.classList.toggle("gallery-selection-mode", selecting);
    const host = root.querySelector("#gallery-selection-toolbar");
    if (!host) return;
    host.hidden = !selecting;
    host.setAttribute("aria-busy", String(busy));
    const focusedAction = host.contains(document.activeElement) ? document.activeElement.dataset.bulkAction : null;
    const plan = selectionPlan(selected, selectionState());
    const classic = state.galleryLayout === "classic";
    const all = classic ? galleryViewChecked(inventory, selected) === "true" : visibleCards.length > 0 && visibleCards.every((card) => selected.has(keyFor(card)));
    host.innerHTML = `<span class="selection-count" role="status" aria-label="${selected.size} selected" title="${escapeHtml(plan.summary)}">${selected.size}<span class="selection-count-label"> selected</span></span>
      <button type="button" class="button low selection-tool" data-bulk-action="all" aria-label="${classic ? "Select all items in this view" : `Select loaded (${visibleCards.length})`}" title="${classic ? "Select the entire current view, including unloaded items" : `Select all ${visibleCards.length} loaded items`}" ${all || (!classic && !visibleCards.length) || busy || viewBusy ? "disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="7" y="7" width="14" height="14" rx="2" /><path d="M16 3H5a2 2 0 0 0-2 2v11m8-2 2 2 4-4" /></svg></button>
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
    if (busy || viewBusy) return;
    const key = keyFor(card);
    const order = cards().map(keyFor);
    if (range && selecting && order.includes(anchor)) {
      const [start, end] = [order.indexOf(anchor), order.indexOf(key)].sort((a, b) => a - b);
      addKeys(order.slice(start, end + 1));
    } else {
      if (selected.has(key)) selected.delete(key);
      else if (selectedScope || selected.size < 500) selected.add(key);
      else notify("Select at most 500 items at a time.", "error");
      anchor = key;
    }
    selecting = selected.size > 0;
    sync();
    card.querySelector(".card-select-button")?.focus({ preventScroll: true });
  }

  function addKeys(keys) {
    const next = new Set([...selected, ...keys]);
    if (!selectedScope && next.size > 500) { notify("Select at most 500 items at a time, or use Select all in Classic view.", "error"); return; }
    selected = next;
  }

  function finish() {
    if (busy) return;
    viewRequest += 1; viewBusy = false;
    selecting = false; selected.clear(); selectedScope = null; extraGenerations.clear(); sync();
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
    operationPlan = selectionPlan(selected, selectionState());
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
    const plan = selectionPlan(selected, selectionState());
    if (operation === "favorite" ? !plan.favorites.count || plan.favorites.allFavorited : !plan.downloadable) return;
    const selection = operation === "favorite" ? plan.favorites : plan;
    const body = JSON.stringify(requestBody(selection));
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
        for (const id of result.generation_ids) {
          const item = extraGenerations.get(id);
          if (item) extraGenerations.set(id, { ...item, is_favorite: true });
        }
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

  root.addEventListener("gallery-select-group", (event) => {
    if (busy) return;
    const { id, generations } = event.detail;
    const visibleGenerations = getState().favoritesFilter
      ? generations.filter((item) => item.is_favorite)
      : generations;
    const ids = visibleGenerations.map((item) => item.id);
    const all = ids.length && ids.every((item) => selected.has(`generation:${item}`));
    const next = new Set(selected);
    for (const item of ids) { if (all) next.delete(`generation:${item}`); else next.add(`generation:${item}`); }
    if (!selectedScope && next.size > 500) { notify("Select at most 500 items at a time. Clear some selections first.", "error"); return; }
    groupMembers.set(id, new Set(ids));
    for (const item of visibleGenerations) extraGenerations.set(item.id, item);
    selected = next; selecting = selected.size > 0;
    sync();
  });
  root.addEventListener("click", (event) => {
    if (event.target.closest("[data-select-view]")) {
      event.preventDefault(); event.stopImmediatePropagation(); void selectView(); return;
    }
    const action = event.target.closest("[data-bulk-action]")?.dataset.bulkAction;
    const card = event.target.closest("#gallery [data-gallery-card]");
    if (card && (selecting || event.target.closest(".card-select-button"))) {
      event.preventDefault(); event.stopImmediatePropagation(); choose(card, event.shiftKey); return;
    }
    if (!action) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (busy) return;
    if (action === "all") {
      if (getState().galleryLayout === "classic") void selectView(false);
      else { addKeys(cards().map(keyFor)); sync(); }
    }
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
    if ((!selecting && getState().galleryLayout !== "classic") || busy || activeDialog() || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); finish(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      if (getState().galleryLayout === "classic") void selectView(false);
      else { addKeys(cards().map(keyFor)); sync(); }
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
