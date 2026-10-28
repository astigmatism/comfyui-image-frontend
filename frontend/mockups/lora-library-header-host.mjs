// Preview host: forwards the control panel state to the scene iframe through
// its query string, so a screenshot run can address any state by URL alone.

const frame = document.querySelector("#lhh-preview-frame");
const scenarioSelect = document.querySelector("#preview-scenario");
const popoverSelect = document.querySelector("#preview-popover");
const layoutButtons = [...document.querySelectorAll("[data-preview-layout]")];
const sizeButtons = [...document.querySelectorAll("[data-preview-size]")];

const scenarios = [
  ["synced", "1 · All workflows in sync"],
  ["drifted", "2 · Needs sync (1 missing)"],
  ["conflict", "3 · Publishing conflict"],
  ["uploading", "4 · Uploading weight file"],
  ["awaiting", "5 · Upload paused, retry available"],
  ["working", "6 · Operation running"],
  ["repair", "7 · Repair required"],
  ["error", "8 · Failed operation"],
  ["unavailable", "9 · No library available"],
  ["checking", "10 · Checking…"],
];

scenarioSelect.append(...scenarios.map(([value, label]) => Object.assign(document.createElement("option"), { value, textContent: label })));

const query = new URLSearchParams(location.search);
scenarioSelect.value = scenarios.some(([value]) => value === query.get("scenario")) ? query.get("scenario") : "drifted";
popoverSelect.value = query.get("popover") === "open" ? "open" : "closed";
let layout = query.get("layout") === "current" ? "current" : "proposed";
let mobile = query.get("size") === "mobile";

function sync() {
  document.body.classList.toggle("lhh-preview-mobile", mobile);
  for (const button of layoutButtons) {
    const selected = button.dataset.previewLayout === layout;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  for (const button of sizeButtons) {
    const selected = (button.dataset.previewSize === "mobile") === mobile;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  const params = new URLSearchParams({ scenario: scenarioSelect.value, layout, popover: popoverSelect.value, open: "true" });
  const next = `./lora-library-header-scene.html?${params}`;
  if (frame.getAttribute("src") === next) frame.contentWindow?.location.reload();
  else frame.setAttribute("src", next);
}

scenarioSelect.addEventListener("change", sync);
popoverSelect.addEventListener("change", sync);
for (const button of layoutButtons) button.addEventListener("click", () => { layout = button.dataset.previewLayout; sync(); });
for (const button of sizeButtons) button.addEventListener("click", () => { mobile = button.dataset.previewSize === "mobile"; sync(); });
document.querySelector('[data-preview-action="open"]').addEventListener("click", () => frame.contentWindow?.previewOpenDialog?.());

sync();
