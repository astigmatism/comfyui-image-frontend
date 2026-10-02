import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "default" });

test("Creative Direction reruns refine in the background, restore progress, and stop remaining work", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(15_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "rerun.creative");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await generate(page, "creative rerun lighthouse");
  await generate(page, "creative rerun orchard");
  const cards = page.locator('#gallery [data-gallery-card="generation"]');
  await expect(cards).toHaveCount(2);
  const panel = page.locator("#generation-panel");
  await panel.getByRole("button", { name: "Creative Direction", exact: true }).click();
  await panel.locator("#creative-direction").fill("cinematic light");
  await panel.locator('[name="assistant-mode"][value="create"]').check();
  const originalInstructions = await panel.locator("#prompt-assistant-instructions").inputValue();

  const open = async () => {
    await selectCard(page, cards.first());
    await selectCard(page, cards.nth(1));
    await page.locator('[data-bulk-action="rerun"]').click();
  };
  await open();
  const dialog = page.locator("#gallery-rerun-dialog");
  const toggle = dialog.getByRole("switch", { name: "Use Creative Direction" });
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  const direction = dialog.getByRole("textbox", { name: "Creative Direction", exact: true });
  await expect(direction).toHaveValue("cinematic light");
  await direction.fill("");
  await expect(dialog.getByRole("button", { name: "Refine & Queue" })).toBeDisabled();
  await direction.fill("watercolor at dusk");
  await dialog.locator(".prompt-preprocessor summary").click();
  await dialog.getByLabel("Refinement instructions").fill("Keep the subjects and change the rendering.");
  await dialog.getByLabel("Thinking mode").uncheck();
  await expect(dialog.getByLabel("Refinement instructions")).toBeVisible();
  await dialog.getByLabel("Generations per prompt").fill("2");
  await dialog.getByLabel("Folder name").fill("Creative rerun results");
  await expect(dialog.locator("[data-rerun-total]")).toHaveText("Refine 2 prompts · Generate up to 4 images");
  await expect(panel.locator("#creative-direction")).toHaveValue("cinematic light");
  await expect(panel.locator("#prompt-assistant-instructions")).toHaveValue(originalInstructions);
  await page.setViewportSize({ width: 360, height: 740 });
  await expect(dialog.getByRole("button", { name: "Refine & Queue" })).toBeInViewport();
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("creative-rerun-mobile.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const accepted = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/gallery/prompt-rerun" && response.request().method() === "POST");
  await dialog.getByRole("button", { name: "Refine & Queue" }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON().refinement).toEqual({ creative_direction: "watercolor at dusk", think: false, instructions: "Keep the subjects and change the rendering." });
  const result = await response.json();
  await expect(dialog).toBeHidden();
  const progress = page.locator("#prompt-rerun-progress-host");
  await expect(progress).toBeVisible();
  await expect(progress.getByRole("heading", { name: "All prompts processed" })).toBeVisible();
  await expect(progress).toContainText("4 of 4 images queued");
  await progress.locator("summary").first().click();
  await progress.locator(".rerun-prompt-list summary").first().click();
  await expect(progress.locator("pre").first()).toHaveText("creative rerun lighthouse");
  await expect(progress.locator("pre").nth(1)).toContainText("watercolor at dusk");
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("creative-rerun-progress.png") });
  await page.reload();
  await expect(progress.getByRole("heading", { name: "All prompts processed" })).toBeVisible();
  await expect(cards).toHaveCount(4);
  const detail = await (await page.request.get(`/api/generations/${(await (await page.request.get(`/api/generations?collection_id=${result.collection.id}`)).json()).items[0].id}`)).json();
  expect(detail.final_prompt).toContain("watercolor at dusk");

  await page.goto("/");
  await expect(cards).toHaveCount(2);
  await open();
  await toggle.check();
  await direction.fill("a different direction");
  const nextAccepted = page.waitForResponse((item) => new URL(item.url()).pathname === "/api/gallery/prompt-rerun" && item.request().method() === "POST");
  await dialog.getByRole("button", { name: "Refine & Queue" }).click();
  const nextRun = (await (await nextAccepted).json()).run;
  await progress.getByRole("button", { name: "Stop remaining" }).click();
  await expect(progress.getByRole("heading", { name: "Prompt rerun stopped" })).toBeVisible();
  const stopped = await (await page.request.get(`/api/gallery/prompt-rerun/${nextRun.id}`)).json();
  expect(stopped.counts.cancelled).toBeGreaterThan(0);
  await page.reload();
  await expect(progress.getByRole("heading", { name: "Prompt rerun stopped" })).toBeVisible();
  expect(errors).toEqual([]);
});

async function signIn(page, username, password) {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function setForcedPassword(page, password) {
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password", { exact: true }).fill(password);
  await page.getByLabel("Confirm new password").fill(password);
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.locator(".gallery-viewport")).toBeVisible();
}

async function signInAdmin(page) {
  const login = () => page.waitForResponse((response) => new URL(response.url()).pathname === "/api/auth/login" && response.request().method() === "POST");
  let response = login();
  await signIn(page, "admin", "E2EAdminPermanent123!");
  if ((await response).ok()) return;
  response = login();
  await signIn(page, "admin", "E2EAdminTemporary123!");
  expect((await response).ok()).toBe(true);
  await setForcedPassword(page, "E2EAdminPermanent123!");
}

async function signInFreshUser(page, username) {
  await signInAdmin(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const headers = { "X-CSRF-Token": session.csrf_token };
  const created = await page.request.post("/api/admin/users", { headers, data: { username, temporary_password: "E2EUserTemporary123!" } });
  expect(created.status()).toBe(201);
  expect((await page.request.post("/api/auth/logout", { headers })).ok()).toBe(true);
  await page.goto("/");
  await signIn(page, username, "E2EUserTemporary123!");
  await setForcedPassword(page, "E2EUserPermanent123!");
}

async function selectPublishedSource(page, name) {
  const selector = page.locator("#workflow-source");
  await selector.click();
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog).toBeVisible();
  const workflow = dialog.locator("[data-source-workflow-choice]");
  const value = await workflow.locator("option").filter({ hasText: name }).getAttribute("value");
  await workflow.selectOption(value);
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(selector).toHaveAttribute("data-source-key", value);
}

async function generate(page, prompt) {
  await page.locator("#generation-quantity").fill("1");
  await page.locator("#generation-quantity").blur();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(prompt);
  const response = page.waitForResponse((item) => ["/api/generations", "/api/generations/batch"].includes(new URL(item.url()).pathname) && item.request().method() === "POST");
  await page.getByRole("button", { name: "Generate" }).click();
  expect((await response).status()).toBe(201);
}

async function selectCard(page, card) {
  const select = card.locator(".card-select-button");
  await select.focus();
  await select.press("Space");
  await expect(select).toHaveAttribute("aria-checked", "true");
}

test("Prompt Re-run queues the exact selected prompts into a new folder with new settings", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "rerun.user");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await generate(page, "rerun lighthouse at dawn");
  await generate(page, "rerun orchard in fog");
  const cards = page.locator('#gallery [data-gallery-card="generation"]');
  await expect(cards).toHaveCount(2);
  await expect(cards.first()).toHaveClass(/status-succeeded/);
  await expect(cards.nth(1)).toHaveClass(/status-succeeded/);

  const panelBefore = await page.locator("#generation-quantity").inputValue();
  await selectCard(page, cards.first());
  await page.locator("#gallery-selection-toolbar").getByRole("button", { name: "Select loaded (2)" }).click();
  await expect(page.locator("#gallery-selection-toolbar").getByRole("status")).toHaveText("2 selected");
  const tool = page.locator('#gallery-selection-toolbar [data-bulk-action="rerun"]');
  await expect(tool).toBeEnabled();
  await tool.click();

  const dialog = page.locator("#gallery-rerun-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-rerun-summary]")).toHaveText("2 prompts from 2 generations");
  await expect(dialog.locator("details:not(.prompt-preprocessor), .rerun-prompts")).toHaveCount(0);
  const sourceTrigger = dialog.locator("#rerun-workflow-source");
  const sourceDialog = page.locator("#source-picker-dialog");
  const originalSourceSummary = await sourceTrigger.textContent();
  const mainPanel = page.locator("#generation-panel");
  const mainSourceSummary = await page.locator("#workflow-source").textContent();
  const mainWidth = await mainPanel.getByRole("spinbutton", { name: "Width", exact: true }).inputValue();
  const mainHeight = await mainPanel.getByRole("spinbutton", { name: "Height", exact: true }).inputValue();
  const mainLoras = await mainPanel.locator(".lora-stack").textContent();
  const preferencesBefore = await (await page.request.get("/api/preferences")).json();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await sourceTrigger.click();
  await expect(dialog).toBeVisible();
  await sourceDialog.getByRole("button", { name: "Clear all", exact: true }).click();
  await expect(sourceDialog.getByRole("button", { name: "Apply", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(sourceDialog).toBeHidden();
  await expect(sourceTrigger).toBeFocused();
  await expect(sourceTrigger).toHaveText(originalSourceSummary);

  await sourceTrigger.click();
  const checkpoints = sourceDialog.locator("[data-source-model-choice]");
  await sourceDialog.getByRole("button", { name: "Clear all", exact: true }).click();
  await checkpoints.nth(0).check();
  await checkpoints.nth(1).check();
  await sourceDialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(sourceDialog).toBeHidden();
  await expect(sourceTrigger).toBeFocused();
  await expect(page.locator("#workflow-source")).toHaveText(mainSourceSummary);

  // The same LoRA manager edits only this draft, including remembered strengths.
  const loraTrigger = dialog.getByRole("button", { name: "Open LoRA manager" });
  const manager = page.locator("#lora-manager-dialog");
  await loraTrigger.click();
  await manager.getByRole("button", { name: "Toggle Alpha" }).click();
  await manager.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(loraTrigger).toBeFocused();
  await expect(dialog.locator('[data-control-section-status="lora-loras"]')).toHaveText("0 active");
  await loraTrigger.click();
  await manager.getByRole("button", { name: "Toggle Beta" }).click();
  await manager.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("1.25");
  await manager.getByRole("spinbutton", { name: "Beta strength", exact: true }).press("Tab");
  await manager.getByRole("button", { name: "Reorder Beta" }).press("ArrowUp");
  await expect(manager.locator("[data-lora-subject-preview]")).toHaveText("Selected prompts stay unchanged");
  await manager.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(loraTrigger).toBeFocused();
  await dialog.getByRole("button", { name: "LoRAs", exact: true }).click();
  await expect(dialog.locator(".lm-summary-name")).toHaveText("Beta");
  await expect(dialog.locator(".lm-summary-strength")).toHaveText("1.25");
  await expect(dialog.locator("[data-lora-editable], [data-lora-disable]")).toHaveCount(0);
  await expect(mainPanel.locator(".lora-stack")).toHaveText(mainLoras);

  // Resolution uses the panel's editor, without touching panel values or recents.
  const resolutionToggle = dialog.getByRole("button", { name: "Resolution", exact: true });
  await expect(resolutionToggle).toHaveAttribute("aria-expanded", "false");
  await resolutionToggle.click();
  const width = dialog.getByRole("spinbutton", { name: "Width", exact: true });
  const height = dialog.getByRole("spinbutton", { name: "Height", exact: true });
  await width.fill("1024");
  await height.fill("1024");
  await height.press("Tab");
  const grid = dialog.locator("[data-resolution-grid]");
  await grid.locator('[data-resolution-handle="width"]').press("ArrowRight");
  await expect(width).toHaveValue("1088");
  await expect(dialog.locator('.resolution-recent-badge[data-resolution-recent-value="1088x1024"]')).toBeVisible();
  const handle = grid.locator('[data-resolution-handle="both"]');
  await handle.hover();
  const handleBox = await handle.boundingBox();
  const box = await grid.boundingBox();
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.25, { steps: 5 });
  await page.mouse.up();
  await expect(width).toHaveValue("1536");
  await expect(height).toHaveValue("1536");
  await dialog.locator("[data-resolution-preset]").selectOption("1024x1024");
  await expect(width).toHaveValue("1024");
  await expect(height).toHaveValue("1024");
  const squareRecent = dialog.locator('.resolution-recent-badge[data-resolution-recent-value="1024x1024"]');
  await expect(squareRecent.locator(".resolution-recent-apply")).toBeVisible();
  await width.fill("768");
  await squareRecent.locator(".resolution-recent-apply").click();
  await expect(width).toHaveValue("1024");
  await squareRecent.locator(".resolution-recent-remove").click();
  await expect(squareRecent).toHaveCount(0);
  await mainPanel.getByRole("spinbutton", { name: "Width", exact: true }).evaluate((input, expected) => { if (input.value !== expected) throw new Error("Panel width changed"); }, mainWidth);
  await expect(mainPanel.getByRole("spinbutton", { name: "Height", exact: true })).toHaveValue(mainHeight);
  expect(await (await page.request.get("/api/preferences")).json()).toEqual(preferencesBefore);
  const duplicateIds = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
    return ids.filter((id, index) => ids.indexOf(id) !== index);
  });
  expect(duplicateIds).toEqual([]);
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("rerun-desktop-expanded.png") });
  await resolutionToggle.click();
  await expect(resolutionToggle).toHaveAttribute("aria-expanded", "false");
  await dialog.getByRole("button", { name: "LoRAs", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "LoRAs", exact: true })).toHaveAttribute("aria-expanded", "false");
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("rerun-desktop-compact.png") });
  await page.setViewportSize({ width: 360, height: 740 });
  await expect(dialog.getByRole("button", { name: /^Queue/ })).toBeInViewport();
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("rerun-mobile.png") });
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await resolutionToggle.click();
  await dialog.getByLabel("Generations per prompt").fill("2");
  await expect(dialog.locator("[data-rerun-total]")).toHaveText("Queues 8 generations");
  await dialog.getByLabel("Reuse original seed").check();
  await expect(dialog.getByLabel("Generations per prompt")).toHaveValue("1");
  await expect(dialog.getByLabel("Generations per prompt")).toBeDisabled();
  await dialog.getByLabel("Random per image").check();
  await dialog.getByLabel("Generations per prompt").fill("1");
  await dialog.getByLabel("Generations per prompt").press("Tab");
  await dialog.getByLabel("Keep each original image's resolution").check();
  await expect(width).toBeDisabled();
  await expect(dialog.locator("[data-resolution-preset]")).toBeDisabled();
  await dialog.getByLabel("Folder name").fill("Re-run favorites");

  const submission = page.waitForResponse((item) => new URL(item.url()).pathname === "/api/gallery/prompt-rerun" && item.request().method() === "POST");
  await dialog.getByRole("button", { name: "Queue 4 generations" }).click();
  const response = await submission;
  expect(response.status(), await response.text()).toBe(201);
  const body = JSON.parse(response.request().postData());
  expect(body).not.toHaveProperty("parameters.prompt");
  expect(body.model_variants).toHaveLength(2);
  expect(body.keep_original_resolution).toBe(true);
  expect(body.parameters.loras[0]).toEqual({ id: "b", strength: 1.25 });
  expect(errors).toEqual([]);
  const result = await response.json();
  expect(result.collection.name).toBe("Re-run favorites");
  expect(result.items.filter((item) => item.generation)).toHaveLength(4);

  await expect(dialog).toBeHidden();
  await expect(page.locator("#toast-region")).toContainText("Queued 4 generations into “Re-run favorites”.");
  await expect(page).toHaveURL(new RegExp(`${result.collection.id}`));
  await expect(cards).toHaveCount(4);
  await expect(page.locator(".gallery-selection-mode")).toHaveCount(0);
  await expect(page.locator("#generation-quantity")).toHaveValue(panelBefore);

  const listing = await (await page.request.get(`/api/generations?collection_id=${result.collection.id}&limit=60`)).json();
  const prompts = [];
  for (const item of listing.items) {
    const detail = await (await page.request.get(`/api/generations/${item.id}`)).json();
    prompts.push(detail.final_prompt);
  }
  expect(prompts.sort()).toEqual(["rerun lighthouse at dawn", "rerun lighthouse at dawn", "rerun orchard in fog", "rerun orchard in fog"]);
});


test("rerun source changes survive delayed previews and cancelled dialogs ignore late loads", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "rerun.cancel");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await generate(page, "rerun transactional draft");
  const card = page.locator('#gallery [data-gallery-card="generation"]').first();
  await expect(card).toHaveClass(/status-succeeded/);
  await selectCard(page, card);
  const toolbar = page.locator('#gallery-selection-toolbar [data-bulk-action="rerun"]');
  const dialog = page.locator("#gallery-rerun-dialog");
  const sourceTrigger = dialog.locator("#rerun-workflow-source");
  const picker = page.locator("#source-picker-dialog");
  const mainKey = await page.locator("#workflow-source").getAttribute("data-source-key");
  let releasePreview;
  const previewGate = new Promise((resolve) => { releasePreview = resolve; });
  await page.route("**/api/gallery/prompt-rerun/preview", async (route) => {
    const response = await route.fetch();
    await previewGate;
    await route.fulfill({ response });
  });
  await toolbar.click();
  await expect(dialog.locator("[data-rerun-summary]")).toContainText("Reading");
  await sourceTrigger.click();
  const workflow = picker.locator("[data-source-workflow-choice]");
  const otherKey = await workflow.locator(`option:not([value="${mainKey}"]):not([disabled])`).first().getAttribute("value");
  await workflow.selectOption(otherKey);
  await picker.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(picker).toBeHidden();
  await expect(sourceTrigger).toHaveAttribute("data-source-key", otherKey);
  releasePreview();
  await expect(dialog.getByRole("button", { name: /^Queue/ })).toBeEnabled();
  await dialog.getByLabel("Folder name").fill("Discard this draft");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(toolbar).toBeFocused();
  await expect(page.locator("#workflow-source")).toHaveAttribute("data-source-key", mainKey);

  await toolbar.click();
  await expect(sourceTrigger).toHaveAttribute("data-source-key", mainKey);
  await expect(dialog.getByLabel("Folder name")).not.toHaveValue("Discard this draft");
  await expect(dialog.getByRole("button", { name: "Resolution", exact: true })).toHaveAttribute("aria-expanded", "false");
  let releaseSource;
  let requestedSource;
  const sourceGate = new Promise((resolve) => { releaseSource = resolve; });
  const sourceRequested = new Promise((resolve) => { requestedSource = resolve; });
  let fail = true;
  await page.route(`**/api/workflows/${otherKey}`, async (route) => {
    if (fail) {
      fail = false;
      await route.fulfill({ status: 422, json: { error: { code: "source_unavailable", message: "Source temporarily unavailable" } } });
      return;
    }
    const response = await route.fetch();
    requestedSource();
    await sourceGate;
    await route.fulfill({ response });
  });
  await sourceTrigger.click();
  await workflow.selectOption(otherKey);
  await picker.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(picker.getByRole("alert")).toContainText("Source temporarily unavailable");
  await page.setViewportSize({ width: 320, height: 640 });
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("rerun-source-error-mobile.png") });
  await picker.getByRole("button", { name: "Apply", exact: true }).click();
  await sourceRequested;
  await page.keyboard.press("Escape");
  await expect(picker).toBeHidden();
  await expect(sourceTrigger).toBeFocused();
  const responseReceived = page.waitForResponse((response) => response.url().endsWith(`/api/workflows/${otherKey}`));
  releaseSource();
  await responseReceived;
  await expect(sourceTrigger).toHaveAttribute("data-source-key", mainKey);
  // Exercise another interaction after the late response has had a chance to settle.
  await dialog.getByRole("button", { name: "Resolution", exact: true }).click();
  await expect(dialog.getByRole("spinbutton", { name: "Width", exact: true })).toBeVisible();
  await expect(sourceTrigger).toHaveAttribute("data-source-key", mainKey);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
});

test("sidebar LoRA shortcuts and thumbnail autosaves persist without changing Subject", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "lora.shortcuts");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  const sourceKey = await page.locator("#workflow-source").getAttribute("data-source-key");
  const panel = page.locator("#generation-panel");
  const manager = page.locator("#lora-manager-dialog");
  await page.getByRole("switch", { name: "Use Prompt Generation" }).check();
  await panel.getByRole("button", { name: "Open LoRA manager" }).click();
  await manager.getByRole("button", { name: "Toggle Alpha" }).click();
  await manager.getByRole("button", { name: "Toggle Beta" }).click();
  await manager.screenshot({ path: testInfo.outputPath("lora-manager-enabled.png"), animations: "disabled" });
  await manager.getByRole("button", { name: "Apply", exact: true }).click();
  const subject = page.getByRole("textbox", { name: "Subject name", exact: true });
  await subject.fill("Keep my subject");
  await subject.blur();
  const strength = panel.getByRole("spinbutton", { name: "Alpha strength", exact: true });
  await strength.fill("0.65");
  await strength.press("Enter");
  await panel.getByRole("button", { name: "Increase Alpha strength" }).click();
  await expect(strength).toHaveValue("1.15");
  await panel.getByRole("button", { name: "Disable Beta" }).click();
  await expect(panel.locator('[data-control-section-status="lora-loras"]')).toHaveText("1 active");
  await expect(subject).toHaveValue("Keep my subject");
  await panel.locator('.lora-stack').screenshot({ path: testInfo.outputPath("lora-sidebar-shortcuts.png"), animations: "disabled" });
  await expect.poll(async () => {
    const preferences = await (await page.request.get("/api/preferences")).json();
    return preferences.settings.sources?.[sourceKey]?.values?.loras;
  }).toEqual([{ id: "a", strength: 1.15 }, { id: "b", strength: 0 }]);

  await panel.getByRole("button", { name: "Open LoRA manager" }).click();
  await manager.getByRole("button", { name: "Add image for Alpha" }).click();
  await manager.locator("[data-lora-file]").setInputFiles({ name: "sample.png", mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64") });
  await expect(manager.locator('[data-lora-id="a"] .lm-image-status')).toHaveText("Saved");
  await manager.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(panel.locator('.lm-summary-thumb img')).toHaveCount(1);
  await page.reload();
  await expect(strength).toHaveValue("1.15");
  await expect(subject).toHaveValue("Keep my subject");
  await expect(panel.locator('.lm-summary-thumb img')).toHaveCount(1);
  await panel.getByRole("button", { name: "Open LoRA manager" }).click();
  await manager.getByRole("button", { name: "Toggle Beta" }).click();
  await expect(manager.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue("1.00");
  await manager.getByRole("button", { name: "Change image for Alpha" }).hover();
  await manager.getByRole("button", { name: "Remove image for Alpha" }).click();
  await expect(manager.locator('[data-lora-id="a"] .lm-image-status')).toHaveText("Saved");
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(panel.locator('.lm-summary-item')).toHaveCount(1);
  await expect(panel.locator('.lm-summary-thumb img')).toHaveCount(0);
});
