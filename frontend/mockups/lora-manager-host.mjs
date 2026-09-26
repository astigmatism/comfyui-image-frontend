const frame = document.querySelector("#lm-preview-frame");

document.querySelectorAll("[data-preview-size]").forEach((button) => {
  button.addEventListener("click", () => {
    const mobile = button.dataset.previewSize === "mobile";
    document.body.classList.toggle("lm-preview-mobile", mobile);
    document.querySelectorAll("[data-preview-size]").forEach((item) => {
      const selected = item === button;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    frame.contentWindow?.previewSetViewport?.(mobile ? "mobile" : "desktop");
  });
});

document.querySelectorAll("[data-preview-action]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.previewAction === "open") frame.contentWindow?.previewOpenDialog?.();
    if (button.dataset.previewAction === "reset") frame.contentWindow?.previewReset?.();
  });
});

frame.addEventListener("load", () => {
  frame.contentWindow?.previewSetViewport?.(document.body.classList.contains("lm-preview-mobile") ? "mobile" : "desktop");
});
