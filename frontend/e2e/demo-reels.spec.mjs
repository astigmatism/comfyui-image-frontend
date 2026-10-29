import { expect, test } from "@playwright/test";

let originalSettings = null;
let fixtureGenerationId = null;

test.afterEach(async ({ page }) => {
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { "X-CSRF-Token": session.csrf_token };
  if (fixtureGenerationId) await page.request.delete(`/api/generations/${fixtureGenerationId}`, { headers });
  if (originalSettings) {
    const current = await (await page.request.get("/api/admin/demo-reels")).json();
    const data = Object.fromEntries(["paused", "rating_user_id", "slots", "recipes", "strengths"].map((key) => [key, originalSettings[key]]));
    await page.request.put("/api/admin/demo-reels", { headers, data: { ...data, revision: current.revision } });
  }
  originalSettings = null;
  fixtureGenerationId = null;
});

async function signIn(page) {
  const anonymous = await (await page.request.get("/api/auth/session")).json();
  for (const password of ["E2EAdminPermanent123!", "E2EAdminTemporary123!"]) {
    const response = await page.request.post("/api/auth/login", { headers: { "X-CSRF-Token": anonymous.csrf_token }, data: { username: "admin", password } });
    if (!response.ok()) continue;
    const session = await response.json();
    if (session.user.must_change_password) await page.request.post("/api/auth/password", { headers: { "X-CSRF-Token": session.csrf_token }, data: { new_password: "E2EAdminPermanent123!" } });
    await page.goto("/");
    await expect(page.locator("#workflow-source")).toBeEnabled();
    return (await page.request.get("/api/auth/session")).json();
  }
  throw new Error("Could not sign in to the local fake application");
}

// A LoRA with a thumbnail opens its reel from the caption icon; a thumbnail-free
// LoRA has no caption, so the same action is its menu's Demo Reel entry.
async function openDemoReel(picker, card) {
  const caption = card.getByRole("button", { name: /^Open Demo Reel/ });
  if (await caption.count()) return caption.click();
  const menu = card.getByRole("button", { name: /^Actions for / });
  if (await menu.getAttribute("aria-expanded") !== "true") await menu.click();
  await picker.getByRole("button", { name: "Demo Reel", exact: true }).click();
}

test("Demo Reel slots save independently, and caption browsing preserves the LoRA selection draft", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const session = await signIn(page);
  originalSettings = await (await page.request.get("/api/admin/demo-reels")).json();
  const sources = await (await page.request.get("/api/workflows")).json();
  let detail;
  for (const source of sources) {
    const candidate = await (await page.request.get(`/api/workflows/${source.source_key}`)).json();
    if (candidate.interface.inputs.some((control) => control.type === "lora_stack")) { detail = candidate; break; }
  }
  expect(detail).toBeTruthy();
  await page.locator("#workflow-source").click();
  const sourcePicker = page.locator("#source-picker-dialog");
  await sourcePicker.locator("[data-source-workflow-choice]").selectOption(detail.source_key);
  await sourcePicker.getByRole("button", { name: "Apply", exact: true }).click();
  await page.locator('[data-lora-open][data-lora-control-id="loras"]').click();
  const picker = page.locator("#lora-manager-dialog");
  await expect(picker.getByRole("button", { name: "Toggle Alpha", exact: true })).toBeVisible();
  await picker.getByRole("button", { name: "Toggle Alpha", exact: true }).click();
  await picker.getByRole("button", { name: "Demo Reel settings" }).click();
  const editor = page.locator("#demo-reel-settings-dialog");
  await expect(editor).toBeVisible();
  await editor.locator('[data-demo-recipe-enabled="krea2"]').check();
  await expect(editor.locator('[data-demo-source="krea2"]')).toBeVisible();
  await editor.locator('[data-demo-source="krea2"]').selectOption(detail.source_key);
  await editor.locator("[data-demo-paused]").check();
  await editor.getByRole("button", { name: "Add prompt slot" }).click();
  let last = editor.locator("[data-demo-slot]").last();
  await last.locator('[data-demo-slot-field="title"]').fill("Portrait study");
  await last.locator('[data-demo-slot-field="prompt"]').fill("a studio portrait of {subject}");
  await editor.getByRole("button", { name: "Add prompt slot" }).click();
  last = editor.locator("[data-demo-slot]").last();
  await last.locator('[data-demo-slot-field="title"]').fill("Outdoor study");
  await last.locator('[data-demo-slot-field="prompt"]').fill("{subject} beside a lake");
  const secondId = await last.getAttribute("data-demo-slot");
  await last.locator("[data-demo-slot-handle]").focus();
  await page.keyboard.press("ArrowUp");
  await expect(editor.locator("[data-demo-slot]").first()).toHaveAttribute("data-demo-slot", secondId);
  await expect(editor.locator("[data-demo-preview]").first()).toContainText("Alpha beside a lake");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(editor.getByRole("button", { name: "Save", exact: true })).toBeInViewport();
  await editor.screenshot({ path: testInfo.outputPath("demo-reel-settings-mobile.png"), animations: "disabled" });
  const saved = page.waitForResponse((response) => response.url().endsWith("/api/admin/demo-reels") && response.request().method() === "PUT");
  await editor.getByRole("button", { name: "Save", exact: true }).click();
  expect((await saved).ok()).toBe(true);
  await expect(editor).not.toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  const card = picker.locator('[data-lora-id="a"]');
  await openDemoReel(picker, card);
  const reel = page.locator("#demo-reel-dialog");
  await expect(reel).toBeVisible();
  await expect(reel.getByRole("heading", { name: "Alpha · Demo Reel" })).toBeVisible();
  await expect(reel.getByText("No Demo Reel images yet.", { exact: false })).toBeVisible();
  const refreshedWorkflow = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/workflows/${detail.source_key}`);
  await reel.getByRole("button", { name: "Edit Demo Reel", exact: true }).click();
  expect((await refreshedWorkflow).ok()).toBe(true);
  await expect(editor.locator("[data-demo-settings-status]")).toContainText("paused");
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await reel.getByRole("button", { name: "Back to LoRAs" }).click();
  await expect(card.getByRole("button", { name: "Toggle Alpha", exact: true })).toHaveAttribute("aria-pressed", "true");

  // Supply two retained images to exercise the real application photo viewer without a five-minute wall-clock delay.
  const parameters = Object.fromEntries(detail.interface.inputs.map((control) => [control.id, control.type === "seed" ? null : control.default]));
  parameters.prompt = "Demo Reel browser fixture";
  const accepted = await page.request.post("/api/generations", { headers: { "X-CSRF-Token": session.csrf_token, "Idempotency-Key": crypto.randomUUID() }, data: { source_key: detail.source_key, revision: detail.revision, parameters } });
  expect(accepted.ok()).toBe(true);
  const first = await accepted.json();
  fixtureGenerationId = first.id;
  await expect.poll(async () => (await (await page.request.get(`/api/generations/${first.id}`)).json()).status).toBe("succeeded");
  const completed = await (await page.request.get(`/api/generations/${first.id}`)).json();
  const artifact = completed.artifacts.find((artifact) => artifact.canonical) || completed.artifacts[0];
  const config = await (await page.request.get("/api/admin/demo-reels")).json();
  const identity = detail.interface.inputs.find((control) => control.type === "lora_stack").items[0].lora_identity;
  const firstImage = { ...first, status: "succeeded", display_artifact: artifact, demo_reel: { can_manage: true }, recall_available: true };
  const secondImage = { ...firstImage, id: crypto.randomUUID(), display_artifact: { ...artifact, id: crypto.randomUUID() } };
  await page.route(`**/api/demo-reels/krea2/${identity}?*`, async (route) => {
    await route.fulfill({ json: { label: "Alpha", subject: "Alpha", entries: [{ id: "one", slot_id: config.slots[0].id, checkpoint_label: "Model one", state: "ready", generation: firstImage }, { id: "two", slot_id: config.slots[1].id, checkpoint_label: "Model two", state: "ready", generation: secondImage }], slots: config.slots, completed: 2, expected: 2, status: "complete", next_offset: null, can_manage: true, paused: true } });
  });
  await openDemoReel(picker, card);
  await expect(reel.getByRole("button", { name: "View Model one image" })).toBeVisible();
  await reel.screenshot({ path: testInfo.outputPath("demo-reel-gallery-desktop.png"), animations: "disabled" });
  await reel.getByRole("button", { name: "View Model one image" }).click();
  const viewer = page.locator("#photo-viewer");
  await expect(viewer).toBeVisible();
  await expect(viewer.locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", first.id);
  await viewer.getByRole("button", { name: "View older generation" }).click();
  await expect(viewer.locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", secondImage.id);
  await viewer.getByRole("button", { name: "Close image viewer" }).click();
  await reel.getByRole("button", { name: "Back to LoRAs" }).click();
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  expect((await (await page.request.get("/api/admin/demo-reels")).json()).slots).toEqual(config.slots);
  expect(errors).toEqual([]);
});
