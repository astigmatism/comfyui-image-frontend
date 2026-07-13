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

async function openAccountMenu(page) {
  const accountMenu = page.locator(".account-menu");
  if ((await accountMenu.getAttribute("open")) === null) {
    await accountMenu.locator("summary").click();
  }
}

test("bootstrap, user administration, generation, progressive card, recall, and scale persistence", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminTemporary123!");
  await setForcedPassword(page, "E2EAdminPermanent123!");

  await openAccountMenu(page);
  await page.getByRole("menuitem", { name: "Administration" }).click();
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();
  await page.getByLabel("Username", { exact: true }).fill("artist.one");
  await page.getByLabel("Temporary password").fill("E2EUserTemporary123!");
  await page.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByRole("cell", { name: "artist.one" })).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();

  await openAccountMenu(page);
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await signIn(page, "artist.one", "E2EUserTemporary123!");
  await setForcedPassword(page, "E2EUserPermanent123!");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("slow multi lighthouse at dusk");
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.locator(".gallery-card")).toHaveCount(1);
  await expect(page.locator(".gallery-card .card-media img")).toBeVisible();
  await expect(page.locator(".gallery-card")).toHaveClass(/status-(running|succeeded)/);
  await expect(page.locator(".gallery-card .batch-count")).toHaveText("2");

  const cardMedia = page.locator(".gallery-card .card-media").first();
  const detailDialog = page.locator("#detail-dialog");
  await cardMedia.click();
  await expect(detailDialog).toHaveAttribute("open", "");
  await detailDialog.getByRole("button", { name: "Close details" }).click();
  await expect(detailDialog).not.toHaveAttribute("open", "");
  await cardMedia.click();
  await expect(detailDialog).toHaveAttribute("open", "");
  await detailDialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(detailDialog).not.toHaveAttribute("open", "");

  const footer = page.locator(".gallery-card .card-footer").first();
  await expect(footer.locator("button")).toHaveCount(1);
  await expect(footer.getByRole("button", { name: "Recall settings" })).toBeVisible();
  await expect(footer.locator(".card-metadata")).toContainText("Fake Progressive Workflow ·");
  await expect(footer).not.toContainText(/seed|Complete|Running|slow multi/i);

  await page.locator("#prompt-assistant > summary").click();
  const cardCountBeforeCompose = await page.locator(".gallery-card").count();
  await page.getByRole("textbox", { name: "Creative direction", exact: true }).fill("cinematic blue hour");
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

test("resolution canvas snaps all three handles and synchronizes its details and inputs", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminPermanent123!");

  const grid = page.locator("[data-resolution-grid]");
  await grid.scrollIntoViewIfNeeded();
  const initialGridBox = await grid.boundingBox();
  const panelBox = await page.locator(".panel-scroll").boundingBox();
  expect(initialGridBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  expect(initialGridBox.width).toBeGreaterThan(panelBox.width - 40);

  const dragHandle = async (name, targetWidthFraction, targetHeightFraction) => {
    const handle = grid.locator(`[data-resolution-handle="${name}"]`);
    await handle.scrollIntoViewIfNeeded();
    const gridBox = await grid.boundingBox();
    const handleBox = await handle.boundingBox();
    expect(gridBox).not.toBeNull();
    expect(handleBox).not.toBeNull();
    const targetX = gridBox.x + gridBox.width * targetWidthFraction;
    const targetY = gridBox.y + gridBox.height * (1 - targetHeightFraction);
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(targetX, targetY, { steps: 4 });
    await page.mouse.up();
  };

  await dragHandle("both", 0.5, 0.78125);
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("1024");
  await expect(page.getByLabel("Height", { exact: true })).toHaveValue("1600");
  await expect(page.locator("[data-resolution-summary]")).toHaveText("1024 × 1600 · 1.64 MP · 16:25");

  await dragHandle("width", 0.75, 0.5);
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("1536");
  await expect(page.getByLabel("Height", { exact: true })).toHaveValue("1600");

  await dragHandle("height", 0.5, 0.5);
  await expect(page.getByLabel("Width", { exact: true })).toHaveValue("1536");
  await expect(page.getByLabel("Height", { exact: true })).toHaveValue("1024");
  await expect(page.locator("[data-resolution-summary]")).toHaveText("1536 × 1024 · 1.57 MP · 3:2");
});

test("failed and cancelled attempts remain one-card, recallable history", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("please fail after checkpoint");
  await page.getByRole("button", { name: "Generate" }).click();
  const card = page.locator(".gallery-card").first();
  await expect(card.locator(".media-status")).toContainText("Failed");
  await expect(card.getByRole("button", { name: "Recall settings" })).toBeEnabled();
  await expect(card).toHaveCount(1);
});

test("working card reserves final aspect ratio and cancels in place", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await page.getByLabel("Width", { exact: true }).fill("384");
  await page.getByLabel("Height", { exact: true }).fill("512");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow cancellation sample");
  await page.getByRole("button", { name: "Generate" }).click();

  const card = page.locator(".gallery-card").first();
  await expect(card).toHaveClass(/status-running/);
  await expect(card.locator(".card-media img")).toBeVisible();
  await expect(card.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  const generationId = await card.getAttribute("data-generation-id");

  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(card.locator(".media-status")).toContainText("Cancelled generation");
  await expect(card.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
  await expect(page.locator(`.gallery-card[data-generation-id="${generationId}"]`)).toHaveCount(1);
  await expect(card.getByRole("button", { name: "Recall settings" })).toBeEnabled();
  const frame = await card.locator(".card-media-frame").boundingBox();
  expect(frame).not.toBeNull();
  expect(Math.abs(frame.width / frame.height - 384 / 512)).toBeLessThan(0.02);
});
