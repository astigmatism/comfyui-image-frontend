import { loraDefaultPositiveStrength, loraStackError, moveLora, strongestLoraTrigger } from "./lora-stack.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const encoded = (value) => encodeURIComponent(value);

export function loraImagePath(sourceKey, controlId) {
  return `/api/workflows/${encoded(sourceKey)}/lora-images/${encoded(controlId)}`;
}

export function loraPublicationRevisionMatches(first, second) {
  if (!first || !second) return !first && !second;
  return ["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"].every((key) => first[key] === second[key]);
}

function subjectPreview(control, values, subjectAvailable = true) {
  if (subjectAvailable === null) return "Selected prompts stay unchanged";
  const strongest = strongestLoraTrigger(control, values);
  if (!strongest.entry) return "Subject unchanged · no LoRA enabled";
  if (!subjectAvailable) return "Subject unchanged · Prompt Generation subject is unavailable";
  if (!strongest.triggerWord) return "Subject unchanged · LoRA has no published title";
  return `Subject on Apply: ${strongest.triggerWord}${strongest.triggerSource === "title" ? " (LoRA title)" : ""}`;
}

export function loraManagerMarkup({ control, values, memory = {}, images = {}, sourceName = "Workflow", subjectAvailable = true, error = "", busy = false, imageStates = {}, imagesLoading = false, imageError = "" }) {
  const rows = Array.isArray(values) ? values : control.default;
  const items = new Map(control.items.map((item) => [item.id, item]));
  const enabledCount = rows.filter((entry) => entry.strength > 0).length;
  const subject = subjectPreview(control, rows, subjectAvailable);
  const minimum = loraDefaultPositiveStrength(control);
  const list = rows.map((entry) => {
    const item = items.get(entry.id) || { label: entry.id };
    const imageState = imageStates[entry.id] || {};
    const image = imageState.saving ? imageState.preview : images[entry.id]?.image_url || null;
    const imageDisabled = imagesLoading || imageState.saving;
    const remembered = Number(memory[entry.id]);
    const strength = entry.strength > 0 ? entry.strength : remembered > 0 && remembered <= control.maximum ? remembered : minimum || 0;
    const label = escape(item.label);
    const id = escape(entry.id);
    const disabled = entry.strength <= 0;
    return `<li class="lm-row ${disabled ? "" : "is-enabled"}" data-lora-id="${id}">
      <button type="button" class="lm-row-select" data-lora-toggle="${id}" aria-label="Toggle ${label}" aria-pressed="${!disabled}" title="${disabled ? "Enable" : "Disable"} ${label}" ${minimum === null ? "disabled" : ""}></button>
      <div class="lm-image-cell"><button type="button" class="lm-image-button" data-lora-image-change="${id}" aria-label="${image ? "Change" : "Add"} image for ${label}" ${imageDisabled ? "disabled" : ""}>${image ? `<img src="${escape(image)}" alt="" />` : '<span class="lm-add-image" aria-hidden="true"><b>＋</b><span>Add image</span></span>'}</button><button type="button" class="lm-image-action" data-lora-image-remove="${id}" aria-label="Remove image for ${label}" title="Remove image for ${label}" ${image ? "" : "hidden"} ${imageDisabled ? "disabled" : ""}>×</button><span class="lm-image-status${imageState.error ? " is-error" : ""}" role="${imageState.error ? "alert" : "status"}">${escape(imageState.saving ? "Saving…" : imageState.message || "")}</span></div>
      <button type="button" class="icon-button lm-drag-handle" data-lora-handle draggable="true" aria-label="Reorder ${label}" aria-description="Drag to reorder, or use the Up and Down arrow keys.">⠿</button>
      <div class="lm-row-info"><span class="lm-row-heading"><span class="lm-row-name" title="${escape(item.description || "No verified trigger word is published; Subject name uses this LoRA title.")}">${label}</span><span class="lm-row-state">${disabled ? "Off" : "✓ Enabled"}</span></span><span class="lm-row-description">${escape(item.description || "Usage guidance not published")}</span></div>
    <div class="lm-strength"><span class="lm-strength-label">${disabled ? "Strength when enabled" : "Strength"}</span><input type="range" data-lora-range="${id}" min="${Math.max(Number(control.step), Number(control.minimum))}" max="${control.maximum}" step="${control.step}" value="${strength}" aria-label="${label} strength slider" ${disabled ? "disabled" : ""} /><input type="number" data-lora-number="${id}" min="${Math.max(Number(control.step), Number(control.minimum))}" max="${control.maximum}" step="${control.step}" value="${Number(strength).toFixed(2)}" aria-label="${label} strength" ${disabled ? "disabled" : ""} /></div>
    </li>`;
  }).join("");
  return `<form method="dialog" class="dialog-frame lm-dialog-frame"><header class="dialog-header lm-dialog-header"><div><h2 id="lora-manager-title">Manage LoRAs</h2><p>${escape(sourceName)} · Shared LoRA library</p></div><button type="button" class="icon-button lm-close-button" data-lora-cancel aria-label="Cancel and close LoRA manager">×</button></header>
    <div class="lm-dialog-content"><div class="lm-dialog-intro"><div><strong>${enabledCount} of ${rows.length} enabled</strong><span>Applied in list order, top to bottom</span></div><p>Thumbnail changes save automatically and are shared with everyone. Cancel does not undo them.</p></div>${imageError ? `<p class="form-error lm-image-load-error" role="alert">${escape(imageError)} <button type="button" class="button low" data-lora-images-reload>Reload images</button></p>` : ""}<ol class="lm-list" aria-label="Available LoRAs">${list}</ol><p class="visually-hidden" data-lora-reorder-status role="status" aria-live="polite"></p></div>
    <footer class="dialog-actions lm-dialog-footer"><button type="button" class="button low" data-lora-all-off ${enabledCount ? "" : "disabled"}>All off</button><span class="lm-subject-preview" data-lora-subject-preview>${escape(subject)}</span><div class="lm-footer-buttons"><button type="button" class="button secondary" data-lora-cancel>Cancel</button><button type="button" class="button primary" data-lora-apply ${busy ? "disabled" : ""}>${busy ? "Applying…" : "Apply"}</button></div>${error ? `<p class="form-error lm-error" role="alert">${escape(error)}</p>` : ""}</footer></form><input class="visually-hidden" type="file" data-lora-file accept="image/png,image/jpeg,image/webp" tabindex="-1" aria-hidden="true" />`;
}

export function installLoraManager(root, { api, context, apply, onImages, notify = () => {} }) {
  let draft = null;
  let returnFocus = null;
  let fileTarget = null;
  let dragId = null;
  let touchDrag = null;
  const dialog = () => root.querySelector("#lora-manager-dialog");
  // Image work outlives the selection draft, including Cancel and owner closure.
  const imageStores = new Map();
  const storeKey = (ctx) => JSON.stringify([ctx.sourceKey, ctx.control.id,
    ...["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"].map((key) => ctx.publicationRevision?.[key] || null)]);
  const matches = (store, ctx) => ctx && store.active && store.key === storeKey(ctx);
  const refreshImages = (store) => { if (draft?.imageStore === store) render(); };
  const publishImages = (store) => {
    for (const owner of ["panel", "rerun"]) {
      if (matches(store, context(store.controlId, owner))) onImages(store.sourceKey, store.controlId, structuredClone(store.images), owner);
    }
  };
  // Thumbnails are shared by every workflow that lists the LoRA, so a saved change
  // makes other workflows' cached image lists stale; they reload when next opened.
  const forgetOtherStores = (store) => {
    for (const [key, other] of imageStores) {
      if (other !== store && !other.loading && !Object.values(other.states || {}).some((item) => item?.saving)) {
        other.active = false;
        imageStores.delete(key);
      }
    }
  };
  function loadImages(store) {
    if (store.loading) return store.loading;
    store.loadError = "";
    store.loading = (async () => {
      try {
        const payload = await api(loraImagePath(store.sourceKey, store.controlId));
        if (!store.active) return;
        store.images = Object.fromEntries((payload.items || []).map((item) => [item.id, item]));
        store.loaded = true;
        publishImages(store);
      } catch (error) {
        store.loaded = false;
        store.loadError = `Images could not be loaded: ${error.message || "Try again."}`;
      } finally {
        store.loading = null;
        refreshImages(store);
      }
    })();
    refreshImages(store);
    return store.loading;
  }
  function saveImage(id, action, file = null) {
    const session = draft;
    const store = session?.imageStore;
    if (!store?.loaded || store.loading || store.states[id]?.saving) return;
    if (!matches(store, context(session.control.id, session.owner))) {
      store.loadError = "The workflow changed. Reopen the LoRA manager.";
      refreshImages(store);
      return;
    }
    const preview = file ? URL.createObjectURL(file) : null;
    const version = store.images[id]?.version;
    store.states[id] = { saving: true, preview };
    refreshImages(store);
    // Serialize mutations because every response includes the entire image catalog.
    store.queue = store.queue.then(async () => {
      try {
        if (!store.active || !store.loaded) throw new Error("Reload images before trying again.");
        if (!version) throw new Error("Image versions are unavailable. Reload images and try again.");
        const change = { id, version, action };
        const form = new FormData();
        if (file) { change.file_key = "image_0"; form.append("image_0", file); }
        form.append("changes", JSON.stringify([change]));
        const result = await api(loraImagePath(store.sourceKey, store.controlId), { method: "POST", body: form });
        if (!store.active) return;
        store.images = Object.fromEntries((result.items || []).map((item) => [item.id, item]));
        store.states[id] = { message: "Saved" };
        publishImages(store);
        forgetOtherStores(store);
      } catch (error) {
        const conflict = error.code === "lora_image_conflict";
        const message = conflict
          ? "This image changed elsewhere. Review the latest image and try your edit again."
          : `Image was not saved: ${error.message || "Try again."}`;
        // Reconcile even a lost response: the server may have committed the upload.
        if (store.active) await loadImages(store);
        store.states[id] = { error: true, message };
        if (!dialog()?.open || draft?.imageStore !== store) notify(message, "error");
      } finally {
        if (preview) URL.revokeObjectURL(preview);
        if (store.states[id]?.saving) delete store.states[id];
        refreshImages(store);
      }
    });
  }
  const render = (focus = null) => {
    if (!draft) return;
    const target = dialog();
    const active = target.contains(document.activeElement) ? document.activeElement : null;
    let inputValue = null;
    if (!focus && active) {
      const attribute = [...active.attributes].find((item) => item.name.startsWith("data-lora-"));
      if (attribute) {
        focus = `[${attribute.name}="${CSS.escape(attribute.value)}"]`;
        if (active.closest("[data-lora-id]")) focus = `[data-lora-id="${CSS.escape(active.closest("[data-lora-id]").dataset.loraId)}"] ${focus}`;
        if (active.matches("[data-lora-number]")) inputValue = active.value;
      }
    }
    const scrollTop = target.querySelector(".lm-dialog-content")?.scrollTop || 0;
    const markup = loraManagerMarkup({ control: draft.control, values: draft.values, memory: draft.memory, images: draft.imageStore.images, imageStates: draft.imageStore.states, imagesLoading: !draft.imageStore.loaded || Boolean(draft.imageStore.loading), imageError: draft.imageStore.loadError, sourceName: draft.sourceName, subjectAvailable: draft.subjectAvailable, error: draft.error, busy: draft.busy });
    const frame = target.querySelector(".lm-dialog-frame");
    if (frame) {
      // Keep the file input alive if another thumbnail finishes saving while
      // the native file chooser is open.
      const template = document.createElement("template");
      template.innerHTML = markup;
      frame.replaceWith(template.content.querySelector(".lm-dialog-frame"));
    } else target.innerHTML = markup;
    target.querySelector(".lm-dialog-content").scrollTop = scrollTop;
    if (focus) {
      const element = target.querySelector(focus);
      if (inputValue !== null && element) element.value = inputValue;
      if (element?.disabled) element.closest("[data-lora-id]")?.querySelector("[data-lora-toggle]")?.focus({ preventScroll: true });
      else element?.focus({ preventScroll: true });
    }
  };
  const close = () => dialog()?.close("cancel");
  async function open(button) {
    const owner = button.dataset.controlContext || "panel";
    const ctx = context(button.dataset.loraControlId, owner);
    if (!ctx || dialog()?.open) return;
    returnFocus = button;
    const key = storeKey(ctx);
    let imageStore = imageStores.get(key);
    if (!imageStore) {
      imageStore = { key, sourceKey: ctx.sourceKey, controlId: ctx.control.id, active: true,
        images: structuredClone(ctx.images || {}), states: {}, loaded: false, loading: null, loadError: "", queue: Promise.resolve() };
      imageStores.set(key, imageStore);
    }
    draft = { ...ctx, owner, publicationRevision: structuredClone(ctx.publicationRevision || null), values: structuredClone(ctx.values), memory: { ...ctx.memory }, imageStore, error: "", busy: false };
    render();
    dialog().oncancel = (event) => { if (draft?.busy) event.preventDefault(); };
    dialog().showModal();
    dialog().querySelector("[data-lora-toggle]")?.focus({ preventScroll: true });
    if (!Object.values(imageStore.states).some((state) => state.saving)) await loadImages(imageStore);
  }
  function updateStrength(id, strength) {
    if (!draft) return;
    draft.values = draft.values.map((entry) => entry.id === id ? { id, strength } : entry);
    if (strength > 0) draft.memory[id] = strength;
    const preview = dialog()?.querySelector("[data-lora-subject-preview]");
    if (preview) preview.textContent = subjectPreview(draft.control, draft.values, draft.subjectAvailable);
  }
  function toggle(id) {
    if (!draft) return;
    const previous = draft.values.find((entry) => entry.id === id)?.strength || 0;
    if (previous > 0) draft.memory[id] = previous;
    updateStrength(id, previous > 0 ? 0 : draft.memory[id] || loraDefaultPositiveStrength(draft.control));
    render(`[data-lora-toggle="${CSS.escape(id)}"]`);
  }
  function move(id, to) {
    if (!draft || to < 0 || to >= draft.values.length) return;
    draft.values = moveLora(draft.values, id, to);
    render(`[data-lora-id="${CSS.escape(id)}"] [data-lora-handle]`);
    const label = draft.control.items.find((item) => item.id === id)?.label || id;
    dialog()?.querySelector("[data-lora-reorder-status]")?.replaceChildren(document.createTextNode(`${label} moved to position ${to + 1} of ${draft.values.length}.`));
  }
  async function save() {
    if (!draft || draft.busy) return;
    const session = draft;
    const current = context(draft.control.id, draft.owner);
    if (current?.sourceKey !== draft.sourceKey || !loraPublicationRevisionMatches(current?.publicationRevision, draft.publicationRevision)) {
      draft.error = "The workflow changed. Reopen the LoRA manager.";
      render();
      return;
    }
    const error = loraStackError(draft.control, draft.values);
    if (error) { draft.error = error; render(); return; }
    draft.busy = true;
    draft.error = "";
    render();
    try {
      if (draft !== session) return;
      apply(draft.control.id, draft.values, draft.memory, draft.sourceKey, draft.owner);
      dialog().close("apply");
    } catch (error) {
      if (draft !== session) return;
      draft.error = error.message || "Could not apply LoRAs.";
      draft.busy = false;
      render();
    }
  }
  root.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-lora-open]");
    if (opener) { void open(opener); return; }
    if (!draft || !event.target.closest("#lora-manager-dialog")) return;
    const selection = event.target.closest("[data-lora-toggle]");
    if (selection && !selection.disabled) { toggle(selection.dataset.loraToggle); return; }
    if (event.target.closest("[data-lora-cancel]")) { if (!draft.busy) close(); return; }
    if (event.target.closest("[data-lora-apply]")) { void save(); return; }
    if (event.target.closest("[data-lora-all-off]")) {
      for (const entry of draft.values) if (entry.strength > 0) draft.memory[entry.id] = entry.strength;
      draft.values = draft.values.map((entry) => ({ id: entry.id, strength: 0 }));
      render(); return;
    }
    if (event.target.closest("[data-lora-images-reload]")) {
      if (!Object.values(draft.imageStore.states).some((state) => state.saving)) void loadImages(draft.imageStore);
      return;
    }
    const image = event.target.closest("[data-lora-image-change]");
    if (image && !image.disabled) {
      fileTarget = { id: image.dataset.loraImageChange, session: draft };
      dialog().querySelector("[data-lora-file]")?.click();
      return;
    }
    const remove = event.target.closest("[data-lora-image-remove]");
    if (remove && !remove.disabled) saveImage(remove.dataset.loraImageRemove, "remove");
  });
  root.addEventListener("change", (event) => {
    if (!draft) return;
    const fileInput = event.target.closest("[data-lora-file]");
    if (fileInput) {
      const file = fileInput.files?.[0];
      fileInput.value = "";
      if (!file || !fileTarget || fileTarget.session !== draft) return;
      const id = fileTarget.id;
      fileTarget = null;
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        draft.imageStore.states[id] = { error: true, message: "Choose a PNG, JPEG, or WebP image." };
        render(); return;
      }
      saveImage(id, "set", file);
      return;
    }
    const number = event.target.closest("[data-lora-number]");
    if (number) {
      const value = Number(number.value);
      const id = number.dataset.loraNumber;
      if (!number.checkValidity() || !(value > 0)) { number.reportValidity(); return; }
      updateStrength(id, value);
      render(`[data-lora-number="${CSS.escape(id)}"]`);
    }
  });
  root.addEventListener("input", (event) => {
    if (!draft) return;
    const range = event.target.closest("[data-lora-range]");
    if (range) {
      const id = range.dataset.loraRange;
      updateStrength(id, Number(range.value));
      const number = range.closest(".lm-strength")?.querySelector("[data-lora-number]");
      if (number) number.value = Number(range.value).toFixed(2);
    }
    const number = event.target.closest("[data-lora-number]");
    if (number && number.checkValidity() && Number(number.value) > 0) {
      const id = number.dataset.loraNumber;
      updateStrength(id, Number(number.value));
      const range = number.closest(".lm-strength")?.querySelector("[data-lora-range]");
      if (range) range.value = number.value;
    }
  });
  root.addEventListener("keydown", (event) => {
    const handle = event.target.closest("#lora-manager-dialog [data-lora-handle]");
    if (!draft || !handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const id = handle.closest("[data-lora-id]").dataset.loraId;
    move(id, draft.values.findIndex((entry) => entry.id === id) + (event.key === "ArrowUp" ? -1 : 1));
  });
  root.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("#lora-manager-dialog [data-lora-handle]");
    if (!draft || !handle) return;
    dragId = handle.closest("[data-lora-id]").dataset.loraId;
    event.dataTransfer.setData("text/plain", dragId);
    event.dataTransfer.effectAllowed = "move";
  });
  root.addEventListener("dragover", (event) => {
    if (!dragId) return;
    const row = event.target.closest("#lora-manager-dialog [data-lora-id]");
    if (row) { event.preventDefault(); row.classList.add("is-drop-target"); }
  });
  root.addEventListener("dragleave", (event) => event.target.closest("#lora-manager-dialog [data-lora-id]")?.classList.remove("is-drop-target"));
  root.addEventListener("drop", (event) => {
    if (!draft || !dragId) return;
    const row = event.target.closest("#lora-manager-dialog [data-lora-id]");
    if (!row) return;
    event.preventDefault();
    const index = draft.values.findIndex((entry) => entry.id === row.dataset.loraId);
    move(dragId, index);
    dragId = null;
  });
  root.addEventListener("dragend", () => { dragId = null; });
  root.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest("#lora-manager-dialog [data-lora-handle]");
    if (!draft || !handle || event.pointerType === "mouse") return;
    touchDrag = handle.closest("[data-lora-id]").dataset.loraId;
    handle.setPointerCapture(event.pointerId);
  });
  root.addEventListener("pointermove", (event) => {
    if (!draft || !touchDrag) return;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("#lora-manager-dialog [data-lora-id]");
    dialog()?.querySelectorAll(".is-drop-target").forEach((row) => row.classList.remove("is-drop-target"));
    target?.classList.add("is-drop-target");
  });
  root.addEventListener("pointerup", (event) => {
    if (!draft || !touchDrag) return;
    const id = touchDrag;
    touchDrag = null;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("#lora-manager-dialog [data-lora-id]");
    dialog()?.querySelectorAll(".is-drop-target").forEach((row) => row.classList.remove("is-drop-target"));
    if (target) move(id, draft.values.findIndex((entry) => entry.id === target.dataset.loraId));
  });
  root.addEventListener("pointercancel", () => { touchDrag = null; });
  root.addEventListener("close", (event) => {
    if (event.target !== dialog()) return;
    const controlId = draft?.control.id;
    const owner = draft?.owner;
    fileTarget = null;
    draft = null;
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    else if (controlId) root.querySelector(`${owner === "rerun" ? "#gallery-rerun-dialog[open]" : "#generation-panel"} [data-lora-open][data-lora-control-id="${CSS.escape(controlId)}"]`)?.focus({ preventScroll: true });
  }, true);
  return {
    close,
    closeForOwner: (owner) => { if (draft?.owner === owner) close(); },
    isOpen: () => Boolean(dialog()?.open),
    // A null key means every workflow changed (a shared LoRA library operation).
    invalidateSource: (sourceKey) => {
      for (const [key, store] of imageStores) {
        if (sourceKey === null || store.sourceKey === sourceKey) { store.active = false; imageStores.delete(key); }
      }
      if (sourceKey !== null && draft?.sourceKey !== sourceKey) return;
      if (draft) close();
    },
    refresh: () => { if (draft && dialog()?.open) render(); },
  };
}
