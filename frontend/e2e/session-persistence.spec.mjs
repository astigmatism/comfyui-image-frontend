import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

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

async function signInAdminWithCurrentFixturePassword(page) {
  let responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/auth/login" &&
      response.request().method() === "POST",
  );
  await signIn(page, "admin", "E2EAdminPermanent123!");
  if ((await responsePromise).ok()) return;

  responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/auth/login" &&
      response.request().method() === "POST",
  );
  await signIn(page, "admin", "E2EAdminTemporary123!");
  expect((await responsePromise).ok()).toBe(true);
  await setForcedPassword(page, "E2EAdminPermanent123!");
}

async function selectPublishedSource(page, name) {
  const selector = page.locator("#workflow-source");
  await selector.click();
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog).toBeVisible();
  const workflow = dialog.locator("[data-source-workflow-choice]");
  const option = workflow.locator("option").filter({ hasText: name });
  await expect(option).toHaveCount(1);
  const value = await option.getAttribute("value");
  expect(value).toBeTruthy();
  await workflow.selectOption(value);
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(selector).toHaveAttribute("data-source-key", value);
  await expect(selector).toBeFocused();
}

test("control bar values, active source, and section states persist across reload", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");

  const sectionTrigger = (key) =>
    page.locator(`[data-control-section="${key}"] .control-section-trigger`);

  // Defaults: prompt, seed, and resolution are expanded; Creative Direction and
  // LoRAs start collapsed.
  await expect(sectionTrigger("prompt")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("seed")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("resolution")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("creative-direction")).toHaveAttribute("aria-expanded", "false");
  await expect(sectionTrigger("group-loras")).toHaveAttribute("aria-expanded", "false");

  // Change the control values.
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("persistence lighthouse");
  await page.getByLabel("Random seed", { exact: true }).uncheck();
  await page.getByLabel("Seed value", { exact: true }).fill("987654321");
  await page.getByRole("spinbutton", { name: "Width", exact: true }).fill("1280");
  await page.getByRole("spinbutton", { name: "Height", exact: true }).fill("960");

  // LoRA strength lives in the collapsed LoRAs section.
  await sectionTrigger("group-loras").click();
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("1.25");

  // Creative Direction lives in its own collapsed section.
  await sectionTrigger("creative-direction").click();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("dusk harbor");

  // Flip the seed section against its default while Creative Direction stays open.
  await sectionTrigger("seed").click();
  await expect(sectionTrigger("seed")).toHaveAttribute("aria-expanded", "false");
  await expect(sectionTrigger("creative-direction")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("group-loras")).toHaveAttribute("aria-expanded", "true");

  await expect(page.locator(".shared-settings-status")).toContainText("Settings saved across devices");
  await page.reload();

  // The active source, its values, and the section states all come back.
  await expect(page.locator("#workflow-source")).toContainText("Moody Krea 2 Mix V4");
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(
    "persistence lighthouse",
  );
  await expect(page.getByLabel("Random seed", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Seed value", { exact: true })).toHaveValue("987654321");
  await expect(page.getByRole("spinbutton", { name: "Width", exact: true })).toHaveValue("1280");
  await expect(page.getByRole("spinbutton", { name: "Height", exact: true })).toHaveValue("960");
  await expect(page.getByRole("textbox", { name: "Creative Direction", exact: true })).toHaveValue(
    "dusk harbor",
  );

  // Section preferences persist: the seed section stays collapsed, and the
  // sections we opened (Creative Direction, LoRAs) stay expanded. LoRAs also
  // keeps its strength value.
  await expect(sectionTrigger("seed")).toHaveAttribute("aria-expanded", "false");
  await expect(sectionTrigger("creative-direction")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("group-loras")).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue(
    "1.25",
  );
});
