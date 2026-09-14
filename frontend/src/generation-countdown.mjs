import { activeGenerationEta, formatGenerationEta, formatNextInCountdown } from "./render.mjs";

export function nextGenerationCompletionTimestamp(generations, now) {
  let earliest = null;
  for (const generation of generations) {
    const completion = activeGenerationEta(generation, now)?.completionTimestamp;
    if (typeof completion === "number" && Number.isFinite(completion)
      && (earliest === null || completion < earliest)) earliest = completion;
  }
  return earliest;
}

export function updatePhotoViewerNextIn(root, generations, visible, now = Date.now()) {
  const dialog = root.querySelector("#photo-viewer");
  if (!dialog?.open) return;
  const badge = dialog.querySelector(".photo-viewer-next-in");
  if (!badge) return;
  badge.hidden = !visible;
  if (!visible) {
    badge.textContent = "";
    return;
  }
  const completion = nextGenerationCompletionTimestamp(generations, now);
  const text = formatNextInCountdown(completion === null ? null : (completion - now) / 1_000);
  if (badge.textContent !== text) badge.textContent = text;
}

export function refreshGenerationEtaElements(root, now = Date.now()) {
  for (const eta of root.querySelectorAll("[data-generation-eta-completion]")) {
    const completionTimestamp = Number(eta.getAttribute("data-generation-eta-completion"));
    if (!Number.isFinite(completionTimestamp)) continue;
    const text = formatGenerationEta((completionTimestamp - now) / 1000);
    if (!text) continue;
    if (eta.textContent !== text) eta.textContent = text;
    const progress = eta.closest(".generation-progress");
    const bar = progress?.querySelector("[data-progress-valuetext-base]");
    const baseValueText = bar?.getAttribute("data-progress-valuetext-base");
    if (baseValueText) bar.setAttribute("aria-valuetext", `${baseValueText}, ${text.replace(/^About/, "about")}`);
  }
}
