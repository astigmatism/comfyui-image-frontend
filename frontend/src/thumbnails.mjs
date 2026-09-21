import { api } from "./api.mjs";

export function createThumbnailScheduler({ limit = 4, load = (url, signal) => api(url, {
  responseType: "blob", signal, deadlineMs: 30_000, operation: "Thumbnail",
}), createURL = URL.createObjectURL, revokeURL = URL.revokeObjectURL } = {}) {
  const entries = new Map();
  const queue = [];
  let active = 0;
  function drain() {
    while (active < limit && queue.length) {
      const entry = queue.shift();
      if (!entry.listeners.size) continue;
      active += 1;
      entry.running = true;
      Promise.resolve().then(() => load(entry.url, entry.controller.signal)).then((blob) => {
        if (!entry.listeners.size) return;
        entry.objectURL = createURL(blob);
        for (const listener of entry.listeners) listener({ url: entry.objectURL });
      }).catch((error) => {
        if (entry.controller.signal.aborted) return;
        entry.error = error;
        for (const listener of entry.listeners) listener({ error });
      }).finally(() => { active -= 1; drain(); });
    }
  }
  function subscribe(url, listener) {
    let entry = entries.get(url);
    if (!entry) {
      entry = { url, controller: new AbortController(), listeners: new Set() };
      entries.set(url, entry);
      queue.push(entry);
    }
    entry.listeners.add(listener);
    if (entry.objectURL || entry.error) queueMicrotask(() => {
      if (entry.listeners.has(listener)) listener({ url: entry.objectURL, error: entry.error });
    });
    drain();
    return () => {
      entry.listeners.delete(listener);
      if (entry.listeners.size) return;
      entry.controller.abort();
      if (entry.objectURL) revokeURL(entry.objectURL);
      entries.delete(url);
      const index = queue.indexOf(entry);
      if (index >= 0) queue.splice(index, 1);
    };
  }
  function retry(url) {
    const entry = entries.get(url);
    if (!entry?.error) return;
    entry.error = null;
    queue.push(entry);
    drain();
  }
  return { subscribe, retry, snapshot: () => ({ active, queued: queue.length, retained: entries.size }) };
}

export function installThumbnails(root) {
  const scheduler = createThumbnailScheduler();
  const images = new Map();
  function release(img, item) {
    item.stop?.();
    item.stop = null;
    item.retry?.remove();
    item.retry = null;
    img.removeAttribute("src");
  }
  function start(img, item) {
    if (item.stop) return;
    item.stop = scheduler.subscribe(item.url, ({ url, error }) => {
      if (!images.has(img) || !item.visible) return;
      if (url) {
        item.retry?.remove();
        item.retry = null;
        img.src = url;
        img.alt = item.alt;
        return;
      }
      if (error && item.retry) item.retry.disabled = false;
      if (error && !item.retry) {
        img.alt = "Thumbnail unavailable";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button secondary thumbnail-retry";
        button.textContent = "Retry thumbnail";
        button.setAttribute("aria-label", "Retry unavailable thumbnail");
        button.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          button.disabled = true;
          scheduler.retry(item.url);
        });
        // Keep retry controls outside the existing image/collection buttons.
        (img.closest(".card-media-frame, .collection-tile") || img.parentElement).append(button);
        item.retry = button;
      }
    });
  }
  const visibility = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const item = images.get(entry.target);
      if (!item) continue;
      item.visible = entry.isIntersecting;
      if (item.visible) start(entry.target, item);
      else release(entry.target, item);
    }
  });
  function scan() {
    for (const [img, item] of images) {
      if (!root.contains(img) || img.dataset.thumbnailSrc !== item.url) {
        visibility.unobserve(img);
        release(img, item);
        images.delete(img);
      }
    }
    for (const img of root.querySelectorAll("img[data-thumbnail-src]")) {
      if (images.has(img)) continue;
      images.set(img, { url: img.dataset.thumbnailSrc, visible: false, alt: img.alt });
      visibility.observe(img);
    }
  }
  const changes = new MutationObserver(scan);
  changes.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-thumbnail-src"] });
  scan();
  return () => {
    changes.disconnect();
    visibility.disconnect();
    for (const [img, item] of images) release(img, item);
    images.clear();
  };
}
