import { expect, test } from "@playwright/test";

const grades = ["A", "B", "C", "D", "F"];
const identity = index => `cp1_${index.toString(16).padStart(64, "0")}`;
const rankButton = (page, grade) => page.getByRole("button", { name: `Show rank ${grade}`, exact: true });
const cards = page => page.locator('[data-gallery-card="generation"]');

async function fixture(page, { count = 135, activity = false } = {}) {
  const data = { requests: [], operations: [], holdNext: null, failRankSave: false, preferences: {
    settings_initialized: true, settings: { gallery_layout: "classic" }, revision: 1, gallery_scale: 20,
    checkpoint_tiers: Object.fromEntries(grades.map((grade, index) => [grade, [identity(index)]])),
  } };
  data.items = Array.from({ length: count }, (_, index) => ({
    id: `g${index}`, collection_id: null, status: "succeeded", is_favorite: index % 2 === 0,
    image_count: 1, accepted_at: new Date(Date.UTC(2026, 8, 24, 0, 0, -index)).toISOString(),
    prompt_fingerprint: "same", checkpoint_id: identity(index % 5), checkpoint_label: `Checkpoint ${grades[index % 5]}`,
    expected_width: 1024, expected_height: 1024,
    display_artifact: { id: `a${index}`, kind: "image", width: 1024, height: 1024, thumbnail_url: `/api/artifacts/a${index}/thumbnail`, content_url: `/api/artifacts/a${index}/content` },
  }));
  const folders = [{ id: "folder", parent_id: null, name: "Portraits", generation_count: 1, previews: [], previews_enabled: true }];
  const filter = scope => data.items.filter(item => {
    const grade = grades.find(grade => data.preferences.checkpoint_tiers[grade]?.includes(item.checkpoint_id)) || "C";
    return item.collection_id === (scope.collection_id || null)
      && !(scope.excluded_checkpoint_ranks || []).includes(grade)
      && (!scope.favorites_only || item.is_favorite) && (!scope.unfavorited_only || !item.is_favorite);
  });
  await page.addInitScript(() => { window.EventSource = class extends EventTarget { constructor() { super(); window.rankFilterEvents = this; } close() {} }; });
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    const scope = { collection_id: url.searchParams.get("collection_id"), favorites_only: url.searchParams.get("favorites_only") === "true", unfavorited_only: url.searchParams.get("unfavorited_only") === "true", excluded_checkpoint_ranks: url.searchParams.getAll("excluded_checkpoint_ranks") };
    let result = {};
    if (path.startsWith("/api/artifacts/")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"><rect width="1024" height="1024" fill="#21435b"/></svg>' });
    if (path === "/api/auth/session") result = { authenticated: true, app_title: "Home Image Studio", csrf_token: "fixture", user: { id: "rank-filter", username: "gallery", role: "user", must_change_password: false } };
    else if (path === "/api/collections") result = folders;
    else if (path === "/api/generations") {
      data.requests.push(scope);
      const items = filter(scope), offset = Number(url.searchParams.get("cursor") || 0);
      result = { items: items.slice(offset, offset + 24), next_cursor: offset + 24 < items.length ? String(offset + 24) : null };
      if (data.holdNext) { const hold = data.holdNext; data.holdNext = null; await hold; }
    } else if (path === "/api/gallery/items") result = {
      generations: filter(scope), collection_ids: scope.favorites_only || scope.unfavorited_only || scope.excluded_checkpoint_ranks.length || scope.collection_id ? [] : ["folder"],
    };
    else if (path.endsWith("/lookup")) {
      const body = request.postDataJSON(), items = filter(body);
      result = body.generation_ids.filter(id => items.some(item => item.id === id)).map(id => ({ generation_id: id, group: { id: "g0", generation_count: items.length, previous_generation_id: null, after_cursor: String(count) } }));
    } else if (path.endsWith("/members")) {
      let items = filter(scope);
      if (url.searchParams.get("cursor")) {
        const cursor = JSON.parse(Buffer.from(url.searchParams.get("cursor"), "base64url"));
        items = items.filter(item => item.accepted_at < cursor.accepted_at);
      }
      result = { items: url.searchParams.get("selection") === "true" ? items : items.slice(0, 60), next_cursor: null };
    } else if (path === "/api/gallery/favorite") {
      const body = request.postDataJSON(); data.operations.push(body);
      data.items.forEach(item => { if (body.generation_ids.includes(item.id)) item.is_favorite = true; });
      result = body;
    } else if (/^\/api\/generations\/[^/]+$/.test(path)) result = data.items.find(item => item.id === path.split("/").at(-1));
    else if (path === "/api/preferences") {
      if (request.method() === "PUT" && data.failRankSave) { data.failRankSave = false; return route.fulfill({ status: 503, json: { error: { message: "Could not save rank" } } }); }
      if (request.method() === "PUT") data.preferences = { ...data.preferences, ...request.postDataJSON(), revision: data.preferences.revision + 1 };
      result = data.preferences;
    } else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/generation-activity") result = activity ? {
      remaining_count: 4, running_count: 1, snapshot_at: new Date().toISOString(), current_eta: { completion_at: new Date(Date.now() + 84000).toISOString() }, queue_eta: { completion_at: new Date(Date.now() + 492000).toISOString() },
    } : { remaining_count: 0, collections: [], run: null };
    else if (["/api/services", "/api/workflows", "/api/prompt-generations", "/api/generation-preparations"].includes(path)) result = [];
    else if (path === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  await expect(page.locator('.classic-gallery-count')).toHaveText(`${count} generations · 1 folder`);
  await expect(cards(page).first()).toBeVisible();
  return data;
}

async function onlyAB(page) {
  for (const grade of ["C", "D", "F"]) await rankButton(page, grade).click();
  await expect(page.locator('.classic-gallery-count')).toHaveText("54 generations");
}

test("rank toggles intersect Favorites and whole-view selections include only matching unloaded images", async ({ page }) => {
  const data = await fixture(page);
  await onlyAB(page);
  await expect(page.locator('[data-gallery-card="collection"]')).toHaveCount(0);
  for (const grade of ["C", "D", "F"]) await expect(page.locator(`#gallery .checkpoint-tier-${grade}`)).toHaveCount(0);
  const favorites = page.getByRole("button", { name: "Favorites", exact: true });
  await favorites.click();
  await expect(page.locator('.classic-gallery-count')).toHaveText("27 generations");
  await expect(page.locator('[data-gallery-card="generation"]:not(.is-favorited)')).toHaveCount(0);
  await favorites.click();
  await expect(favorites).toHaveAttribute("aria-pressed", "mixed");
  await expect(page.locator('.classic-gallery-count')).toHaveText("27 generations");
  await page.getByRole("checkbox", { name: "Select all items in this view" }).click();
  await expect(page.locator('#gallery-selection-toolbar')).toContainText("27 selected");
  await page.getByRole("button", { name: "Add to Favorites", exact: true }).click();
  await expect.poll(() => data.operations.length).toBe(1);
  expect(data.operations[0].generation_ids).toHaveLength(27);
  expect(data.operations[0].scope).toEqual({ collection_id: null, favorites_only: false, unfavorited_only: true, excluded_checkpoint_ranks: ["C", "D", "F"] });
  expect(data.operations[0].generation_ids.every(id => [0, 1].includes(Number(id.slice(1)) % 5))).toBe(true);
  await expect(page.getByRole("heading", { name: "No images match these filters" })).toBeVisible();
});

test("all-off recovery preserves Favorites; filters survive layout and folder changes but reset on reload", async ({ page }) => {
  await fixture(page);
  const favorites = page.getByRole("button", { name: "Favorites", exact: true });
  await favorites.click();
  for (const grade of grades) await rankButton(page, grade).click();
  await expect(page.getByRole("heading", { name: "All model ranks are hidden" })).toBeVisible();
  await page.getByRole("button", { name: "Show all ranks", exact: true }).click();
  await expect(favorites).toHaveAttribute("aria-pressed", "true");
  for (const grade of grades) await expect(rankButton(page, grade)).toHaveAttribute("aria-pressed", "true");
  await rankButton(page, "C").focus(); await page.keyboard.press("Space");
  await expect(rankButton(page, "C")).toBeFocused();
  await page.getByRole("button", { name: "Grouped", exact: true }).click();
  await expect(rankButton(page, "C")).toHaveAttribute("aria-pressed", "false");
  await page.evaluate(() => { window.location.hash = "#/c/folder"; });
  await expect(page).toHaveURL(/#\/c\/folder$/);
  await expect(page.locator("#collection-bar")).toContainText("Portraits");
  await expect(rankButton(page, "C")).toHaveAttribute("aria-pressed", "false");
  await page.reload();
  for (const grade of grades) await expect(rankButton(page, grade)).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Favorites", exact: true })).toHaveAttribute("aria-pressed", "false");
});

test("group counts and selection use matching members and filter changes clear selections", async ({ page }) => {
  await fixture(page);
  await onlyAB(page);
  await page.getByRole("button", { name: "Grouped", exact: true }).click();
  const select = page.getByRole("checkbox", { name: "Select group of 54 generations" });
  await expect(select).toBeEnabled();
  await select.click();
  await expect(page.locator('#gallery-selection-toolbar')).toContainText("54 selected");
  await expect(select).toHaveAttribute("aria-checked", "true");
  await rankButton(page, "B").click();
  await expect(page.locator('#gallery-selection-toolbar')).toBeHidden();
  await expect(page.getByRole("checkbox", { name: "Select group of 27 generations" })).toBeEnabled();
});

test("late filtered requests are discarded and remote rank changes refresh matching images", async ({ page }) => {
  const data = await fixture(page);
  let release;
  data.holdNext = new Promise(resolve => { release = resolve; });
  const before = data.requests.length;
  await rankButton(page, "C").click();
  await expect.poll(() => data.requests.length).toBeGreaterThan(before);
  await rankButton(page, "D").click();
  await expect(page.locator('.classic-gallery-count')).toHaveText("81 generations");
  release();
  await expect(page.locator('#gallery .checkpoint-tier-C, #gallery .checkpoint-tier-D')).toHaveCount(0);
  data.preferences.checkpoint_tiers = { ...data.preferences.checkpoint_tiers, A: [identity(0), identity(2)], C: [] };
  data.preferences.revision++;
  await page.evaluate(() => window.rankFilterEvents.dispatchEvent(new MessageEvent("preferences.updated", { data: "{}" })));
  await expect(page.locator('.classic-gallery-count')).toHaveText("108 generations");
  await expect(page.locator('[data-gallery-card="generation"][data-generation-id="g2"]')).toBeVisible();
});

test("viewer navigation skips excluded ranks and a local rank edit removes matching gallery cards", async ({ page }) => {
  const data = await fixture(page);
  await onlyAB(page);
  await page.locator('[data-action="open-photo"][data-generation-id="g0"]').evaluate(button => button.click());
  const viewer = page.locator('#photo-viewer');
  await expect(viewer).toBeVisible();
  await viewer.locator('[data-action="navigate-photo"][data-direction="older"]').click();
  await expect(viewer.locator('.photo-viewer-frame')).toHaveAttribute("data-photo-generation-id", "g1");
  await viewer.locator('[data-action="navigate-photo"][data-direction="older"]').click();
  await expect(viewer.locator('.photo-viewer-frame')).toHaveAttribute("data-photo-generation-id", "g5");
  await viewer.locator('[data-rank-step="1"]').click();
  await expect(viewer.locator('.photo-viewer-checkpoint > .checkpoint-rank')).toHaveText("B");
  data.failRankSave = true;
  await viewer.locator('[data-rank-step="1"]').click();
  await expect(viewer.locator(".checkpoint-rank-feedback")).toContainText("wasn’t saved. Still B.");
  await expect(page.locator(".classic-gallery-count")).toHaveText("54 generations");
  await viewer.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(viewer.locator('.photo-viewer-checkpoint > .checkpoint-rank')).toHaveText("C");
  await expect(page.locator('.classic-gallery-count')).toHaveText("27 generations");
  await viewer.locator('[data-action="navigate-photo"][data-direction="older"]').click();
  await expect(viewer.locator('.photo-viewer-frame')).toHaveAttribute("data-photo-generation-id", "g6");
});

test("filters fit desktop and narrow headers with activity and selection", async ({ page }, testInfo) => {
  await fixture(page, { activity: true });
  for (const width of [1800, 1600, 1440, 1280, 1201, 1024, 1000, 850, 801, 800, 600, 390, 320]) {
    await page.setViewportSize({ width, height: 950 });
    for (const selecting of [false, true]) {
      if (selecting) await page.getByRole("checkbox", { name: "Select all items in this view" }).click();
      await expect.poll(async () => page.evaluate(() => {
        const topbar = document.querySelector('.topbar').getBoundingClientRect();
        const gallery = document.querySelector('#gallery-viewport').getBoundingClientRect();
        const controls = [...document.querySelectorAll('.topbar button, .topbar input, .topbar summary')].filter(element => element.getClientRects().length && !element.closest('.menu-popover')).map(element => element.getBoundingClientRect());
        const fits = controls.every(rect => rect.left >= 0 && rect.right <= innerWidth + 1 && rect.bottom <= topbar.bottom + 1);
        const overlap = controls.some((a, i) => controls.slice(i + 1).some(b => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1));
        return fits && !overlap && gallery.top >= topbar.bottom - 1 && document.documentElement.scrollWidth <= innerWidth;
      }), { message: `Header geometry at ${width}px, selection ${selecting}` }).toBe(true);
      for (const grade of grades) {
        await expect(rankButton(page, grade)).toBeVisible();
        const box = await rankButton(page, grade).boundingBox();
        if (width <= 800) { expect(box.width).toBeGreaterThanOrEqual(44); expect(box.height).toBeGreaterThanOrEqual(44); }
      }
      if (selecting) await page.keyboard.press("Escape");
    }
    if (width === 320) {
      await page.getByRole("button", { name: "Open generation controls", exact: true }).click();
      const header = await page.locator(".topbar").boundingBox();
      await expect.poll(async () => (await page.locator(".control-panel").boundingBox()).y).toBeGreaterThanOrEqual(header.y + header.height);
      await expect.poll(async () => (await page.locator(".panel-scrim").boundingBox()).y).toBeGreaterThanOrEqual(header.y + header.height);
      await page.getByRole("button", { name: "Open generation controls", exact: true }).click();
    }
    if ([1440, 390].includes(width)) await page.screenshot({ path: testInfo.outputPath(`rank-filters-${width}.png`) });
  }
});
