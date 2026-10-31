// Preview host: forwards the control panel state to the scene iframe through
// its query string, so a screenshot run can address any state by URL alone.
// Narrow is a real phone viewport: the iframe is resized, and the application's
// own breakpoints decide what the scene renders.

const frame = document.querySelector("#swb-preview-frame");
const stage = document.querySelector(".swb-preview-stage");
const scenarioSelect = document.querySelector("#preview-scenario");
const sizeButtons = [...document.querySelectorAll("[data-preview-size]")];

const scenarios = [
  ["live", "1 · Live published catalog"],
  ["single", "2 · One workflow published"],
  ["offline", "3 · Nothing available (runtime offline)"],
];

scenarioSelect.append(
  ...scenarios.map(([value, label]) =>
    Object.assign(document.createElement("option"), { value, textContent: label }),
  ),
);

const query = new URLSearchParams(location.search);
scenarioSelect.value = scenarios.some(([value]) => value === query.get("scenario"))
  ? query.get("scenario")
  : "live";
let mobile = query.get("size") === "mobile";

function sync() {
  stage.dataset.previewSize = mobile ? "mobile" : "desktop";
  for (const button of sizeButtons) {
    const selected = (button.dataset.previewSize === "mobile") === mobile;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  const params = new URLSearchParams({
    scenario: scenarioSelect.value,
    size: mobile ? "mobile" : "desktop",
    open: "true",
  });
  const next = `./source-workflow-buttons-scene.html?${params}`;
  if (frame.getAttribute("src") === next) frame.contentWindow?.location.reload();
  else frame.setAttribute("src", next);
}

scenarioSelect.addEventListener("change", sync);
for (const button of sizeButtons)
  button.addEventListener("click", () => {
    mobile = button.dataset.previewSize === "mobile";
    sync();
  });
document.querySelector('[data-preview-action="open"]').addEventListener("click", () => frame.contentWindow?.previewOpenDialog?.());

sync();
