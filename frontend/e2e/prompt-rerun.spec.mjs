import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "default" });

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
  await expect(async () => {
    await card.scrollIntoViewIfNeeded({ timeout: 2000 });
    await card.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await card.hover();
    await expect(card.locator('[role="group"]')).toHaveCSS("opacity", "1", { timeout: 2000 });
    await card.locator(".card-select-button").click({ trial: true, timeout: 1500 });
  }).toPass({ timeout: 10000 });
  await card.locator(".card-select-button").click();
}

test("Prompt Re-run queues the exact selected prompts into a new folder with new settings", async ({ page }) => {
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
  await dialog.locator("details.rerun-prompts summary").click();
  await expect(dialog.locator(".rerun-prompts li")).toHaveText([/rerun lighthouse at dawn/, /rerun orchard in fog/]);

  const checkpoints = dialog.locator('[name="rerun_checkpoint"]');
  expect(await checkpoints.count()).toBeGreaterThan(1);
  for (const box of await checkpoints.all()) if (await box.isChecked()) await box.uncheck();
  await expect(dialog.getByRole("button", { name: /^Queue/ })).toBeDisabled();
  await expect(dialog.locator('[data-rerun-error="checkpoints"]')).toBeVisible();
  await checkpoints.nth(0).check();
  await checkpoints.nth(1).check();
  await dialog.getByLabel("Generations per prompt").fill("2");
  await expect(dialog.locator("[data-rerun-total]")).toHaveText("Queues 8 generations");
  await dialog.getByLabel("Generations per prompt").fill("1");
  await dialog.getByLabel("Generations per prompt").press("Tab");
  await dialog.getByLabel("Keep each original image's resolution").check();
  await expect(dialog.locator('[name="rerun_width"]')).toBeDisabled();
  await dialog.getByLabel("Folder name").fill("Re-run favorites");

  const submission = page.waitForResponse((item) => new URL(item.url()).pathname === "/api/gallery/prompt-rerun" && item.request().method() === "POST");
  await dialog.getByRole("button", { name: "Queue 4 generations" }).click();
  const response = await submission;
  expect(response.status(), await response.text()).toBe(201);
  const body = JSON.parse(response.request().postData());
  expect(body).not.toHaveProperty("parameters.prompt");
  expect(body.model_variants).toHaveLength(2);
  expect(body.keep_original_resolution).toBe(true);
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
