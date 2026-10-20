import { loraDefaultPositiveStrength, loraStackError, strongestLoraTrigger } from "./lora-stack.mjs";
import { migrateInterfaceState, migrateLoraStrengthMemory } from "./lib.mjs";
import { loraLibraryFormMarkup, loraLibraryToolsMarkup } from "./admin-loras.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
export const uncategorized = { id: "uncategorized", name: "Uncategorized", system: true };
const alphabetic = (a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }) || a.id.localeCompare(b.id);

export function groupedLoras(items, grouping) {
  const groups = grouping?.groups || [uncategorized];
  const known = new Set(groups.map((group) => group.id));
  return groups.map((group) => ({ ...group, items: items.filter((item) => {
    const assigned = grouping?.assignments?.[item.lora_identity] || "uncategorized";
    return (known.has(assigned) ? assigned : "uncategorized") === group.id;
  }).sort(alphabetic) }));
}

// Gallery order never changes the execution array. Reordering the active strip
// replaces only its positive slots, keeping every disabled entry exactly once.
export function moveEnabledLora(values, id, to) {
  const enabled = values.filter((entry) => entry.strength > 0);
  const from = enabled.findIndex((entry) => entry.id === id);
  if (from < 0 || to < 0 || to >= enabled.length) return values;
  enabled.splice(to, 0, enabled.splice(from, 1)[0]);
  let index = 0;
  return values.map((entry) => ({ ...(entry.strength > 0 ? enabled[index++] : entry) }));
}

export function reconcileLoraDraft(previous, current) {
  const source = { inputs: [previous.control] };
  const target = { inputs: [current.control] };
  const values = migrateInterfaceState(target, source, { [previous.control.id]: previous.values }).values[current.control.id];
  const memory = migrateLoraStrengthMemory(target, source, { [previous.control.id]: previous.values }, { [previous.control.id]: previous.memory })[current.control.id] || {};
  for (const [id, strength] of Object.entries(memory)) {
    const probe = values.map((entry) => entry.id === id ? { id, strength } : entry);
    if (loraStackError(current.control, probe)) delete memory[id];
  }
  return { ...current, values, memory };
}

export function subjectPreview(control, values, subjectAvailable = true) {
  if (subjectAvailable === null) return "Selected prompts stay unchanged";
  const strongest = strongestLoraTrigger(control, values);
  if (!strongest.entry) return "Subject unchanged · no LoRA enabled";
  if (!subjectAvailable) return "Subject unchanged · Prompt Generation subject is unavailable";
  if (!strongest.triggerWord) return "Subject unchanged · LoRA has no published title";
  return `Subject on Apply: ${strongest.triggerWord}${strongest.triggerSource === "title" ? " (LoRA title)" : ""}`;
}

function panelMarkup(state) {
  const { panel, control, grouping, adminController, groupsBusy } = state;
  if (!panel) return "";
  if (["install", "edit"].includes(panel.kind)) return loraLibraryFormMarkup(adminController, panel);
  const item = control.items.find((candidate) => candidate.id === panel.id);
  const group = grouping?.groups.find((candidate) => candidate.id === panel.id);
  if (panel.kind === "details") return `<section class="lm-editor"><h3>${escape(item?.label)}</h3><p>${escape(item?.description || "Usage guidance not published")}</p><h4>Published trigger word</h4><p class="lm-trigger-word">${escape(item?.trigger_word || "No verified trigger word is published; Subject name uses this LoRA title.")}</p><button type="button" class="button secondary" data-lora-panel-close>Back to gallery</button></section>`;
  const move = panel.kind === "move";
  return `<form class="lm-editor" data-lora-group-form="${escape(panel.kind)}"><h3>${move ? `Move ${escape(item?.label)} to a group` : group ? "Rename group" : "New group"}</h3>
    ${move ? `<label class="field">Group<select name="group_id">${(grouping?.groups || []).map((entry) => `<option value="${escape(entry.id)}" ${entry.id === grouping.assignments[item?.lora_identity] ? "selected" : ""}>${escape(entry.name)}</option>`).join("")}</select></label>` : `<label class="field">Group name<input name="name" required maxlength="80" value="${escape(panel.name ?? group?.name ?? "")}" /></label>`}
    <div class="button-row"><button type="submit" class="button primary" ${groupsBusy ? "disabled" : ""}>${groupsBusy ? "Saving…" : "Save"}</button><button type="button" class="button secondary" data-lora-panel-close ${groupsBusy ? "disabled" : ""}>Back to gallery</button></div></form>`;
}

function menuMarkup(state) {
  const item = state.control.items.find((candidate) => candidate.id === state.menuId);
  if (!item) return "";
  const id = escape(item.id);
  const saving = state.imagesLoading || state.imageStates[item.id]?.saving || !state.imageSources?.[item.id] && state.management;
  const library = state.adminController?.state.library;
  const libraryItem = library?.items?.find((entry) => entry.id === item.id);
  const pending = ["awaiting_upload", "running", "repair_required"].includes(state.adminController?.currentOperation()?.status);
  const adminDisabled = pending || state.adminController?.state.busy || !library?.eligible || library.key !== state.libraryKey || !item.lora_identity || libraryItem?.lora_identity !== item.lora_identity;
  return `<div class="lm-card-popover" data-lora-popover role="group" aria-label="Actions for ${escape(item.label)}">
    <button type="button" data-lora-details="${id}">Usage details</button>
    <button type="button" data-lora-image-change="${id}" ${saving ? "disabled" : ""}>${state.images[item.id]?.image_url ? "Change" : "Add"} thumbnail…</button>
    ${state.images[item.id]?.image_url ? `<button type="button" data-lora-image-remove="${id}" ${saving ? "disabled" : ""}>Remove thumbnail</button>` : ""}
    ${state.admin ? `<hr /><button type="button" data-lora-move="${id}" ${!state.grouping || state.groupsBusy ? "disabled" : ""}>Move to group…</button><button type="button" data-lora-edit="${id}" ${adminDisabled ? "disabled" : ""}>Edit LoRA details…</button><button type="button" class="destructive" data-lora-delete="${id}" ${adminDisabled ? "disabled" : ""}>Delete LoRA…</button>` : ""}</div>`;
}

export function loraManagerMarkup(options) {
  const state = { values: options.control.default, memory: {}, images: {}, imageStates: {}, sourceName: "Workflow", ...options };
  const { control, values, images, imageStates, sourceName, subjectAvailable, management, admin, adminController, grouping, groupingError, groupingLoading, panel, busy, error, imageError } = state;
  const enabled = values.filter((entry) => entry.strength > 0);
  const itemMap = new Map(control.items.map((item) => [item.id, item]));
  const adminState = adminController?.state;
  const matchingLibrary = adminState?.library?.key === state.libraryKey;
  const pendingOperation = ["awaiting_upload", "running", "repair_required"].includes(adminController?.currentOperation()?.status);
  const sharedBusy = busy || state.groupsBusy || adminState?.busy;
  const groups = groupedLoras(control.items, grouping);
  const grid = groups.map((group) => `<section class="lm-group" data-lora-group="${escape(group.id)}"><header class="lm-group-heading"><h3>${escape(groupingError ? "Groups unavailable" : groupingLoading ? "Loading groups…" : group.name)}</h3><span class="muted">${group.items.length} LoRA${group.items.length === 1 ? "" : "s"}</span>${admin && !group.system && grouping ? `<div class="lm-group-actions"><button type="button" class="button low" data-lora-group-rename="${escape(group.id)}" ${sharedBusy ? "disabled" : ""}>Rename</button><button type="button" class="button low" data-lora-group-delete="${escape(group.id)}" ${sharedBusy ? "disabled" : ""}>Delete group</button></div>` : ""}</header><ul class="lm-gallery-grid" aria-label="${escape(group.name)} LoRAs">${group.items.map((item) => {
    const id = escape(item.id), label = escape(item.label);
    const active = enabled.some((entry) => entry.id === item.id);
    const imageState = imageStates[item.id] || {};
    const image = imageState.saving ? imageState.preview : images[item.id]?.image_url;
    return `<li class="lm-card${active && !management ? " is-enabled" : ""}" data-lora-id="${id}"><button type="button" class="lm-thumbnail" ${management ? `data-lora-details="${id}" aria-label="Details for ${label}"` : `data-lora-toggle="${id}" aria-label="Toggle ${label}" aria-pressed="${active}"`} ${!management && loraDefaultPositiveStrength(control) === null ? "disabled" : ""}>${image ? `<img src="${escape(image)}" alt="" loading="lazy" />` : '<span class="lm-no-image" aria-hidden="true">No thumbnail</span>'}${!management ? `<span class="lm-selection-mark" aria-hidden="true">${active ? "✓" : ""}</span>` : ""}</button><div class="lm-caption"><span class="lm-card-name" title="${label}">${label}</span><button type="button" class="icon-button lm-menu-button" data-lora-menu="${id}" aria-label="Actions for ${label}" aria-expanded="${state.menuId === item.id}">☰</button></div><span class="lm-image-status${imageState.error ? " is-error" : ""}" role="${imageState.error ? "alert" : "status"}">${escape(imageState.saving ? "Saving…" : imageState.message || "")}</span></li>`;
  }).join("")}</ul>${!group.items.length ? '<p class="lm-group-empty muted">No LoRAs in this group.</p>' : ""}</section>`).join("");
  return `<div class="dialog-frame lm-dialog-frame"><header class="dialog-header lm-dialog-header"><div><h2 id="lora-manager-title">${management ? "LoRA library" : "LoRAs"}</h2><p>${escape(sourceName)} · Shared LoRA library</p></div>${management && adminState?.libraries.length > 1 ? `<label class="field lm-family-picker">Model family<select data-lora-library-family ${sharedBusy ? "disabled" : ""}>${adminState.libraries.map((entry) => `<option value="${escape(entry.key)}" ${entry.key === adminState.selectedLibraryKey ? "selected" : ""}>${escape(entry.label)}</option>`).join("")}</select></label>` : ""}<button type="button" class="icon-button lm-close-button" data-lora-cancel aria-label="${management ? "Close LoRA library" : "Cancel and close LoRA manager"}">×</button></header>
    ${!management ? `<div class="lm-order-bar"><div class="lm-order-label"><strong>${enabled.length} of ${values.length} enabled</strong><span>Application order · drag to reorder</span></div><ol class="lm-order-list" aria-label="LoRA application order">${enabled.map((entry, index) => `<li class="lm-order-chip" data-lora-order-id="${escape(entry.id)}"><button type="button" class="icon-button lm-drag-handle" data-lora-handle draggable="true" aria-label="Reorder ${escape(itemMap.get(entry.id)?.label)}" aria-description="Drag to reorder, or use arrow keys.">⠿</button><span>${index + 1}. ${escape(itemMap.get(entry.id)?.label || entry.id)}</span><button type="button" class="icon-button" data-lora-toggle="${escape(entry.id)}" aria-label="Disable ${escape(itemMap.get(entry.id)?.label)}">×</button></li>`).join("")}</ol><button type="button" class="button low" data-lora-all-off ${enabled.length ? "" : "disabled"}>All off</button></div>` : ""}
    <div class="lm-dialog-content">${admin ? loraLibraryToolsMarkup(adminController) : ""}${groupingError ? `<p class="form-error" role="alert">${escape(groupingError)} <button type="button" class="button low" data-lora-groups-reload>Reload groups</button></p>` : ""}${imageError ? `<p class="form-error" role="alert">${escape(imageError)} <button type="button" class="button low" data-lora-images-reload>Reload images</button></p>` : ""}${panel ? panelMarkup(state) : grid}<p class="visually-hidden" data-lora-reorder-status role="status" aria-live="polite"></p></div>
    <footer class="dialog-actions lm-dialog-footer">${admin ? `<div class="lm-footer-admin"><button type="button" class="button secondary" data-lora-add ${!matchingLibrary || pendingOperation || !adminState?.library?.eligible || sharedBusy ? "disabled" : ""}>＋ Add LoRA</button><button type="button" class="button low" data-lora-group-add ${!grouping || sharedBusy ? "disabled" : ""}>＋ New group</button></div>` : ""}<div class="lm-footer-context">${!management ? `<span class="lm-subject-preview" data-lora-subject-preview>${escape(subjectPreview(control, values, subjectAvailable))}</span>` : ""}<small>Thumbnail${admin ? ", library, and group" : ""} changes save immediately${management ? "." : "; Cancel keeps those changes."}</small></div><div class="lm-footer-buttons">${management ? '<button type="button" class="button primary" data-lora-cancel>Done</button>' : `<button type="button" class="button secondary" data-lora-cancel>Cancel</button><button type="button" class="button primary" data-lora-apply ${sharedBusy || state.reconcileError ? "disabled" : ""}>${busy ? "Refreshing…" : "Apply"}</button>`}</div>${error ? `<p class="form-error lm-error" role="alert">${escape(error)}</p>` : ""}</footer>${menuMarkup(state)}</div><input class="visually-hidden" type="file" data-lora-file accept="image/png,image/jpeg,image/webp" tabindex="-1" aria-hidden="true" />`;
}
