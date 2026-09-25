import { expect, test } from "@playwright/test";

const image = '<svg xmlns="http://www.w3.org/2000/svg" width="2048" height="1536"><rect width="2048" height="1536" fill="#225674"/></svg>';
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

async function fixture(page, { pages = 1, running = false, oversized = false, collections = [] } = {}) {
  const holds = new Map(), updates = new Map(), requests = [], writes = [];
  const generation = (index) => ({
    id: `photo-${index}`, collection_id: null, status: running && index === 0 ? "running" : "succeeded",
    prompt_fingerprint: "fixture", accepted_at: new Date(Date.UTC(2026, 8, 24, 0, 0, 60 - index)).toISOString(),
    workflow_display_name: `Workflow ${index}`, checkpoint_label: `Checkpoint ${index}`,
    expected_width: 2048, expected_height: 1536, image_count: 1,
    display_artifact: { id: `image-${index}`, kind: "image", width: oversized ? 8192 : 2048, height: oversized ? 8192 : 1536,
      content_url: `/api/artifacts/image-${index}/content`, thumbnail_url: `/api/artifacts/image-${index}/thumbnail` },
    ...updates.get(index),
  });
  await page.addInitScript(() => {
    window.EventSource = class extends EventTarget { constructor() { super(); window.fixtureEvents = this; } close() {} };
    window.probe = { thumbnailScans: 0, selectionScans: 0 };
    const query = Element.prototype.querySelectorAll;
    Element.prototype.querySelectorAll = function (selector) {
      if (this.id === "gallery-viewport" && selector === "img[data-thumbnail-src]") probe.thumbnailScans++;
      if (this.id === "app" && selector === "#gallery [data-gallery-card]") probe.selectionScans++;
      return query.call(this, selector);
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    const key = path === "/api/generations" ? `page:${url.searchParams.get("cursor") || 0}` : path;
    requests.push(key);
    if (request.method() !== "GET") writes.push({ path, method: request.method() });
    const hold = holds.get(key);
    if (hold) await hold.promise;
    if (hold?.error) return route.fulfill({ status: 404, json: { error: { message: "Fixture unavailable" } } });
    if (path.endsWith("/content") || path.endsWith("/thumbnail")) return route.fulfill({ contentType: "image/svg+xml", body: image });
    let result = {};
    if (path === "/api/auth/session") result = { authenticated: true, csrf_token: "fixture", user: { id: "viewer-fixture", username: "fixture", role: "user", must_change_password: false } };
    else if (path === "/api/generations") {
      const offset = Number(url.searchParams.get("cursor") || 0);
      result = hold?.page || { items: Array.from({ length: 24 }, (_, i) => generation(offset + i)), next_cursor: offset + 24 < pages * 24 ? String(offset + 24) : null };
    } else if (/^\/api\/generations\/photo-\d+$/.test(path)) result = generation(Number(path.split("-").at(-1)));
    else if (path.endsWith("/favorite")) { const index = Number(path.split("/").at(-2).split("-").at(-1)); updates.set(index, { ...updates.get(index), is_favorite: request.method() === "PUT" }); }
    else if (path === "/api/preferences") result = { settings_initialized: true, settings: {}, revision: 1, gallery_scale: 40, checkpoint_tiers: {} };
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/generation-activity") result = { remaining_count: 0, collections: [], run: null };
    else if (path === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
    else if (path === "/api/collections") result = collections;
    else if (["/api/services", "/api/workflows", "/api/prompt-generations", "/api/generation-preparations"].includes(path) || path.endsWith("/lookup")) result = [];
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  await expect(page.locator('[data-action="open-photo"][data-generation-id="photo-0"]')).toBeVisible();
  return { requests, updates, writes, generation, hold(key) { const gate = deferred(); holds.set(key, gate); return gate; } };
}

const viewer = (page) => page.locator("#photo-viewer");
const photo = (page) => viewer(page).locator(".photo-viewer-media img");
async function open(page, index = 0) {
  await page.locator(`[data-action="open-photo"][data-generation-id="photo-${index}"]`).evaluate((button) => button.click());
  await expect(viewer(page).locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", `photo-${index}`);
  await expect.poll(() => photo(page).evaluate((img) => img.complete && img.naturalWidth > 0)).toBe(true);
}
async function progress(page, count = 1) {
  for (let i = 0; i < count; i++) await page.evaluate((value) => {
    fixtureEvents.dispatchEvent(new MessageEvent("generation.progress", { data: JSON.stringify({ generation_id: "photo-0", payload: { progress: { kind: "node", label: "Sampling", value, maximum: 100, fraction: value / 100 } } }) }));
  }, i + 1);
}
async function frame(page, id) { await expect(viewer(page).locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", `photo-${id}`); }

test("same-artifact progress preserves the viewer image, zoom and focused controls", async ({ page }) => {
  await fixture(page, { running: true }); await open(page);
  await page.evaluate(() => { window.savedPhoto = document.querySelector(".photo-viewer-media img"); });
  await viewer(page).getByRole("button", { name: "Fit", exact: true }).click();
  await progress(page, 12);
  expect(await page.evaluate(() => savedPhoto === document.querySelector(".photo-viewer-media img"))).toBe(true);
  await expect(photo(page)).toHaveAttribute("data-photo-zoom", "1");
  await expect(viewer(page).getByRole("button", { name: "Fit", exact: true })).toBeFocused();
});

test("closing releases the displayed image and reopening works", async ({ page }) => {
  await fixture(page); await open(page);
  await page.evaluate(() => { window.savedPhoto = document.querySelector(".photo-viewer-media img"); });
  await viewer(page).getByRole("button", { name: "Close image viewer" }).click();
  await expect(viewer(page).locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => savedPhoto.hasAttribute("src"))).toBe(false);
  await open(page, 1);
});

test("progress text does not rescan all thumbnails or selection cards", async ({ page }) => {
  await fixture(page, { running: true }); await open(page);
  await progress(page);
  const before = await page.evaluate(() => ({ ...probe }));
  await progress(page, 20);
  expect(await page.evaluate(() => ({ ...probe }))).toEqual(before);
});

test("repeated boundary navigation shares pagination and keeps the browser responsive", async ({ page }) => {
  const f = await fixture(page, { pages: 2 }); const next = f.hold("page:24"); await open(page, 23);
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => f.requests.filter((url) => url === "page:24").length).toBe(1);
  await page.keyboard.press("ArrowRight");
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(f.requests.filter((url) => url === "page:24")).toHaveLength(1);
  next.resolve(); await frame(page, 24);
});

for (const action of ["reverse", "close", "folder"]) test(`pending pagination cannot override ${action}`, async ({ page }) => {
  const f = await fixture(page, { pages: 2 }); const next = f.hold("page:24"); await open(page, 23);
  await page.keyboard.press("ArrowRight"); await expect.poll(() => f.requests.includes("page:24")).toBe(true);
  if (action === "reverse") { await page.keyboard.press("ArrowLeft"); await frame(page, 22); }
  else if (action === "close") await viewer(page).getByRole("button", { name: "Close image viewer" }).click();
  else await page.evaluate(() => { location.hash = "#/c/another"; });
  next.resolve();
  if (action === "reverse") { await expect(page.locator('[data-gallery-card][data-generation-id="photo-47"]')).toHaveCount(1); await frame(page, 22); }
  else await expect(viewer(page)).not.toHaveAttribute("open");
});

test("image transitions retain matching action targets and suppress obsolete completion", async ({ page }) => {
  const f = await fixture(page, { oversized: true }); await open(page);
  const b = f.hold("/api/artifacts/image-1/content"), c = f.hold("/api/artifacts/image-2/content");
  await page.keyboard.press("ArrowRight"); await expect.poll(() => f.requests.includes("/api/artifacts/image-1/content")).toBe(true);
  await frame(page, 0);
  await expect(viewer(page).locator(".photo-viewer-download")).toHaveAttribute("href", "/api/artifacts/image-0/content");
  await expect(viewer(page).locator(".photo-viewer-delete")).toHaveAttribute("data-generation-id", "photo-0");
  await viewer(page).locator(".photo-viewer-favorite").click();
  expect(f.writes.at(-1)).toEqual({ path: "/api/generations/photo-0/favorite", method: "PUT" });
  await page.keyboard.press("ArrowRight"); await expect.poll(() => f.requests.includes("/api/artifacts/image-2/content")).toBe(true);
  c.resolve(); await frame(page, 2); b.resolve();
  await expect(photo(page)).toHaveAttribute("src", "/api/artifacts/image-2/content");
  await expect(viewer(page).locator(".photo-viewer-download")).toHaveAttribute("href", "/api/artifacts/image-2/content");
});

for (const kind of ["error", "unchanged", "empty"]) test(`pagination handles ${kind} responses without retry loops`, async ({ page }) => {
  const f = await fixture(page, { pages: 2 }); const gate = f.hold("page:24");
  if (kind === "error") gate.error = true;
  else gate.page = { items: [], next_cursor: kind === "unchanged" ? "24" : null };
  gate.resolve(); await open(page, 23); await page.keyboard.press("ArrowRight");
  if (kind === "empty") await expect(viewer(page).getByRole("button", { name: "View older generation" })).toHaveCount(0);
  else {
    await expect(viewer(page).getByRole("button", { name: "Retry", exact: true })).toBeVisible();
    expect(f.requests.filter((key) => key === "page:24")).toHaveLength(1);
    gate.error = false; gate.page = null;
    await viewer(page).getByRole("button", { name: "Retry", exact: true }).click(); await frame(page, 24);
  }
});

test("a changed artifact replaces the displayed preview and updates download atomically", async ({ page }) => {
  const f = await fixture(page, { running: true }); await open(page);
  const gate = f.hold("/api/artifacts/final/content");
  f.updates.set(0, { status: "succeeded", display_artifact: { ...f.generation(0).display_artifact, id: "final", content_url: "/api/artifacts/final/content" } });
  await page.evaluate(() => fixtureEvents.dispatchEvent(new MessageEvent("generation.terminal", { data: JSON.stringify({ generation_id: "photo-0" }) })));
  await expect.poll(() => f.requests.includes("/api/artifacts/final/content")).toBe(true);
  await expect(photo(page)).toHaveAttribute("src", "/api/artifacts/image-0/content");
  await expect(viewer(page).locator(".photo-viewer-download")).toHaveAttribute("href", "/api/artifacts/image-0/content");
  gate.resolve();
  await expect(photo(page)).toHaveAttribute("src", "/api/artifacts/final/content");
  await expect(viewer(page).locator(".photo-viewer-download")).toHaveAttribute("href", "/api/artifacts/final/content");
});

test("an image failure retains the displayed image and retry succeeds", async ({ page }) => {
  const f = await fixture(page, { oversized: true }); await open(page);
  const gate = f.hold("/api/artifacts/image-1/content"); gate.error = true; gate.resolve();
  await page.keyboard.press("ArrowRight"); await expect(viewer(page).getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  await frame(page, 0); gate.error = false;
  await viewer(page).getByRole("button", { name: "Retry", exact: true }).click(); await frame(page, 1);
});

test("closing during decoding cannot overwrite a synchronously reopened viewer", async ({ page }) => {
  await page.addInitScript(() => {
    const decode = HTMLImageElement.prototype.decode;
    HTMLImageElement.prototype.decode = async function () {
      await decode.call(this);
      if (this.src.endsWith("/image-1/content")) await new Promise((resolve) => { window.releaseDecode = resolve; });
    };
  });
  await fixture(page, { oversized: true }); await open(page);
  await page.keyboard.press("ArrowRight"); await page.waitForFunction(() => Boolean(window.releaseDecode));
  await page.evaluate(() => {
    document.querySelector('[data-action="close-photo"]').click();
    document.querySelector('[data-action="open-photo"][data-generation-id="photo-2"]').click();
    releaseDecode();
  });
  await frame(page, 2); await expect(photo(page)).toHaveAttribute("src", "/api/artifacts/image-2/content");
});

test("preloading reuses one neighbor, changes direction, and pauses for slideshow", async ({ page }) => {
  const f = await fixture(page); await open(page);
  await expect.poll(() => f.requests.filter((url) => url.endsWith("/content"))).toEqual(["/api/artifacts/image-0/content", "/api/artifacts/image-1/content"]);
  await page.keyboard.press("ArrowRight"); await frame(page, 1);
  await expect.poll(() => f.requests.includes("/api/artifacts/image-2/content")).toBe(true);
  expect(f.requests.filter((url) => url === "/api/artifacts/image-1/content")).toHaveLength(1);
  await page.keyboard.press("ArrowLeft"); await frame(page, 0);
  await viewer(page).getByRole("button", { name: "Slideshow", exact: true }).click();
  await frame(page, 0);
  const before = f.requests.filter((url) => url.endsWith("/content")).length;
  await progress(page, 3);
  expect(f.requests.filter((url) => url.endsWith("/content"))).toHaveLength(before);
});

test("oversized images skip speculative loading but open on demand", async ({ page }) => {
  const f = await fixture(page, { oversized: true }); await open(page);
  await page.evaluate(() => new Promise(requestAnimationFrame));
  expect(f.requests.filter((url) => url.endsWith("/content"))).toEqual(["/api/artifacts/image-0/content"]);
  await page.keyboard.press("ArrowRight"); await frame(page, 1);
});

test("pan and zoom accumulate input and render once per frame", async ({ page }) => {
  await fixture(page); await open(page);
  const result = await page.evaluate(async () => {
    const image = document.querySelector(".photo-viewer-media img");
    const before = Number(image.dataset.photoPanX);
    let transforms = 0;
    const observer = new MutationObserver((records) => { transforms += records.filter((r) => r.attributeName === "data-photo-pan-x").length; });
    observer.observe(image, { attributes: true });
    for (let i = 0; i < 30; i++) image.dispatchEvent(new WheelEvent("wheel", { deltaX: 2, bubbles: true, cancelable: true }));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    observer.disconnect(); return { delta: Number(image.dataset.photoPanX) - before, transforms };
  });
  expect(result).toEqual({ delta: -60, transforms: 1 });
});

test("navigation controls keep their direction and focus when the opposite button appears", async ({ page }) => {
  await fixture(page); await open(page);
  const older = viewer(page).getByRole("button", { name: "View older generation" });
  await older.click(); await frame(page, 1); await expect(older).toBeFocused();
  await page.keyboard.press("Enter"); await frame(page, 2);
});

test("closing or signing out during download discards late results", async ({ page }) => {
  const f = await fixture(page, { oversized: true }); await open(page);
  const gate = f.hold("/api/artifacts/image-1/content");
  await page.keyboard.press("ArrowRight"); await expect.poll(() => f.requests.includes("/api/artifacts/image-1/content")).toBe(true);
  await page.locator('[data-action="logout"]').evaluate((button) => button.click());
  await expect(viewer(page)).toHaveCount(0); gate.resolve();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.locator(".photo-viewer-media img")).toHaveCount(0);
});

test("hiding a tab drops its preloaded original", async ({ page }) => {
  await page.addInitScript(() => {
    const NativeImage = window.Image;
    window.testImages = [];
    window.Image = function (...args) { const image = new NativeImage(...args); testImages.push(image); return image; };
  });
  const f = await fixture(page); await open(page);
  await expect.poll(() => f.requests.includes("/api/artifacts/image-1/content")).toBe(true);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(await page.evaluate(() => testImages.filter((image) => image.getAttribute("src")?.endsWith("/content")).map((image) => image.getAttribute("src")))).toEqual(["/api/artifacts/image-0/content"]);
  await frame(page, 0);
});

test("gallery scrolling and boundary navigation share their pending page", async ({ page }) => {
  const f = await fixture(page, { pages: 2 }); const gate = f.hold("page:24");
  await page.locator("#gallery-sentinel").evaluate((sentinel) => sentinel.scrollIntoView());
  await expect.poll(() => f.requests.filter((key) => key === "page:24").length).toBe(1);
  await open(page, 23); await page.keyboard.press("ArrowRight");
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(f.requests.filter((key) => key === "page:24")).toHaveLength(1);
  gate.resolve(); await frame(page, 24);
});

test("an empty advancing page continues to the next page and the end stays stable", async ({ page }) => {
  const f = await fixture(page, { pages: 3 });
  const empty = f.hold("page:24"), next = f.hold("page:48");
  empty.page = { items: [], next_cursor: "48" }; empty.resolve();
  next.page = { items: [f.generation(48)], next_cursor: null };
  await open(page, 23); await page.keyboard.press("ArrowRight");
  await expect.poll(() => f.requests.includes("page:48")).toBe(true);
  next.resolve(); await frame(page, 48);
  await page.keyboard.press("ArrowRight"); await frame(page, 48);
  expect(f.requests.filter((key) => key === "page:24")).toHaveLength(1);
  expect(f.requests.filter((key) => key === "page:48")).toHaveLength(1);
  await page.keyboard.press("ArrowLeft"); await frame(page, 23);
});

test("download and delete requests use the displayed photo while its replacement waits", async ({ page }) => {
  const f = await fixture(page, { oversized: true }); await open(page);
  const gate = f.hold("/api/artifacts/image-1/content"), deleted = [];
  await page.route("**/api/generations/photo-0", (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deleted.push(new URL(route.request().url()).pathname);
    return route.fulfill({ status: 204 });
  });
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => f.requests.includes("/api/artifacts/image-1/content")).toBe(true);
  await expect(viewer(page).locator(".photo-viewer-checkpoint")).toHaveText("Checkpoint 0");
  await expect(photo(page)).toHaveAttribute("alt", /Workflow 0/);
  const download = page.waitForEvent("download");
  await viewer(page).getByRole("link", { name: "Download current image" }).click();
  expect(new URL((await download).url()).pathname).toBe("/api/artifacts/image-0/content");
  page.once("dialog", (dialog) => dialog.accept());
  await viewer(page).getByRole("button", { name: "Delete generation", exact: true }).click();
  await expect(viewer(page)).not.toHaveAttribute("open");
  expect(deleted).toEqual(["/api/generations/photo-0"]);
  gate.resolve(); await open(page, 2);
  await expect(photo(page)).toHaveAttribute("src", "/api/artifacts/image-2/content");
});

test("closing during download permits immediate reopening before the old response", async ({ page }) => {
  const f = await fixture(page, { oversized: true }); await open(page);
  const gate = f.hold("/api/artifacts/image-1/content");
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => f.requests.includes("/api/artifacts/image-1/content")).toBe(true);
  await page.evaluate(() => {
    document.querySelector('[data-action="close-photo"]').click();
    document.querySelector('[data-action="open-photo"][data-generation-id="photo-2"]').click();
  });
  gate.resolve(); await frame(page, 2);
  await expect(photo(page)).toHaveAttribute("src", "/api/artifacts/image-2/content");
});

test("batched zoom preserves the pointer anchor and a drag commits its final position", async ({ page }) => {
  await fixture(page); await open(page);
  await expect(photo(page)).toHaveAttribute("data-photo-zoom", /\d/);
  const result = await page.evaluate(async () => {
    const image = document.querySelector(".photo-viewer-media img");
    const bounds = image.closest(".photo-viewer-media").getBoundingClientRect();
    const pan = () => ({ x: Number(image.dataset.photoPanX), y: Number(image.dataset.photoPanY), zoom: Number(image.dataset.photoZoom) });
    const before = pan(), anchor = { x: 100, y: -50 };
    const paint = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    for (let i = 0; i < 12; i++) image.dispatchEvent(new WheelEvent("wheel", {
      deltaY: -2, ctrlKey: true, bubbles: true, cancelable: true,
      clientX: bounds.x + bounds.width / 2 + anchor.x, clientY: bounds.y + bounds.height / 2 + anchor.y,
    }));
    await paint(); const zoomed = pan();
    const pointer = (type, x, y) => image.dispatchEvent(new PointerEvent(type, { pointerId: 1, button: 0, clientX: x, clientY: y, bubbles: true, cancelable: true }));
    pointer("pointerdown", 100, 100);
    pointer("pointermove", 120, 110); pointer("pointermove", 150, 125); pointer("pointermove", 170, 145);
    pointer("pointerup", 170, 145); await paint();
    return { before, anchor, zoomed, dragged: pan() };
  });
  for (const axis of ["x", "y"]) expect((result.anchor[axis] - result.zoomed[axis]) / result.zoomed.zoom).toBeCloseTo((result.anchor[axis] - result.before[axis]) / result.before.zoom, 5);
  expect(result.dragged.x - result.zoomed.x).toBeCloseTo(70, 5);
  expect(result.dragged.y - result.zoomed.y).toBeCloseTo(45, 5);
});

test("closing details releases its image sources and permits reopening", async ({ page }) => {
  const f = await fixture(page);
  f.updates.set(0, { artifacts: [{ ...f.generation(0).display_artifact, state: "final", role: "final" }] });
  const button = page.locator('[data-action="open-detail"][data-generation-id="photo-0"]');
  await button.evaluate((element) => element.click());
  const detail = page.locator("#detail-dialog");
  await expect(detail.locator("img")).toHaveCount(1);
  await page.evaluate(() => { window.detailImage = document.querySelector("#detail-dialog img"); });
  await detail.getByRole("button", { name: "Close details" }).click();
  await expect(detail.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => detailImage.hasAttribute("src"))).toBe(false);
  await button.evaluate((element) => element.click());
  await expect(detail.locator("img")).toHaveAttribute("src", "/api/artifacts/image-0/content");
});

test("cancelled speculative image elements are collectable after repeated closes", async ({ page, context }) => {
  test.setTimeout(60_000);
  await page.addInitScript(() => {
    const NativeImage = window.Image;
    window.imageReferences = [];
    window.Image = function (...args) { const image = new NativeImage(...args); imageReferences.push(new WeakRef(image)); return image; };
  });
  const f = await fixture(page), gate = f.hold("/api/artifacts/image-1/content");
  for (let i = 1; i <= 10; i++) {
    await open(page);
    await expect.poll(() => f.requests.filter((key) => key === "/api/artifacts/image-1/content").length).toBe(i);
    await viewer(page).getByRole("button", { name: "Close image viewer" }).evaluate((button) => button.click());
  }
  gate.resolve();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const cdp = await context.newCDPSession(page);
  await cdp.send("Runtime.discardConsoleEntries");
  await cdp.send("HeapProfiler.collectGarbage");
  expect(await page.evaluate(() => imageReferences.filter((ref) => ref.deref()).length)).toBe(0);
});

test("a live refresh started before a move cannot restore the moved card", async ({ page }) => {
  const f = await fixture(page, { collections: [{ id: "destination", name: "Destination", parent_id: null, generation_count: 0 }] });
  const gate = f.hold("/api/generations/photo-0");
  await page.evaluate(() => fixtureEvents.dispatchEvent(new MessageEvent("generation.terminal", { data: JSON.stringify({ generation_id: "photo-0" }) })));
  await expect.poll(() => f.requests.includes("/api/generations/photo-0")).toBe(true);
  await page.route("**/api/generations?*", (route) => route.fulfill({ json: { items: Array.from({ length: 23 }, (_, i) => f.generation(i + 1)), next_cursor: null } }));
  await page.route("**/api/gallery/transfer", (route) => route.fulfill({ json: { operation: "move", generation_ids: ["photo-0"], collection_ids: [] } }));
  const card = page.locator('.gallery-card[data-generation-id="photo-0"]');
  await card.locator(".card-select-button").evaluate((button) => button.click());
  await page.getByRole("button", { name: "Move / Copy…" }).click();
  const dialog = page.locator("#gallery-transfer-dialog");
  await dialog.getByRole("radio", { name: "Destination" }).check();
  await dialog.getByRole("button", { name: "Move here", exact: true }).click();
  await expect(card).toHaveCount(0);
  const finished = page.waitForEvent("requestfinished", { predicate: (request) => request.url().endsWith("/api/generations/photo-0") });
  gate.resolve(); await finished;
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(card).toHaveCount(0);
});
