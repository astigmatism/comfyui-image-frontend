// Preview host: forwards the control panel state to the scene iframe through its
// query string, so a screenshot run can address any state by URL alone.

const frame = document.querySelector("#lgs-preview-frame");
const modeSelect = document.querySelector("#preview-mode");
const popoverSelect = document.querySelector("#preview-popover");
const scaleSelect = document.querySelector("#preview-scale");
const layoutButtons = [...document.querySelectorAll("[data-preview-layout]")];
const sizeButtons = [...document.querySelectorAll("[data-preview-size]")];

const query = new URLSearchParams(location.search);
let layout = query.get("layout") === "current" ? "current" : "proposed";
let mode = query.get("mode") === "picker" ? "picker" : "management";
let popover = query.get("popover") === "open" ? "open" : "closed";
let scale = ["50", "100", "150"].includes(query.get("scale")) ? query.get("scale") : "100";
let mobile = query.get("size") === "mobile";
modeSelect.value = mode;
popoverSelect.value = popover;
scaleSelect.value = scale;

function sync() {
  document.body.classList.toggle("lgs-preview-mobile", mobile);
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
  const params = new URLSearchParams({ layout, mode, popover, scale, open: "true" });
  const next = `./lora-group-styles-scene.html?${params}`;
  if (frame.getAttribute("src") === next) frame.contentWindow?.location.reload();
  else frame.setAttribute("src", next);
}

modeSelect.addEventListener("change", () => { mode = modeSelect.value; sync(); });
popoverSelect.addEventListener("change", () => { popover = popoverSelect.value; sync(); });
scaleSelect.addEventListener("change", () => { scale = scaleSelect.value; sync(); });
for (const button of layoutButtons) button.addEventListener("click", () => { layout = button.dataset.previewLayout; sync(); });
for (const button of sizeButtons) button.addEventListener("click", () => { mobile = button.dataset.previewSize === "mobile"; sync(); });
document.querySelector('[data-preview-action="open"]').addEventListener("click", () => frame.contentWindow?.previewOpenDialog?.());

sync();
