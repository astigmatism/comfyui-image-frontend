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

async function selectPublishedSource(page, name) {
  const selector = page.locator("#workflow-source");
  const option = selector.locator("option").filter({ hasText: name });
  await expect(option).toHaveCount(1);
  const value = await option.getAttribute("value");
  expect(value).toBeTruthy();
  await selector.selectOption(value);
  await expect(selector).toHaveValue(value);
}

async function generateAndExpectAccepted(page) {
  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations" &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Generate" }).click();
  const response = await responsePromise;
  expect(response.status(), await response.text()).toBe(201);
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
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("slow multi lighthouse at dusk");
  await generateAndExpectAccepted(page);
  await expect(page.locator(".gallery-card")).toHaveCount(1);
  await expect(page.locator(".gallery-card .card-media img")).toBeVisible();
  await expect(page.locator(".gallery-card")).toHaveClass(/status-(running|succeeded)/);
  await expect(page.locator(".gallery-card .batch-count")).toHaveText("4");

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
  await expect(footer.locator("button")).toHaveCount(2);
  await expect(footer.getByRole("button", { name: "Add to Favorites" })).toBeVisible();
  await expect(footer.getByRole("button", { name: "Recall settings" })).toBeVisible();
  await expect(footer.locator(".card-metadata")).toContainText("Generic Landscape ·");
  await expect(footer).not.toContainText(/seed|Complete|Running|slow multi/i);

  await footer.getByRole("button", { name: "Add to Favorites" }).click();
  await expect(footer.getByRole("button", { name: "Remove from Favorites" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  const favoritesDialog = page.locator("#favorites-dialog");
  await expect(favoritesDialog).toHaveAttribute("open", "");
  await expect(favoritesDialog.locator(".favorite-item")).toHaveCount(1);
  await expect(favoritesDialog.locator(".favorite-thumbnail img")).toBeVisible();
  await expect(favoritesDialog.locator(".favorite-prompt")).toHaveText(
    "slow multi lighthouse at dusk",
  );
  await favoritesDialog.getByRole("button", { name: "Close Favorites" }).click();

  await prompt.fill("temporary controls");
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await favoritesDialog.getByRole("button", { name: "Recall", exact: true }).click();
  await expect(favoritesDialog).not.toHaveAttribute("open", "");
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");

  await page.locator("#prompt-assistant > summary").click();
  const cardCountBeforeCompose = await page.locator(".gallery-card").count();
  await page.getByRole("textbox", { name: "Creative direction", exact: true }).fill("cinematic blue hour");
  await page.getByRole("button", { name: "Compose Prompt" }).click();
  await expect(prompt).toHaveValue(/cinematic blue hour/);
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  await footer.getByRole("button", { name: "Recall settings" }).click();
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await favoritesDialog.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(favoritesDialog.getByRole("heading", { name: "No favorites yet" })).toBeVisible();
  await favoritesDialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(footer.getByRole("button", { name: "Add to Favorites" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  const scale = page.locator("#gallery-scale");
  await scale.fill("100");
  await scale.dispatchEvent("change");
  await expect(page.locator("#gallery")).toHaveClass(/gallery-full/);
  await page.waitForTimeout(400);
  await page.reload();
  await expect(page.locator("#gallery-scale")).toHaveValue("100");
});

test("published Krea source exposes strict outputs and the complete authored result hierarchy", async ({
  page,
}) => {
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminPermanent123!");
  await selectPublishedSource(page, "Krea 2 NSFW V4");

  await expect(page.locator('[data-control-group="Basic"]')).toHaveCount(5);
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(
    "a tree with chickens",
  );
  await expect(page.locator('[data-control-id*="negative" i]')).toHaveCount(0);

  const width = page.getByRole("spinbutton", { name: "Width", exact: true });
  const height = page.getByRole("spinbutton", { name: "Height", exact: true });
  await expect(width).toHaveValue("1080");
  await expect(width).toHaveAttribute("min", "16");
  await expect(width).toHaveAttribute("max", "2048");
  await expect(width).toHaveAttribute("step", "8");
  await expect(height).toHaveValue("1920");
  await expect(height).toHaveAttribute("min", "16");
  await expect(height).toHaveAttribute("max", "2048");
  await expect(height).toHaveAttribute("step", "8");
  await expect(page.locator("[data-resolution-grid]")).toHaveCount(0);

  const sourceKey = await page.locator("#workflow-source").inputValue();
  const sourceResponse = await page.evaluate(async (key) => {
    const response = await fetch(`/api/workflows/${encodeURIComponent(key)}`);
    return { status: response.status, body: await response.json() };
  }, sourceKey);
  expect(sourceResponse.status).toBe(200);
  expect(
    sourceResponse.body.interface.outputs.map(({ id, role, kind }) => ({ id, role, kind })),
  ).toEqual([
    { id: "base", role: "preview", kind: "image" },
    { id: "second_pass", role: "comparison", kind: "image" },
    { id: "final", role: "final", kind: "image" },
  ]);

  const seedMode = page.getByLabel("Seed mode", { exact: true });
  const seedValue = page.getByLabel("Seed value", { exact: true });
  await expect(seedMode).toHaveValue("random");
  await expect(seedValue).toBeDisabled();
  await seedMode.selectOption("fixed");
  await expect(seedValue).toBeEnabled();
  await expect(seedValue).toHaveAttribute("data-maximum", "1125899906842624");
  await seedValue.fill("1125899906842624");

  const upscale = page.getByLabel("Enable SeedVR2 upscale", { exact: true });
  await expect(upscale).not.toBeChecked();
  await upscale.check();
  await expect(upscale).toBeChecked();
  await expect(upscale.locator("xpath=..")).toContainText("On");

  await width.fill("1024");
  await height.fill("1600");
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
  const advanced = page.locator(".advanced-group");
  await expect(advanced).not.toHaveAttribute("open", "");
  await advanced.locator("summary").click();
  const strength = page.getByRole("spinbutton", { name: "KNP V4.1 strength", exact: true });
  await expect(strength).toHaveValue("1");
  await expect(strength).toHaveAttribute("min", "0");
  await expect(strength).toHaveAttribute("max", "2");
  await expect(strength).toHaveAttribute("step", "0.05");
  const strengthSlider = page.getByRole("slider", { name: "KNP V4.1 strength slider" });
  await expect(strengthSlider).toHaveValue("1");
  await strengthSlider.fill("1.25");
  await expect(strength).toHaveValue("1.25");
  await strength.fill("0.5");
  await expect(strengthSlider).toHaveValue("0.5");
  await expect(page.locator(".source-notice.warning")).toHaveCount(0);

  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("multi authored output hierarchy");
  await generateAndExpectAccepted(page);

  const card = page.locator(".gallery-card").first();
  await expect(card).toHaveClass(/status-succeeded/, { timeout: 30_000 });
  await expect(card.locator(".batch-count")).toHaveText("8");
  await card.locator(".card-media").click();

  const detailDialog = page.locator("#detail-dialog");
  await expect(detailDialog).toHaveAttribute("open", "");
  const primary = detailDialog.locator(".result-image-group").filter({
    hasText: "Primary result",
  });
  const prototypes = detailDialog.locator(".result-image-group").filter({
    hasText: "Prototypes and earlier passes",
  });
  const comparisons = detailDialog.locator(".result-image-group").filter({
    hasText: "Comparisons and alternates",
  });
  const additionalImages = detailDialog.locator(".result-image-group").filter({
    hasText: "Additional images",
  });

  await expect(primary.locator("figure")).toHaveCount(2);
  await expect(primary).toContainText("Final");
  await expect(primary).toContainText("batch 1");
  await expect(primary).toContainText("batch 2");
  await expect(prototypes.locator("figure")).toHaveCount(2);
  await expect(prototypes).toContainText("Base");
  await expect(comparisons.locator("figure")).toHaveCount(2);
  await expect(comparisons).toContainText("Second pass");
  await expect(additionalImages.locator("figure")).toHaveCount(2);
  await expect(detailDialog.locator("a.artifact-download")).toHaveCount(8);

  const declaredMetadata = detailDialog.locator("details.result-details");
  await declaredMetadata.locator("summary").click();
  await expect(declaredMetadata.locator("pre")).toContainText('"output_id": "base"');
  await expect(declaredMetadata.locator("pre")).toContainText('"output_id": "second_pass"');
  await expect(declaredMetadata.locator("pre")).toContainText('"output_id": "final"');
  await expect(declaredMetadata.locator("pre")).toContainText('"cardinality": "many"');

  const additionalOutputs = detailDialog.locator(".result-section").filter({
    hasText: "Additional outputs",
  });
  await expect(additionalOutputs).toContainText('"900"');
  await expect(additionalOutputs).toContainText('"901"');
  await expect(additionalOutputs).toContainText("complete native text result");
  await expect(additionalOutputs).toContainText("asset_sha256");

  const rawHistory = detailDialog.locator("details.raw-history");
  await rawHistory.locator("summary").click();
  await expect(rawHistory.locator("pre")).toContainText('"outputs"');
  await expect(rawHistory.locator("pre")).toContainText("publisher_timing");
  await expect(rawHistory.locator("pre")).toContainText("complete native text result");
  await detailDialog.getByRole("button", { name: "Close", exact: true }).click();
});

test("backend field errors disclose Advanced controls and stale compositions do not cross sources", async ({
  page,
}) => {
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminPermanent123!");
  await selectPublishedSource(page, "Krea 2 NSFW V4");

  let releaseGeneration;
  const generationGate = new Promise((resolve) => {
    releaseGeneration = resolve;
  });
  await page.route("**/api/generations", async (route) => {
    await generationGate;
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "parameter_validation_failed",
          message: "Published parameters were rejected.",
          fields: { knpv4_1_strength: "Server-side strength rejection." },
        },
      }),
    });
  });
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.locator("#workflow-source")).toBeDisabled();
  releaseGeneration();
  const advanced = page.locator(".advanced-group");
  await expect(advanced).toHaveAttribute("open", "");
  const strength = page.getByRole("spinbutton", { name: "KNP V4.1 strength", exact: true });
  await expect(strength).toHaveAttribute("aria-invalid", "true");
  await expect(strength).toBeFocused();
  await page.unroute("**/api/generations");

  await selectPublishedSource(page, "Generic Landscape");
  await page.locator("#prompt-assistant > summary").click();
  await page.getByRole("textbox", { name: "Creative direction", exact: true }).fill("stale request");
  let releaseComposition;
  const compositionGate = new Promise((resolve) => {
    releaseComposition = resolve;
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await compositionGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        composition_id: "stale-composition",
        prompt: "this prompt belongs to the previous source",
        model: "fake-model",
      }),
    });
  });
  await page.getByRole("button", { name: "Compose Prompt" }).click();
  await expect(page.getByRole("button", { name: "Composing…" })).toBeDisabled();
  await selectPublishedSource(page, "Krea 2 NSFW V4");
  releaseComposition();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(
    "a tree with chickens",
  );
  await expect(page.locator("#toast-region")).toContainText("was not applied");
  await page.unroute("**/api/prompt-assistant/compose");
});

test("failed and cancelled attempts remain one-card, recallable history", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await selectPublishedSource(page, "Generic Landscape");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("please fail after checkpoint");
  await generateAndExpectAccepted(page);
  const card = page.locator(".gallery-card").first();
  await expect(card.locator(".media-status")).toContainText("Failed");
  await expect(card.getByRole("button", { name: "Recall settings" })).toBeEnabled();
  await expect(card).toHaveCount(1);
});

test("working card reserves final aspect ratio and cancels in place", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await selectPublishedSource(page, "Krea 2 NSFW V4");
  await page.getByRole("spinbutton", { name: "Width", exact: true }).fill("384");
  await page.getByRole("spinbutton", { name: "Height", exact: true }).fill("512");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow cancellation sample");
  await generateAndExpectAccepted(page);

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
