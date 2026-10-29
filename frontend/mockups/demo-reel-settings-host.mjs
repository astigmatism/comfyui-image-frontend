// Preview host: forwards the control panel state to the scene iframe through its
// query string, so a screenshot run can address any state by URL alone.

const frame = document.querySelector("#drs-preview-frame");
const layoutButtons = [...document.querySelectorAll("[data-preview-layout]")];
const sizeButtons = [...document.querySelectorAll("[data-preview-size]")];
const generationSelect = document.querySelector("#preview-generation");
const advancedSelect = document.querySelector("#preview-advanced");
const errorSelect = document.querySelector("#preview-error");

const query = new URLSearchParams(location.search);
let layout = query.get("layout") === "current" ? "current" : "proposed";
let generation = query.get("gen") === "closed" ? "closed" : "open";
let advanced = query.get("adv") === "open" ? "open" : "closed";
let error = query.get("error") === "on" ? "on" : "off";
let mobile = query.get("size") === "mobile";

function sync() {
  document.body.classList.toggle("drs-preview-mobile", mobile);
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
  const params = new URLSearchParams({ layout, gen: generation, adv: advanced, error, open: "true" });
  const next = `./demo-reel-settings-scene.html?${params}`;
  if (frame.getAttribute("src") === next) frame.contentWindow?.location.reload();
  else frame.setAttribute("src", next);
}

for (const button of layoutButtons) button.addEventListener("click", () => { layout = button.dataset.previewLayout; sync(); });
for (const button of sizeButtons) button.addEventListener("click", () => { mobile = button.dataset.previewSize === "mobile"; sync(); });
generationSelect.addEventListener("change", () => { generation = generationSelect.value; sync(); });
advancedSelect.addEventListener("change", () => { advanced = advancedSelect.value; sync(); });
errorSelect.addEventListener("change", () => { error = errorSelect.value; sync(); });
document.querySelector('[data-preview-action="open"]').addEventListener("click", () => frame.contentWindow?.previewOpenDialog?.());

generationSelect.value = generation;
advancedSelect.value = advanced;
errorSelect.value = error;
sync();
