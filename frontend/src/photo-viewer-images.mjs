import { releaseImage as release } from "./image-cleanup.mjs";

export const photoKey = (artifact) => artifact ? `${artifact.id}:${artifact.content_url}` : null;

// Removing src alone need not settle decode(), so abort also settles our promise.
export function decodePhoto(artifact, signal, { speculative = false } = {}) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.fetchPriority = speculative ? "low" : "high";
    image.draggable = false;
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      image.onload = null;
      image.onerror = null;
      if (error) { release(image); reject(error); }
      else resolve(image);
    };
    const abort = () => finish(signal.reason || new DOMException("Aborted", "AbortError"));
    const timer = setTimeout(() => finish(new Error("Image loading timed out. Try again.")), 30_000);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    // Do not leave a native decode promise waiting on a cancelled network load.
    // Decode only downloaded data; listeners are removed on every exit path.
    image.onload = () => image.decode().then(() => finish(), (error) => finish(error));
    image.onerror = () => finish(new Error("Image is unavailable or could not be decoded."));
    image.src = artifact.content_url;
  });
}

// Only two slots: displayed and pending. Preload policy is independently removable.
export function createPhotoViewerImages({ load = decodePhoto } = {}) {
  let current = null, pending = null, failedPreloadKey = null;
  function discard(entry) {
    if (!entry) return;
    entry.controller.abort();
    release(entry.image);
    entry.image = null;
  }
  function clearPending() { discard(pending); pending = null; }
  function prepare(artifact, speculative) {
    const key = photoKey(artifact);
    if (pending?.key === key) { if (!speculative) pending.speculative = false; return pending; }
    clearPending();
    const entry = { key, controller: new AbortController(), speculative, image: null };
    pending = entry;
    entry.promise = Promise.resolve().then(() => {
      if (entry.controller.signal.aborted) throw entry.controller.signal.reason;
      return load(artifact, entry.controller.signal, { speculative });
    }).then((image) => {
      if (entry.controller.signal.aborted) { release(image); throw entry.controller.signal.reason; }
      entry.image = image;
      return image;
    }).catch((error) => {
      if (entry.speculative && !entry.controller.signal.aborted) failedPreloadKey = entry.key;
      if (pending === entry) pending = null;
      discard(entry);
      throw error;
    });
    entry.promise.catch(() => {});
    return entry;
  }
  return {
    async show(artifact, commit = () => {}) {
      const key = photoKey(artifact);
      if (current?.key === key) {
        clearPending();
        if (commit(current.image) === false) throw new DOMException("Obsolete image", "AbortError");
        return current.image;
      }
      const entry = prepare(artifact, false);
      const image = await entry.promise;
      if (entry.controller.signal.aborted) throw entry.controller.signal.reason;
      // Validate and commit the consumer's image/actions in one synchronous step
      // before releasing the previously displayed image.
      if (commit(image) === false) {
        if (pending === entry) clearPending();
        else discard(entry);
        throw new DOMException("Obsolete image", "AbortError");
      }
      if (entry.controller.signal.aborted) throw entry.controller.signal.reason;
      if (current !== entry) { discard(current); current = entry; }
      if (pending === entry) pending = null;
      return image;
    },
    preload(artifact) {
      if (!artifact || current?.key === photoKey(artifact)) { if (pending?.speculative) clearPending(); return; }
      if (pending && !pending.speculative) return;
      if (photoKey(artifact) === failedPreloadKey) return;
      prepare(artifact, true);
    },
    clearPending,
    dispose() { clearPending(); discard(current); current = null; failedPreloadKey = null; },
    snapshot: () => ({ current: current?.key || null, pending: pending?.key || null, speculative: Boolean(pending?.speculative) }),
  };
}
