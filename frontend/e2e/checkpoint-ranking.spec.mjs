import { expect, test } from "@playwright/test";

const identity = index => "cp1_" + index.toString(16).padStart(64, "0");
const image = '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1200"><rect width="1600" height="1200" fill="#21435b"/></svg>';
const revision = { publication_id: "fixture", workflow_sha256: "a".repeat(64), api_sha256: "b".repeat(64), manifest_sha256: "c".repeat(64) };
const modelName = "Moody Krea 2 V5 BF16";
const checkpointValue = (workflow, index) => `${workflow}-choice-${index}`;

async function fixture(page, { activity = false } = {}) {
  const choices = workflow => Array.from({ length: workflow === "first" ? 30 : 12 }, (_, i) => ({
    value: checkpointValue(workflow, i), checkpoint_id: identity(i),
    label: i < 2 ? modelName : `Checkpoint ${i + 1}`,
  }));
  const sources = ["first", "second"].map(key => ({
    source_key: key, id: key, display_name: `Workflow ${key}`, available: true, readiness: "ready", revision,
    interface: { inputs: [
      { id: "prompt", type: "string", label: "Prompt", semantic_role: "positive_prompt", default: "A quiet landscape" },
      { id: "checkpoint", type: "choice", label: "Checkpoint", semantic_role: "model", default: checkpointValue(key, 0), choices: choices(key) },
    ], outputs: [] },
    model_selectors: [{ parameter_id: "checkpoint", label: "Checkpoint", default: checkpointValue(key, 0), choices: choices(key) }],
  }));
  const control = { fail: false, hold: null, writes: [],
    preferences: { settings_initialized: true, settings: { active_source: "first" }, revision: 1, gallery_scale: 45, checkpoint_tiers: {} },
  };
  const generations = Array.from({ length: 3 }, (_, index) => ({
    id: `rank-photo-${index}`, collection_id: null, status: "succeeded", prompt_fingerprint: `prompt-${index}`,
    accepted_at: new Date(Date.UTC(2026, 8, 24 - index)).toISOString(),
    workflow_display_name: `Workflow ${index ? "second" : "first"}`, checkpoint_label: modelName,
    checkpoint_id: identity(index === 2 ? 1 : 0), expected_width: 1600, expected_height: 1200, image_count: 1,
    display_artifact: { id: `rank-image-${index}`, kind: "image", width: 1600, height: 1200,
      content_url: `/api/artifacts/rank-image-${index}/content`, thumbnail_url: `/api/artifacts/rank-image-${index}/thumbnail` },
  }));
  await page.addInitScript(() => {
    window.EventSource = class extends EventTarget { constructor() { super(); window.fixtureEvents = this; } close() {} };
  });
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("/content") || path.endsWith("/thumbnail")) return route.fulfill({ contentType: "image/svg+xml", body: image });
    let result = {};
    if (path === "/api/auth/session") result = { authenticated: true, csrf_token: "fixture", user: { id: "rank-fixture", username: "fixture", role: "user", must_change_password: false } };
    else if (path === "/api/preferences") {
      if (request.method() === "PUT") {
        const body = request.postDataJSON(); control.writes.push(body);
        if (control.hold) await control.hold;
        if (control.fail) { control.fail = false; return route.fulfill({ status: 503, json: { error: { message: "Rank save unavailable" } } }); }
        if (body.expected_revision !== control.preferences.revision) return route.fulfill({ status: 409, json: { error: { message: "Settings changed" } } });
        control.preferences = { ...control.preferences, ...body, revision: control.preferences.revision + 1 };
      }
      result = control.preferences;
    } else if (path === "/api/workflows") result = sources;
    else if (path.startsWith("/api/workflows/")) result = sources.find(source => source.source_key === path.split("/").at(-1));
    else if (path === "/api/generations") result = { items: generations, next_cursor: null };
    else if (path.startsWith("/api/generations/")) result = generations.find(item => item.id === path.split("/").at(-1)) || {};
    else if (path === "/api/auto-generation") result = { enabled: false, status: "disabled", revision: 1 };
    else if (path === "/api/generation-activity") result = activity
      ? { remaining_count: 4, running_count: 1, snapshot_at: new Date().toISOString(), current_eta: { completion_at: new Date(Date.now() + 84000).toISOString() }, queue_eta: { completion_at: new Date(Date.now() + 492000).toISOString() } }
      : { remaining_count: 0, collections: [], run: null };
    else if (path === "/api/comfyui-instances") result = { instances: [], default_instance_id: null };
    else if (["/api/services", "/api/collections", "/api/prompt-generations", "/api/generation-preparations"].includes(path) || path.endsWith("/lookup")) result = [];
    return route.fulfill({ json: result });
  });
  await page.goto("/");
  await expect(page.locator("#workflow-source")).toBeEnabled();
  return control;
}

const viewer = page => page.locator("#photo-viewer");
const card = (page, index) => page.locator(`[data-gallery-card][data-generation-id="rank-photo-${index}"]`);
const pickerCard = (dialog, workflow, index) => dialog.locator(`[data-checkpoint-card][data-checkpoint-value="${checkpointValue(workflow, index)}"]`);
async function openPhoto(page) {
  await page.locator('[data-action="open-photo"][data-generation-id="rank-photo-0"]').evaluate(button => button.click());
  await expect(viewer(page).locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", "rank-photo-0");
  await expect.poll(() => viewer(page).locator(".photo-viewer-media img").evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
}

test("30 checkpoints start in C; picker cancel, drag, ordering and shared workflow ranks", async ({ page }) => {
  const state = await fixture(page);
  await page.locator("#workflow-source").click();
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog.locator(".checkpoint-tier-C [data-checkpoint-card]")).toHaveCount(30);
  await pickerCard(dialog, "first", 0).locator("[data-checkpoint-drag-handle]").dragTo(dialog.locator(".checkpoint-tier-A .checkpoint-tier-grid"));
  await expect(dialog.locator(".checkpoint-tier-A [data-checkpoint-card]")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(card(page, 0).locator(".checkpoint-rank")).toHaveText("C");
  expect(state.preferences.checkpoint_tiers.A || []).toHaveLength(0);

  await page.locator("#workflow-source").click();
  const handle = pickerCard(dialog, "first", 0).locator("[data-checkpoint-drag-handle]");
  await handle.focus(); await handle.press("Alt+ArrowUp");
  await expect(dialog.locator(".checkpoint-tier-B [data-checkpoint-card]")).toHaveCount(1);
  await pickerCard(dialog, "first", 2).locator("[data-checkpoint-drag-handle]").dragTo(pickerCard(dialog, "first", 0));
  await expect(dialog.locator(".checkpoint-tier-B [data-checkpoint-card]")).toHaveCount(2);
  let release; state.hold = new Promise(resolve => { release = resolve; });
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  state.hold = null; release();
  await expect(dialog).not.toBeVisible();
  await expect.poll(() => state.preferences.checkpoint_tiers.B).toEqual([identity(2), identity(0)]);
  await expect(card(page, 0).locator(".checkpoint-rank")).toHaveText("B");
  await expect(card(page, 1).locator(".checkpoint-rank")).toHaveText("B");
  await expect(card(page, 2).locator(".checkpoint-rank")).toHaveText("C");
  await page.locator("#workflow-source").click();
  await dialog.locator("[data-source-workflow-choice]").selectOption("second");
  await expect(dialog.locator(".checkpoint-tier-B [data-checkpoint-card]")).toHaveCount(2);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.reload();
  await expect(card(page, 0).locator(".checkpoint-rank")).toHaveText("B");
});

test("fullscreen inline arrows save immediately, handle failure, respect limits and preserve the photo", async ({ page }, testInfo) => {
  const state = await fixture(page); await openPhoto(page);
  const badge = viewer(page).locator(".photo-viewer-checkpoint");
  const rank = badge.locator(".checkpoint-rank");
  const up = badge.locator('[data-rank-step="-1"]'), down = badge.locator('[data-rank-step="1"]');
  await viewer(page).getByRole("button", { name: "Fit", exact: true }).click();
  await page.evaluate(() => { window.savedPhoto = document.querySelector(".photo-viewer-media img"); });
  await viewer(page).getByRole("button", { name: "Slideshow", exact: true }).click();
  const photo = viewer(page).locator(".photo-viewer-media img");
  await photo.dispatchEvent("wheel", { deltaY: -300, ctrlKey: true, clientX: 620, clientY: 340 });
  await expect.poll(async () => Number(await photo.getAttribute("data-photo-zoom"))).toBeGreaterThan(1);
  const zoom = await photo.getAttribute("data-photo-zoom");
  const bounds = await badge.boundingBox();
  let release; state.hold = new Promise(resolve => { release = resolve; });
  await up.focus(); await up.press("Enter");
  await expect(rank).toHaveText("B");
  await expect(viewer(page).locator(".checkpoint-rank-feedback")).toHaveText("C → B · Saving…");
  await expect(up).toBeDisabled(); await expect(down).toBeDisabled();
  expect(await badge.boundingBox()).toEqual(bounds);
  state.hold = null; release();
  await expect(viewer(page).locator(".checkpoint-rank-feedback")).toHaveText("C → B · Saved");
  await expect(up).toBeFocused();
  await expect(card(page, 1).locator(".checkpoint-rank")).toHaveText("B");
  state.fail = true; await up.click();
  await expect(viewer(page).locator(".checkpoint-rank-feedback")).toContainText("wasn’t saved. Still B.");
  await expect(rank).toHaveText("B");
  expect(state.preferences.checkpoint_tiers.B).toContain(identity(0));
  await viewer(page).getByRole("button", { name: "Retry", exact: true }).click();
  await expect(rank).toHaveText("A"); await expect(down).toBeEnabled(); await expect(up).toBeDisabled();
  for (const grade of ["B", "C", "D", "F"]) {
    await down.click(); await expect(rank).toHaveText(grade); await expect(up).toBeEnabled();
  }
  await expect(down).toBeDisabled();
  expect(await page.evaluate(() => savedPhoto === document.querySelector(".photo-viewer-media img"))).toBe(true);
  await expect(viewer(page).locator(".photo-viewer-media")).toHaveAttribute("data-photo-view-mode", "fit");
  await expect(photo).toHaveAttribute("data-photo-zoom", zoom);
  await expect(viewer(page).getByRole("switch", { name: "Slideshow mode" })).toHaveAttribute("aria-checked", "true");
  await expect(viewer(page).locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", "rank-photo-0");
  await page.screenshot({ path: testInfo.outputPath("fullscreen-ranks.png") });
});

test("long names and inline arrows fit narrow screens and remote ranks refresh open badges", async ({ page }, testInfo) => {
  const state = await fixture(page, { activity: true }); await openPhoto(page);
  const badge = viewer(page).locator(".photo-viewer-checkpoint");
  await badge.locator(".checkpoint-name").evaluate(node => { node.textContent = "Moody Krea 2 V5 — Experimental Cinematic Portrait Fine Detail Edition BF16"; });
  const dock = viewer(page).locator(".photo-viewer-activity-host .activity-pair");
  await expect(dock).toBeVisible();
  for (const width of [1440, 1200, 1024, 768, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const up = await badge.locator('[data-rank-step="-1"]').boundingBox();
    const down = await badge.locator('[data-rank-step="1"]').boundingBox();
    expect(up.y).toBe(down.y); expect(down.x).toBeGreaterThan(up.x);
    expect(down.x + down.width).toBeLessThanOrEqual(width);
    const rankBox = await badge.boundingBox(), dockBox = await dock.boundingBox();
    expect(rankBox.x).toBeGreaterThanOrEqual(0);
    const separated = (a, b) => a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;
    expect(separated(rankBox, dockBox), `Rank and generation status at ${width}px`).toBe(true);
    expect(separated(await viewer(page).locator(".photo-viewer-fullscreen").boundingBox(), dockBox), `Full screen and generation status at ${width}px`).toBe(true);
  }
  await page.screenshot({ path: testInfo.outputPath("narrow-ranks.png") });
  state.preferences.checkpoint_tiers = { D: [identity(0)] }; state.preferences.revision++;
  await page.evaluate(() => fixtureEvents.dispatchEvent(new MessageEvent("preferences.updated", { data: "{}" })));
  await expect(badge.locator(".checkpoint-rank")).toHaveText("D");
  await expect(card(page, 1).locator(".checkpoint-rank")).toHaveText("D");
});
