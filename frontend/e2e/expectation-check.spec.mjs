import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "default" });

// The fake vision reviewer (backend/tests/fake_services.py) scores an expectation 35
// on the first review of its expectation set when it contains this phrase, and 100
// afterwards; "never passes" always scores 30. A unique token per test keeps the
// reviewer's per-set memory independent between tests.
const SECOND_LOOK = "needs a second look";
const NEVER_PASSES = "never passes";

test("Apply & verify revises until every expectation passes and applies the qualified prompt", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  page.setDefaultTimeout(20_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "expectations.apply");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  const token = uniqueToken();
  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill(`a quiet harbor at dawn ${token}`);
  const panel = await openExpectations(page);
  await panel.locator("#creative-direction").fill("soft painterly light");
  await enableExpectations(panel, [`A calm harbor ${token}`, `Warm light ${SECOND_LOOK} ${token}`]);
  await expect(panel.getByRole("button", { name: "Apply & verify" })).toBeVisible();

  const accepted = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/prompt-assistant/checks" && response.request().method() === "POST");
  await panel.getByRole("button", { name: "Apply & verify" }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  const body = response.request().postDataJSON();
  expect(body.purpose).toBe("apply");
  expect(body.expectations).toEqual([`A calm harbor ${token}`, `Warm light ${SECOND_LOOK} ${token}`]);
  expect(body.items).toHaveLength(1);
  expect(body.items[0].prompt_assistant_run_id).toBeUndefined();
  expect(response.request().headers()["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/u);

  const dialog = page.locator("#expectation-check-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Stop" })).toBeVisible();
  await expect(page.locator("#generate-button")).toBeDisabled();
  const first = dialog.locator('[data-attempt-number="1"]');
  await expect(first.locator(".expectation-pill")).toHaveText("Not met", { timeout: 60_000 });
  await expect(first.locator("img")).toBeVisible();
  await expect(first.locator(".expectation-result-score").nth(1)).toHaveText("35");
  const second = dialog.locator('[data-attempt-number="2"]');
  await expect(second.locator(".expectation-pill")).toHaveText("Passed", { timeout: 60_000 });
  await expect(dialog.locator(".expectation-banner.is-passed")).toContainText("Passed on attempt 2 with 100/100.");
  await expect(dialog.locator(".expectation-banner")).toContainText("The qualified prompt was applied to the Prompt field.");
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("expectations-passed.png") });

  const latest = (await (await page.request.get("/api/prompt-assistant/checks/latest")).json()).check;
  expect(latest.status).toBe("passed");
  await expect(prompt).toHaveValue(latest.final_prompt);
  await expect(dialog.getByRole("button", { name: "Prompt applied" })).toBeDisabled();
  const cards = page.locator('#gallery [data-gallery-card="generation"]');
  await expect(cards).toHaveCount(2);
  await expect(page.locator("#generate-button")).toBeEnabled();

  await dialog.getByRole("button", { name: "Delete failed attempts (1)" }).click();
  await expect(dialog.locator(".expectation-confirm")).toContainText("Delete 1 attempt image from the gallery?");
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(cards).toHaveCount(1);
  await expect(first.locator(".expectation-attempt-media")).toContainText("Image deleted");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toBeHidden();
  const status = panel.locator("#expectation-status");
  await expect(status).toContainText("Passed · 100/100");
  await expect(status).toContainText("Prompt applied");
  expect(errors).toEqual([]);
});

test("Generate with Creative Direction verifies once and queues the rest of the batch", async ({ page }) => {
  test.setTimeout(150_000);
  page.setDefaultTimeout(20_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "expectations.generate");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  const token = uniqueToken();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(`a lighthouse keeper ${token}`);
  const panel = await openExpectations(page);
  await panel.getByRole("switch", { name: "Use Creative Direction" }).check();
  await panel.locator("#creative-direction").fill("cinematic film still");
  await enableExpectations(panel, [`A lighthouse ${token}`]);
  await expect(page.locator("#prompt-pipeline-flow")).toContainText("Refine → Vision check (≤5) → Image");
  await page.locator("#generation-quantity").fill("2");
  await page.locator("#generation-quantity").blur();

  const accepted = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/prompt-assistant/checks" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Generate" }).click();
  const body = (await accepted).request().postDataJSON();
  expect(body.purpose).toBe("generate");
  expect(body.items).toHaveLength(2);
  const dialog = page.locator("#expectation-check-dialog");
  await expect(dialog.locator(".expectation-banner.is-passed")).toContainText("1 more", { timeout: 60_000 });
  await expect(dialog.locator(".expectation-banner")).toContainText("That image is the first of your 2");
  const cards = page.locator('#gallery [data-gallery-card="generation"]');
  await expect(cards).toHaveCount(2);
  const latest = (await (await page.request.get("/api/prompt-assistant/checks/latest")).json()).check;
  expect(latest.queued.generation_ids).toHaveLength(1);
  expect(errors).toEqual([]);
});

test("a running check stops, restores after reload, and fits a narrow screen", async ({ page }, testInfo) => {
  test.setTimeout(150_000);
  page.setDefaultTimeout(20_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInFreshUser(page, "expectations.stop");
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  const token = uniqueToken();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(`an orchard in fog ${token}`);
  const panel = await openExpectations(page);
  await panel.locator("#creative-direction").fill("muted watercolor");
  await enableExpectations(panel, [`Apple trees ${token}`, `Golden fog ${NEVER_PASSES} ${token}`]);
  await panel.getByRole("button", { name: "Apply & verify" }).click();
  const dialog = page.locator("#expectation-check-dialog");
  await expect(dialog.locator('[data-attempt-number="1"] .expectation-pill')).toHaveText("Not met", { timeout: 60_000 });

  await page.setViewportSize({ width: 360, height: 740 });
  await expect(dialog.getByRole("button", { name: "Stop" })).toBeInViewport();
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await dialog.locator(".expectation-check-content").evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ animations: "disabled", path: testInfo.outputPath("expectations-narrow.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await dialog.getByRole("button", { name: "Stop" }).click();
  await expect(dialog.locator("#expectation-check-title .expectation-pill")).toHaveText("Stopped");
  await expect(dialog.locator(".expectation-banner.is-stopped")).toContainText("You stopped this check");
  await expect(dialog.getByRole("button", { name: "Use latest prompt" })).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("#generate-button")).toBeEnabled();

  await page.reload();
  const restored = page.locator("#expectation-status");
  await expandCreativeDirection(page);
  await expect(restored).toContainText("Stopped at attempt");
  await restored.getByRole("button", { name: "View" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("#expectation-check-title .expectation-pill")).toHaveText("Stopped");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await restored.getByRole("button", { name: "Dismiss expectation check status" }).click();
  await expect(restored).toBeHidden();
  await page.reload();
  await expandCreativeDirection(page);
  await expect(page.locator("#expectations-enabled")).toBeChecked();
  await expect(page.locator("#expectation-status")).toBeHidden();
  expect(errors).toEqual([]);
});

function uniqueToken() {
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

async function expandCreativeDirection(page) {
  const panel = page.locator("#generation-panel");
  const trigger = panel.getByRole("button", { name: "Creative Direction", exact: true });
  await expect(trigger).toBeVisible();
  if ((await trigger.getAttribute("aria-expanded")) !== "true") await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  return panel;
}

async function openExpectations(page) {
  const panel = await expandCreativeDirection(page);
  const details = panel.locator(".prompt-expectations");
  if (!(await details.evaluate((node) => node.open))) await details.locator("summary").click();
  // Vision availability arrives from the cached router health.
  await expect(panel.locator("#expectations-enabled")).toBeEnabled({ timeout: 45_000 });
  return panel;
}

async function enableExpectations(panel, expectations) {
  await panel.locator("#expectations-enabled").check();
  await expect(panel.locator("[data-expectations-badge]")).toHaveText(/^On · /u);
  await panel.locator("#creative-direction-expectations").fill(expectations.join("\n"));
  await expect(panel.locator("[data-expectations-count]")).toHaveText(`${expectations.length} of 12 expectations`);
}

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
