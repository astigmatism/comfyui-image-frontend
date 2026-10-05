const frame = document.querySelector("#xc-preview-frame");
const scenario = document.querySelector("#xc-preview-scenario");
const params = new URLSearchParams(location.hash.slice(1));

function scene() {
  return frame.contentWindow;
}

function setViewport(mode) {
  const mobile = mode === "mobile";
  document.body.classList.toggle("xc-preview-mobile", mobile);
  document.querySelectorAll("[data-preview-size]").forEach((item) => {
    const selected = item.dataset.previewSize === mode;
    item.classList.toggle("is-selected", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
  scene()?.previewSetViewport?.(mobile ? "mobile" : "desktop");
}

document.querySelectorAll("[data-preview-size]").forEach((button) => {
  button.addEventListener("click", () => setViewport(button.dataset.previewSize));
});

scenario.addEventListener("change", () => {
  scene()?.previewSetScenario?.(scenario.value, { openModal: scenario.value !== "configured" && !scenario.value.startsWith("vision") && scenario.value !== "auto-on" });
  scene()?.previewSetViewport?.(document.body.classList.contains("xc-preview-mobile") ? "mobile" : "desktop");
});

document.querySelectorAll("[data-preview-action]").forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.previewAction;
    if (action === "open") scene()?.previewOpenDialog?.();
    if (action === "simulate") scene()?.previewSimulate?.("apply");
    if (action === "reset") scene()?.previewReset?.();
  });
});

frame.addEventListener("load", () => {
  const initial = params.get("scenario");
  if (initial) {
    scenario.value = initial;
    scene()?.previewSetScenario?.(initial, { openModal: params.get("modal") === "1" });
  }
  setViewport(params.get("viewport") === "mobile" ? "mobile" : "desktop");
  if (params.get("simulate")) scene()?.previewSimulate?.(params.get("simulate"));
});
