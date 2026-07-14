import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

async function signIn(page, username, password) {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
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
  await selector.click();
  const option = page.locator("[data-primary-source-key]").filter({ hasText: name });
  await expect(option).toHaveCount(1);
  const value = await option.getAttribute("data-primary-source-key");
  expect(value).toBeTruthy();
  await option.click();
  await expect(selector).toHaveAttribute("data-source-key", value);
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
  return response;
}

test("bootstrap, user administration, generation, progressive card, recall, and scale persistence", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminTemporary123!");
  await setForcedPassword(page, "E2EAdminPermanent123!");

  await openAccountMenu(page);
  await page.getByRole("menuitem", { name: "Administration" }).click();
  await expect(page.getByRole("heading", { name: "Administration" })).toBeVisible();
  await expect(page.getByLabel("Temporary password")).toHaveAttribute("minlength", "8");
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
  await expect(page.locator(".gallery-card")).toHaveClass(/status-succeeded/);

  const cardMedia = page.locator(".gallery-card .card-media").first();
  const photoViewer = page.locator("#photo-viewer");
  const detailDialog = page.locator("#detail-dialog");
  await cardMedia.click();
  await expect(photoViewer).toHaveAttribute("open", "");
  const fullscreenHost = photoViewer.locator(".photo-viewer-host");
  await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
  const viewerMedia = photoViewer.locator(".photo-viewer-media");
  const viewerImage = viewerMedia.locator("img");
  const sizingControls = photoViewer.getByRole("group", { name: "Image sizing" });
  const fitButton = sizingControls.getByRole("button", { name: "Fit" });
  const fillButton = sizingControls.getByRole("button", { name: "Fill" });
  const fullscreenButton = photoViewer.getByRole("button", { name: "Full screen", exact: true });
  await expect(viewerImage).toBeVisible();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fit");
  await expect(viewerImage).toHaveCSS("object-fit", "contain");
  await expect(fitButton).toHaveAttribute("aria-pressed", "true");
  await expect(fullscreenButton).toHaveAttribute("aria-pressed", "false");
  await fullscreenButton.click();
  await expect.poll(() => fullscreenHost.evaluate((host) => document.fullscreenElement === host)).toBe(true);
  await expect(photoViewer.getByRole("button", { name: "Exit full screen", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  await fillButton.click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fill");
  await expect(viewerImage).toHaveCSS("object-fit", "contain");
  await expect(fillButton).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThan(1);
  const filledBounds = await viewerImage.boundingBox();
  const filledMediaBounds = await viewerMedia.boundingBox();
  expect(filledBounds).toBeTruthy();
  expect(filledMediaBounds).toBeTruthy();
  expect(filledBounds.width).toBeGreaterThanOrEqual(filledMediaBounds.width - 1);
  expect(filledBounds.height).toBeGreaterThanOrEqual(filledMediaBounds.height - 1);
  const filledCenter = {
    x: filledBounds.x + filledBounds.width / 2,
    y: filledBounds.y + filledBounds.height / 2,
  };
  await page.mouse.move(filledCenter.x, filledCenter.y);
  await page.mouse.down();
  await page.mouse.move(filledCenter.x, filledCenter.y + 24);
  await page.mouse.up();
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-y"))).toBeCloseTo(24, 4);

  await fitButton.click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fit");
  await expect(viewerImage).toHaveAttribute("data-photo-zoom", "1");

  const viewerBounds = await viewerImage.boundingBox();
  const mediaBounds = await viewerMedia.boundingBox();
  expect(viewerBounds).toBeTruthy();
  expect(mediaBounds).toBeTruthy();
  expect(viewerBounds.width).toBeLessThanOrEqual(mediaBounds.width + 1);
  expect(viewerBounds.height).toBeLessThanOrEqual(mediaBounds.height + 1);
  expect(
    Math.abs(viewerBounds.width - mediaBounds.width) < 1 ||
      Math.abs(viewerBounds.height - mediaBounds.height) < 1,
  ).toBe(true);
  const imageCenter = {
    x: viewerBounds.x + viewerBounds.width / 2,
    y: viewerBounds.y + viewerBounds.height / 2,
  };
  await page.mouse.move(imageCenter.x, imageCenter.y);
  await page.mouse.wheel(0, -100);
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThan(1);
  const zoomedIn = Number(await viewerImage.getAttribute("data-photo-zoom"));
  await page.mouse.wheel(0, 100);
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeLessThan(zoomedIn);

  await page.mouse.wheel(0, -200);
  const panXBefore = Number(await viewerImage.getAttribute("data-photo-pan-x"));
  const panYBefore = Number(await viewerImage.getAttribute("data-photo-pan-y"));
  await page.mouse.down();
  await page.mouse.move(imageCenter.x + 36, imageCenter.y + 24);
  await page.mouse.up();
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-x"))).toBeCloseTo(panXBefore + 36, 4);
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-y"))).toBeCloseTo(panYBefore + 24, 4);

  const viewerClose = photoViewer.getByRole("button", { name: "Close image viewer" });
  await expect(viewerClose).toHaveCSS("opacity", "1");
  await page.waitForTimeout(2200);
  await expect(viewerClose).toHaveCSS("opacity", "0");
  await page.mouse.move(80, 80);
  await expect(viewerClose).toHaveCSS("opacity", "1");
  await page.keyboard.press("Escape");
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
  await expect(photoViewer.getByRole("button", { name: "Full screen", exact: true })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await page.keyboard.press("Escape");
  await expect(photoViewer).not.toHaveAttribute("open", "");

  await cardMedia.click();
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
  await viewerClose.click();
  await expect(photoViewer).not.toHaveAttribute("open", "");

  const metadata = page.locator(".gallery-card .card-metadata").first();
  await metadata.click();
  await expect(detailDialog).toHaveAttribute("open", "");
  await detailDialog.getByRole("button", { name: "Close details" }).click();
  await expect(detailDialog).not.toHaveAttribute("open", "");
  await metadata.click();
  await expect(detailDialog).toHaveAttribute("open", "");
  await detailDialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(detailDialog).not.toHaveAttribute("open", "");

  const footer = page.locator(".gallery-card .card-footer").first();
  await expect(footer.locator("button")).toHaveCount(4);
  await expect(footer.getByRole("link", { name: "Download current image" })).toBeVisible();
  await expect(footer.getByRole("button", { name: "Add to Favorites" })).toBeVisible();
  await expect(footer.getByRole("button", { name: "Recall settings" })).toBeVisible();
  await expect(footer.getByRole("button", { name: "Delete generation" })).toBeVisible();
  await expect(footer.locator(".card-metadata")).toHaveText("Generic Landscape");
  await expect(footer).not.toContainText(/seed|Complete|Running|slow multi/i);

  const downloadPromise = page.waitForEvent("download");
  await footer.getByRole("link", { name: "Download current image" }).click();
  await downloadPromise;

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

  await selectPublishedSource(page, "Krea 2 NSFW V4");
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");

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
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  const deleteButton = page
    .locator(".gallery-card")
    .first()
    .getByRole("button", { name: "Delete generation" });
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain("It will disappear from your history and cannot be undone.");
    return dialog.dismiss();
  });
  await deleteButton.click();
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  page.once("dialog", (dialog) => dialog.accept());
  await deleteButton.click();
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose - 1);
  await expect(page.locator("#toast-region")).toContainText("Generation deleted.");
});

test("checked generation sources share comparison settings and report incompatible selections", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Krea 2 NSFW V4");

  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("selected-source comparison lighthouse");
  await page.getByRole("spinbutton", { name: "Width", exact: true }).fill("768");
  await page.getByRole("spinbutton", { name: "Height", exact: true }).fill("1024");
  await page.getByLabel("Seed mode", { exact: true }).selectOption("fixed");
  await page.getByLabel("Seed value", { exact: true }).fill("424242");

  const generationRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      generationRequests.push(request.postDataJSON());
    }
  });

  await page.locator("#workflow-source").click();
  await page
    .getByLabel("Use Generic Landscape with the same prompt, resolution, and seed", { exact: true })
    .check();
  await expect(page.locator("#workflow-source")).toContainText("2 sources");
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.locator("#toast-region")).toContainText(
    "Queued 1 of 2 selected generation sources.",
  );
  await expect(page.locator("#toast-region")).toContainText(
    "Generic Landscape: Does not publish comparison controls for width, height, seed.",
  );
  await expect.poll(() => generationRequests.length).toBe(1);

  const kreaRequest = generationRequests[0];
  expect(kreaRequest).toBeTruthy();
  expect(kreaRequest.parameters).toEqual({
    prompt: "selected-source comparison lighthouse",
    width: 768,
    height: 1024,
    seed: "424242",
  });
  await expect(page.locator(".gallery-card").nth(0)).toContainText("Krea 2 NSFW V4");
});

test("gallery defaults to request initiation order when the page arrives unsorted", async ({
  page,
}) => {
  await page.route("**/api/generations?limit=24", async (route) => {
    const generation = (id, acceptedAt, status) => ({
      id,
      accepted_at: acceptedAt,
      status,
      workflow_display_name: id,
      artifact_count: 0,
      image_count: 0,
      final_artifact_count: 0,
      display_artifact: null,
      recall_available: false,
      cancel_allowed: status === "running",
      is_favorite: false,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          generation("oldest", "2026-07-14T12:00:00Z", "succeeded"),
          generation("newest-active", "2026-07-14T12:02:00Z", "running"),
          generation("previous", "2026-07-14T12:01:00Z", "succeeded"),
        ],
        next_cursor: null,
      }),
    });
  });

  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await expect(page.locator(".gallery-card")).toHaveCount(3);
  const cardIds = await page
    .locator(".gallery-card")
    .evaluateAll((cards) => cards.map((card) => card.dataset.generationId));
  expect(cardIds).toEqual(["newest-active", "previous", "oldest"]);
});

test("focused prompt editor isolates canceled drafts and applies composed prompts and assistant settings", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const dialog = page.locator("#prompt-editor-dialog");
  const openEditor = page.getByRole("button", { name: "Open focused prompt editor" });
  const columnAssistant = page.locator("#prompt-assistant");
  await columnAssistant.locator("summary").click();
  const columnDirection = columnAssistant.getByRole("textbox", {
    name: "Creative direction",
    exact: true,
  });
  const columnCreateMode = columnAssistant.getByRole("radio", {
    name: "Create from creative direction",
  });
  await columnDirection.fill("column direction");
  await columnCreateMode.check();
  await prompt.fill("draft that should remain");
  await openEditor.click();
  await expect(dialog).toHaveAttribute("open", "");

  const focusedPrompt = dialog.getByRole("textbox", { name: "Prompt editor" });
  const focusedDirection = dialog.getByRole("textbox", {
    name: "Creative direction",
    exact: true,
  });
  const focusedRefineMode = dialog.getByRole("radio", { name: "Refine current prompt" });
  const focusedCreateMode = dialog.getByRole("radio", {
    name: "Create from creative direction",
  });
  await expect(focusedPrompt).toHaveValue("draft that should remain");
  await expect(focusedDirection).toHaveValue("column direction");
  await expect(focusedCreateMode).toBeChecked();
  await focusedPrompt.fill("this canceled draft should not be applied");
  await focusedDirection.fill("canceled direction");
  await focusedRefineMode.check();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(prompt).toHaveValue("draft that should remain");
  await expect(columnDirection).toHaveValue("column direction");
  await expect(columnCreateMode).toBeChecked();
  await expect(openEditor).toBeFocused();

  const longPrompt = Array.from({ length: 60 }, () => "cinematic detail").join(" ");
  await openEditor.click();
  await expect(focusedDirection).toHaveValue("column direction");
  await expect(focusedCreateMode).toBeChecked();
  await focusedPrompt.fill(longPrompt);
  await expect(dialog.locator("[data-prompt-word-count]")).toHaveText("120 words");
  await expect(dialog.locator("[data-prompt-character-count]")).toHaveText(
    `${longPrompt.length.toLocaleString()} characters`,
  );
  await focusedDirection.fill("focused assistant direction");
  await focusedRefineMode.check();
  await dialog.getByRole("button", { name: "Compose Prompt" }).click();
  const composedPrompt = `${longPrompt}, focused assistant direction`;
  await expect(focusedPrompt).toHaveValue(composedPrompt);
  await expect(prompt).toHaveValue("draft that should remain");
  await expect(columnDirection).toHaveValue("column direction");
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(prompt).toHaveValue(composedPrompt);
  await expect(columnDirection).toHaveValue("focused assistant direction");
  await expect(columnAssistant.getByRole("radio", { name: "Refine current prompt" })).toBeChecked();
  await expect(openEditor).toBeFocused();
});

test("background service polling does not interrupt focused generation controls", async ({ page }) => {
  await page.addInitScript(() => {
    const setInterval = window.setInterval.bind(window);
    window.setInterval = (handler, delay, ...args) =>
      setInterval(handler, delay === 10_000 ? 100 : delay, ...args);
  });
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminPermanent123!");
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("focus remains here");
  await prompt.press("End");
  const promptPoll = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/services" &&
      response.request().method() === "GET",
  );
  await promptPoll;
  await expect(prompt).toBeFocused();
  await prompt.pressSequentially(" while typing", { delay: 20 });
  await expect(prompt).toHaveValue("focus remains here while typing");

  const iterations = page.getByRole("spinbutton", { name: "Iterations", exact: true });
  await iterations.focus();
  const numericPoll = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/services" &&
      response.request().method() === "GET",
  );
  await numericPoll;
  await expect(iterations).toBeFocused();
});

test("published Krea source exposes choice controls, strict outputs, and the authored result hierarchy", async ({
  page,
}) => {
  await page.goto("/");
  await signIn(page, "admin", "E2EAdminPermanent123!");
  const carriedPrompt = await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .inputValue();
  await selectPublishedSource(page, "Krea 2 NSFW V4");

  await expect(page.locator('[data-control-group="Basic"]')).toHaveCount(4);
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(carriedPrompt);
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

  const grid = page.locator("[data-resolution-grid]");
  await expect(grid).toHaveCount(1);
  await expect(page.locator("[data-resolution-summary]")).toHaveText(
    "1080 × 1920 · 2.07 MP · 9:16",
  );
  const dragHandle = async (name, targetWidthFraction, targetHeightFraction) => {
    const handle = grid.locator(`[data-resolution-handle="${name}"]`);
    await handle.scrollIntoViewIfNeeded();
    const gridBox = await grid.boundingBox();
    const handleBox = await handle.boundingBox();
    expect(gridBox).not.toBeNull();
    expect(handleBox).not.toBeNull();
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      gridBox.x + gridBox.width * targetWidthFraction,
      gridBox.y + gridBox.height * (1 - targetHeightFraction),
      { steps: 4 },
    );
    await page.mouse.up();
  };

  await dragHandle("both", 0.5, 0.78125);
  await expect(width).toHaveValue("1024");
  await expect(height).toHaveValue("1600");
  await expect(page.locator("[data-resolution-summary]")).toHaveText(
    "1024 × 1600 · 1.64 MP · 16:25",
  );
  await dragHandle("width", 0.75, 0.5);
  await expect(width).toHaveValue("1536");
  await expect(height).toHaveValue("1600");
  await dragHandle("height", 0.5, 0.5);
  await expect(width).toHaveValue("1536");
  await expect(height).toHaveValue("1024");

  const sourceKey = await page.locator("#workflow-source").getAttribute("data-source-key");
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
  const publishedLora = sourceResponse.body.interface.inputs.find(({ id }) => id === "lora");
  expect(publishedLora).toMatchObject({
    id: "lora",
    type: "choice",
    label: "LoRA",
    default: "knp_v4_1",
    choices: [
      { value: "knp_v4_1", label: "KNP v4.1", default_strength: 1 },
      { value: "knp_v3_1", label: "KNP v3.1", default_strength: 0.5 },
      { value: "knp_v2", label: "KNP v2", default_strength: 1 },
      {
        value: "mysticxxx_krea2_v1",
        label: "MysticXXX Krea2 v1",
        default_strength: 1,
      },
    ],
  });
  expect(JSON.stringify(publishedLora)).not.toMatch(/safetensors|options_json|binding/i);

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
  await expect(page.locator("[data-resolution-summary]")).toHaveText(
    "1024 × 1600 · 1.64 MP · 16:25",
  );
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
  const advanced = page.locator(".advanced-group");
  await expect(advanced).not.toHaveAttribute("open", "");
  await advanced.locator("summary").click();
  const lora = page.getByRole("combobox", { name: "LoRA", exact: true });
  await expect(lora).toHaveValue("knp_v4_1");
  await expect(lora.locator("option")).toHaveCount(4);
  await expect(lora.locator("option")).toHaveText([
    "KNP v4.1",
    "KNP v3.1",
    "KNP v2",
    "MysticXXX Krea2 v1",
  ]);
  const loraValues = await lora
    .locator("option")
    .evaluateAll((options) => options.map(({ value }) => value));
  expect(loraValues).toEqual([
    "knp_v4_1",
    "knp_v3_1",
    "knp_v2",
    "mysticxxx_krea2_v1",
  ]);
  await expect(advanced).not.toContainText(/safetensors|options_json/i);

  const strength = page.getByRole("spinbutton", { name: "LoRA Strength", exact: true });
  await expect(strength).toHaveValue("1");
  await expect(strength).toHaveAttribute("min", "0");
  await expect(strength).toHaveAttribute("max", "2");
  await expect(strength).toHaveAttribute("step", "0.05");
  await lora.selectOption("knp_v3_1");
  await expect(lora).toHaveValue("knp_v3_1");
  await expect(strength).toHaveValue("0.5");

  const strengthSlider = page.getByRole("slider", { name: "LoRA Strength slider" });
  await expect(strengthSlider).toHaveValue("0.5");
  await strength.fill("0.7");
  await expect(strengthSlider).toHaveValue("0.7");
  await lora.selectOption("knp_v2");
  await expect(strength).toHaveValue("0.7");
  await lora.selectOption("knp_v3_1");
  await expect(strength).toHaveValue("0.7");
  await expect(page.locator(".source-notice.warning")).toHaveCount(0);

  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("multi authored output hierarchy");
  const generationResponse = await generateAndExpectAccepted(page);
  const generationRequest = generationResponse.request().postDataJSON();
  expect(generationRequest.parameters).toMatchObject({
    width: 1024,
    height: 1600,
    lora: "knp_v3_1",
    lora_strength: 0.7,
  });
  expect(JSON.stringify(generationRequest)).not.toMatch(/safetensors|options_json|binding/i);

  const card = page.locator(".gallery-card").first();
  await expect(card).toHaveClass(/status-succeeded/, { timeout: 30_000 });
  await expect(card.locator(".batch-count")).toHaveText("8");
  await card.locator(".card-metadata").click();

  const detailDialog = page.locator("#detail-dialog");
  await expect(detailDialog).toHaveAttribute("open", "");
  const submittedInputs = detailDialog.locator(".generation-inputs");
  await expect(submittedInputs).toContainText("multi authored output hierarchy");
  await expect(submittedInputs).toContainText("1024 × 1600");
  await expect(submittedInputs).toContainText("Seed");
  await expect(submittedInputs).toContainText("KNP v3.1");
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

  const technicalProvenance = detailDialog.locator("details.provenance").filter({
    hasText: "Technical provenance",
  });
  await technicalProvenance.locator("summary").click();
  const effectiveParameters = technicalProvenance
    .getByText("Effective parameters", { exact: true })
    .locator("xpath=following-sibling::dd[1]");
  await expect(effectiveParameters).toContainText('"lora": "knp_v3_1"');
  await expect(effectiveParameters).toContainText('"lora_strength": 0.7');
  await expect(effectiveParameters).not.toContainText(/safetensors|options_json|binding/i);

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
          fields: { lora_strength: "Server-side strength rejection." },
        },
      }),
    });
  });
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.locator("#workflow-source")).toBeDisabled();
  releaseGeneration();
  const advanced = page.locator(".advanced-group");
  await expect(advanced).toHaveAttribute("open", "");
  const strength = page.getByRole("spinbutton", { name: "LoRA Strength", exact: true });
  await expect(strength).toHaveAttribute("aria-invalid", "true");
  await expect(strength).toBeFocused();
  await page.unroute("**/api/generations");

  await selectPublishedSource(page, "Generic Landscape");
  const promptBeforeComposition = await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .inputValue();
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
    promptBeforeComposition,
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

  await card.locator(".card-media").click();
  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect(photoViewer.locator(".photo-viewer-status")).toBeVisible();
  await expect(photoViewer.locator(".photo-viewer-status")).toContainText(/Running|Preparing|image/i);
  const viewedGenerationId = await photoViewer.locator(".photo-viewer-frame").getAttribute("data-photo-generation-id");
  await page.keyboard.press("ArrowRight");
  await expect(photoViewer.locator(".photo-viewer-frame")).not.toHaveAttribute(
    "data-photo-generation-id",
    viewedGenerationId,
  );
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();

  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(card.locator(".media-status")).toContainText("Cancelled generation");
  await expect(card.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
  await expect(page.locator(`.gallery-card[data-generation-id="${generationId}"]`)).toHaveCount(1);
  await expect(card.getByRole("button", { name: "Recall settings" })).toBeEnabled();
  const frame = await card.locator(".card-media-frame").boundingBox();
  expect(frame).not.toBeNull();
  expect(Math.abs(frame.width / frame.height - 384 / 512)).toBeLessThan(0.02);
});
