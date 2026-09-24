import { api } from "./api.mjs";

async function decodeThumbnail(url, signal) {
  const image = new Image();
  const abort = () => image.removeAttribute("src");
  signal.addEventListener("abort", abort, { once: true });
  try {
    if (signal.aborted) throw signal.reason;
    image.src = url;
    await image.decode();
  } finally {
    signal.removeEventListener("abort", abort);
    image.removeAttribute("src");
  }
}

export function createThumbnailScheduler({ limit = 4, load = (url, signal) => api(url, {
  responseType: "blob", signal, deadlineMs: 30_000, operation: "Thumbnail",
}), decode = decodeThumbnail, createURL = URL.createObjectURL, revokeURL = URL.revokeObjectURL,
maxIdleEntries = 96, maxIdleBytes = 16 * 1024 * 1024, idleMs = 60_000,
now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  const entries = new Map();
  const queue = [];
  let active = 0;
  let idleOrder = 0;
  let disposed = false;
  let drainQueued = false;
  let expiryTimer;
  function evict(entry) {
    entry.controller.abort();
    if (entry.objectURL) revokeURL(entry.objectURL);
    entry.objectURL = null;
    if (entries.get(entry.url) === entry) entries.delete(entry.url);
    const index = queue.indexOf(entry);
    if (index >= 0) queue.splice(index, 1);
  }
  function prune() {
    clearTimer(expiryTimer);
    expiryTimer = null;
    const idle = [...entries.values()].filter((entry) => !entry.listeners.size && entry.ready)
      .sort((a, b) => a.idleOrder - b.idleOrder);
    let bytes = idle.reduce((sum, entry) => sum + entry.bytes, 0);
    while (idle.length && (idle.length > maxIdleEntries || bytes > maxIdleBytes || now() - idle[0].idleAt >= idleMs)) {
      const entry = idle.shift();
      bytes -= entry.bytes;
      evict(entry);
    }
    if (idle.length) {
      expiryTimer = setTimer(prune, Math.max(1, idleMs - (now() - idle[0].idleAt)));
      expiryTimer?.unref?.();
    }
  }
  const priority = (entry) => Math.min(...entry.listeners.values());
  function scheduleDrain() {
    if (drainQueued || disposed) return;
    drainQueued = true;
    queueMicrotask(() => { drainQueued = false; drain(); });
  }
  function drain() {
    queue.sort((a, b) => priority(a) - priority(b));
    while (!disposed && active < limit && queue.length) {
      const entry = queue.shift();
      if (!entry.listeners.size) continue;
      active += 1;
      entry.running = true;
      Promise.resolve().then(() => {
        if (entry.controller.signal.aborted) throw entry.controller.signal.reason;
        return load(entry.url, entry.controller.signal);
      }).then(async (blob) => {
        if (entry.controller.signal.aborted || !entry.listeners.size) return;
        entry.objectURL = createURL(blob);
        entry.bytes = blob.size;
        await decode(entry.objectURL, entry.controller.signal);
        if (entry.controller.signal.aborted || !entry.listeners.size) return;
        entry.ready = true;
        for (const listener of entry.listeners.keys()) listener({ url: entry.objectURL });
      }).catch((error) => {
        if (entry.controller.signal.aborted) return;
        if (entry.objectURL) revokeURL(entry.objectURL);
        entry.objectURL = null;
        entry.error = error;
        for (const listener of entry.listeners.keys()) listener({ error });
      }).finally(() => { entry.running = false; active -= 1; scheduleDrain(); });
    }
  }
  function subscribe(url, listener, { priority: rank = 0 } = {}) {
    if (disposed) throw new Error("Thumbnail scheduler is disposed.");
    prune();
    let entry = entries.get(url);
    if (!entry) {
      entry = { url, controller: new AbortController(), listeners: new Map(), ready: false };
      entries.set(url, entry);
      queue.push(entry);
    }
    entry.listeners.set(listener, rank);
    if (entry.ready || entry.error) queueMicrotask(() => {
      if (entry.listeners.has(listener)) listener({ url: entry.objectURL, error: entry.error });
    });
    scheduleDrain();
    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      entry.listeners.delete(listener);
      if (entry.listeners.size) return;
      if (entry.ready) { entry.idleAt = now(); entry.idleOrder = ++idleOrder; prune(); }
      else evict(entry);
    };
    stop.setPriority = (value) => {
      if (stopped) return;
      entry.listeners.set(listener, value);
      scheduleDrain();
    };
    return stop;
  }
  function retry(url) {
    const entry = entries.get(url);
    if (!entry?.error || entry.running) return;
    entry.error = null;
    queue.push(entry);
    scheduleDrain();
  }
  return {
    subscribe, retry,
    snapshot: () => ({ active, queued: queue.length, retained: entries.size }),
    dispose() {
      disposed = true;
      clearTimer(expiryTimer);
      for (const entry of entries.values()) { entry.listeners.clear(); evict(entry); }
    },
  };
}

// Owned by the authenticated gallery shell. Both observers use its actual
// scrolling element, rather than the browser viewport outside that element.
export function installThumbnails(root, { scheduler = createThumbnailScheduler() } = {}) {
  const images = new Map();
  let disposed = false;
  function release(img, item) {
    item.revision += 1;
    img.dataset.thumbnailState = "pending";
    img.removeAttribute("src");
    item.stop?.();
    item.stop = null;
    item.retry?.remove();
    item.retry = null;
  }
  function unavailable(img, item) {
    img.dataset.thumbnailState = "error";
    img.removeAttribute("src");
    if (item.retry) { item.retry.disabled = false; return; }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button secondary thumbnail-retry";
    button.dataset.thumbnailRetry = "";
    button.textContent = "Retry thumbnail";
    button.setAttribute("aria-label", "Retry unavailable thumbnail");
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      button.disabled = true;
      img.dataset.thumbnailState = "pending";
      if (item.decodeFailed) {
        item.decodeFailed = false;
        item.stop?.();
        item.stop = null;
        start(img, item);
      } else scheduler.retry(item.url);
    });
    // Keep retry controls outside the existing image/collection buttons.
    (img.closest(".card-media-frame, .collection-tile") || img.parentElement).append(button);
    item.retry = button;
  }
  function start(img, item) {
    if (item.stop) return;
    const bounds = img.getBoundingClientRect();
    const viewport = root.getBoundingClientRect();
    const visible = bounds.bottom > viewport.top && bounds.top < viewport.bottom;
    item.stop = scheduler.subscribe(item.url, async ({ url, error }) => {
      const revision = ++item.revision;
      const current = () => !disposed && images.get(img) === item && item.near
        && item.revision === revision && root.contains(img) && img.dataset.thumbnailSrc === item.url;
      if (!current()) return;
      if (error) { unavailable(img, item); return; }
      img.src = url;
      try {
        await img.decode();
        if (!current()) return;
        item.retry?.remove();
        item.retry = null;
        img.dataset.thumbnailState = "ready";
      } catch {
        if (!current()) return;
        item.decodeFailed = true;
        unavailable(img, item);
      }
    }, { priority: visible ? 0 : 1 });
  }
  const visibility = new IntersectionObserver((entries) => {
    for (const entry of entries) images.get(entry.target)?.stop?.setPriority(entry.isIntersecting ? 0 : 1);
  }, { root });
  const nearby = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const item = images.get(entry.target);
      if (!item) continue;
      item.near = entry.isIntersecting;
      if (item.near) start(entry.target, item);
      else if (item.stop) release(entry.target, item);
    }
  }, { root, rootMargin: "600px 0px" });
  function scan() {
    for (const [img, item] of images) {
      if (!root.contains(img) || img.dataset.thumbnailSrc !== item.url) {
        visibility.unobserve(img);
        nearby.unobserve(img);
        release(img, item);
        images.delete(img);
      }
    }
    for (const img of root.querySelectorAll("img[data-thumbnail-src]")) {
      if (images.has(img)) continue;
      img.dataset.thumbnailState = "pending";
      images.set(img, { url: img.dataset.thumbnailSrc, near: false, revision: 0 });
      visibility.observe(img);
      nearby.observe(img);
    }
  }
  const changes = new MutationObserver(scan);
  changes.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-thumbnail-src"] });
  scan();
  return () => {
    disposed = true;
    changes.disconnect();
    visibility.disconnect();
    nearby.disconnect();
    for (const [img, item] of images) release(img, item);
    images.clear();
    scheduler.dispose();
  };
}
