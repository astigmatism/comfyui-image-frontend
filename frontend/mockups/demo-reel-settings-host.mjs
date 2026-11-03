// Preview host: forwards the control panel state to the scene iframe through its
// query string, so a screenshot run can address any state by URL alone.

const frame = document.querySelector("#drs-preview-frame");
const layoutButtons = [...document.querySelectorAll("[data-preview-layout]")];
const sizeButtons = [...document.querySelectorAll("[data-preview-size]")];
const generationSelect = document.querySelector("#preview-generation");
const advancedSelect = document.querySelector("#preview-advanced");
const errorSelect = document.querySelector("#preview-error");
const checkpointSelect = document.querySelector("#preview-checkpoints");
const pictureSelect = document.querySelector("#preview-picture");

const query = new URLSearchParams(location.search);
let layout = query.get("layout") === "current" ? "current" : "proposed";
let generation = query.get("gen") === "closed" ? "closed" : "open";
let advanced = query.get("adv") === "open" ? "open" : "closed";
let error = query.get("error") === "on" ? "on" : "off";
let checkpoints = ["auto", "all", "none"].includes(query.get("cps")) ? query.get("cps") : "some";
let picture = query.get("picout") === "on" ? "on" : "off";
let mobile = query.get("size") === "mobile";
// Screenshots of the new card ask the scene to bring it to the top of the dialog.
const focus = query.get("focus") === "checkpoints" ? "checkpoints" : "";

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
  const params = new URLSearchParams({ layout, gen: generation, adv: advanced, error, cps: checkpoints, picout: picture, open: "true" });
  if (focus) params.set("focus", focus);
  const next = `./demo-reel-settings-scene.html?${params}`;
  if (frame.getAttribute("src") === next) frame.contentWindow?.location.reload();
  else frame.setAttribute("src", next);
}

for (const button of layoutButtons) button.addEventListener("click", () => { layout = button.dataset.previewLayout; sync(); });
for (const button of sizeButtons) button.addEventListener("click", () => { mobile = button.dataset.previewSize === "mobile"; sync(); });
generationSelect.addEventListener("change", () => { generation = generationSelect.value; sync(); });
advancedSelect.addEventListener("change", () => { advanced = advancedSelect.value; sync(); });
errorSelect.addEventListener("change", () => { error = errorSelect.value; sync(); });
checkpointSelect.addEventListener("change", () => { checkpoints = checkpointSelect.value; sync(); });
pictureSelect.addEventListener("change", () => { picture = pictureSelect.value; sync(); });
document.querySelector('[data-preview-action="open"]').addEventListener("click", () => frame.contentWindow?.previewOpenDialog?.());

generationSelect.value = generation;
advancedSelect.value = advanced;
errorSelect.value = error;
checkpointSelect.value = checkpoints;
pictureSelect.value = picture;
sync();
