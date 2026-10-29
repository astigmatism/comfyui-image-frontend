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
  // Model families is collapsed by default, so open it before touching a family.
  await editor.getByText("Model families", { exact: false }).click();
  await expect(editor.locator(".demo-reel-family").first()).toBeVisible();
  await editor.locator('[data-demo-recipe-enabled="krea2"]').check();
  await expect(editor.locator('[data-demo-source="krea2"]')).toBeVisible();
  await editor.locator('[data-demo-source="krea2"]').selectOption(detail.source_key);
  await editor.locator("[data-demo-paused]").check();
  await editor.getByRole("button", { name: "Add prompt slot" }).click();
  // Slot headers are the LoRA dialog's group headers, by class and by metrics.
  const slotActions = editor.locator(".demo-reel-slot-actions").first();
  await expect(slotActions).toHaveClass(/lm-group-actions/);
  expect(await slotActions.locator(".icon-button").first().evaluate((node) => [getComputedStyle(node).width, getComputedStyle(node).height])).toEqual(["30px", "30px"]);
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
  await expect(editor.locator("[data-demo-slot]").first().locator('[data-demo-slot-field="prompt"]')).toHaveValue("{subject} beside a lake");
  await expect(editor.locator("[data-demo-slot]").first().locator(".demo-reel-slot-hint")).toContainText("trigger phrase");
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
  // The first paint of the empty reel says so with a gallery empty state, not a table.
  await expect(reel.getByText("No Demo Reel images yet.", { exact: false })).toBeVisible();
  const refreshedWorkflow = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/workflows/${detail.source_key}`);
  await reel.getByRole("button", { name: "Edit Demo Reel", exact: true }).click();
  expect((await refreshedWorkflow).ok()).toBe(true);
  await expect(editor.locator("[data-demo-settings-status]")).toContainText("paused");
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await reel.getByRole("button", { name: "Close Demo Reel" }).click();
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
  const firstImage = { ...first, status: "succeeded", cancel_allowed: false, checkpoint_label: "Model one", display_artifact: artifact, demo_reel: { can_manage: true }, recall_available: true };
  const secondImage = { ...firstImage, id: crypto.randomUUID(), checkpoint_label: "Model two", display_artifact: { ...artifact, id: crypto.randomUUID() } };
  await page.route(`**/api/demo-reels/krea2/${identity}?*`, async (route) => {
    await route.fulfill({ json: { label: "Alpha", subject: "Alpha", entries: [{ id: "one", slot_id: config.slots[0].id, checkpoint_label: "Model one", state: "ready", generation: firstImage }, { id: "two", slot_id: config.slots[1].id, checkpoint_label: "Model two", state: "ready", generation: secondImage }], slots: config.slots, completed: 2, expected: 2, status: "complete", next_offset: null, can_manage: true, paused: true } });
  });
  // The two images stand in for a reel that has already produced a pair, so the
  // reel's own action endpoint is served beside them.
  await page.route(`**/api/demo-reels/krea2/${identity}/actions`, async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    for (const generation of [firstImage, secondImage]) {
      if ((body.generation_ids || []).includes(generation.id)) generation.is_favorite = body.action === "favorite";
    }
    await route.fulfill({ json: { ok: true } });
  });
  await openDemoReel(picker, card);
  // The reel is the grouped gallery: one collapsible group per prompt slot, and each
  // slot holds the gallery's own image cards with no caption underneath.
  await expect(reel.locator(".demo-reel-slot-group")).toHaveCount(2);
  const firstCard = reel.locator(`[data-gallery-card="generation"][data-generation-id="${first.id}"]`);
  const secondCard = reel.locator(`[data-gallery-card="generation"][data-generation-id="${secondImage.id}"]`);
  await expect(firstCard).toBeVisible();
  await expect(firstCard.locator(".checkpoint-name")).toHaveText("Model one");
  await expect(firstCard.locator(".card-media img")).toBeVisible();
  expect(await firstCard.evaluate((card) => card.getBoundingClientRect().width)).toBeGreaterThanOrEqual(300);
  // Every action belongs to the card and appears on hover, never as text below it.
  await expect(firstCard.locator(".demo-reel-card-actions > *")).toHaveCount(9);
  // The pointer rests outside the dialog: nothing is revealed until it comes back.
  await page.mouse.move(4, 4);
  await expect(firstCard.locator(".card-actions")).toHaveCSS("opacity", "0");
  await firstCard.hover();
  await expect(firstCard).toHaveClass(/card-controls-visible/);
  await expect(firstCard.locator(".card-actions")).toHaveCSS("opacity", "1");
  await expect(firstCard.getByRole("button", { name: "Regenerate this image" })).toBeVisible();
  // The shared reel's switches live in the title area as icons; here generation is paused.
  await expect(reel.getByRole("button", { name: "Resume background generation" })).toBeVisible();
  await reel.screenshot({ path: testInfo.outputPath("demo-reel-gallery-desktop.png"), animations: "disabled" });
  await page.setViewportSize({ width: 390, height: 844 });
  await reel.screenshot({ path: testInfo.outputPath("demo-reel-gallery-mobile.png"), animations: "disabled" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  // Favoriting travels to the reel's own action endpoint, and the card keeps the state.
  const favorited = page.waitForResponse((response) => response.url().endsWith("/actions") && response.request().method() === "POST");
  await firstCard.getByRole("button", { name: "Add to Favorites" }).click();
  expect((await favorited).ok()).toBe(true);
  await expect(firstCard.getByRole("button", { name: "Remove from Favorites" })).toHaveAttribute("aria-pressed", "true");
  // The image opens in the application's viewer and walks the reel with it.
  await firstCard.locator(".card-media").click();
  const viewer = page.locator("#photo-viewer");
  await expect(viewer).toBeVisible();
  await expect(viewer.locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", first.id);
  await viewer.getByRole("button", { name: "View older generation" }).click();
  await expect(viewer.locator(".photo-viewer-frame")).toHaveAttribute("data-photo-generation-id", secondImage.id);
  await viewer.getByRole("button", { name: "Close image viewer" }).click();
  // A slot collapses like a prompt group, hiding its cards but not its header.
  const firstGroup = reel.locator(".demo-reel-slot-group").first();
  await firstGroup.getByRole("button", { name: /^Collapse / }).click();
  await expect(firstCard).toBeHidden();
  await firstGroup.getByRole("button", { name: /^Expand / }).click();
  await expect(firstCard).toBeVisible();
  await reel.getByRole("button", { name: "Close Demo Reel" }).click();
  await picker.getByRole("button", { name: "Cancel", exact: true }).click();
  expect((await (await page.request.get("/api/admin/demo-reels")).json()).slots).toEqual(config.slots);
  expect(errors).toEqual([]);
});
