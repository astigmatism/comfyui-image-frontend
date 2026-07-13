import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

async function signIn(page, username, password) {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function setForcedPassword(page, password) {
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password").fill(password);
  await page.getByLabel("Confirm new password").fill(password);
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.locator(".gallery-viewport")).toBeVisible();
}

test("bootstrap, user administration, generation, progressive card, recall, and scale persistence", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminTemporary123!");
  await setForcedPassword(page, "E2EAdminPermanent123!");

  await page.locator('summary[aria-label="Account menu"]').click();
  await page.getByRole("menuitem", { name: "Administration" }).click();
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();
  await page.getByLabel("Username", { exact: true }).fill("artist.one");
  await page.getByLabel("Temporary password").fill("E2EUserTemporary123!");
  await page.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByRole("cell", { name: "artist.one" })).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();

  await page.locator('summary[aria-label="Account menu"]').click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await signIn(page, "artist.one", "E2EUserTemporary123!");
  await setForcedPassword(page, "E2EUserPermanent123!");

  const prompt = page.getByLabel("Prompt");
  await prompt.fill("slow multi lighthouse at dusk");
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.locator(".gallery-card")).toHaveCount(1);
  await expect(page.locator(".gallery-card .card-media img")).toBeVisible();
  await expect(page.locator(".gallery-card .media-status")).toContainText(/Running|Complete/);
  await expect(page.locator(".gallery-card .batch-count")).toHaveText("2");

  const footer = page.locator(".gallery-card .card-footer").first();
  await expect(footer.locator("button")).toHaveCount(1);
  await expect(footer.getByRole("button", { name: "Recall settings" })).toBeVisible();
  await expect(footer.locator(".card-metadata")).toContainText("Fake Progressive Workflow ·");
  await expect(footer).not.toContainText(/seed|Complete|Running|slow multi/i);

  await page.locator("#prompt-assistant > summary").click();
  const cardCountBeforeCompose = await page.locator(".gallery-card").count();
  await page.getByLabel("Creative direction").fill("cinematic blue hour");
  await page.getByRole("button", { name: "Compose Prompt" }).click();
  await expect(prompt).toHaveValue(/cinematic blue hour/);
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  await footer.getByRole("button", { name: "Recall settings" }).click();
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  const scale = page.locator("#gallery-scale");
  await scale.fill("100");
  await scale.dispatchEvent("change");
  await expect(page.locator("#gallery")).toHaveClass(/gallery-full/);
  await page.waitForTimeout(400);
  await page.reload();
  await expect(page.locator("#gallery-scale")).toHaveValue("100");
});

test("failed and cancelled attempts remain one-card, recallable history", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await page.getByLabel("Prompt").fill("please fail after checkpoint");
  await page.getByRole("button", { name: "Generate" }).click();
  const card = page.locator(".gallery-card").first();
  await expect(card.locator(".media-status")).toContainText("Failed");
  await expect(card.getByRole("button", { name: "Recall settings" })).toBeEnabled();
  await expect(card).toHaveCount(1);
});
