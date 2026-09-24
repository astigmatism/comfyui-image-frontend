import { expect, test } from "@playwright/test";

async function fixture(page, { count = 525, activity = false } = {}) {
  const data = { arrivals: [], requests: [], operations: [], failInventory: false };
  const settings = { gallery_layout: "grouped", prompt_generation: { enabled: false, active_source: null, sources: {} }, active_source: null, runtime_id: null, sources: {}, model_selections: {}, quantity: 1, control_sections: {}, recent_resolutions: {}, creative_direction: "", assistant_mode: "refine", assistant_think: true, assistant_instructions: {}, use_creative_direction: false, max_generations: 200 };
  let preferences = { settings_initialized: true, settings, revision: 1, gallery_scale: 20, checkpoint_tiers: {} };
  const folders = [{ id: "folder", parent_id: null, name: "Landscapes", generation_count: 3, previews: [], is_favorite: true }];
  const generation = (index) => ({
    id: `g${index}`, collection_id: null, status: "succeeded", is_favorite: index % 3 === 0,
    image_count: 1, accepted_at: new Date(Date.UTC(2026, 8, 24, 0, 0, -index)).toISOString(),
    prompt_fingerprint: index < 100 ? "A" : "B", expected_width: index % 2 ? 512 : 1024, expected_height: index % 2 ? 1024 : 512,
    display_artifact: { id: `a${index}`, kind: "image", thumbnail_url: `/api/artifacts/a${index}/thumbnail`, content_url: `/api/artifacts/a${index}/content` },
  });
  data.items = Array.from({ length: count }, (_, index) => generation(index));
  await page.addInitScript(() => {
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(...args) { super(...args); window.galleryEvents = this; }
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    let result = {};
    if (path.startsWith("/api/artifacts/")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" fill="#25384d"/><path d="M0 400L180 100L360 400Z" fill="#66818f"/></svg>' });
    if (path === "/api/auth/session") result = { authenticated: true, app_title: "ImageGen V2", csrf_token: "fixture", user: { id: "gallery-view", username: "gallery", role: "user", must_change_password: false } };
    else if (path === "/api/collections") result = folders;
    else if (path === "/api/generations") {
      const offset = Number(url.searchParams.get("cursor") || 0);
      data.requests.push(offset);
      result = { items: [...(!offset ? data.arrivals : []), ...data.items.slice(offset, offset + 24)], next_cursor: offset + 24 < count ? String(offset + 24) : null };
    } else if (path === "/api/gallery/items") {
      if (data.failInventory) return route.fulfill({ status: 503, json: { error: { message: "Unavailable" } } });
      const favorites = url.searchParams.get("favorites_only") === "true";
      result = { generations: [...data.arrivals, ...data.items].filter((item) => !favorites || item.is_favorite), collection_ids: folders.filter((item) => !favorites || item.is_favorite).map((item) => item.id) };
    } else if (path.endsWith("/lookup")) {
      result = request.postDataJSON().generation_ids.map((id) => ({ generation_id: id, group: { id: Number(id.slice(1)) < 100 ? "g0" : "g100", generation_count: Number(id.slice(1)) < 100 ? Math.min(count, 100) : count - 100, previous_generation_id: null, after_cursor: Number(id.slice(1)) < 100 ? "100" : String(count) } }));
    } else if (/^\/api\/generations\/[^/]+$/.test(path)) result = [...data.arrivals, ...data.items].find((item) => item.id === path.split("/").at(-1));
    else if (path === "/api/gallery/favorite") {
      const body = request.postDataJSON(); data.operations.push(body);
      for (const item of data.items) if (body.generation_ids.includes(item.id)) item.is_favorite = true;
      result = body;
    } else if (path === "/api/preferences") {
      if (request.method() === "PUT") preferences = { ...preferences, ...request.postDataJSON(), revision: preferences.revision + 1 };
      result = preferences;
    } else if (path === "/api/events") return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/generation-activity") result = { remaining_count: activity ? 2 : 0, collections: [], run: null };
    else if (["/api/services", "/api/workflows", "/api/prompt-generations", "/api/generation-preparations"].includes(path)) result = [];
    else if (path === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  await expect(page.locator('[data-generation-id="g0"] img')).toHaveAttribute("data-thumbnail-state", "ready");
  await expect(page.getByRole("button", { name: "Grouped", exact: true })).toHaveAttribute("aria-pressed", "true");
  data.saved = () => preferences;
  data.arrive = async () => {
    const item = { ...generation(-1), id: "arrival" };
    data.arrivals.push(item);
    await page.evaluate((generation_id) => window.galleryEvents.dispatchEvent(new MessageEvent("generation.queued", { data: JSON.stringify({ generation_id, status: "succeeded" }) })), item.id);
  };
  return data;
}

const all = (page) => page.getByRole("checkbox", { name: "Select all items in this view" });
const selectedCount = (page) => page.locator("#gallery-selection-toolbar .selection-count");

test("classic selection spans unloaded cards, supports exclusions, and persists layout and scale", async ({ page }) => {
  const data = await fixture(page);
  await page.evaluate(() => { window.originalCard = document.querySelector('[data-gallery-card="generation"]'); window.originalImage = window.originalCard.querySelector("img"); });
  await page.getByRole("button", { name: "Classic", exact: true }).click();
  await expect(page.locator(".prompt-group")).toHaveCount(0);
  await expect(page.locator(".classic-gallery-count")).toHaveText("525 generations · 1 folder");
  expect(await page.evaluate(() => window.originalCard.isConnected && window.originalImage.isConnected)).toBe(true);
  await all(page).focus(); await page.keyboard.press("Space");
  await expect(selectedCount(page)).toHaveText("526 selected");
  await page.locator('[data-generation-id="g0"] .card-select-button').click();
  await expect(all(page)).toHaveAttribute("aria-checked", "mixed");
  await page.getByRole("button", { name: "Add to Favorites", exact: true }).click();
  await expect.poll(() => data.operations.length).toBe(1);
  expect(data.operations[0].generation_ids).toHaveLength(524);
  expect(data.operations[0].generation_ids).not.toContain("g0");
  expect(data.operations[0].collection_ids).toEqual(["folder"]);
  expect(data.operations[0].scope).toEqual({ collection_id: null, favorites_only: false });
  await page.getByRole("button", { name: "Grouped", exact: true }).click();
  await expect(selectedCount(page)).toHaveText("525 selected");
  await page.getByRole("button", { name: "Classic", exact: true }).click();
  await expect(selectedCount(page)).toHaveText("525 selected");
  await page.getByRole("slider", { name: "Gallery scale" }).fill("35");
  await expect.poll(() => data.saved().settings.gallery_layout).toBe("classic");
  await expect.poll(() => data.saved().gallery_scale).toBe(35);
  await page.reload();
  await expect(page.getByRole("button", { name: "Classic", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("slider", { name: "Gallery scale" })).toHaveValue("35");
  await expect(page.locator(".classic-gallery-header")).toBeVisible();
});

test("new arrivals remain unselected; Favorites selects only matching unloaded items", async ({ page }) => {
  const data = await fixture(page);
  await page.getByRole("button", { name: "Classic", exact: true }).click();
  await all(page).click();
  await expect(selectedCount(page)).toHaveText("526 selected");
  await data.arrive();
  await expect(page.locator('[data-gallery-card="generation"][data-generation-id="arrival"]')).toBeVisible();
  await expect(all(page)).toHaveAttribute("aria-checked", "mixed");
  await expect(selectedCount(page)).toHaveText("526 selected");
  await expect(page.locator('[data-generation-id="arrival"] .card-select-button')).toHaveAttribute("aria-checked", "false");
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await expect(page.locator("#gallery-selection-toolbar")).toBeHidden();
  await expect(page.locator(".classic-gallery-count")).toHaveText("175 generations · 1 folder");
  await all(page).click();
  await expect(selectedCount(page)).toHaveText("176 selected");
  await page.keyboard.press("Escape");
  await expect(all(page)).toHaveAttribute("aria-checked", "false");
  await all(page).focus();
  await page.keyboard.press("Control+a");
  await expect(selectedCount(page)).toHaveText("176 selected");
});

test("classic resumes pagination through a previously skipped collapsed group", async ({ page }) => {
  const data = await fixture(page);
  await page.locator('[data-group-toggle="g0"]').click();
  await expect.poll(() => data.requests.includes(100)).toBe(true);
  await page.getByRole("button", { name: "Classic", exact: true }).click();
  await page.locator("#gallery-sentinel").scrollIntoViewIfNeeded();
  await expect.poll(() => data.requests.includes(24)).toBe(true);
  await expect(page.locator('[data-gallery-card="generation"][data-generation-id="g24"]')).toHaveCount(1);
  await all(page).click();
  await expect(selectedCount(page)).toHaveText("526 selected");
});

test("failed inventory preserves selection and can be retried", async ({ page }) => {
  const data = await fixture(page, { count: 12 });
  await page.getByRole("button", { name: "Classic", exact: true }).click();
  await all(page).click();
  await page.locator('[data-generation-id="g0"] .card-select-button').click();
  await expect(selectedCount(page)).toHaveText("12 selected");
  data.failInventory = true;
  await all(page).click();
  await expect(page.locator(".classic-gallery-count")).toHaveText("Count unavailable");
  await expect(selectedCount(page)).toHaveText("12 selected");
  data.failInventory = false;
  await all(page).click();
  await expect(selectedCount(page)).toHaveText("13 selected");
});

test("toolbar and selection actions fit desktop and narrow screens", async ({ page }, testInfo) => {
  await fixture(page, { count: 12, activity: true });
  await page.getByRole("button", { name: "Classic", exact: true }).click();
  for (const width of [1440, 1280, 1024, 900, 800, 600, 480, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const selecting of [false, true]) {
      if (selecting) await all(page).click();
      const bounds = await page.locator('.topbar button:visible, .topbar input:visible, .topbar summary:visible, .classic-gallery-header button').evaluateAll((nodes) => nodes.map((node) => {
        const rect = node.getBoundingClientRect(); return { label: node.getAttribute("aria-label") || node.textContent, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      }));
      const galleryTop = (await page.locator("#gallery-viewport").boundingBox()).y;
      for (const rect of bounds) {
        expect(rect.left, `${width}: ${rect.label}`).toBeGreaterThanOrEqual(0);
        expect(rect.right, `${width}: ${rect.label}`).toBeLessThanOrEqual(width);
        if (rect.label !== "Select all items in this view" || rect.top < galleryTop) expect(rect.bottom, `${width}: ${rect.label}`).toBeLessThanOrEqual(galleryTop);
      }
      for (let i = 0; i < bounds.length; i++) for (let j = i + 1; j < bounds.length; j++) {
        const a = bounds[i], b = bounds[j];
        const overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
        expect(overlap, `${width}: ${a.label} overlaps ${b.label}`).toBe(false);
      }
      if (width === 390 && selecting) await page.screenshot({ path: testInfo.outputPath("classic-gallery-mobile.png") });
      if (selecting) await page.getByRole("button", { name: "Clear selection", exact: true }).click();
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: testInfo.outputPath("classic-gallery-desktop.png") });
});
