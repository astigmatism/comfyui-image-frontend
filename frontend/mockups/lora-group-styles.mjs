// Interactive design preview for the LoRA dialog styling pass.
//
// The proposed design is the production loraManagerMarkup() and src/styles.css,
// so this preview cannot drift from the application. `layout=current` takes that
// same markup and swaps in the retired group header, caption and full-size
// no-thumbnail card, kept below as a frozen snapshot for the comparison.

import { loraManagerMarkup, groupedLoras } from "../src/lora-gallery.mjs";
import { loraDefaultPositiveStrength } from "../src/lora-stack.mjs";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const params = new URLSearchParams(location.search);

const identity = (letter) => `lr1_${letter.repeat(64)}`;
const artwork = ["./assets/cartographer.webp", "./assets/night-botanist.webp", "./assets/glass-orchid.webp"];

const library = {
  key: "krea2",
  label: "Moody Krea2",
  eligible: true,
  in_sync: true,
  can_sync: false,
  conflicts: [],
  reason: null,
  members: [{ source_key: "moody-krea2-simple", display_name: "Moody Krea2 Simple v31", in_sync: true }],
  expected_library: [{ source_key: "moody-krea2-simple", revision: { publication_id: "one" } }],
};

// A group of style LoRAs, a mixed character group and an all-minimal group: the
// three shapes the dialog has to serve.
const items = [
  { id: "famegrid", label: "famegrid", trigger_word: null, lora_identity: identity("a") },
  { id: "nicegirls", label: "NiceGirls UltraReal", trigger_word: null, lora_identity: identity("b") },
  ...Array.from({ length: 5 }, (_, index) => ({
    id: `heroline-${index + 1}`,
    label: ["Heroline", "Heroline Dawn", "Heroline Ember", "Heroline Meadow", "Heroline Vesper"][index],
    trigger_word: `heroline ${["portrait", "dawn", "ember", "meadow", "vesper"][index]}`,
    lora_identity: identity("cdefg"[index]),
  })),
  { id: "coppertone", label: "Coppertone Skin Tones", trigger_word: null, lora_identity: identity("h") },
  { id: "grain", label: "Fine Film Grain 35mm", trigger_word: null, lora_identity: identity("i") },
  { id: "unsorted", label: "Untitled upload from 2026-09", trigger_word: null, lora_identity: identity("j") },
];
const groups = [
  { id: "style", name: "Style" },
  { id: "bem-yhero", name: "BeMyHero" },
  { id: "uncategorized", name: "Uncategorized", system: true },
];
const assignments = {
  [identity("a")]: "style",
  [identity("b")]: "style",
  ...Object.fromEntries(items.filter((item) => /^heroline/.test(item.id)).map((item) => [item.lora_identity, "bem-yhero"])),
  [identity("h")]: "uncategorized",
  [identity("i")]: "uncategorized",
  [identity("j")]: "uncategorized",
};
const thumbnails = Object.fromEntries(items.filter((item) => /^heroline/.test(item.id)).map((item, index) => [
  item.id,
  { image_url: artwork[index % artwork.length], version: "1" },
]));

// ------------------------------------------------------- frozen old design ---

// The retired dialog: text buttons in the group header, Demo Reel on a second
// caption line, and a no-thumbnail LoRA taking a whole card. Frozen verbatim
// from src/lora-gallery.mjs before the styling pass, so the comparison stays
// honest while the rules it needs are kept in lora-group-styles.css.
function retiredGrid(context) {
  const { control, values, images, imageStates, management, admin, grouping, groupingError, groupingLoading, groups, enabled, sharedBusy, state } = context;
  const grid = groups.map((group, groupIndex) => `<section class="lm-group" data-lora-group="${escape(group.id)}"><header class="lm-group-heading"><h3>${escape(groupingError ? "Groups unavailable" : groupingLoading ? "Loading groups…" : group.name)}</h3><span class="muted">${group.items.length} LoRA${group.items.length === 1 ? "" : "s"}</span>${admin && grouping ? `<div class="lm-group-actions"><button type="button" class="button low" data-lora-group-up="${escape(group.id)}" aria-label="Move ${escape(group.name)} up" title="Move group up" ${sharedBusy || groupIndex === 0 ? "disabled" : ""}>↑</button><button type="button" class="button low" data-lora-group-down="${escape(group.id)}" aria-label="Move ${escape(group.name)} down" title="Move group down" ${sharedBusy || groupIndex === groups.length - 1 ? "disabled" : ""}>↓</button>${!group.system ? `<button type="button" class="button low" data-lora-group-rename="${escape(group.id)}" ${sharedBusy ? "disabled" : ""}>Rename</button><button type="button" class="button low" data-lora-group-delete="${escape(group.id)}" ${sharedBusy ? "disabled" : ""}>Delete group</button>` : ""}</div>` : ""}</header><ul class="lm-gallery-grid" aria-label="${escape(group.name)} LoRAs">${group.items.map((item) => {
    const id = escape(item.id), label = escape(item.label);
    const active = enabled.some((entry) => entry.id === item.id);
    const imageState = imageStates[item.id] || {};
    const image = imageState.saving ? imageState.preview : images[item.id]?.image_url;
    return `<li class="lm-card${active && !management ? " is-enabled" : ""}" data-lora-id="${id}"><button type="button" class="lm-thumbnail" ${management ? `data-lora-details="${id}" aria-label="Details for ${label}"` : `data-lora-toggle="${id}" aria-label="Toggle ${label}" aria-pressed="${active}"`} ${!management && loraDefaultPositiveStrength(control) === null ? "disabled" : ""}>${image ? `<img src="${escape(image)}" alt="" loading="lazy" />` : '<span class="lm-no-image" aria-hidden="true">No thumbnail</span>'}${!management ? `<span class="lm-selection-mark" aria-hidden="true">${active ? "✓" : ""}</span>` : ""}</button><div class="lm-caption"><div class="lm-caption-title"><span class="lm-card-name" title="${label}">${label}</span><button type="button" class="lm-demo-reel-link" data-lora-demo-reel="${id}" ${!state.libraryKey || !item.lora_identity ? "disabled" : ""}>Demo Reel</button></div><button type="button" class="icon-button lm-menu-button" data-lora-menu="${id}" aria-label="Actions for ${label}" aria-expanded="${state.menuId === item.id}">☰</button></div><span class="lm-image-status${imageState.error ? " is-error" : ""}" role="${imageState.error ? "alert" : "status"}">${escape(imageState.saving ? "Saving…" : imageState.message || "")}</span></li>`;
  }).join("")}</ul>${!group.items.length ? '<p class="lm-group-empty muted">No LoRAs in this group.</p>' : ""}</section>`).join("");
  return grid;
}

function retire(markup, context) {
  return markup
    .replace(/<section class="lm-group"[\s\S]*?<\/section>(?=<p class="visually-hidden" data-lora-reorder-status)/, () => retiredGrid(context))
    .replace(
      /<button type="button" class="icon-button lm-header-button" data-lora-demo-settings[^>]*>[\s\S]*?<\/button>/,
      '<button type="button" class="button low lgs-retired-button" data-lora-demo-settings aria-label="Demo Reel settings">Demo Reel settings</button>',
    );
}

// ------------------------------------------------------------------- render ---

const view = {
  layout: params.get("layout") === "current" ? "current" : "proposed",
  mode: params.get("mode") === "picker" ? "picker" : "management",
  popover: params.get("popover") === "open",
  scale: Number(params.get("scale")) || 100,
  open: params.get("open") !== "false",
};
const admin = view.mode === "management";
const minimalItem = items.find((item) => !thumbnails[item.id]);

function frameMarkup() {
  const enabled = new Set(["famegrid", "heroline-2"]);
  const options = {
    control: { id: "library", type: "lora_stack", label: "LoRAs", minimum: 0, maximum: 2, step: 0.05, items, default: items.map((item) => ({ id: item.id, strength: 0 })) },
    values: items.map((item) => ({ id: item.id, strength: enabled.has(item.id) ? 1 : 0 })),
    memory: {},
    images: thumbnails,
    imageStates: {},
    sourceName: "Moody Krea2 Simple v31",
    management: admin,
    admin,
    adminController: {
      state: { library, libraries: [library], selectedLibraryKey: "krea2", loading: false, busy: false, uploadPercent: null, status: "", error: "", pendingUploads: new Map() },
      currentOperation: () => null,
    },
    libraryKey: "krea2",
    grouping: { revision: 0, groups, assignments },
    menuId: view.popover ? minimalItem.id : null,
    thumbnailScale: view.scale,
  };
  const markup = loraManagerMarkup(options);
  if (view.layout !== "current") return markup;
  const state = { values: options.control.default, memory: {}, images: {}, imageStates: {}, ...options };
  // The retired template reads the locals loraManagerMarkup derives internally.
  return retire(markup, {
    ...options,
    state,
    groupingError: null,
    groupingLoading: false,
    groups: groupedLoras(options.control.items, options.grouping),
    enabled: options.values.filter((entry) => entry.strength > 0),
    sharedBusy: false,
  });
}

const dialog = document.querySelector("#lora-manager-dialog");
const note = document.querySelector("#scene-note");

// Mirrors positionMenu() in src/lora-manager.mjs so the preview places the
// popover exactly as the production dialog will.
function positionPopover() {
  const menu = dialog.querySelector("[data-lora-popover]");
  const button = dialog.querySelector(`[data-lora-menu="${CSS.escape(minimalItem.id)}"]`);
  if (!menu || !button) return;
  const box = dialog.getBoundingClientRect();
  const anchor = button.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(box.width - menu.offsetWidth - 8, anchor.right - box.left - menu.offsetWidth))}px`;
  menu.style.top = `${Math.max(8, Math.min(box.height - menu.offsetHeight - 8, anchor.bottom - box.top))}px`;
}

function render() {
  document.body.classList.toggle("lgs-retired", view.layout === "current");
  dialog.innerHTML = frameMarkup();
  note.textContent = `${view.layout === "current" ? "Retired design" : "Current design"} · ${view.mode === "management" ? "management" : "picker"}${view.popover ? " · menu open" : ""}`;
  if (view.open && !dialog.open) dialog.showModal();
  if (!view.open && dialog.open) dialog.close();
  positionPopover();
}

dialog.addEventListener("click", (event) => {
  if (event.target.closest("[data-lora-cancel]")) { view.open = false; render(); return; }
  if (view.popover && !event.target.closest("[data-lora-popover]")) { view.popover = false; render(); }
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !view.popover) return;
  event.preventDefault();
  view.popover = false;
  render();
});

window.previewOpenDialog = () => { view.open = true; render(); };
window.previewSetViewport = () => {};
window.previewSetLayout = (next) => { view.layout = next === "current" ? "current" : "proposed"; render(); };
window.previewSetMode = (mode) => { view.mode = mode === "picker" ? "picker" : "management"; render(); };
window.previewSetPopover = (open) => { view.popover = Boolean(open); render(); };
window.previewSetScale = (scale) => { view.scale = Number(scale) || 100; render(); };

render();
