// Set to false to disable speculative loading independently of the viewer fixes.
export const PHOTO_VIEWER_PRELOAD_ENABLED = true;
export const PHOTO_VIEWER_PRELOAD_BYTES = 32 * 1024 * 1024;

// Brief visits and rapid navigation should not start speculative transfers.
export function createPhotoViewerPreloader(load, { delayMs = 100, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let key = null, timer = null;
  return {
    update(artifact) {
      const next = artifact ? `${artifact.id}:${artifact.content_url}` : null;
      if (next === key) return;
      clearTimer(timer);
      timer = null;
      key = next;
      load(null);
      if (artifact) timer = setTimer(() => { timer = null; load(artifact); }, delayMs);
    },
    dispose() { clearTimer(timer); timer = null; key = null; load(null); },
  };
}

export function photoViewerPreloadArtifact(generations, currentId, direction = "older") {
  if (!PHOTO_VIEWER_PRELOAD_ENABLED) return null;
  const images = generations.filter((item) => item.display_artifact?.kind === "image");
  const index = images.findIndex((item) => item.id === currentId);
  if (index < 0) return null;
  const artifact = images[index + (direction === "newer" ? -1 : 1)]?.display_artifact;
  const { width, height } = artifact || {};
  return Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0 &&
    width * height * 4 <= PHOTO_VIEWER_PRELOAD_BYTES ? artifact : null;
}
