const initialRows = [
  {
    id: "tifa",
    label: "Tifa",
    description: "Verified trigger: TifaLockhart",
    trigger: "TifaLockhart",
    strength: 1,
    lastStrength: 1,
    image: "./assets/cartographer.webp",
  },
  {
    id: "claire",
    label: "Claire",
    description: "Usage guidance not published",
    trigger: null,
    strength: 0.65,
    lastStrength: 0.65,
    image: "./assets/night-botanist.webp",
  },
  {
    id: "spread",
    label: "Spread",
    description: "Usage guidance not published",
    trigger: null,
    strength: 0,
    lastStrength: 1,
    image: "./assets/glass-orchid.webp",
  },
  {
    id: "nexblend",
    label: "NexBlend08",
    description: "Usage guidance not published",
    trigger: null,
    strength: 0,
    lastStrength: 1,
    image: null,
  },
];

const shell = document.querySelector("#app");
const dialog = document.querySelector("#lm-dialog");
const list = document.querySelector("#lm-list");
const imagePicker = document.querySelector("#lm-image-picker");
const summary = document.querySelector("#lm-summary");
const subject = document.querySelector("#lm-subject");
const manageButton = document.querySelector("#lm-manage-button");
const toast = document.querySelector("#lm-toast");

let committed = structuredClone(initialRows);
let draft = null;
let imageTargetId = null;
let dragId = null;
let touchDrag = null;
let toastTimer = null;
let returnFocus = manageButton;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/gu, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function formatStrength(value) {
  return Number(value).toFixed(2);
}

function activeRows(rows) {
  return rows.filter((row) => row.strength > 0);
}

function strongestRow(rows) {
  return rows.reduce((best, row) => row.strength > 0 && (!best || row.strength > best.strength) ? row : best, null);
}

function imageMarkup(row) {
  return row.image
    ? `<img src="${escapeHtml(row.image)}" alt="" />`
    : '<span class="lm-add-image" aria-hidden="true"><b>＋</b><span>Add image</span></span>';
}

function renderSummary() {
  const active = activeRows(committed);
  document.querySelector("#lm-panel-count").textContent = `${active.length} active`;
  summary.innerHTML = active.length
    ? `<ul class="lm-summary-list">${active.map((row) => `<li class="lm-summary-item"><span class="lm-summary-thumb">${row.image ? `<img src="${escapeHtml(row.image)}" alt="" />` : '<span aria-hidden="true">✧</span>'}</span><span class="lm-summary-name">${escapeHtml(row.label)}</span><span class="lm-summary-strength">${formatStrength(row.strength)}</span></li>`).join("")}</ul>`
    : '<p class="lm-empty-summary">No LoRAs enabled.</p>';
}

function rowMarkup(row) {
  const enabled = row.strength > 0;
  const strength = enabled ? row.strength : row.lastStrength;
  const label = escapeHtml(row.label);
  const id = escapeHtml(row.id);
  return `<li class="lm-row ${enabled ? "is-enabled" : ""}" data-lora-id="${id}">
    <button type="button" class="lm-row-select" data-lora-toggle="${id}" aria-label="Toggle ${label}" aria-pressed="${enabled}" title="${enabled ? "Disable" : "Enable"} ${label}"></button>
    <div class="lm-image-cell">
      <button type="button" class="lm-image-button" data-image-change="${id}" aria-label="${row.image ? "Change" : "Add"} image for ${label}" title="${row.image ? "Change" : "Add"} image for ${label}">${imageMarkup(row)}</button>
      <button type="button" class="lm-image-action" data-image-remove="${id}" aria-label="Remove image for ${label}" title="Remove image for ${label}" ${row.image ? "" : "hidden"}>×</button>
    </div>
    <button type="button" class="icon-button lm-drag-handle" draggable="true" aria-label="Reorder ${label}" aria-description="Drag to reorder, or use the Up and Down arrow keys.">⠿</button>
    <div class="lm-row-info"><span class="lm-row-name">${label}</span><span class="lm-row-description">${escapeHtml(row.description)}</span><span class="lm-row-state">${enabled ? "Enabled" : "Off"}</span></div>
    <div class="lm-strength"><span class="lm-strength-label">${enabled ? "Strength" : "Strength when enabled"}</span><input type="range" data-lora-range="${id}" min="0.05" max="2" step="0.05" value="${strength}" aria-label="${label} strength slider" ${enabled ? "" : "disabled"} /><input type="number" data-lora-number="${id}" min="0.05" max="2" step="0.05" value="${formatStrength(strength)}" aria-label="${label} strength" ${enabled ? "" : "disabled"} /></div>
  </li>`;
}

function updateSubjectPreview() {
  if (!draft) return;
  const strongest = strongestRow(draft);
  const preview = !strongest
    ? "Subject unchanged · no LoRA enabled"
    : strongest.trigger
      ? `Subject on Apply: ${strongest.trigger}`
      : `Subject on Apply: ${strongest.label} (LoRA title)`;
  document.querySelector("#lm-subject-preview").textContent = preview;
}

function renderDialog() {
  if (!draft) return;
  const scrollTop = dialog.querySelector(".lm-dialog-content").scrollTop;
  const count = activeRows(draft).length;
  document.querySelector("#lm-dialog-count").textContent = `${count} of ${draft.length} enabled`;
  document.querySelector("#lm-all-off").disabled = count === 0;
  list.innerHTML = draft.map(rowMarkup).join("");
  dialog.querySelector(".lm-dialog-content").scrollTop = scrollTop;
  updateSubjectPreview();
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 3000);
}

function openDialog() {
  if (dialog.open) return;
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : manageButton;
  draft = structuredClone(committed);
  dialog.returnValue = "";
  renderDialog();
  dialog.showModal();
  queueMicrotask(() => list.querySelector("[data-lora-toggle]")?.focus({ preventScroll: true }));
}

function closeDialog(value) {
  if (dialog.open) dialog.close(value);
}

function applyDialog() {
  if (!draft) return;
  const invalid = list.querySelector('input[type="number"]:invalid');
  if (invalid) {
    invalid.reportValidity();
    invalid.focus();
    return;
  }
  committed = structuredClone(draft);
  const strongest = strongestRow(committed);
  if (strongest) subject.value = strongest.trigger || strongest.label;
  renderSummary();
  closeDialog("apply");
  showToast("LoRA choices and images applied to this preview.");
}

function rowById(id) {
  return draft?.find((row) => row.id === id);
}

function moveRow(id, targetIndex) {
  if (!draft) return;
  const index = draft.findIndex((row) => row.id === id);
  if (index < 0 || targetIndex < 0 || targetIndex >= draft.length || index === targetIndex) return;
  const [row] = draft.splice(index, 1);
  draft.splice(targetIndex, 0, row);
  renderDialog();
  list.querySelector(`[data-lora-id="${CSS.escape(id)}"] .lm-drag-handle`)?.focus({ preventScroll: true });
  document.querySelector("#lm-reorder-status").textContent = `${row.label} moved to position ${targetIndex + 1} of ${draft.length}.`;
}

manageButton.addEventListener("click", openDialog);
document.querySelector("#lm-lora-trigger").addEventListener("click", () => {
  const section = document.querySelector("#lm-lora-section");
  const trigger = document.querySelector("#lm-lora-trigger");
  const body = document.querySelector("#lm-lora-body");
  const expanded = !section.classList.contains("is-expanded");
  section.classList.toggle("is-expanded", expanded);
  trigger.setAttribute("aria-expanded", String(expanded));
  body.setAttribute("aria-hidden", String(!expanded));
  body.inert = !expanded;
});
document.querySelector("#lm-close-button").addEventListener("click", () => closeDialog("cancel"));
document.querySelector("#lm-cancel-button").addEventListener("click", () => closeDialog("cancel"));
document.querySelector("#lm-apply-button").addEventListener("click", applyDialog);
document.querySelector("#lm-all-off").addEventListener("click", () => {
  for (const row of draft || []) {
    if (row.strength > 0) row.lastStrength = row.strength;
    row.strength = 0;
  }
  renderDialog();
});

dialog.addEventListener("close", () => {
  draft = null;
  returnFocus?.focus({ preventScroll: true });
});

list.addEventListener("change", (event) => {
  const number = event.target.closest("[data-lora-number]");
  if (number) {
    if (!number.checkValidity()) {
      number.reportValidity();
      return;
    }
    const row = rowById(number.dataset.loraNumber);
    if (!row) return;
    row.strength = Number(number.value);
    row.lastStrength = row.strength;
    renderDialog();
    list.querySelector(`[data-lora-number="${CSS.escape(row.id)}"]`)?.focus({ preventScroll: true });
  }
});

list.addEventListener("input", (event) => {
  const range = event.target.closest("[data-lora-range]");
  if (range) {
    const row = rowById(range.dataset.loraRange);
    if (!row) return;
    row.strength = Number(range.value);
    row.lastStrength = row.strength;
    const number = range.closest(".lm-strength")?.querySelector("[data-lora-number]");
    if (number) number.value = formatStrength(row.strength);
    updateSubjectPreview();
  }
  const number = event.target.closest("[data-lora-number]");
  if (number && number.checkValidity()) {
    const row = rowById(number.dataset.loraNumber);
    if (!row) return;
    row.strength = Number(number.value);
    row.lastStrength = row.strength;
    const range = number.closest(".lm-strength")?.querySelector("[data-lora-range]");
    if (range) range.value = number.value;
    updateSubjectPreview();
  }
});

list.addEventListener("click", (event) => {
  const selection = event.target.closest("[data-lora-toggle]");
  if (selection) {
    const row = rowById(selection.dataset.loraToggle);
    if (!row) return;
    if (row.strength > 0) {
      row.lastStrength = row.strength;
      row.strength = 0;
    } else row.strength = row.lastStrength || 1;
    renderDialog();
    list.querySelector(`[data-lora-toggle="${CSS.escape(row.id)}"]`)?.focus({ preventScroll: true });
    return;
  }
  const change = event.target.closest("[data-image-change]");
  if (change) {
    imageTargetId = change.dataset.imageChange;
    imagePicker.value = "";
    imagePicker.click();
    return;
  }
  const remove = event.target.closest("[data-image-remove]");
  if (remove) {
    const row = rowById(remove.dataset.imageRemove);
    if (!row) return;
    row.image = null;
    renderDialog();
    list.querySelector(`[data-image-change="${CSS.escape(row.id)}"]`)?.focus({ preventScroll: true });
  }
});

imagePicker.addEventListener("change", () => {
  const file = imagePicker.files?.[0];
  const row = rowById(imageTargetId);
  if (!file || !row) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
    showToast("Choose a PNG, JPEG, or WebP image.");
    return;
  }
  row.image = URL.createObjectURL(file);
  renderDialog();
  list.querySelector(`[data-image-change="${CSS.escape(row.id)}"]`)?.focus({ preventScroll: true });
});

list.addEventListener("keydown", (event) => {
  const handle = event.target.closest(".lm-drag-handle");
  if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
  event.preventDefault();
  const id = handle.closest("[data-lora-id]").dataset.loraId;
  const index = draft.findIndex((row) => row.id === id);
  moveRow(id, index + (event.key === "ArrowUp" ? -1 : 1));
});

list.addEventListener("dragstart", (event) => {
  const handle = event.target.closest(".lm-drag-handle");
  if (!handle) return;
  dragId = handle.closest("[data-lora-id]").dataset.loraId;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", dragId);
  handle.closest(".lm-row").classList.add("is-dragging");
});

list.addEventListener("dragover", (event) => {
  const row = event.target.closest(".lm-row");
  if (!dragId || !row) return;
  event.preventDefault();
  list.querySelectorAll(".is-drop-target").forEach((item) => item.classList.remove("is-drop-target"));
  if (row.dataset.loraId !== dragId) row.classList.add("is-drop-target");
});

list.addEventListener("drop", (event) => {
  const row = event.target.closest(".lm-row");
  if (!dragId || !row) return;
  event.preventDefault();
  moveRow(dragId, draft.findIndex((item) => item.id === row.dataset.loraId));
  dragId = null;
});

list.addEventListener("dragend", () => {
  dragId = null;
  list.querySelectorAll(".is-drop-target, .is-dragging").forEach((item) => item.classList.remove("is-drop-target", "is-dragging"));
});

list.addEventListener("pointerdown", (event) => {
  const handle = event.target.closest(".lm-drag-handle");
  if (event.pointerType === "mouse" || !handle) return;
  event.preventDefault();
  handle.setPointerCapture(event.pointerId);
  touchDrag = { id: handle.closest("[data-lora-id]").dataset.loraId, pointerId: event.pointerId, handle, targetId: null };
  handle.closest(".lm-row").classList.add("is-dragging");
});

list.addEventListener("pointermove", (event) => {
  if (!touchDrag || event.pointerId !== touchDrag.pointerId) return;
  const row = document.elementFromPoint(event.clientX, event.clientY)?.closest(".lm-row");
  list.querySelectorAll(".is-drop-target").forEach((item) => item.classList.remove("is-drop-target"));
  touchDrag.targetId = row?.dataset.loraId || null;
  if (row && touchDrag.targetId !== touchDrag.id) row.classList.add("is-drop-target");
});

function endTouchDrag(event) {
  if (!touchDrag || event.pointerId !== touchDrag.pointerId) return;
  const active = touchDrag;
  touchDrag = null;
  active.handle.closest(".lm-row")?.classList.remove("is-dragging");
  list.querySelectorAll(".is-drop-target").forEach((item) => item.classList.remove("is-drop-target"));
  if (event.type === "pointerup" && active.targetId) moveRow(active.id, draft.findIndex((row) => row.id === active.targetId));
  if (active.handle.hasPointerCapture(event.pointerId)) active.handle.releasePointerCapture(event.pointerId);
}

for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) list.addEventListener(type, endTouchDrag);

document.querySelector("#lm-panel-toggle").addEventListener("click", () => {
  const open = shell.classList.toggle("panel-open");
  document.querySelector("#lm-panel-toggle").setAttribute("aria-expanded", String(open));
});
document.querySelector("#lm-panel-scrim").addEventListener("click", () => shell.classList.remove("panel-open"));

window.previewOpenDialog = openDialog;
window.previewReset = () => {
  if (dialog.open) closeDialog("cancel");
  committed = structuredClone(initialRows);
  subject.value = "A cinematic portrait";
  renderSummary();
  showToast("Sample reset.");
};
window.previewSetViewport = (mode) => {
  const open = mode === "mobile";
  shell.classList.toggle("panel-open", open);
  document.querySelector("#lm-panel-toggle").setAttribute("aria-expanded", String(open));
};

renderSummary();
window.previewSetViewport(matchMedia("(max-width: 800px)").matches ? "mobile" : "desktop");
