import { loraDefaultPositiveStrength, loraStackError, moveLora, strongestLoraTrigger } from "./lora-stack.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const encoded = (value) => encodeURIComponent(value);

export function loraImagePath(sourceKey, controlId) {
  return `/api/workflows/${encoded(sourceKey)}/lora-images/${encoded(controlId)}`;
}

function subjectPreview(control, values, subjectAvailable = true) {
  const strongest = strongestLoraTrigger(control, values);
  if (!strongest.entry) return "Subject unchanged · no LoRA enabled";
  if (!subjectAvailable) return "Subject unchanged · Prompt Generation subject is unavailable";
  if (!strongest.triggerWord) return "Subject unchanged · LoRA has no published title";
  return `Subject on Apply: ${strongest.triggerWord}${strongest.triggerSource === "title" ? " (LoRA title)" : ""}`;
}

export function loraManagerMarkup({ control, values, memory = {}, images = {}, sourceName = "Workflow", subjectAvailable = true, error = "", busy = false }) {
  const rows = Array.isArray(values) ? values : control.default;
  const items = new Map(control.items.map((item) => [item.id, item]));
  const enabledCount = rows.filter((entry) => entry.strength > 0).length;
  const subject = subjectPreview(control, rows, subjectAvailable);
  const minimum = loraDefaultPositiveStrength(control);
  const list = rows.map((entry) => {
    const item = items.get(entry.id) || { label: entry.id };
    const image = images[entry.id]?.image_url || null;
    const remembered = Number(memory[entry.id]);
    const strength = entry.strength > 0 ? entry.strength : remembered > 0 && remembered <= control.maximum ? remembered : minimum || 0;
    const label = escape(item.label);
    const id = escape(entry.id);
    const disabled = entry.strength <= 0;
    return `<li class="lm-row ${disabled ? "" : "is-enabled"}" data-lora-id="${id}">
      <button type="button" class="lm-row-select" data-lora-toggle="${id}" aria-label="Toggle ${label}" aria-pressed="${!disabled}" title="${disabled ? "Enable" : "Disable"} ${label}" ${minimum === null ? "disabled" : ""}></button>
      <div class="lm-image-cell"><button type="button" class="lm-image-button" data-lora-image-change="${id}" aria-label="${image ? "Change" : "Add"} image for ${label}">${image ? `<img src="${escape(image)}" alt="" />` : '<span class="lm-add-image" aria-hidden="true"><b>＋</b><span>Add image</span></span>'}</button><button type="button" class="lm-image-action" data-lora-image-remove="${id}" aria-label="Remove image for ${label}" title="Remove image for ${label}" ${image ? "" : "hidden"}>×</button></div>
      <button type="button" class="icon-button lm-drag-handle" data-lora-handle draggable="true" aria-label="Reorder ${label}" aria-description="Drag to reorder, or use the Up and Down arrow keys.">⠿</button>
      <div class="lm-row-info"><span class="lm-row-name" title="${escape(item.description || "No verified trigger word is published; Subject name uses this LoRA title.")}">${label}</span><span class="lm-row-description">${escape(item.description || "Usage guidance not published")}</span><span class="lm-row-state">${disabled ? "Off" : "Enabled"}</span></div>
    <div class="lm-strength"><span class="lm-strength-label">${disabled ? "Strength when enabled" : "Strength"}</span><input type="range" data-lora-range="${id}" min="${Math.max(Number(control.step), Number(control.minimum))}" max="${control.maximum}" step="${control.step}" value="${strength}" aria-label="${label} strength slider" ${disabled ? "disabled" : ""} /><input type="number" data-lora-number="${id}" min="${Math.max(Number(control.step), Number(control.minimum))}" max="${control.maximum}" step="${control.step}" value="${Number(strength).toFixed(2)}" aria-label="${label} strength" ${disabled ? "disabled" : ""} /></div>
    </li>`;
  }).join("");
  return `<form method="dialog" class="dialog-frame lm-dialog-frame"><header class="dialog-header lm-dialog-header"><div><h2 id="lora-manager-title">Manage LoRAs</h2><p>${escape(sourceName)} · Available for this workflow</p></div><button type="button" class="icon-button lm-close-button" data-lora-cancel aria-label="Cancel and close LoRA manager">×</button></header>
    <div class="lm-dialog-content"><div class="lm-dialog-intro"><div><strong>${enabledCount} of ${rows.length} enabled</strong><span>Applied in list order, top to bottom</span></div><p>Reference images are shared with everyone after Apply.</p></div><ol class="lm-list" aria-label="Available LoRAs">${list}</ol><p class="visually-hidden" data-lora-reorder-status role="status" aria-live="polite"></p></div>
    <footer class="dialog-actions lm-dialog-footer"><button type="button" class="button low" data-lora-all-off ${enabledCount ? "" : "disabled"}>All off</button><span class="lm-subject-preview" data-lora-subject-preview>${escape(subject)}</span><div class="lm-footer-buttons"><button type="button" class="button secondary" data-lora-cancel>Cancel</button><button type="button" class="button primary" data-lora-apply ${busy ? "disabled" : ""}>${busy ? "Applying…" : "Apply"}</button></div>${error ? `<p class="form-error lm-error" role="alert">${escape(error)}</p>` : ""}</footer></form><input class="visually-hidden" type="file" data-lora-file accept="image/png,image/jpeg,image/webp" tabindex="-1" aria-hidden="true" />`;
}

export function installLoraManager(root, { api, context, apply, onImages }) {
  let draft = null;
  let returnFocus = null;
  let fileTarget = null;
  let dragId = null;
  let touchDrag = null;
  const dialog = () => root.querySelector("#lora-manager-dialog");
  const revoke = () => {
    if (!draft) return;
    for (const change of draft.changes.values()) if (change.preview) URL.revokeObjectURL(change.preview);
  };
  const render = (focus = null) => {
    if (!draft) return;
    const target = dialog();
    const scrollTop = target.querySelector(".lm-dialog-content")?.scrollTop || 0;
    target.innerHTML = loraManagerMarkup({ control: draft.control, values: draft.values, memory: draft.memory, images: draft.images, sourceName: draft.sourceName, subjectAvailable: draft.subjectAvailable, error: draft.error, busy: draft.busy });
    target.querySelector(".lm-dialog-content").scrollTop = scrollTop;
    if (focus) target.querySelector(focus)?.focus({ preventScroll: true });
  };
  const close = () => dialog()?.close("cancel");
  async function open(button) {
    const ctx = context(button.dataset.loraControlId);
    if (!ctx || dialog()?.open) return;
    returnFocus = button;
    draft = { ...ctx, values: structuredClone(ctx.values), memory: { ...ctx.memory }, images: structuredClone(ctx.images || {}), changes: new Map(), error: "", busy: false };
    render();
    dialog().showModal();
    dialog().querySelector("[data-lora-toggle]")?.focus({ preventScroll: true });
    try {
      const payload = await api(loraImagePath(ctx.sourceKey, ctx.control.id));
      if (!draft || draft.sourceKey !== ctx.sourceKey || draft.control.id !== ctx.control.id) return;
      const serverImages = Object.fromEntries((payload.items || []).map((item) => [item.id, item]));
      draft.images = structuredClone(serverImages);
      for (const [id, change] of draft.changes) draft.images[id] = { ...draft.images[id], image_url: change.preview || null };
      onImages(ctx.sourceKey, ctx.control.id, serverImages);
      render("[data-lora-toggle]");
    } catch (error) {
      if (draft) { draft.error = `Images could not be loaded: ${error.message}`; render(); }
    }
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
    if (context(draft.control.id)?.sourceKey !== draft.sourceKey) {
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
      if (draft.changes.size) {
        const form = new FormData();
        const changes = [];
        let index = 0;
        for (const [id, change] of draft.changes) {
          const version = draft.images[id]?.version;
          if (!version) throw new Error("Image versions are unavailable. Close and reopen the manager.");
          if (change.action === "set") {
            const fileKey = `image_${index++}`;
            form.append(fileKey, change.file);
            changes.push({ id, version, action: "set", file_key: fileKey });
          } else changes.push({ id, version, action: "remove" });
        }
        form.append("changes", JSON.stringify(changes));
        const result = await api(loraImagePath(draft.sourceKey, draft.control.id), { method: "POST", body: form });
        draft.images = Object.fromEntries((result.items || []).map((item) => [item.id, item]));
        onImages(draft.sourceKey, draft.control.id, structuredClone(draft.images));
      }
      apply(draft.control.id, draft.values, draft.memory, draft.sourceKey);
      dialog().close("apply");
    } catch (error) {
      draft.error = error.code === "lora_image_conflict" ? "An image changed while this manager was open. Close and reopen it to review the latest images before applying." : error.message || "Could not apply LoRAs.";
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
    if (event.target.closest("[data-lora-cancel]")) { close(); return; }
    if (event.target.closest("[data-lora-apply]")) { void save(); return; }
    if (event.target.closest("[data-lora-all-off]")) {
      for (const entry of draft.values) if (entry.strength > 0) draft.memory[entry.id] = entry.strength;
      draft.values = draft.values.map((entry) => ({ id: entry.id, strength: 0 }));
      render(); return;
    }
    const image = event.target.closest("[data-lora-image-change]");
    if (image) {
      fileTarget = image.dataset.loraImageChange;
      dialog().querySelector("[data-lora-file]")?.click();
      return;
    }
    const remove = event.target.closest("[data-lora-image-remove]");
    if (remove) {
      const id = remove.dataset.loraImageRemove;
      const prior = draft.changes.get(id);
      if (prior?.preview) URL.revokeObjectURL(prior.preview);
      draft.changes.set(id, { action: "remove" });
      draft.images[id] = { ...draft.images[id], image_url: null };
      render(`[data-lora-image-change="${CSS.escape(id)}"]`);
    }
  });
  root.addEventListener("change", (event) => {
    if (!draft) return;
    const fileInput = event.target.closest("[data-lora-file]");
    if (fileInput) {
      const file = fileInput.files?.[0];
      if (!file || !fileTarget) return;
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) { draft.error = "Choose a PNG, JPEG, or WebP image."; render(); return; }
      const prior = draft.changes.get(fileTarget);
      if (prior?.preview) URL.revokeObjectURL(prior.preview);
      const preview = URL.createObjectURL(file);
      draft.changes.set(fileTarget, { action: "set", file, preview });
      draft.images[fileTarget] = { ...draft.images[fileTarget], image_url: preview };
      render(`[data-lora-image-change="${CSS.escape(fileTarget)}"]`);
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
    revoke();
    draft = null;
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    else if (controlId) root.querySelector(`[data-lora-open][data-lora-control-id="${CSS.escape(controlId)}"]`)?.focus({ preventScroll: true });
  }, true);
  return { close, isOpen: () => Boolean(dialog()?.open) };
}
