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
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog).toBeVisible();
  const row = dialog.locator("[data-source-row-key]").filter({ hasText: name });
  await expect(row).toHaveCount(1);
  const option = row.locator("[data-source-primary-key]");
  const value = await option.getAttribute("data-source-primary-key");
  expect(value).toBeTruthy();
  await option.check();
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
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
  const sizingControl = photoViewer.getByRole("group", { name: "Image sizing" });
  const playbackControl = photoViewer.getByRole("group", { name: "Playback mode" });
  const oneToOneButton = sizingControl.getByRole("button", { name: "1:1", exact: true });
  const fitButton = sizingControl.getByRole("button", { name: "Fit", exact: true });
  const fillButton = sizingControl.getByRole("button", { name: "Fill", exact: true });
  const fullscreenButton = photoViewer.getByRole("button", { name: "Full screen", exact: true });
  await expect(viewerImage).toBeVisible();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fill");
  await expect(viewerImage).toHaveCSS("object-fit", "contain");
  await expect(fitButton).toHaveAttribute("aria-pressed", "false");
  await expect(fillButton).toHaveAttribute("aria-pressed", "true");
  await expect(oneToOneButton).toHaveAttribute("aria-pressed", "false");
  await expect(sizingControl.getByRole("switch", { name: "Fill image" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await expect(fullscreenButton).toHaveAttribute("aria-pressed", "false");
  await fullscreenButton.click();
  await expect.poll(() => fullscreenHost.evaluate((host) => document.fullscreenElement === host)).toBe(true);
  await expect(photoViewer.getByRole("button", { name: "Exit full screen", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const oneToOneBounds = await oneToOneButton.boundingBox();
  const fitFillBounds = await photoViewer.locator(".photo-viewer-mode").boundingBox();
  expect(oneToOneBounds).toBeTruthy();
  expect(fitFillBounds).toBeTruthy();
  expect(oneToOneBounds.x + oneToOneBounds.width).toBeLessThan(fitFillBounds.x);
  expect(fitFillBounds.x - (oneToOneBounds.x + oneToOneBounds.width)).toBeLessThanOrEqual(12);

  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThanOrEqual(1);
  const filledBounds = await viewerImage.boundingBox();
  const filledMediaBounds = await viewerMedia.boundingBox();
  expect(filledBounds).toBeTruthy();
  expect(filledMediaBounds).toBeTruthy();
  expect(filledBounds.width).toBeGreaterThanOrEqual(filledMediaBounds.width - 1);
  expect(filledBounds.height).toBeGreaterThanOrEqual(filledMediaBounds.height - 1);
  expect(Math.abs(filledBounds.y - filledMediaBounds.y)).toBeLessThan(1);

  await fitButton.click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fit");
  await expect(viewerImage).toHaveAttribute("data-photo-zoom", "1");
  await expect(fitButton).toHaveAttribute("aria-pressed", "true");
  await expect(fillButton).toHaveAttribute("aria-pressed", "false");
  const fittedBounds = await viewerImage.boundingBox();
  expect(fittedBounds.width).toBeLessThanOrEqual(filledMediaBounds.width + 1);
  expect(fittedBounds.height).toBeLessThanOrEqual(filledMediaBounds.height + 1);
  await fillButton.click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fill");
  await expect(fillButton).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThanOrEqual(1);
  await expect.poll(async () => (await viewerImage.boundingBox()).y).toBeCloseTo(filledMediaBounds.y, 0);

  const visibleCenter = {
    x: filledMediaBounds.x + filledMediaBounds.width / 2,
    y: filledMediaBounds.y + filledMediaBounds.height / 2,
  };
  const basePanY = Number(await viewerImage.getAttribute("data-photo-pan-y"));
  await page.mouse.move(visibleCenter.x, visibleCenter.y);
  await page.mouse.down();
  await page.mouse.move(visibleCenter.x, visibleCenter.y + 24);
  await page.mouse.up();
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-y"))).toBeCloseTo(basePanY + 24, 4);

  await page.mouse.move(visibleCenter.x, visibleCenter.y);
  const verticalPanBefore = Number(await viewerImage.getAttribute("data-photo-pan-y"));
  const zoomBeforeVerticalScroll = Number(await viewerImage.getAttribute("data-photo-zoom"));
  await page.mouse.wheel(0, 100);
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-y"))).toBeCloseTo(verticalPanBefore - 100, 4);
  await expect(viewerImage).toHaveAttribute("data-photo-zoom", String(zoomBeforeVerticalScroll));

  const horizontalPanBefore = Number(await viewerImage.getAttribute("data-photo-pan-x"));
  await page.mouse.wheel(80, 0);
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-x"))).toBeCloseTo(horizontalPanBefore - 80, 4);

  const baseZoom = Number(await viewerImage.getAttribute("data-photo-zoom"));
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -100);
  await page.keyboard.up("Control");
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThan(baseZoom);
  const zoomedIn = Number(await viewerImage.getAttribute("data-photo-zoom"));
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, 100);
  await page.keyboard.up("Control");
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeLessThan(zoomedIn);

  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -200);
  await page.keyboard.up("Control");
  const panXBefore = Number(await viewerImage.getAttribute("data-photo-pan-x"));
  const panYBefore = Number(await viewerImage.getAttribute("data-photo-pan-y"));
  await page.mouse.down();
  await page.mouse.move(visibleCenter.x + 36, visibleCenter.y + 24);
  await page.mouse.up();
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-x"))).toBeCloseTo(panXBefore + 36, 4);
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-pan-y"))).toBeCloseTo(panYBefore + 24, 4);

  const naturalSize = await viewerImage.evaluate((image) => ({
    width: image.naturalWidth,
    height: image.naturalHeight,
  }));
  await oneToOneButton.click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "actual");
  await expect(oneToOneButton).toHaveAttribute("aria-pressed", "true");
  await expect(fitButton).toHaveAttribute("aria-pressed", "false");
  await expect(fillButton).toHaveAttribute("aria-pressed", "false");
  await expect(viewerImage).toHaveAttribute("data-photo-pan-x", "0");
  await expect(viewerImage).toHaveAttribute("data-photo-pan-y", "0");
  await expect.poll(async () => (await viewerImage.boundingBox()).width).toBeCloseTo(naturalSize.width, 1);
  await expect.poll(async () => (await viewerImage.boundingBox()).height).toBeCloseTo(naturalSize.height, 1);

  const viewerClose = photoViewer.getByRole("button", { name: "Close image viewer" });
  await expect(viewerClose).toHaveCSS("opacity", "1");
  await page.waitForTimeout(2200);
  await expect(viewerClose).toHaveCSS("opacity", "0");
  await expect(sizingControl).toHaveCSS("opacity", "0");
  await expect(playbackControl).toHaveCSS("opacity", "0");
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

  const cardCountBeforeCompose = await page.locator(".gallery-card").count();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("cinematic blue hour");
  await page.getByRole("button", { name: "Apply Creative Direction" }).click();
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

test("photo viewer slideshow waits for a generation's final completed image", async ({
  page,
  context,
}) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("slideshow baseline");
  const baselineResponse = await generateAndExpectAccepted(page);
  const baseline = await baselineResponse.json();
  const baselineCard = page.locator(`.gallery-card[data-generation-id="${baseline.id}"]`);
  await expect(baselineCard).toHaveClass(/status-succeeded/);
  await baselineCard.locator(".card-media").click();

  const photoViewer = page.locator("#photo-viewer");
  const viewerFrame = photoViewer.locator(".photo-viewer-frame");
  const viewerMedia = photoViewer.locator(".photo-viewer-media");
  const viewerImage = viewerMedia.locator("img");
  const sizingControl = photoViewer.getByRole("group", { name: "Image sizing" });
  const playbackControl = photoViewer.getByRole("group", { name: "Playback mode" });
  await expect(viewerFrame).toHaveAttribute("data-photo-generation-id", baseline.id);
  await playbackControl.getByRole("button", { name: "Slideshow", exact: true }).click();
  await expect(playbackControl.getByRole("button", { name: "Hold", exact: true })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await expect(
    playbackControl.getByRole("button", { name: "Slideshow", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(playbackControl.getByRole("switch", { name: "Slideshow mode" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fill");
  await expect(sizingControl.getByRole("button", { name: "Fill", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(sizingControl.getByRole("button", { name: "Fit", exact: true })).toBeEnabled();
  await sizingControl.getByRole("button", { name: "Fit", exact: true }).click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fit");
  await expect(viewerImage).toHaveAttribute("data-photo-zoom", "1");
  await sizingControl.getByRole("button", { name: "Fill", exact: true }).click();
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fill");
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThan(1);

  const producer = await context.newPage();
  await producer.goto("/");
  await expect(producer.locator(".gallery-viewport")).toBeVisible();
  await selectPublishedSource(producer, "Generic Landscape");
  const producerPrompt = producer.getByRole("textbox", { name: "Prompt", exact: true });
  await producerPrompt.fill("slow slideshow final boundary");
  const nextResponse = await generateAndExpectAccepted(producer);
  const next = await nextResponse.json();
  let progressiveDetail;
  await expect.poll(async () => {
    progressiveDetail = await (await producer.request.get(`/api/generations/${next.id}`)).json();
    return progressiveDetail.status === "running" && progressiveDetail.display_artifact
      ? progressiveDetail.display_artifact.state
      : null;
  }).toBe("provisional");
  expect(progressiveDetail.display_artifact.state).toBe("provisional");
  await expect(viewerFrame).toHaveAttribute("data-photo-generation-id", baseline.id);
  await expect(viewerImage).not.toHaveAttribute("src", progressiveDetail.display_artifact.content_url);

  await expect.poll(async () => {
    const detail = await (await producer.request.get(`/api/generations/${next.id}`)).json();
    return detail.status;
  }).toBe("succeeded");
  const completedDetail = await (await producer.request.get(`/api/generations/${next.id}`)).json();
  expect(completedDetail.status).toBe("succeeded");
  expect(completedDetail.display_artifact.state).toBe("final");
  await expect(viewerFrame).toHaveAttribute("data-photo-generation-id", next.id);
  await expect(viewerImage).toHaveAttribute("src", completedDetail.display_artifact.content_url);
  await expect(viewerMedia).toHaveAttribute("data-photo-view-mode", "fill");
  await expect.poll(async () => Number(await viewerImage.getAttribute("data-photo-zoom"))).toBeGreaterThan(1);

  await page.mouse.move(80, 80);
  await playbackControl.getByRole("button", { name: "Hold", exact: true }).click();
  await expect(playbackControl.getByRole("button", { name: "Hold", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    playbackControl.getByRole("button", { name: "Slideshow", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await producerPrompt.fill("hold this completed slideshow image");
  const heldResponse = await generateAndExpectAccepted(producer);
  const held = await heldResponse.json();
  await expect.poll(async () => {
    const detail = await (await producer.request.get(`/api/generations/${held.id}`)).json();
    return detail.status;
  }).toBe("succeeded");
  await expect(viewerFrame).toHaveAttribute("data-photo-generation-id", next.id);

  await producer.close();
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
});

test("progressive bootstrap renders while optional status is delayed and localizes failures", async ({
  page,
}) => {
  let releaseServices;
  let noteServiceRequest;
  const servicesReleased = new Promise((resolve) => {
    releaseServices = resolve;
  });
  const serviceRequested = new Promise((resolve) => {
    noteServiceRequest = resolve;
  });
  await page.route("**/api/services", async (route) => {
    noteServiceRequest();
    await servicesReleased;
    await route.continue();
  });
  await page.route("**/api/prompt-assistant/status", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "assistant_unavailable",
          message: "Prompt Assistant maintenance.",
          fields: {},
        },
      }),
    });
  });

  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await serviceRequested;

  await expect(page.locator(".gallery-viewport")).toBeVisible({ timeout: 2_000 });
  await expect(page.getByRole("heading", { name: "Application unavailable" })).toHaveCount(0);
  await expect(page.locator("#service-banner")).toContainText("Checking generation service");
  const generateButton = page.getByRole("button", { name: "Generate" });
  await expect(generateButton).toBeDisabled();
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("service status is still pending");
  await expect(generateButton).toBeDisabled();

  releaseServices();
  await expect(page.locator("#assistant-message")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Apply Creative Direction" })).toBeDisabled();
  await expect(generateButton).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Application unavailable" })).toHaveCount(0);
});

test("workflow catalog failure stays local and can be retried", async ({ page }) => {
  let catalogRequests = 0;
  await page.route("**/api/workflows", async (route) => {
    catalogRequests += 1;
    if (catalogRequests === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "workflow_catalog_unavailable",
            message: "Published source catalog maintenance.",
            fields: {},
          },
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);

  await expect(page.locator(".gallery-viewport")).toBeVisible();
  await expect(page.locator("#gallery")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Application unavailable" })).toHaveCount(0);
  await expect(page.locator("#generation-panel .source-notice.warning")).toHaveText(
    "Published source catalog maintenance.",
  );
  await expect(page.getByRole("button", { name: "Generate" })).toBeDisabled();
  await page.getByRole("button", { name: "Retry generation sources" }).click();
  await expect(page.locator("#workflow-source")).not.toHaveAttribute("data-source-key", "");
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
});

test("speech status failure disables voice controls with the service message", async ({ page }) => {
  await page.route("**/api/speech-to-text/status", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "speech_to_text_unavailable",
          message: "Voice input maintenance.",
          fields: {},
        },
      }),
    });
  });

  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  await expect(page.locator(".gallery-viewport")).toBeVisible();
  await expect(page.locator("#gallery")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Application unavailable" })).toHaveCount(0);
  const voiceButtons = page.locator('[data-action="toggle-speech-recording"]');
  await expect(voiceButtons.first()).toBeVisible();
  await expect
    .poll(() =>
      voiceButtons.evaluateAll(
        (buttons) =>
          buttons.length > 0 &&
          buttons.every(
            (button) => button.disabled && button.title === "Voice input maintenance.",
          ),
      ),
    )
    .toBe(true);
});

test("initial gallery snapshot commits before buffered live updates", async ({ page, context }) => {
  test.setTimeout(45_000);
  let releaseGallery;
  let markSnapshotCaptured;
  const galleryReleased = new Promise((resolve) => {
    releaseGallery = resolve;
  });
  const snapshotCaptured = new Promise((resolve) => {
    markSnapshotCaptured = resolve;
  });
  await page.route("**/api/generations?limit=24", async (route) => {
    const snapshot = await route.fetch();
    markSnapshotCaptured();
    await galleryReleased;
    await route.fulfill({ response: snapshot });
  });

  const eventsConnected = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/events",
  );
  let producer = null;
  try {
    await page.goto("/");
    await signInAdminWithCurrentFixturePassword(page);
    await Promise.all([snapshotCaptured, eventsConnected]);
    await expect(page.getByRole("heading", { name: "Loading gallery…" })).toBeVisible();

    producer = await context.newPage();
    await producer.goto("/");
    await selectPublishedSource(producer, "Generic Landscape");
    await producer
      .getByRole("textbox", { name: "Prompt", exact: true })
      .fill("buffered live update after gallery snapshot");
    await expect(producer.getByRole("button", { name: "Generate" })).toBeEnabled();
    const accepted = await generateAndExpectAccepted(producer);
    const generation = await accepted.json();

    const refreshed = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/generations/${generation.id}` &&
        response.request().method() === "GET",
    );
    releaseGallery();
    await refreshed;
    const card = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
    await expect(card).toBeVisible();

    await expect(card).toHaveClass(/status-succeeded/, { timeout: 15_000 });
    page.once("dialog", (dialog) => dialog.accept());
    await card.getByRole("button", { name: "Delete generation" }).click();
    await expect(card).toHaveCount(0);
  } finally {
    releaseGallery();
    await producer?.close();
  }
});

test("failed initial gallery snapshot preserves buffered live generations", async ({
  page,
  context,
}) => {
  test.setTimeout(45_000);
  let releaseGallery;
  let noteGalleryRequest;
  const galleryReleased = new Promise((resolve) => {
    releaseGallery = resolve;
  });
  const galleryRequested = new Promise((resolve) => {
    noteGalleryRequest = resolve;
  });
  await page.route("**/api/generations?limit=24", async (route) => {
    noteGalleryRequest();
    await galleryReleased;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "gallery_unavailable",
          message: "Gallery snapshot maintenance.",
          fields: {},
        },
      }),
    });
  });

  const eventsConnected = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/events",
  );
  let producer = null;
  try {
    await page.goto("/");
    await signInAdminWithCurrentFixturePassword(page);
    await Promise.all([galleryRequested, eventsConnected]);

    producer = await context.newPage();
    await producer.goto("/");
    await selectPublishedSource(producer, "Generic Landscape");
    await producer
      .getByRole("textbox", { name: "Prompt", exact: true })
      .fill("buffered update survives unavailable gallery snapshot");
    await expect(producer.getByRole("button", { name: "Generate" })).toBeEnabled();
    const accepted = await generateAndExpectAccepted(producer);
    const generation = await accepted.json();

    const refreshed = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/generations/${generation.id}` &&
        response.request().method() === "GET",
    );
    releaseGallery();
    await refreshed;

    await expect(page.getByText("Gallery snapshot maintenance.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry gallery" })).toBeVisible();
    const card = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
    await expect(card).toBeVisible();
    await expect(card).toHaveClass(/status-succeeded/, { timeout: 15_000 });

    page.once("dialog", (dialog) => dialog.accept());
    await card.getByRole("button", { name: "Delete generation" }).click();
    await expect(card).toHaveCount(0);
  } finally {
    releaseGallery();
    await producer?.close();
  }
});

test("checked generation sources reuse compatible settings without blocking partial interfaces", async ({
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
  const sourceDialog = page.locator("#source-picker-dialog");
  let genericCheckbox = page.getByLabel(
    "Include Generic Landscape",
    { exact: true },
  );
  await genericCheckbox.check();
  await sourceDialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator("#workflow-source")).not.toContainText("2 sources");

  await page.locator("#workflow-source").click();
  genericCheckbox = page.getByLabel("Include Generic Landscape", { exact: true });
  await expect(genericCheckbox).not.toBeChecked();
  const architectureHeading = sourceDialog.getByRole("columnheader", { name: "Architecture" });
  await architectureHeading.getByRole("button").click();
  await expect(architectureHeading).toHaveAttribute("aria-sort", "ascending");
  await sourceDialog.getByRole("button", { name: "Select all", exact: true }).click();
  genericCheckbox = page.getByLabel("Include Generic Landscape", { exact: true });
  await expect(genericCheckbox).toBeChecked();
  await sourceDialog.getByRole("button", { name: "Deselect all", exact: true }).click();
  genericCheckbox = page.getByLabel("Include Generic Landscape", { exact: true });
  await expect(genericCheckbox).not.toBeChecked();

  const genericSourceKey = await genericCheckbox.getAttribute("data-source-draft-key");
  const primarySourceKey = await page.locator("#workflow-source").getAttribute("data-source-key");
  await genericCheckbox.check();
  await sourceDialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.locator("#workflow-source")).toContainText("2 sources");
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(page.locator("#toast-region")).toContainText(
    "2 generations queued across 2 selected sources.",
  );
  await expect.poll(() => generationRequests.length).toBe(2);

  const kreaRequest = generationRequests.find((request) => request.source_key === primarySourceKey);
  const genericRequest = generationRequests.find((request) => request.source_key === genericSourceKey);
  expect(kreaRequest).toBeTruthy();
  expect(genericRequest).toBeTruthy();
  expect(kreaRequest.parameters).toEqual({
    prompt: "selected-source comparison lighthouse",
    width: 768,
    height: 1024,
    seed: "424242",
    enable_seedvr2_upscale: false,
    lora: "knp_v4_1",
    lora_strength: 1,
  });
  expect(genericRequest.parameters).toEqual({
    prompt: "selected-source comparison lighthouse",
  });
  await expect(page.locator(".gallery-card").filter({ hasText: "Krea 2 NSFW V4" })).toBeVisible();
  await expect(page.locator(".gallery-card").filter({ hasText: "Generic Landscape" })).toBeVisible();
});

test("gallery defaults to request initiation order when the page arrives unsorted", async ({
  page,
}) => {
  await page.route("**/api/events?**", (route) => route.abort());
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
  const columnDirection = columnAssistant.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const columnCreateMode = columnAssistant.getByRole("radio", {
    name: "New Prompt from Creative Direction",
  });
  await columnDirection.fill("column direction");
  await columnCreateMode.check();
  await prompt.fill("draft that should remain");
  await openEditor.click();
  await expect(dialog).toHaveAttribute("open", "");

  const focusedPrompt = dialog.getByRole("textbox", { name: "Prompt editor" });
  const focusedDirection = dialog.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const focusedRefineMode = dialog.getByRole("radio", { name: "Refine Current Prompt" });
  const focusedCreateMode = dialog.getByRole("radio", {
    name: "New Prompt from Creative Direction",
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
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  const composedPrompt = `${longPrompt}, focused assistant direction`;
  await expect(focusedPrompt).toHaveValue(composedPrompt);
  await expect(prompt).toHaveValue("draft that should remain");
  await expect(columnDirection).toHaveValue("column direction");
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(prompt).toHaveValue(composedPrompt);
  await expect(columnDirection).toHaveValue("focused assistant direction");
  await expect(columnAssistant.getByRole("radio", { name: "Refine Current Prompt" })).toBeChecked();
  await expect(openEditor).toBeFocused();
});

test("voice input records and inserts transcripts at the cursor in every prompt surface", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const stream = { getTracks: () => [{ stop() {} }] };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
    class FakeMediaRecorder {
      static isTypeSupported() {
        return true;
      }

      constructor(_stream, options = {}) {
        this.mimeType = options.mimeType || "audio/webm";
        this.state = "inactive";
        this.ondataavailable = null;
        this.onerror = null;
        this.onstop = null;
      }

      start() {
        this.state = "recording";
      }

      stop() {
        this.state = "inactive";
        queueMicrotask(() => {
          this.ondataavailable?.({
            data: new Blob(["deterministic microphone audio"], { type: this.mimeType }),
          });
          this.onstop?.();
        });
      }
    }
    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: FakeMediaRecorder,
    });
  });

  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const promptMic = page.locator('[data-speech-target="control-prompt"]');
  await prompt.fill("blue sky");
  await prompt.evaluate((element) => element.setSelectionRange(4, 4));
  await promptMic.click();
  await expect(promptMic).toHaveAttribute("aria-label", "Stop recording for Prompt");
  await expect(promptMic).toHaveClass(/is-recording/);
  await promptMic.click();
  await expect(prompt).toHaveValue("blue transcribed speech sky");

  const columnAssistant = page.locator("#prompt-assistant");
  const columnDirection = columnAssistant.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const columnDirectionMic = columnAssistant.locator(
    '[data-speech-target="creative-direction"]',
  );
  await columnDirection.fill("soft light");
  await columnDirection.evaluate((element) => element.setSelectionRange(4, 4));
  await columnDirectionMic.click();
  await columnDirectionMic.click();
  await expect(columnDirection).toHaveValue("soft transcribed speech light");

  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  const dialog = page.locator("#prompt-editor-dialog");
  const focusedPrompt = dialog.getByRole("textbox", { name: "Prompt editor" });
  const focusedPromptMic = dialog.locator('[data-speech-target="prompt-editor-textarea"]');
  await focusedPrompt.evaluate((element) => {
    const cursor = element.value.length;
    element.setSelectionRange(cursor, cursor);
  });
  await focusedPromptMic.click();
  await focusedPromptMic.click();
  await expect(focusedPrompt).toHaveValue(
    "blue transcribed speech sky transcribed speech",
  );

  const focusedDirection = dialog.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const focusedDirectionMic = dialog.locator(
    '[data-speech-target="prompt-editor-creative-direction"]',
  );
  await focusedDirection.evaluate((element) => element.setSelectionRange(0, 0));
  await focusedDirectionMic.click();
  await focusedDirectionMic.click();
  await expect(focusedDirection).toHaveValue(
    "transcribed speech soft transcribed speech light",
  );
});

test("background service polling does not interrupt focused generation controls", async ({ page }) => {
  await page.addInitScript(() => {
    const setTimeout = window.setTimeout.bind(window);
    window.setTimeout = (handler, delay, ...args) =>
      setTimeout(handler, delay === 10_000 ? 100 : delay, ...args);
  });
  let workflowCatalogRequests = 0;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/workflows" && request.method() === "GET") {
      workflowCatalogRequests += 1;
    }
  });
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
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

  await page.locator("#workflow-source").click();
  const sourceDialog = page.locator("#source-picker-dialog");
  await expect(sourceDialog).toBeVisible();
  await sourceDialog.evaluate((dialog) => {
    window.__sourceDialogBeforeServicePoll = dialog;
  });
  const catalogRequestsBeforePoll = workflowCatalogRequests;
  const openMenuPoll = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/services" &&
      response.request().method() === "GET",
  );
  await openMenuPoll;
  await expect(sourceDialog).toBeVisible();
  expect(
    await page.evaluate(
      () => window.__sourceDialogBeforeServicePoll === document.querySelector("#source-picker-dialog"),
    ),
  ).toBe(true);
  expect(workflowCatalogRequests).toBe(catalogRequestsBeforePoll);

  let holdChangedServicePoll = true;
  let releaseChangedServicePoll;
  let reportChangedServicePoll;
  const changedServicePollHeld = new Promise((resolve) => {
    reportChangedServicePoll = resolve;
  });
  const changedServicePollReleased = new Promise((resolve) => {
    releaseChangedServicePoll = resolve;
  });
  await page.route("**/api/services", async (route) => {
    if (holdChangedServicePoll) {
      holdChangedServicePoll = false;
      reportChangedServicePoll();
      await changedServicePollReleased;
    }
    const response = await route.fetch();
    const services = await response.json();
    const comfy = services.find((item) => item.service === "comfyui");
    if (comfy) {
      comfy.available = false;
      comfy.message = "ComfyUI is temporarily unavailable during this poll.";
    }
    await route.fulfill({ response, json: services });
  });
  await changedServicePollHeld;

  const sourceTable = sourceDialog.locator(".source-picker-table-wrap");
  const sourceTableScrollTop = await sourceTable.evaluate((table) => {
    table.style.height = "36px";
    table.scrollTop = 24;
    window.__sourceTableBeforeChangedServicePoll = table;
    return table.scrollTop;
  });
  expect(sourceTableScrollTop).toBeGreaterThan(0);
  const changedServicePoll = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/services" &&
      response.request().method() === "GET",
  );
  releaseChangedServicePoll();
  await changedServicePoll;
  expect(
    await page.evaluate(
      () => window.__sourceDialogBeforeServicePoll === document.querySelector("#source-picker-dialog"),
    ),
  ).toBe(true);
  expect(
    await page.evaluate(
      () =>
        window.__sourceTableBeforeChangedServicePoll ===
        document.querySelector("#source-picker-dialog .source-picker-table-wrap"),
    ),
  ).toBe(true);
  await expect(sourceTable).toHaveJSProperty("scrollTop", sourceTableScrollTop);
  expect(workflowCatalogRequests).toBe(catalogRequestsBeforePoll);

  const deferredCatalogRefresh = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/workflows" &&
      response.request().method() === "GET",
  );
  await sourceDialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await deferredCatalogRefresh;
  await expect(page.getByRole("button", { name: "Generate" })).toBeDisabled();
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
  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await expect(prompt).toHaveValue(carriedPrompt);
  const promptSection = page.locator('[data-control-section="prompt"]');
  const promptSectionTrigger = promptSection.getByRole("button", { name: "Prompt", exact: true });
  await expect(promptSectionTrigger).toHaveAttribute("aria-expanded", "true");
  await expect(
    promptSection.getByRole("button", { name: "Start voice input for Prompt" }),
  ).toBeVisible();
  await expect(
    promptSection.getByRole("button", { name: "Open focused prompt editor" }),
  ).toBeVisible();
  await promptSectionTrigger.click();
  await expect(promptSectionTrigger).toHaveAttribute("aria-expanded", "false");
  await expect(prompt).toBeHidden();
  await expect(
    promptSection.getByRole("button", { name: "Start voice input for Prompt" }),
  ).toBeVisible();
  await expect(
    promptSection.getByRole("button", { name: "Open focused prompt editor" }),
  ).toBeVisible();
  await promptSectionTrigger.click();
  await expect(prompt).toBeVisible();
  for (const [key, title] of [
    ["resolution", "Resolution"],
    ["seed", "Seed"],
    ["upscaling", "Upscaling"],
  ]) {
    await expect(
      page.locator(`[data-control-section="${key}"]`).getByRole("button", {
        name: title,
        exact: true,
      }),
    ).toHaveAttribute("aria-expanded", "true");
  }
  await expect(
    page.locator('[data-control-section="advanced"] .control-section-trigger'),
  ).toHaveAttribute("aria-expanded", "false");
  const resolutionSection = page.locator('[data-control-section="resolution"]');
  const resolutionSectionStatus = resolutionSection.locator(
    '[data-control-section-status="resolution"]',
  );
  const resolutionSectionTrigger = resolutionSection.getByRole("button", {
    name: "Resolution",
    exact: true,
  });
  await expect(resolutionSectionStatus).toHaveText("1080 × 1920");
  await resolutionSectionTrigger.click();
  await expect(resolutionSectionStatus).toBeVisible();
  await resolutionSectionTrigger.click();

  const seedSection = page.locator('[data-control-section="seed"]');
  const seedSectionStatus = seedSection.locator('[data-control-section-status="seed"]');
  const seedSectionTrigger = seedSection.getByRole("button", { name: "Seed", exact: true });
  await expect(seedSectionStatus).toHaveText("Random");
  await seedSectionTrigger.click();
  await expect(seedSectionStatus).toBeVisible();
  await seedSectionTrigger.click();
  const generationPanel = page.locator("#generation-panel");
  await expect(generationPanel.getByRole("tooltip")).toHaveCount(0);
  await expect(generationPanel.locator(".help-text")).toHaveCount(0);
  await prompt.focus();
  await expect(generationPanel.getByRole("tooltip")).toHaveCount(0);
  await prompt.blur();
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
  const resolutionEditor = page.locator(".resolution-editor");
  const editorBox = await resolutionEditor.boundingBox();
  const gridBox = await grid.boundingBox();
  const widthBox = await width.boundingBox();
  const heightBox = await height.boundingBox();
  expect(editorBox).not.toBeNull();
  expect(gridBox).not.toBeNull();
  expect(widthBox).not.toBeNull();
  expect(heightBox).not.toBeNull();
  expect(gridBox.width / editorBox.width).toBeGreaterThan(0.4);
  expect(gridBox.width / editorBox.width).toBeLessThan(0.55);
  expect(widthBox.x).toBeGreaterThan(gridBox.x + gridBox.width);
  expect(Math.abs(widthBox.x - heightBox.x)).toBeLessThan(2);
  expect(heightBox.y).toBeGreaterThan(widthBox.y + widthBox.height);

  await width.focus();
  await expect(generationPanel.getByRole("tooltip")).toHaveCount(0);
  await height.focus();
  await expect(generationPanel.getByRole("tooltip")).toHaveCount(0);
  await height.blur();
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
  await seedMode.focus();
  await expect(generationPanel.getByRole("tooltip")).toHaveCount(0);
  await seedMode.blur();
  await expect(seedMode).toHaveValue("random");
  await expect(seedValue).toBeDisabled();
  await seedMode.selectOption("fixed");
  await expect(seedSectionStatus).toHaveText("Fixed");
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
  await expect(resolutionSectionStatus).toHaveText("1024 × 1600");
  await expect(page.locator("[data-resolution-summary]")).toHaveText(
    "1024 × 1600 · 1.64 MP · 16:25",
  );
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
  const advanced = page.locator(".advanced-group");
  const advancedTrigger = advanced.getByRole("button", { name: "Advanced", exact: true });
  await expect(advancedTrigger).toHaveAttribute("aria-expanded", "false");
  await advancedTrigger.click();
  await expect(advancedTrigger).toHaveAttribute("aria-expanded", "true");
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
  await expect(
    advanced.getByRole("button", { name: "Advanced", exact: true }),
  ).toHaveAttribute("aria-expanded", "true");
  const strength = page.getByRole("spinbutton", { name: "LoRA Strength", exact: true });
  await expect(strength).toHaveAttribute("aria-invalid", "true");
  await expect(strength).toBeFocused();
  await page.unroute("**/api/generations");

  await selectPublishedSource(page, "Generic Landscape");
  const promptBeforeComposition = await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .inputValue();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("stale request");
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
  await page.getByRole("button", { name: "Apply Creative Direction" }).click();
  await expect(page.getByRole("button", { name: "Applying…" })).toBeDisabled();
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

test("cancelling a queued generation removes its card and history", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Krea 2 NSFW V4");
  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });

  await prompt.fill("slow queued cancellation blocker");
  const blockerResponse = await generateAndExpectAccepted(page);
  const blocker = await blockerResponse.json();
  const blockerCard = page.locator(
    `.gallery-card[data-generation-id="${blocker.id}"]`,
  );
  await expect(blockerCard).toHaveClass(/status-running/);

  await prompt.fill("remove this queued generation");
  const queuedResponse = await generateAndExpectAccepted(page);
  const queued = await queuedResponse.json();
  const queuedCard = page.locator(`.gallery-card[data-generation-id="${queued.id}"]`);
  await expect(queuedCard).toHaveClass(/status-queued/);

  const cancelResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/generations/${queued.id}/cancel` &&
      response.request().method() === "POST",
  );
  await queuedCard.getByRole("button", { name: "Cancel", exact: true }).click();
  const cancelResponse = await cancelResponsePromise;
  expect(cancelResponse.status()).toBe(204);
  await expect(queuedCard).toHaveCount(0);
  await expect(page.locator("#toast-region")).toContainText(
    "Queued generation cancelled and removed.",
  );

  const deletedLookup = await page.request.get(`/api/generations/${queued.id}`);
  expect(deletedLookup.status()).toBe(404);

  await expect(blockerCard).toHaveClass(/status-succeeded/);
  page.once("dialog", (dialog) => dialog.accept());
  await blockerCard.getByRole("button", { name: "Delete generation" }).click();
  await expect(blockerCard).toHaveCount(0);
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
  await expect(photoViewer.getByRole("button", { name: "View newer generation" })).toHaveCount(0);
  await expect(photoViewer.getByRole("button", { name: "View older generation" })).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(photoViewer.locator(".photo-viewer-frame")).not.toHaveAttribute(
    "data-photo-generation-id",
    viewedGenerationId,
  );
  await expect(photoViewer.getByRole("button", { name: "View newer generation" })).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(photoViewer.locator(".photo-viewer-frame")).toHaveAttribute(
    "data-photo-generation-id",
    viewedGenerationId,
  );
  await expect(photoViewer.getByRole("button", { name: "View newer generation" })).toHaveCount(0);
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

test("required image input accepts Browse and a retained gallery image drag", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);

  await selectPublishedSource(page, "Generic Landscape");
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("gallery source for image input");
  const sourceResponse = await generateAndExpectAccepted(page);
  const sourceGeneration = await sourceResponse.json();
  const sourceCard = page.locator(
    `.gallery-card[data-generation-id="${sourceGeneration.id}"]`,
  );
  const sourceImage = sourceCard.locator("[data-gallery-artifact-id]");
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`/api/generations/${sourceGeneration.id}`);
        const generation = await response.json();
        return {
          status: generation.status,
          error_code: generation.error_code || null,
          error_message: generation.error_message || null,
        };
      },
      { timeout: 30_000 },
    )
    .toEqual({ status: "succeeded", error_code: null, error_message: null });
  await expect(sourceImage).toBeVisible({ timeout: 30_000 });

  await selectPublishedSource(page, "Moody Desire Image Input");
  const dropzone = page.locator('[data-image-drop-control="reference_image"]');
  const browseInput = page.locator('input[type="file"][data-image-input="true"]');
  await expect(dropzone).toContainText("Drop an image here");
  await expect(page.getByRole("button", { name: "Generate" })).toBeDisabled();

  const browseUpload = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/uploads/reference-images" &&
      response.request().method() === "POST",
  );
  await browseInput.setInputFiles({
    name: "browse-reference.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  expect((await browseUpload).status()).toBe(200);
  await expect(dropzone).toContainText("browse-reference.png");
  await expect(dropzone.locator(".image-input-selection img")).toBeVisible();

  await dropzone.getByRole("button", { name: "Remove" }).click();
  await expect(dropzone).toContainText("Drop an image here");
  const galleryUpload = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.startsWith(
        "/api/uploads/reference-images/from-artifact/",
      ) && response.request().method() === "POST",
  );
  await sourceImage.dragTo(dropzone);
  expect((await galleryUpload).status()).toBe(200);
  await expect(dropzone).toContainText("Gallery image");
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();

  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("edit using the retained gallery image");
  const requestPromise = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  const editedResponse = await generateAndExpectAccepted(page);
  const editedGeneration = await editedResponse.json();
  const request = await requestPromise;
  const parameters = request.postDataJSON().parameters;
  expect(parameters.reference_image).toEqual({ asset_id: expect.any(String) });
  expect(Object.keys(parameters.reference_image)).toEqual(["asset_id"]);
  const retainedAssetId = parameters.reference_image.asset_id;

  await expect
    .poll(
      async () => {
        const response = await page.request.get(`/api/generations/${editedGeneration.id}`);
        return (await response.json()).status;
      },
      { timeout: 30_000 },
    )
    .toBe("succeeded");

  await selectPublishedSource(page, "Generic Landscape");
  await expect(dropzone).toHaveCount(0);
  const editedCard = page.locator(
    `.gallery-card[data-generation-id="${editedGeneration.id}"]`,
  );
  const recallResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/generations/${editedGeneration.id}/recall` &&
      response.request().method() === "GET",
  );
  await editedCard.getByRole("button", { name: "Recall settings" }).click();
  const recallResponse = await recallResponsePromise;
  expect(recallResponse.status()).toBe(200);
  const recalled = await recallResponse.json();
  expect(recalled.source_available).toBe(true);
  expect(recalled.parameters.reference_image).toEqual({ asset_id: retainedAssetId });

  await expect(dropzone).toBeVisible();
  await expect(dropzone).toContainText("Image selected");
  const recalledPreview = dropzone.locator(".image-input-selection img");
  await expect(recalledPreview).toBeVisible();
  await expect
    .poll(() => recalledPreview.evaluate((image) => image.complete && image.naturalWidth > 0))
    .toBe(true);
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(
    "edit using the retained gallery image",
  );

  const regenerationRequestPromise = page.waitForRequest(
    (regenerationRequest) =>
      new URL(regenerationRequest.url()).pathname === "/api/generations" &&
      regenerationRequest.method() === "POST",
  );
  await generateAndExpectAccepted(page);
  const regenerationRequest = await regenerationRequestPromise;
  expect(regenerationRequest.postDataJSON().parameters.reference_image).toEqual({
    asset_id: retainedAssetId,
  });
});
