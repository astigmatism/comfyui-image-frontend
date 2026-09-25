import { expect, test } from "@playwright/test";

async function fixture(page) {
  const now = Date.now();
  const eta = (seconds) => ({ remaining_seconds: seconds, completion_at: new Date(now + seconds * 1000).toISOString(), basis: "batch", confidence: "medium", sample_count: 3 });
  const data = { activity: { remaining_count: 4, running_count: 1, snapshot_at: new Date(now).toISOString(), current_eta: eta(84), queue_eta: eta(492) } };
  const settings = { gallery_layout: "classic", prompt_generation: { enabled: false, active_source: null, sources: {} }, active_source: null, sources: {}, model_selections: {}, quantity: 1, control_sections: {}, recent_resolutions: {}, creative_direction: "", assistant_mode: "refine", assistant_think: true, assistant_instructions: {}, use_creative_direction: false, max_generations: 200 };
  const generation = { id: "photo", status: "succeeded", image_count: 1, accepted_at: new Date(now).toISOString(), expected_width: 512, expected_height: 512, display_artifact: { id: "image", kind: "image", thumbnail_url: "/api/artifacts/image/thumbnail", content_url: "/api/artifacts/image/content" } };
  await page.addInitScript(() => {
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource { constructor(...args) { super(...args); window.countdownEvents = this; } };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let result = {};
    if (path.startsWith("/api/artifacts/")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" fill="#25384d"/></svg>' });
    if (path === "/api/auth/session") result = { authenticated: true, app_title: "ImageGen", csrf_token: "fixture", user: { id: "countdowns", username: "countdowns", role: "user", must_change_password: false } };
    else if (path === "/api/preferences") result = { settings_initialized: true, settings, revision: 1, gallery_scale: 30, checkpoint_tiers: {} };
    else if (path === "/api/generations") result = { items: [generation], next_cursor: null };
    else if (path === "/api/generations/photo") result = generation;
    else if (path === "/api/gallery/items") result = { generations: [generation], collection_ids: [] };
    else if (path === "/api/generation-activity") result = data.activity;
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
    else if (path === "/api/events") return route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" });
    else if (["/api/collections", "/api/services", "/api/workflows", "/api/prompt-generations", "/api/generation-preparations"].includes(path)) result = [];
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  data.refresh = async () => page.evaluate(() => window.countdownEvents.dispatchEvent(new MessageEvent("generation.terminal", { data: JSON.stringify({ generation_id: "done", payload: { status: "succeeded" } }) })));
  return data;
}

test("approved pair ages in place, mirrors viewer and title, and disappears immediately when idle", async ({ page }, testInfo) => {
  const data = await fixture(page);
  const badge = page.locator("#generation-activity-host .activity-pair");
  await expect(badge).toContainText("Current");
  await expect(badge.locator("[data-activity-current]")).toHaveText(/~1:2[0-4]/);
  await expect(page).toHaveTitle(/1:2\d now · 8:\d\d all · ImageGen/);
  await badge.focus();
  await page.evaluate(() => { window.originalCountdownBadge = document.activeElement; });
  const before = await badge.locator("[data-activity-current]").textContent();
  await expect.poll(() => badge.locator("[data-activity-current]").textContent()).not.toBe(before);
  expect(await page.evaluate(() => document.activeElement === window.originalCountdownBadge && window.originalCountdownBadge.isConnected)).toBe(true);
  await expect(badge.locator(".activity-tooltip")).toContainText("3 samples");
  await page.screenshot({ path: testInfo.outputPath("imagegen-countdown-desktop.png") });
  await page.locator('[data-generation-id="photo"] .card-media').click();
  const viewer = page.locator("#photo-viewer .photo-viewer-activity-host .activity-pair");
  await expect(viewer).toBeVisible();
  // Read both clocks in the same browser turn; a captured string can become
  // stale while the viewer opens or a fresh activity snapshot arrives.
  await expect.poll(() => page.evaluate(() => {
    const current = document.querySelector("#generation-activity-host [data-activity-current]")?.textContent;
    const viewer = document.querySelector("#photo-viewer [data-activity-current]")?.textContent;
    return /^~\d/.test(current) && current === viewer;
  })).toBe(true);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(badge).toBeInViewport();
  await expect(page.locator(".account-menu")).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("imagegen-countdown-mobile.png") });
  data.activity = { remaining_count: 0, running_count: 0, snapshot_at: new Date().toISOString() };
  await data.refresh();
  await expect(badge).toHaveCount(0);
  await expect(page).toHaveTitle("ImageGen");
});

test("waiting and overdue never show a misleading numeric total", async ({ page }) => {
  const data = await fixture(page);
  data.activity = { ...data.activity, running_count: 0, current_eta: null, queue_eta: null };
  await data.refresh();
  await expect(page).toHaveTitle("Waiting now · Estimating… all · ImageGen");
  data.activity = { ...data.activity, running_count: 1, current_eta: { completion_at: new Date(Date.now() - 1000).toISOString() }, snapshot_at: new Date().toISOString() };
  await data.refresh();
  await expect(page).toHaveTitle("Overdue now · Estimating… all · ImageGen");
});
