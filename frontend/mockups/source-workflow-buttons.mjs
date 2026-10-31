// Preview of the generation-source picker as it ships. The markup, ordering,
// labels and captions all come from src/render.mjs and src/lib.mjs, so this
// page can only ever show what the application really renders; what it adds is
// the catalog this appliance publishes, so the row can be judged against real
// workflow names instead of invented ones.
//
// Narrow widths are real: the host resizes the iframe, so src/styles.css
// reaches its own breakpoints exactly as it would on a phone.

import { orderWorkflowsByCapability, sourceModelSelectors } from "../src/lib.mjs";
import { sourcePickerDialogMarkup } from "../src/render.mjs";
import { publishedCatalog } from "./source-workflow-buttons-catalog.mjs";

const params = new URLSearchParams(location.search);

// The API returns the catalog sorted by display name; the row reorders it.
const byName = (a, b) => a.display_name.toLowerCase().localeCompare(b.display_name.toLowerCase());
const offline = (source) => ({ ...source, available: false, readiness: "unavailable", cached: false });
const only = (name) => publishedCatalog.find((source) => source.display_name === name);

const catalogs = {
  live: [...publishedCatalog].sort(byName),
  single: [only("Moody Krea2 i2i 4KHD v1")],
  offline: [...publishedCatalog].sort(byName).map(offline),
};

const scenarioNames = Object.keys(catalogs);
const view = {
  scenario: scenarioNames.includes(params.get("scenario")) ? params.get("scenario") : "live",
  open: params.get("open") !== "false",
};

// A standing checkpoint ranking for the board beneath the row, taken from the
// order the publication itself lists its checkpoints.
const identities = publishedCatalog[0].model_selectors[0].choices.map((choice) => choice.checkpoint_id);
const ranks = {
  A: identities.slice(0, 2),
  B: identities.slice(2, 3),
  C: identities.slice(3, 12),
  D: [],
  F: [],
};

const dialog = document.querySelector("#source-picker-dialog");
const note = document.querySelector("#scene-note");
const state = { activeKey: null };

// One selected checkpoint per workflow: the one that publication names as its
// default, so the board always shows a real selection.
const selections = (sources) =>
  Object.fromEntries(
    sources.map((source) => {
      const selector = sourceModelSelectors(source)[0];
      return [source.source_key, { checkpoint: selector ? [selector.default] : [] }];
    }),
  );

// Ranked by the application, so the preview starts on the workflow the row
// puts first rather than on whichever name sorts first in the catalog.
function available() {
  return orderWorkflowsByCapability(catalogs[view.scenario].filter((source) => source.available !== false));
}

function render() {
  const sources = available();
  if (!sources.some((source) => source.source_key === state.activeKey)) {
    state.activeKey = sources[0]?.source_key || "";
  }
  dialog.innerHTML = sourcePickerDialogMarkup(sources, {
    sourceKey: state.activeKey,
    modelSelectionsBySource: selections(sources),
    checkpointTiers: ranks,
  });
  if (view.open && !dialog.open) dialog.showModal();
  if (!view.open && dialog.open) dialog.close();
}

function focusWorkflowChoice() {
  const options = [...dialog.querySelectorAll("[data-source-workflow-choice]")];
  if (!options.length) return;
  const selected = options.find((option) => option.getAttribute("aria-checked") === "true") || options[0];
  options.forEach((option) => option.setAttribute("tabindex", option === selected ? "0" : "-1"));
  selected.focus({ preventScroll: true });
}

dialog.addEventListener("click", (event) => {
  const option = event.target.closest("[data-source-workflow-choice]");
  if (!option) return;
  state.activeKey = option.dataset.sourceWorkflowKey;
  render();
  focusWorkflowChoice();
});

// The row walks with the arrow keys and the draft workflow follows focus,
// which is what role="radiogroup" promises.
dialog.addEventListener("keydown", (event) => {
  const option = event.target.closest("[data-source-workflow-choice]");
  if (!option) return;
  const options = [...option.parentElement.querySelectorAll("[data-source-workflow-choice]")];
  const index = options.indexOf(option);
  const offsets = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
  let next;
  if (event.key in offsets) next = options[(index + offsets[event.key] + options.length) % options.length];
  else if (event.key === "Home") next = options[0];
  else if (event.key === "End") next = options[options.length - 1];
  if (!next) return;
  event.preventDefault();
  state.activeKey = next.dataset.sourceWorkflowKey;
  render();
  focusWorkflowChoice();
});

function reportOrder() {
  const options = [...dialog.querySelectorAll("[data-source-workflow-choice]")];
  note.textContent = options.length
    ? `Left to right: ${options.map((option) => option.title).join(" → ")} · click a workflow or use ←/→`
    : "No published workflow is available.";
}

render();
reportOrder();

document.querySelector("#open-preview").addEventListener("click", () => {
  view.open = true;
  render();
  focusWorkflowChoice();
  reportOrder();
});

window.previewOpenDialog = () => {
  view.open = true;
  render();
  focusWorkflowChoice();
  reportOrder();
};
dialog.addEventListener("close", () => {
  view.open = false;
  reportOrder();
});
dialog.addEventListener("click", reportOrder);
