import { expect, test } from "@playwright/test";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const image = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><path fill="#225674" d="M0 0h512v512H0z"/><circle cx="256" cy="256" r="100" fill="#4a95a0"/></svg>';

async function galleryFixture(page, options = {}) {
  const fixture = { thumbnails: [], history: [], groups: [], revoked: [], thumbnailDelay: 80, groupDelay: 300,
    fail: null, releaseGroups: null, favorites: new Map(), updates: new Map(), ...options };
  const generation = (folder, index) => ({
    id: `${folder}-${index}`, collection_id: folder === "home" ? null : folder,
    status: "succeeded", is_favorite: fixture.favorites.get(`${folder}-${index}`) ?? (fixture.allNonFavorites ? false : index % 3 === 0),
    prompt_fingerprint: "same", accepted_at: new Date(Date.UTC(2026, 8, 24, 0, 0, 60 - index)).toISOString(),
    expected_width: 512, expected_height: 512, image_count: 1,
    display_artifact: { id: `${folder}-a${index}`, kind: "image", thumbnail_url: `/api/artifacts/${folder}-${index}/thumbnail`, content_url: `/api/artifacts/${folder}-${index}/content` },
    ...fixture.updates.get(`${folder}-${index}`),
  });
  await page.addInitScript(() => {
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(...args) { super(...args); window.galleryEvents = this; }
    };
    window.galleryDiagnostics = { invalidFrames: 0, nonFavoriteGoldFrames: 0, revoked: [], writes: 0 };
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = (url) => { window.galleryDiagnostics.revoked.push(url); revoke(url); };
    const property = Object.getOwnPropertyDescriptor(Element.prototype, "innerHTML");
    Object.defineProperty(Element.prototype, "innerHTML", { ...property, set(value) {
      if (this.id === "gallery") window.galleryDiagnostics.writes++;
      return property.set.call(this, value);
    } });
    const frame = () => {
      const viewport = document.querySelector("#gallery-viewport")?.getBoundingClientRect();
      if (viewport) for (const img of document.querySelectorAll("#gallery img")) {
        const bounds = img.getBoundingClientRect();
        if (!bounds.width || !bounds.height || bounds.bottom <= viewport.top || bounds.top >= viewport.bottom) continue;
        if (getComputedStyle(img).opacity !== "0" && (!img.getAttribute("src") || !img.naturalWidth)) window.galleryDiagnostics.invalidFrames++;
      }
      for (const card of document.querySelectorAll("#gallery [data-gallery-card]:not(.is-favorited)")) {
        if (getComputedStyle(card, "::after").content !== "none") window.galleryDiagnostics.nonFavoriteGoldFrames++;
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    let result = {};
    if (path.endsWith("/thumbnail")) {
      fixture.thumbnails.push(path);
      await delay(fixture.thumbnailDelay);
      if (fixture.fail === "http") return route.fulfill({ status: 404, json: { error: { message: "Missing thumbnail" } } });
      return route.fulfill({ contentType: "image/svg+xml", body: fixture.fail === "decode" ? "invalid image" : image });
    }
    if (path === "/api/auth/session") result = { authenticated: true, csrf_token: "fixture", user: { id: "gallery-fixture", username: "gallery-fixture", role: "user", must_change_password: false } };
    else if (path === "/api/collections") {
      await delay(50);
      result = [
        { id: "a", name: "Folder A", parent_id: null, generation_count: 48, previews: [{ thumbnail_url: "/api/artifacts/home-0/thumbnail" }] },
        { id: "b", name: "Folder B", parent_id: "a", generation_count: 48, previews: [{ thumbnail_url: "/api/artifacts/a-0/thumbnail" }] },
      ];
    } else if (path === "/api/generations") {
      const folder = url.searchParams.get("collection_id") || "home";
      fixture.history.push(folder);
      await delay(fixture.historyDelay?.[folder] ?? 80);
      const offset = Number(url.searchParams.get("cursor") || 0);
      result = { items: Array.from({ length: 24 }, (_, index) => generation(folder, offset + index)), next_cursor: offset ? null : "24" };
    } else if (path.endsWith("/lookup")) {
      const data = request.postDataJSON();
      fixture.groups.push(data);
      if (fixture.holdGroups) await new Promise((resolve) => { fixture.releaseGroups = resolve; });
      else await delay(fixture.groupDelay);
      result = data.generation_ids.map((id) => ({ generation_id: id, group: { id: `${data.collection_id || "home"}-47`, generation_count: 48, previous_generation_id: null } }));
    } else if (/^\/api\/generations\/[^/]+$/.test(path)) {
      const [folder, index] = path.split("/").at(-1).split("-");
      result = generation(folder, Number(index));
    } else if (path.endsWith("/favorite")) {
      const id = path.split("/").at(-2);
      fixture.favorites.set(id, request.method() !== "DELETE");
      result = {};
    } else if (path === "/api/events") return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    else if (path === "/api/preferences") result = { settings_initialized: true, settings: {}, revision: 1, gallery_scale: 40, checkpoint_tiers: {} };
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/generation-activity") result = { remaining_count: 0, collections: [], run: null };
    else if (["/api/services", "/api/workflows", "/api/prompt-generations", "/api/generation-preparations"].includes(path)) result = [];
    else if (path === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  await expect(page.locator('[data-generation-id="home-0"] img')).toHaveAttribute("data-thumbnail-state", "ready");
  return fixture;
}

const card = (page, id) => page.locator(`[data-gallery-card="generation"][data-generation-id="${id}"]`);
async function navigate(page, folder) {
  await page.evaluate((id) => { location.hash = id ? `#/c/${id}` : "#/"; }, folder);
  await expect(card(page, `${folder || "home"}-0`).locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
}
async function cleanFrames(page) {
  expect(await page.evaluate(() => ({ invalid: galleryDiagnostics.invalidFrames, gold: galleryDiagnostics.nonFavoriteGoldFrames, writes: galleryDiagnostics.writes })))
    .toEqual({ invalid: 0, gold: 0, writes: 0 });
}

test("metadata, favorites, previews and pagination preserve existing decoded images", async ({ page }) => {
  const fixture = await galleryFixture(page, { holdGroups: true });
  await page.evaluate(() => {
    window.savedCard = document.querySelector('[data-gallery-card="generation"][data-generation-id="home-0"]');
    window.savedImage = savedCard.querySelector("img");
    window.savedSource = savedImage.src;
    window.savedGroup = savedCard.closest("[data-prompt-group]");
    window.savedPreview = document.querySelector(".collection-preview-cell img");
  });
  const selection = card(page, "home-0").locator(".card-select-button");
  await selection.focus(); await selection.click();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("1 selected");
  const imageBounds = await card(page, "home-0").boundingBox();
  await expect.poll(() => Boolean(fixture.releaseGroups)).toBe(true);
  fixture.holdGroups = false; fixture.releaseGroups();
  await expect(page.locator('[data-prompt-group="home-47"]')).toHaveCount(1);
  expect(await page.evaluate(() => savedCard.isConnected && savedImage === savedCard.querySelector("img") && savedImage.src === savedSource && savedGroup === savedCard.closest("[data-prompt-group]"))).toBe(true);
  expect(await card(page, "home-0").boundingBox()).toEqual(imageBounds);
  await expect(selection).toHaveAttribute("aria-checked", "true");
  await expect(selection).toBeFocused();
  await selection.click();
  const favorite = card(page, "home-0").getByRole("button", { name: "Remove from Favorites" });
  await favorite.focus(); await favorite.click();
  await expect(card(page, "home-0")).not.toHaveClass(/is-favorited/);
  expect(await page.evaluate(() => savedImage === savedCard.querySelector("img") && savedImage.src === savedSource)).toBe(true);
  // Attribute-only updates must also keep selection and focused controls current.
  await expect(card(page, "home-0").getByRole("button", { name: "Add to Favorites" })).toBeFocused();
  await page.locator('[data-action="toggle-collection-favorite"]').first().evaluate((button) => button.click());
  expect(await page.evaluate(() => savedPreview === document.querySelector(".collection-preview-cell img"))).toBe(true);
  await page.locator("#gallery-viewport").evaluate((viewport) => { viewport.scrollTop = viewport.scrollHeight; });
  await expect(card(page, "home-47")).toHaveCount(1);
  expect(await page.evaluate(() => savedCard.isConnected && savedImage === savedCard.querySelector("img"))).toBe(true);
  await page.locator("#gallery-viewport").evaluate((viewport) => { viewport.scrollTop = 0; });
  await expect(card(page, "home-0").locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  expect(await page.evaluate(() => savedImage.src === savedSource)).toBe(true);
  expect(fixture.thumbnails.filter((path) => path === "/api/artifacts/home-0/thumbnail")).toHaveLength(1);
  await cleanFrames(page);
});

test("nested navigation, back/forward and scrolling reuse thumbnails without invalid frames", async ({ page }, testInfo) => {
  const fixture = await galleryFixture(page);
  await navigate(page, "a"); await navigate(page, "b");
  await page.goBack(); await expect(card(page, "a-0").locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  await page.goForward(); await expect(card(page, "b-0").locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  await navigate(page, "a");
  expect(fixture.thumbnails.filter((path) => path === "/api/artifacts/a-0/thumbnail")).toHaveLength(1);
  await page.screenshot({ path: testInfo.outputPath("stable-mixed-favorites.png") });
  await cleanFrames(page);
});

test("rapid A to B to A navigation cannot apply stale history or group responses", async ({ page }) => {
  const fixture = await galleryFixture(page, { historyDelay: { a: 450, b: 650 } });
  await page.evaluate(() => { location.hash = "#/c/a"; });
  await expect.poll(() => fixture.history.filter((folder) => folder === "a").length).toBe(1);
  await page.evaluate(() => { location.hash = "#/c/b"; });
  await expect.poll(() => fixture.history.includes("b")).toBe(true);
  await navigate(page, "a");
  await expect(page.locator('[data-prompt-group="a-47"]')).toHaveCount(1);
  await page.waitForTimeout(700);
  expect(await page.locator('[data-gallery-card="generation"]').evaluateAll((cards) => cards.every((item) => item.dataset.generationId.startsWith("a-")))).toBe(true);
  await cleanFrames(page);
});

for (const failure of ["http", "decode"]) test(`${failure} thumbnail failures remain quiet and retry successfully`, async ({ page }, testInfo) => {
  const fixture = await galleryFixture(page);
  fixture.fail = failure; fixture.thumbnailDelay = 250;
  await page.evaluate(() => { location.hash = "#/c/a"; });
  const first = card(page, "a-0");
  await expect(first.locator("img")).toHaveAttribute("data-thumbnail-state", "pending");
  await expect(first.locator("img")).toHaveCSS("opacity", "0");
  await page.screenshot({ path: testInfo.outputPath(`${failure}-pending.png`) });
  const retry = first.getByRole("button", { name: "Retry unavailable thumbnail" });
  await expect(retry).toBeVisible();
  await expect(first.locator("img")).toHaveCSS("opacity", "0");
  fixture.fail = null;
  await retry.click();
  await expect(first.locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  await expect(retry).toHaveCount(0);
  await cleanFrames(page);
});

test("collapse preserves card identity and logout releases all retained object URLs", async ({ page }) => {
  await galleryFixture(page);
  await expect(page.locator('[data-group-toggle="home-47"]')).toHaveCount(1);
  await page.evaluate(() => { window.saved = document.querySelector('[data-generation-id="home-0"] img'); window.source = saved.src; });
  const toggle = page.locator('[data-group-toggle="home-47"]');
  await toggle.click(); await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(card(page, "home-0").locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  expect(await page.evaluate(() => saved === document.querySelector('[data-generation-id="home-0"] img') && saved.src === source)).toBe(true);
  await page.locator('[data-action="logout"]').evaluate((button) => button.click());
  await expect(page.locator("#gallery")).toHaveCount(0);
  expect(await page.evaluate(() => galleryDiagnostics.revoked.includes(source))).toBe(true);
});


test("live status and progress updates retain imagery while a new artifact loads quietly", async ({ page }) => {
  const fixture = await galleryFixture(page);
  await expect(page.locator('[data-prompt-group="home-47"]')).toHaveCount(1);
  await page.evaluate(() => {
    window.liveCard = document.querySelector('[data-gallery-card="generation"][data-generation-id="home-0"]');
    window.liveImage = liveCard.querySelector("img");
    window.liveSource = liveImage.src;
  });
  fixture.updates.set("home-0", { status: "running", progress: { kind: "indeterminate", label: "Processing" } });
  await page.evaluate(() => galleryEvents.dispatchEvent(new MessageEvent("generation.stage", { data: JSON.stringify({ generation_id: "home-0" }) })));
  await expect(card(page, "home-0")).toHaveClass(/status-running/);
  await page.evaluate(() => galleryEvents.dispatchEvent(new MessageEvent("generation.progress", { data: JSON.stringify({ generation_id: "home-0", payload: { progress: { kind: "node", label: "Sampling", value: 2, maximum: 10, fraction: .2 } } }) })));
  await expect(card(page, "home-0").getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");
  expect(await page.evaluate(() => liveImage === liveCard.querySelector("img") && liveImage.src === liveSource)).toBe(true);
  fixture.thumbnailDelay = 400;
  fixture.updates.set("home-0", { status: "succeeded", display_artifact: { id: "replacement", kind: "image", thumbnail_url: "/api/artifacts/replacement/thumbnail" } });
  await page.evaluate(() => galleryEvents.dispatchEvent(new MessageEvent("generation.terminal", { data: JSON.stringify({ generation_id: "home-0" }) })));
  await expect(card(page, "home-0").locator("img")).toHaveAttribute("data-gallery-artifact-id", "replacement");
  await expect(card(page, "home-0").locator("img")).toHaveCSS("opacity", "0");
  await expect(card(page, "home-0").locator("img")).toHaveAttribute("data-thumbnail-state", "ready");
  expect(await page.evaluate(() => liveCard.isConnected && liveImage !== liveCard.querySelector("img"))).toBe(true);
  await cleanFrames(page);
});

test("all non-favorite cards remain neutral during loading and repeated navigation", async ({ page }, testInfo) => {
  await galleryFixture(page, { allNonFavorites: true, thumbnailDelay: 250 });
  await expect(page.locator("#gallery .is-favorited")).toHaveCount(0);
  await navigate(page, "a"); await navigate(page, "");
  await expect(page.locator("#gallery .is-favorited")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("stable-non-favorites.png") });
  await cleanFrames(page);
});

test("regrouping, identical renders and selection preserve nodes and focus", async ({ page }) => {
  await galleryFixture(page);
  const result = await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { reconcileGallery } = await import(new URL("./gallery-dom.mjs", base));
    const { galleryMarkup } = await import(new URL("./render.mjs", base));
    const root = document.createElement("div");
    document.body.append(root);
    const items = [3, 2, 1].map((index) => ({ id: `test-${index}`, status: "succeeded", prompt_fingerprint: "same", accepted_at: `2026-09-24T00:00:0${index}Z`, display_artifact: { id: `artifact-${index}`, kind: "image", thumbnail_url: `/image-${index}` } }));
    const options = { promptGroups: { metadata: new Map(), collapsed: new Set(), changes: new Map() } };
    reconcileGallery(root, galleryMarkup(items, options));
    const cards = [...root.querySelectorAll("[data-gallery-card]")];
    const images = cards.map((node) => node.querySelector("img"));
    cards[0].classList.add("is-selected", "card-controls-visible");
    const selected = cards[0].querySelector(".card-select-button");
    selected.setAttribute("aria-checked", "true"); selected.focus();
    const observer = new MutationObserver(() => {});
    observer.observe(root, { attributes: true, childList: true, subtree: true, characterData: true });
    reconcileGallery(root, galleryMarkup(items, options));
    const unchangedMutations = observer.takeRecords().length;
    const stayedFocused = document.activeElement === selected;
    options.promptGroups.metadata = new Map(items.map((item, index) => [item.id, { id: index === 1 ? "separate" : `canonical-${index}`, generation_count: 1 }]));
    reconcileGallery(root, galleryMarkup(items, options));
    const preserved = cards.every((node, index) => node.isConnected && node.querySelector("img") === images[index]);
    const retainedSelection = cards[0].classList.contains("is-selected") && selected.getAttribute("aria-checked") === "true";
    options.collections = [{ id: "previews", previews: [{ thumbnail_url: "/one" }, { thumbnail_url: "/two" }, { thumbnail_url: "/one" }] }];
    reconcileGallery(root, galleryMarkup(items, options));
    const previews = [...root.querySelectorAll(".collection-preview-cell img")];
    options.collections[0].previews = [{ thumbnail_url: "/two" }, { thumbnail_url: "/one" }, { thumbnail_url: "/one" }];
    reconcileGallery(root, galleryMarkup(items, options));
    const reordered = [...root.querySelectorAll(".collection-preview-cell img")];
    const preservedPreviews = reordered[0] === previews[1] && reordered[1] === previews[0] && reordered[2] === previews[2];
    observer.disconnect(); root.remove();
    return { unchangedMutations, stayedFocused, preserved, retainedSelection, preservedPreviews };
  });
  expect(result).toEqual({ unchangedMutations: 0, stayedFocused: true, preserved: true, retainedSelection: true, preservedPreviews: true });
});
