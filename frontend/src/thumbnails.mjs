import { api } from "./api.mjs";

// Gallery cards are re-rendered by live updates, so a URL's blob must survive
// the <img> elements that reference it being replaced. Completed blobs stay in
// the scheduler until LRU pressure evicts them, and a re-subscribed element
// gets its URL back on a microtask — no second fetch, no blank frame.
export function createThumbnailScheduler({ limit = 4, maxRetained = 256, load = (url, signal) => api(url, {
  responseType: "blob", signal, deadlineMs: 30_000, operation: "Thumbnail",
}), createURL = URL.createObjectURL, revokeURL = URL.revokeObjectURL } = {}) {
  const entries = new Map();
  const queue = [];
  let active = 0;
  let sequence = 0;
  const touch = (entry) => { entry.lastUsed = ++sequence; };
  function deliver(entry, listener) {
    if (entry.blob) {
      if (!entry.objectURL) entry.objectURL = createURL(entry.blob);
      listener({ url: entry.objectURL });
    } else if (entry.error) {
      listener({ error: entry.error });
    }
  }
  function drain() {
    while (active < limit && queue.length) {
      const entry = queue.shift();
      if (!entry.listeners.size) { queue.push(entry); break; }
      active += 1;
      entry.running = true;
      Promise.resolve().then(() => load(entry.url, entry.controller.signal)).then((blob) => {
        if (entry.running) {
          entry.running = false;
          entry.blob = blob;
          touch(entry);
          evict();
        }
        for (const listener of [...entry.listeners]) deliver(entry, listener);
      }).catch((error) => {
        if (entry.controller.signal.aborted) return;
        if (entry.running) {
          entry.running = false;
          entry.error = error;
          evict();
        }
        for (const listener of [...entry.listeners]) deliver(entry, listener);
      }).finally(() => { active -= 1; drain(); });
    }
  }
  function evict() {
    while (entries.size > maxRetained) {
      let oldest = null;
      for (const entry of entries.values()) {
        if (entry.listeners.size || entry.running || queue.includes(entry)) continue;
        if (!oldest || entry.lastUsed < oldest.lastUsed) oldest = entry;
      }
      if (!oldest) return;
      entries.delete(oldest.url);
      if (oldest.objectURL) revokeURL(oldest.objectURL);
    }
  }
  function subscribe(url, listener) {
    let entry = entries.get(url);
    if (!entry) {
      entry = { url, controller: new AbortController(), listeners: new Set() };
      entries.set(url, entry);
      touch(entry);
      queue.push(entry);
      evict();
    }
    entry.listeners.add(listener);
    touch(entry);
    if (entry.blob || entry.error) queueMicrotask(() => {
      if (entry.listeners.has(listener)) deliver(entry, listener);
    });
    drain();
    return () => {
      entry.listeners.delete(listener);
      if (entry.listeners.size) return;
      if (entry.running) return; // Let the in-flight load finish; its blob is cached.
      const index = queue.indexOf(entry);
      if (index >= 0) {
        // Never started: drop the request and the entry.
        queue.splice(index, 1);
        entries.delete(url);
        entry.controller.abort();
        return;
      }
      // Finished: keep the blob cached, release only the live object URL.
      if (entry.objectURL) {
        revokeURL(entry.objectURL);
        entry.objectURL = null;
      }
    };
  }
  function retry(url) {
    const entry = entries.get(url);
    if (!entry?.error) return;
    entry.error = null;
    touch(entry);
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
