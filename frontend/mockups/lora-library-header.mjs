// Interactive design preview for the LoRA dialog toolbar restyle.
//
// The proposed layout is the production loraManagerMarkup() rendered with a
// sample admin controller, so this preview cannot drift from the application.
// `layout=current` takes that same markup and swaps in the retired Library
// status / Demo Reel panels, kept below as a frozen snapshot for the
// before/after comparison.

import { loraManagerMarkup } from "../src/lora-gallery.mjs";
import { loraLibraryMenuId } from "../src/admin-loras.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const params = new URLSearchParams(location.search);

const advanced = { source_key: "moody-krea2-advanced", display_name: "Moody Krea2 Advanced v1", in_sync: true };
const minimal = { source_key: "moody-krea2-minimal", display_name: "Moody Krea2 Minimal v1", in_sync: true };
const driftedMember = { ...minimal, in_sync: false, missing_count: 1 };

const items = [
  { id: "painterly-ink", label: "Painterly ink", trigger_word: "painterly ink study", lora_identity: `lr1_${"a".repeat(64)}` },
  { id: "long-exposure", label: "Long exposure", trigger_word: "long exposure night", lora_identity: `lr1_${"b".repeat(64)}` },
];

// One entry per visible state of the library-status control. `library: null`
// means no published workflow in this family carries a LoRA list.
const scenarios = {
  synced: { members: [advanced, minimal] },
  drifted: { members: [advanced, driftedMember], can_sync: true },
  conflict: { members: [advanced, minimal], conflicts: ["LoRA “Beta” uses different files in different workflows.", "LoRA “Atlas” has no ID in Moody Krea2 Minimal v1."] },
  uploading: { members: [advanced, minimal], busy: true, operation: { id: "op-1", status: "awaiting_upload" }, upload_percent: 42, status: "Uploading weight file to ComfyUI…" },
  awaiting: { members: [advanced, minimal], operation: { id: "op-1", status: "awaiting_upload" }, pending_upload: true, error: "The upload was interrupted. Retrying the same operation…" },
  working: { members: [advanced, minimal], busy: true, operation: { id: "op-2", status: "running" }, status: "Creating installation operation…" },
  repair: { members: [advanced, minimal], operation: { id: "op-3", status: "repair_required" }, error: "A LoRA change needs repair before another change." },
  error: { members: [advanced, minimal], error: "The upload was interrupted. Retrying the same operation…" },
  unavailable: { library: null },
  checking: { members: [advanced, minimal], loading: true },
};

const scenarioNames = Object.keys(scenarios);
const view = {
  scenario: scenarioNames.includes(params.get("scenario")) ? params.get("scenario") : "drifted",
  layout: params.get("layout") === "current" ? "current" : "proposed",
  open: params.get("open") !== "false",
  popover: params.get("popover") === "open",
};

function controllerFor(model) {
  const library = model.library === null ? null : {
    key: "krea2",
    label: "Moody Krea2",
    eligible: true,
    in_sync: !(model.members || []).some((member) => !member.in_sync),
    can_sync: Boolean(model.can_sync),
    conflicts: model.conflicts || [],
    reason: model.reason || null,
    items,
    members: model.members || [],
    expected_library: (model.members || []).map((member) => ({ source_key: member.source_key, revision: { publication_id: "one" } })),
  };
  return {
    state: {
      library,
      libraries: library ? [library, { ...library, key: "flux", label: "Moody Flux", items: [], members: [] }] : [],
      selectedLibraryKey: library?.key || null,
      loading: Boolean(model.loading),
      busy: Boolean(model.busy),
      uploadPercent: model.upload_percent ?? null,
      status: model.status || "",
      error: model.error || "",
      pendingUploads: new Map(model.pending_upload ? [["krea2", { name: "sample.safetensors" }]] : []),
    },
    currentOperation: () => model.operation,
  };
}

// Frozen snapshot of the panels this change retires.
function retiredPanels(controller) {
  const { state } = controller;
  const rows = (state.library?.members || []).map((member) => {
    const text = member.in_sync ? "In sync" : `Needs sync${member.missing_count ? ` · ${member.missing_count} missing` : ""}`;
    return `<li class="admin-lora-member${member.in_sync ? "" : " is-out-of-sync"}"><span>${escape(member.display_name)}</span><span class="admin-lora-member-state">${escape(text)}</span></li>`;
  }).join("");
  return `<details class="lhh-retired" open><summary>Library status</summary>
    <p class="muted">Changes affect every workflow in this model family.</p>
    ${rows ? `<ul class="admin-lora-members">${rows}</ul>` : ""}
    ${state.error ? `<p class="form-error">${escape(state.error)}</p>` : ""}
    ${state.library?.can_sync ? '<button type="button" class="button secondary">Sync library</button> ' : ""}<button type="button" class="button low">Refresh library status</button></details>
    <details class="lhh-retired" open><summary>Demo Reel</summary><p class="muted">Shared prompt slots and idle generation settings for every LoRA.</p><button type="button" class="button secondary">Edit prompts and generation settings</button></details>`;
}

function frameMarkup() {
  const model = scenarios[view.scenario];
  const adminController = controllerFor(model);
  const markup = loraManagerMarkup({
    control: { id: "library", type: "lora_stack", label: "LoRAs", items, default: items.map((item) => ({ id: item.id, strength: 0 })) },
    values: [],
    memory: {},
    images: {},
    imageStates: {},
    sourceName: "Moody Krea2",
    management: true,
    admin: true,
    adminController,
    libraryKey: adminController.state.library?.key || null,
    grouping: {
      revision: 0,
      groups: [{ id: "style", name: "Style" }, { id: "uncategorized", name: "Uncategorized", system: true }],
      assignments: Object.fromEntries(items.map((item) => [item.lora_identity, "style"])),
    },
    menuId: view.popover ? loraLibraryMenuId : null,
    thumbnailScale: 100,
  });
  if (view.layout === "current") {
    return markup
      .replace(/<div class="lm-header-actions">[\s\S]*?(?=<button type="button" class="icon-button lm-close-button")/, '<div class="lm-header-actions">')
      .replace(/<p class="lm-header-notice[\s\S]*?<\/p>/, "")
      .replace('<div class="lm-dialog-content">', `<div class="lm-dialog-content">${retiredPanels(adminController)}`);
  }
  return markup;
}

const dialog = document.querySelector("#lora-manager-dialog");
const note = document.querySelector("#scene-note");

// Mirrors positionMenu() in src/lora-manager.mjs so the preview places the
// popover exactly as the production dialog will.
function positionPopover() {
  const menu = dialog.querySelector("[data-lora-popover]");
  const button = dialog.querySelector("[data-lora-menu]");
  if (!menu || !button) return;
  const box = dialog.getBoundingClientRect();
  const anchor = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(box.width - menu.offsetWidth - 8, anchor.right - box.left - menu.offsetWidth))}px`;
  menu.style.top = `${Math.max(8, Math.min(box.height - menu.offsetHeight - 8, anchor.bottom - box.top))}px`;
}

function render() {
  dialog.innerHTML = frameMarkup();
  note.textContent = `${view.layout === "current" ? "Current design" : "Proposed design"} · ${view.scenario}${view.popover && view.layout === "proposed" ? " · popover open" : ""}`;
  if (view.open && !dialog.open) dialog.showModal();
  if (!view.open && dialog.open) dialog.close();
  positionPopover();
}

dialog.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-lora-menu]");
  if (chip) { view.popover = !view.popover; render(); return; }
  if (event.target.closest("[data-lora-cancel]")) { view.open = false; render(); return; }
  if (event.target.closest("[data-lora-demo-settings]")) { note.textContent = "Opens Demo Reel settings (unchanged flow)."; return; }
  if (event.target.closest("[data-lora-library-sync]")) { note.textContent = "Sync library → same handler as today."; return; }
  if (event.target.closest("[data-lora-library-reload]")) { note.textContent = "Refresh library status → same handler as today."; return; }
  if (event.target.closest("[data-lora-library-retry]")) { note.textContent = "Retry upload → same handler as today."; return; }
  if (event.target.closest("[data-lora-library-cancel-upload]")) { note.textContent = "Cancel pending upload → same handler as today."; return; }
  if (view.popover && !event.target.closest("[data-lora-popover]")) { view.popover = false; render(); }
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !view.popover) return;
  event.preventDefault();
  view.popover = false;
  render();
  dialog.querySelector("[data-lora-menu]")?.focus({ preventScroll: true });
});

window.previewOpenDialog = () => { view.open = true; render(); };
window.previewSetViewport = () => {};
window.previewSetScenario = (next) => { if (scenarioNames.includes(next)) { view.scenario = next; render(); } };
window.previewSetLayout = (next) => { view.layout = next === "current" ? "current" : "proposed"; render(); };
window.previewSetPopover = (open) => { view.popover = Boolean(open); render(); };
window.previewScenarios = scenarioNames;

render();
