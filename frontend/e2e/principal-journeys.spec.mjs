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

async function clickGalleryControl(control) {
  const card = control.locator("xpath=ancestor::*[@data-gallery-card][1]");
  // Collection refreshes can replace a card during preparation; retry preparation
  // without ever repeating the action itself.
  await expect(async () => {
    await control.scrollIntoViewIfNeeded({ timeout: 2000 });
    // Settle scrolling before moving: scrolling cancels pending hover intent.
    await control.evaluate(() => new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    }));
    const box = await control.boundingBox();
    await control.page().mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await expect(card.locator('[role="group"]')).toHaveCSS("opacity", "1", { timeout: 2000 });
  }).toPass({ timeout: 10000 });
  await control.click();
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

async function openSelectionDelete(page, card) {
  await clickGalleryControl(card.locator(".card-select-button"));
  await page.getByRole("button", { name: "Delete…", exact: true }).click();
  return page.locator("#gallery-delete-dialog");
}

async function deleteSelectedCard(page, card) {
  const dialog = await openSelectionDelete(page, card);
  await dialog.getByRole("button", { name: "Delete 1 item", exact: true }).click();
}

test("frontend document loads one content-addressed module graph", async ({ page }) => {
  const assetResponses = [];
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (/^\/assets\/[0-9a-f]{64}\//u.test(path)) assetResponses.push(response);
  });

  const documentResponse = await page.goto("/");
  expect((await documentResponse.allHeaders())["cache-control"]).toBe(
    "no-cache, must-revalidate",
  );
  const appPath = await page.locator('script[type="module"]').getAttribute("src");
  const stylePath = await page.locator('link[rel="stylesheet"]').getAttribute("href");
  expect(appPath).toMatch(/^\/assets\/[0-9a-f]{64}\/app\.mjs$/u);
  expect(stylePath).toMatch(/^\/assets\/[0-9a-f]{64}\/styles\.css$/u);
  const assetVersion = appPath.match(/^\/assets\/([0-9a-f]{64})\//u)?.[1];
  expect(stylePath).toContain(`/assets/${assetVersion}/`);

  const paths = assetResponses.map((response) => new URL(response.url()).pathname);
  expect(paths).toEqual(
    expect.arrayContaining([
      `/assets/${assetVersion}/app.mjs`,
      `/assets/${assetVersion}/api.mjs`,
      `/assets/${assetVersion}/lib.mjs`,
      `/assets/${assetVersion}/render.mjs`,
      `/assets/${assetVersion}/gallery-hover.mjs`,
      `/assets/${assetVersion}/styles.css`,
    ]),
  );
  for (const response of assetResponses) {
    expect((await response.allHeaders())["cache-control"]).toBe(
      "public, max-age=31536000, immutable",
    );
  }

  const reloadResponse = await page.reload();
  expect((await reloadResponse.allHeaders())["cache-control"]).toBe(
    "no-cache, must-revalidate",
  );
  await expect(page.getByRole("heading", { name: "E2E Image Appliance" })).toBeVisible();
  await expect(page.locator('script[type="module"]')).toHaveAttribute("src", appPath);
});

test("bootstrap, user administration, generation, progressive card, recall, and scale persistence", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  // Earlier specs in the same run (generation-quantity) may already have
  // consumed the one-time temporary password; the helper completes the forced
  // password change itself whenever it is still pending.
  await signInAdminWithCurrentFixturePassword(page);

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
  const liveProgress = page.locator(".gallery-card").getByRole("progressbar");
  await expect(liveProgress).toBeVisible();
  await expect(liveProgress).toHaveAttribute("aria-valuetext", /of .* for Processing/);
  await expect(page.locator(".gallery-card .generation-progress-label")).toContainText(
    "Processing",
  );
  await expect(page.locator(".gallery-card .card-media img")).toBeVisible();
  await expect(page.locator(".gallery-card")).toHaveClass(/status-(running|succeeded)/);
  await expect(page.locator(".gallery-card .batch-count")).toHaveText(/^(?:2|4)$/u);
  await expect(page.locator(".gallery-card")).toHaveClass(/status-succeeded/);
  await expect(page.locator(".gallery-card .batch-count")).toHaveText("2");

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

  const detailsButton = page.locator(".gallery-card .card-details-button").first();
  await clickGalleryControl(detailsButton);
  await expect(detailDialog).toHaveAttribute("open", "");
  await detailDialog.getByRole("button", { name: "Close details" }).click();
  await expect(detailDialog).not.toHaveAttribute("open", "");
  await clickGalleryControl(detailsButton);
  await expect(detailDialog).toHaveAttribute("open", "");
  await detailDialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(detailDialog).not.toHaveAttribute("open", "");

  const actions = page.locator(".gallery-card .card-actions").first();
  await expect(actions.locator("button")).toHaveCount(5);
  await expect(actions.getByRole("link", { name: "Download current image" })).toBeVisible();
  await expect(actions.getByRole("button", { name: "Add to Favorites" })).toBeVisible();
  await expect(actions.getByRole("button", { name: "Recall settings" })).toBeVisible();
  await expect(page.locator(".gallery-card .card-select-button").first()).toBeVisible();
  await expect(actions.getByRole("button", { name: "Delete generation" })).toBeVisible();
  await expect(actions.getByRole("button", { name: "Generation details" })).toBeVisible();
  await expect(page.locator(".gallery-card .card-footer")).toHaveCount(0);
  await expect(actions).not.toContainText(/seed|Complete|Running|slow multi/i);

  const downloadPromise = page.waitForEvent("download");
  await clickGalleryControl(actions.getByRole("link", { name: "Download current image" }));
  await downloadPromise;

  await clickGalleryControl(actions.getByRole("button", { name: "Add to Favorites" }));
  await expect(actions.getByRole("button", { name: "Remove from Favorites" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await expect(page).toHaveURL(/#\/favorites$/);
  await expect(page.locator("#collection-bar [aria-current=location]")).toHaveText("Favorites");
  await expect(page.getByRole("button", { name: "Favorites", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#gallery .gallery-card")).toHaveCount(1);
  await expect(page.locator("#gallery .gallery-card")).toHaveClass(/is-favorited/);
  const goldRing = await page.locator("#gallery .gallery-card").evaluate((card) => {
    const style = getComputedStyle(card, "::after");
    return { position: getComputedStyle(card).position, background: style.backgroundImage, mask: style.maskComposite, pointer: style.pointerEvents, animation: style.animationName };
  });
  expect(goldRing.position).toBe("relative");
  expect(goldRing.background).toContain("linear-gradient");
  expect(goldRing.mask).toContain("exclude");
  expect(goldRing.pointer).toBe("none");
  expect(goldRing.animation).toBe("none");
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await page.getByRole("button", { name: "New collection" }).click();
  const folderDialog = page.locator("#collection-dialog");
  await folderDialog.getByLabel("Name", { exact: true }).fill("Favorite journey folder");
  await folderDialog.getByRole("button", { name: "Create collection" }).click();
  const folder = page.locator(".collection-tile").filter({ hasText: "Favorite journey folder" });
  await clickGalleryControl(folder.getByRole("button", { name: "Add to Favorites", exact: true }));
  await expect(folder).toHaveClass(/is-favorited/);
  await expect(folder.getByRole("button", { name: "Remove from Favorites" })).toHaveAttribute("aria-pressed", "true");
  const folderId = await folder.getAttribute("data-collection-id");
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await expect(page.locator("#gallery > [data-gallery-card]")).toHaveCount(2);
  await expect(page.locator("#gallery > [data-gallery-card]").first()).toHaveAttribute("data-gallery-card", "collection");
  await expect(folder).toHaveClass(/is-favorited/);
  await page.mouse.move(10, 10);
  await page.screenshot({ path: test.info().outputPath("favorites-gallery.png") });
  await folder.locator(".collection-tile-open").focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  await expect(folder).toHaveCSS("outline-style", "solid");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`#\\/c\\/${folderId}$`));
  await expect(page.getByRole("heading", { name: "This collection is empty" })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/#\/favorites$/);
  await expect(page.locator("#gallery > [data-gallery-card]")).toHaveCount(2);
  await page.reload();
  await expect(page.locator("#gallery > [data-gallery-card]")).toHaveCount(2);
  await clickGalleryControl(folder.getByRole("button", { name: "Remove from Favorites" }));
  await expect(folder).toHaveCount(0);
  await expect(page.locator("#gallery > [data-gallery-card]")).toHaveCount(1);
  await page.locator("#gallery .card-media").click();
  await expect(photoViewer).toHaveAttribute("open", "");
  await photoViewer.locator(".photo-viewer-favorite").click();
  await expect(page.locator("#gallery .gallery-card")).toHaveCount(0);
  await expect(photoViewer.locator(".photo-viewer-favorite")).toHaveAttribute("aria-pressed", "false");
  await photoViewer.locator(".photo-viewer-favorite").click();
  await expect(page.locator("#gallery .gallery-card")).toHaveCount(1);
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
  await prompt.fill("temporary controls");
  await clickGalleryControl(actions.getByRole("button", { name: "Recall settings" }));
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");

  const cardCountBeforeCompose = await page.locator(".gallery-card").count();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("cinematic blue hour");
  await page.getByRole("button", { name: "Apply Creative Direction" }).click();
  await expect(prompt).toHaveValue(/cinematic blue hour/);
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  await clickGalleryControl(actions.getByRole("button", { name: "Recall settings" }));
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  await selectPublishedSource(page, "Krea 2 NSFW V4");
  await expect(prompt).toHaveValue("slow multi lighthouse at dusk");

  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await clickGalleryControl(actions.getByRole("button", { name: "Remove from Favorites" }));
  await expect(page.getByRole("heading", { name: "No favorites yet — tap the heart on any card or folder" })).toBeVisible();
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await expect(actions.getByRole("button", { name: "Add to Favorites" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);
  await deleteSelectedCard(page, folder);
  await expect(folder).toHaveCount(0);

  const scale = page.locator("#gallery-scale");
  await scale.fill("100");
  await scale.dispatchEvent("change");
  await expect(page.locator("#gallery")).toHaveClass(/gallery-full/);
  await page.waitForTimeout(400);
  await page.reload();
  await expect(page.locator("#gallery-scale")).toHaveValue("100");
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  const deleteDialog = await openSelectionDelete(page, page.locator(".gallery-card").first());
  await expect(deleteDialog).toContainText("This cannot be undone.");
  await deleteDialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose);

  await page.getByRole("button", { name: "Delete…", exact: true }).click();
  await deleteDialog.getByRole("button", { name: "Delete 1 item", exact: true }).click();
  await expect(page.locator(".gallery-card")).toHaveCount(cardCountBeforeCompose - 1);
  await expect(page.locator("#toast-region")).toContainText("Selection deleted.");
});

test("Favorites uses the gallery sentinel for mixed cursor pages and ignores stale navigation responses", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await expect(page.getByRole("button", { name: "New collection" })).toBeEnabled();
  const items = Array.from({ length: 42 }, (_, index) => index % 2
    ? { id: `f-${index}`, item_type: "generation", generation: {
        id: `g-${index}`, status: "succeeded", workflow_display_name: `Saved image ${index}`,
        accepted_at: "2026-09-01T00:00:00Z", is_favorite: true, recall_available: true,
      }, collection: null }
    : { id: `f-${index}`, item_type: "collection", generation: null, collection: {
        id: `c-${index}`, name: `Saved folder ${index}`, parent_id: null, generation_count: 0,
        previews: [], is_favorite: true,
      } });
  let holdNextPage = false;
  let releaseNextPage;
  let requestedNextPage;
  await page.route("**/api/favorites?*", async (route) => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    if (cursor && holdNextPage) {
      requestedNextPage();
      await new Promise((resolve) => { releaseNextPage = resolve; });
    }
    await route.fulfill({ json: { items: cursor ? items.slice(40) : items.slice(0, 40), next_cursor: cursor ? null : "mixed-next" } });
  });
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  const cards = page.locator("#gallery > [data-gallery-card]");
  await expect(cards).toHaveCount(40);
  await page.locator("#gallery-scale").fill("100");
  await expect(page.locator("#gallery")).toHaveClass(/gallery-full/);
  await page.locator("#gallery-sentinel").scrollIntoViewIfNeeded();
  await expect(cards).toHaveCount(42);
  await expect(cards.nth(40)).toHaveAttribute("data-collection-id", "c-40");
  await expect(cards.nth(41)).toHaveAttribute("data-generation-id", "g-41");
  await expect(page.locator("#gallery-sentinel")).toBeHidden();

  await page.getByRole("link", { name: "Home", exact: true }).click();
  holdNextPage = true;
  const nextRequested = new Promise((resolve) => { requestedNextPage = resolve; });
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await expect(cards).toHaveCount(40);
  await page.locator("#gallery-sentinel").scrollIntoViewIfNeeded();
  await nextRequested;
  await page.getByRole("link", { name: "Home", exact: true }).click();
  const fulfilled = page.waitForResponse((response) => response.url().includes("cursor=mixed-next"));
  releaseNextPage();
  await fulfilled;
  await expect(page.locator("#collection-bar [aria-current=location]")).toHaveText("Home");
  await expect(page.locator('#gallery [data-generation-id="g-41"]')).toHaveCount(0);
  await expect(page.locator('#gallery [data-collection-id="c-40"]')).toHaveCount(0);
});

test("Auto-generate in Favorites waits for its queued generation without inserting it into the feed", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "artist.one", "E2EUserPermanent123!");
  await expect(page.locator("#workflow-source")).toBeEnabled();
  await page.getByRole("button", { name: "Favorites", exact: true }).click();
  await expect(page.getByRole("heading", { name: "No favorites yet — tap the heart on any card or folder" })).toBeVisible();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("Favorites background generation");
  const requests = [];
  await page.route("**/api/generations", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    requests.push(route.request().postDataJSON());
    await route.fulfill({ status: 201, json: { id: "favorites-pending", status: "queued", collection_id: null } });
  });
  await page.getByRole("switch", { name: "Auto-generate" }).check();
  await expect.poll(() => requests.length).toBe(1);
  // Give the scheduler several opportunities to retry; the queued job is outside this feed.
  await page.waitForTimeout(500);
  await page.getByRole("switch", { name: "Auto-generate" }).uncheck();
  expect(requests).toHaveLength(1);
  expect(requests[0].collection_id).toBeNull();
  await expect(page.locator("#gallery .gallery-card")).toHaveCount(0);
});

test("collection tiles match square generation cards and follow gallery scale", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);

  await page.getByRole("button", { name: "New collection" }).click();
  const dialog = page.locator("#collection-dialog");
  await dialog.getByLabel("Name").fill("E2E Gallery Scale");
  const createResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/collections" &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "Create collection" }).click();
  expect((await createResponse).status()).toBe(201);

  const tile = page.locator(".collection-tile").filter({ hasText: "E2E Gallery Scale" });
  await expect(tile).toBeVisible();
  const scale = page.locator("#gallery-scale");
  await scale.evaluate((input) => {
    input.value = "0";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const collectionGeometry = async () =>
    page.evaluate(() => {
      const tileElement = [...document.querySelectorAll(".collection-tile")].find((element) =>
        element.textContent.includes("E2E Gallery Scale"),
      );
      const preview = tileElement?.querySelector(".collection-tile-preview");
      // Measure the CSS reference in the same task that inserts it: a live
      // gallery refresh can otherwise remove this synthetic card between reads.
      const card = document.createElement("article");
      card.className = "gallery-card collection-size-reference";
      card.innerHTML = '<div class="card-media-frame"></div>';
      const groupGrid = document.createElement("div");
      groupGrid.className = "prompt-group-grid";
      groupGrid.append(card);
      document.querySelector("#gallery").append(groupGrid);
      const media = card.querySelector(".card-media-frame");
      const geometry = {
        tileWidth: tileElement?.getBoundingClientRect().width || 0,
        previewWidth: preview?.getBoundingClientRect().width || 0,
        previewHeight: preview?.getBoundingClientRect().height || 0,
        cardWidth: card?.getBoundingClientRect().width || 0,
        mediaHeight: media?.getBoundingClientRect().height || 0,
      };
      groupGrid.remove();
      return geometry;
    });
  const compactGeometry = await collectionGeometry();
  expect(compactGeometry.tileWidth).toBeCloseTo(compactGeometry.cardWidth, 0);
  expect(compactGeometry.previewWidth).toBeCloseTo(compactGeometry.previewHeight, 0);
  expect(compactGeometry.previewHeight).toBeCloseTo(compactGeometry.mediaHeight, 0);

  await scale.evaluate((input) => {
    input.value = "75";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect.poll(async () => (await collectionGeometry()).tileWidth).toBeGreaterThan(
    compactGeometry.tileWidth,
  );
  const enlargedGeometry = await collectionGeometry();
  expect(enlargedGeometry.tileWidth).toBeCloseTo(enlargedGeometry.cardWidth, 0);
  expect(enlargedGeometry.previewWidth).toBeCloseTo(enlargedGeometry.previewHeight, 0);
  expect(enlargedGeometry.previewHeight).toBeCloseTo(enlargedGeometry.mediaHeight, 0);

  await openSelectionDelete(page, tile);
  const deleteResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/gallery/delete" &&
      response.request().method() === "POST",
  );
  await page
    .locator("#gallery-delete-dialog")
    .getByRole("button", { name: "Delete 1 item" })
    .click();
  expect((await deleteResponse).status()).toBe(200);
});

test("collections route generation, preview preference, move, and recursive delete", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await expect(page).toHaveURL(/#\/$/u);
  await expect(page.getByRole("navigation", { name: "Collections" })).toBeVisible();

  async function createCollection(name) {
    await page.getByRole("button", { name: "New collection" }).click();
    const dialog = page.locator("#collection-dialog");
    await expect(dialog).toHaveAttribute("open", "");
    const submit = dialog.getByRole("button", { name: "Create collection" });
    await expect(submit).toBeDisabled();
    await dialog.getByLabel("Name").fill(name);
    const responsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/collections" &&
        response.request().method() === "POST",
    );
    await submit.click();
    expect((await responsePromise).status()).toBe(201);
    await expect(dialog).not.toHaveAttribute("open", "");
    await expect(page.locator(".collection-tile").filter({ hasText: name })).toHaveCount(1);
  }

  await createCollection("E2E Source");
  await createCollection("E2E Destination");

  const preRenameTile = page.locator(".collection-tile").filter({ hasText: "E2E Source" });
  await clickGalleryControl(preRenameTile.getByRole("button", { name: "Rename collection E2E Source" }));
  const renameDialog = page.locator("#collection-dialog");
  await renameDialog.getByLabel("Name").fill("E2E Source Renamed");
  await renameDialog.getByRole("button", { name: "Save" }).click();
  await expect(renameDialog).not.toHaveAttribute("open", "");
  await expect(page.locator(".collection-tile").filter({ hasText: "E2E Source Renamed" })).toHaveCount(1);

  await preRenameTile.click();
  await expect(page).toHaveURL(/#\/c\/[^/]+$/u);
  await expect(page.locator(".collection-crumbs")).toContainText("Home/E2E Source Renamed");

  await selectPublishedSource(page, "Generic Landscape");
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("collection preview lighthouse");
  const generationResponse = await generateAndExpectAccepted(page);
  const generation = await generationResponse.json();
  expect(generation.collection_id).toBeTruthy();
  const card = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/u);

  await page.getByRole("link", { name: "Home" }).click();
  const sourceTile = page
    .locator(".collection-tile")
    .filter({ hasText: "E2E Source Renamed" });
  await expect(sourceTile.locator(".collection-count")).toHaveText("1");
  await expect(sourceTile.locator(".collection-preview-grid img")).toHaveCount(1);
  await expect(page.locator(`.gallery-card[data-generation-id="${generation.id}"]`)).toHaveCount(0);

  const sourcePreviewSwitch = sourceTile.getByRole("switch", {
    name: "Previews for E2E Source Renamed",
  });
  const destinationPreviewSwitch = page
    .locator(".collection-tile")
    .filter({ hasText: "E2E Destination" })
    .getByRole("switch", { name: "Previews for E2E Destination" });
  const previewPatch = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.startsWith("/api/collections/") &&
      response.request().method() === "PATCH",
  );
  await clickGalleryControl(sourcePreviewSwitch);
  expect((await previewPatch).ok()).toBe(true);
  await expect(sourcePreviewSwitch).toHaveAttribute("aria-checked", "false");
  await expect(sourceTile.locator(".collection-preview-grid")).toHaveCount(0);
  await expect(destinationPreviewSwitch).toHaveAttribute("aria-checked", "true");
  await page.reload();
  await expect(sourcePreviewSwitch).toHaveAttribute("aria-checked", "false");
  await expect(sourceTile.locator(".collection-preview-grid")).toHaveCount(0);

  await page
    .locator(".collection-tile")
    .filter({ hasText: "E2E Source Renamed" })
    .click();
  const movedCard = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
  await clickGalleryControl(movedCard.locator(".card-select-button"));
  await page.getByRole("button", { name: "Move / Copy…" }).click();
  const moveDialog = page.locator("#gallery-transfer-dialog");
  await expect(moveDialog).toHaveAttribute("open", "");
  await moveDialog.getByRole("radio", { name: "E2E Destination" }).check();
  const moveResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/gallery/transfer" &&
      response.request().method() === "POST",
  );
  await moveDialog.getByRole("button", { name: "Move here", exact: true }).click();
  expect((await moveResponse).ok()).toBe(true);
  await expect(movedCard).toHaveCount(0);

  await page.getByRole("link", { name: "Home" }).click();
  const destinationTile = page
    .locator(".collection-tile")
    .filter({ hasText: "E2E Destination" });
  await expect(destinationTile.locator(".collection-count")).toHaveText("1");
  await expect(destinationTile.locator(".collection-preview-grid")).toHaveCount(1);
  await expect(sourceTile.locator(".collection-preview-grid")).toHaveCount(0);
  await destinationTile.click();
  await expect(page.locator(`.gallery-card[data-generation-id="${generation.id}"]`)).toHaveCount(1);

  await page.getByRole("link", { name: "Home" }).click();
  const deleteDialog = await openSelectionDelete(page, destinationTile);
  await expect(deleteDialog).toContainText("1 generation");
  const deleteResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/gallery/delete" &&
      response.request().method() === "POST",
  );
  await deleteDialog.getByRole("button", { name: "Delete 1 item" }).click();
  expect((await deleteResponse).status()).toBe(200);
  await expect(page).toHaveURL(/#\/$/u);
  await expect(page.locator(".collection-tile").filter({ hasText: "E2E Destination" })).toHaveCount(
    0,
  );

  await deleteSelectedCard(page, page
    .locator(".collection-tile")
    .filter({ hasText: "E2E Source Renamed" }));
  await expect(page.locator(".collection-tile").filter({ hasText: "E2E Source Renamed" })).toHaveCount(
    0,
  );
});

test("runtime selector is a borderless single-line two-instance control", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);

  const row = page.locator(".comfyui-instance-field");
  const selector = page.getByLabel("Runtime", { exact: true });
  await expect(row).toBeVisible();
  const actionOrder = await page.locator(".generation-actions").evaluate((element) =>
    Array.from(element.children).map((child) =>
      child.id ||
      (child.classList.contains("generate-row")
        ? "generate-row"
        : child.classList.contains("auto-generation-options")
          ? "auto-generation-options"
          : child.classList.contains("comfyui-instance-field")
            ? "comfyui-instance-field"
            : "unknown"),
    ),
  );
  expect(actionOrder).toEqual([
    "generate-row",
    "auto-generation-options",
    "auto-generate-status",
    "comfyui-instance-field",
  ]);
  const generateRowOrder = await page.locator(".generate-row").evaluate((element) =>
    Array.from(element.children).map((child) =>
      child.id ||
      (child.classList.contains("generation-quantity") ? "generation-quantity" : "unknown"),
    ),
  );
  expect(generateRowOrder).toEqual(["generate-button", "generation-quantity"]);
  await expect(selector).toHaveValue("default");
  await expect(selector.locator("option")).toHaveText(["Primary", "Secondary"]);
  const status = row.locator("#comfyui-instance-status");
  await expect(row.locator(".comfyui-instance-status-icon")).toHaveText("✅");
  await expect(status).toHaveAttribute("title", "Available");
  await expect(status.locator(".comfyui-instance-status-message")).toHaveText("Available");
  await status.focus();
  await expect(status.locator(".comfyui-instance-status-message")).toBeVisible();

  const presentation = await row.evaluate((element) => {
    const [label, select, status] = element.children;
    const centers = [label, select, status].map((child) => {
      const rect = child.getBoundingClientRect();
      return rect.top + rect.height / 2;
    });
    const style = getComputedStyle(element);
    return {
      backgroundImage: style.backgroundImage,
      borderWidths: [
        style.borderTopWidth,
        style.borderRightWidth,
        style.borderBottomWidth,
        style.borderLeftWidth,
      ],
      centerSpread: Math.max(...centers) - Math.min(...centers),
    };
  });
  expect(presentation.backgroundImage).toBe("none");
  expect(presentation.borderWidths).toEqual(["0px", "0px", "0px", "0px"]);
  expect(presentation.centerSpread).toBeLessThan(1);

  await selector.selectOption("worker-2");
  await expect(selector).toHaveValue("worker-2");
  await expect(status).toHaveAttribute("title", "Available");
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("worker runtime routing check");
  const acceptedResponse = await generateAndExpectAccepted(page);
  const accepted = await acceptedResponse.json();
  expect(accepted.comfyui_instance_id).toBe("worker-2");
  expect(accepted.comfyui_instance_label).toBe("Secondary");
  await expect.poll(async () => {
    const detail = await (await page.request.get(`/api/generations/${accepted.id}`)).json();
    return detail.status;
  }).toBe("succeeded");
  const completed = await (await page.request.get(`/api/generations/${accepted.id}`)).json();
  expect(completed.comfyui_instance_id).toBe("worker-2");
  expect(completed.comfyui_instance_label).toBe("Secondary");
  await expect(
    page.locator(`.gallery-card[data-generation-id="${accepted.id}"] .card-details-button`),
  ).toHaveAttribute("aria-label", "Generation details");
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
  await page.mouse.move(80, 80);
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
});

test("photo viewer slideshow shows a next-in countdown while auto-generate is on", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("next-in countdown baseline");
  const baselineResponse = await generateAndExpectAccepted(page);
  const baseline = await baselineResponse.json();
  const baselineCard = page.locator(`.gallery-card[data-generation-id="${baseline.id}"]`);
  await expect(baselineCard).toHaveClass(/status-succeeded/);
  await baselineCard.locator(".card-media").click();

  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  const nextIn = photoViewer.locator(".photo-viewer-next-in");
  const playbackControl = photoViewer.getByRole("group", { name: "Playback mode" });

  // Auto-generate off: the badge exists in the markup but stays hidden in both playback modes.
  await expect(nextIn).toHaveCount(1);
  await expect(nextIn).toBeHidden();
  await playbackControl.getByRole("button", { name: "Slideshow", exact: true }).click();
  await expect(
    playbackControl.getByRole("switch", { name: "Slideshow mode" }),
  ).toHaveAttribute("aria-checked", "true");
  await expect(nextIn).toBeHidden();
  await page.mouse.move(80, 80);
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
  await expect(photoViewer).not.toHaveAttribute("open", "");

  // Auto-generate on: the badge appears in slideshow mode and survives the advance.
  await page.getByRole("switch", { name: "Auto-generate" }).check();
  await baselineCard.locator(".card-media").click();
  await expect(photoViewer).toHaveAttribute("open", "");
  await playbackControl.getByRole("button", { name: "Slideshow", exact: true }).click();
  await expect(nextIn).toBeVisible();
  await expect(nextIn).toHaveText(/Next (in|up)/);

  await expect
    .poll(async () => {
      const frameId = await photoViewer
        .locator(".photo-viewer-frame")
        .getAttribute("data-photo-generation-id");
      return frameId !== baseline.id;
    }, { timeout: 60_000 })
    .toBe(true);
  await expect(nextIn).toBeVisible();
  await expect(nextIn).toHaveText(/Next (in|up)/);

  // Leaving slideshow (or auto-generate) hides the badge again.
  await page.mouse.move(80, 80);
  await playbackControl.getByRole("button", { name: "Hold", exact: true }).click();
  await expect(nextIn).toBeHidden();
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
  await expect(photoViewer).not.toHaveAttribute("open", "");
  await page.getByRole("switch", { name: "Auto-generate" }).uncheck();
});

test("photo viewer favorite toggle syncs with the gallery card and persists across viewer opens", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("photo viewer favorite");
  const acceptedResponse = await generateAndExpectAccepted(page);
  const accepted = await acceptedResponse.json();
  const card = page.locator(`.gallery-card[data-generation-id="${accepted.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/);
  await card.locator(".card-media").click();

  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect(photoViewer.locator(".photo-viewer-frame")).toHaveAttribute(
    "data-photo-generation-id",
    accepted.id,
  );
  const viewerFavorite = photoViewer.locator(".photo-viewer-toolbar .favorite-button");
  const cardFavorite = card.locator(".favorite-button");
  await expect(viewerFavorite).toHaveAttribute("data-generation-id", accepted.id);
  await expect(viewerFavorite).toHaveAttribute("aria-pressed", "false");
  await expect(viewerFavorite).toHaveAttribute("aria-label", "Add to Favorites");

  await page.mouse.move(80, 80);
  await viewerFavorite.click();
  await expect(viewerFavorite).toHaveAttribute("aria-pressed", "true");
  await expect(viewerFavorite).toHaveAttribute("aria-label", "Remove from Favorites");
  await expect(page.locator("#toast-region")).toContainText("Added to Favorites.");
  await expect(cardFavorite).toHaveAttribute("aria-pressed", "true");

  await page.mouse.move(80, 80);
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
  await expect(photoViewer).not.toHaveAttribute("open", "");
  await card.locator(".card-media").click();
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect(viewerFavorite).toHaveAttribute("aria-pressed", "true");
  await expect(viewerFavorite).toHaveAttribute("aria-label", "Remove from Favorites");

  await page.mouse.move(80, 80);
  await viewerFavorite.click();
  await expect(viewerFavorite).toHaveAttribute("aria-pressed", "false");
  await expect(viewerFavorite).toHaveAttribute("aria-label", "Add to Favorites");
  await expect(page.locator("#toast-region")).toContainText("Removed from Favorites.");
  await expect(cardFavorite).toHaveAttribute("aria-pressed", "false");

  await page.mouse.move(80, 80);
  await photoViewer.getByRole("button", { name: "Close image viewer" }).click();
});

test("photo viewer delete asks for confirmation and removes the generation", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("photo viewer delete");
  const acceptedResponse = await generateAndExpectAccepted(page);
  const accepted = await acceptedResponse.json();
  const card = page.locator(`.gallery-card[data-generation-id="${accepted.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/);
  await card.locator(".card-media").click();

  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  const viewerDelete = photoViewer.locator(".photo-viewer-toolbar .delete-generation-button");
  await expect(viewerDelete).toHaveAttribute("data-generation-id", accepted.id);
  await expect(viewerDelete).toHaveAttribute("aria-label", "Delete generation");

  await page.mouse.move(80, 80);
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain("It will disappear from your history and cannot be undone.");
    return dialog.dismiss();
  });
  await viewerDelete.click();
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect(card).toBeVisible();

  await page.mouse.move(80, 80);
  page.once("dialog", (dialog) => dialog.accept());
  await viewerDelete.click();
  await expect(photoViewer).not.toHaveAttribute("open", "");
  await expect(card).toHaveCount(0);
  await expect(page.locator("#toast-region")).toContainText("Generation deleted.");
});

test("image card toolbar delete confirms and removes the generation", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("card toolbar delete");
  const acceptedResponse = await generateAndExpectAccepted(page);
  const accepted = await acceptedResponse.json();
  const card = page.locator(`.gallery-card[data-generation-id="${accepted.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/);
  const cardDelete = card.getByRole("button", { name: "Delete generation" });

  // Activate the restored delete button via keyboard. A mouse click on the
  // hover-revealed button is reset mid-click when the confirm round-trip
  // shifts scroll/focus, leaving the media button to intercept the pointer.
  // Focus + Enter fires the same native click the delegated handler acts on.
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain("It will disappear from your history and cannot be undone.");
    return dialog.dismiss();
  });
  await cardDelete.focus();
  await expect(cardDelete).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(card).toHaveCount(1);

  page.once("dialog", (dialog) => dialog.accept());
  await cardDelete.focus();
  await page.keyboard.press("Enter");
  await expect(card).toHaveCount(0);
  await expect(page.locator("#toast-region")).toContainText("Generation deleted.");
});

test("folder card toolbar delete confirms and removes the collection", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const response = await page.request.post("/api/collections", {
    headers: { "X-CSRF-Token": session.csrf_token }, data: { name: "Card delete folder" },
  });
  expect(response.status()).toBe(201);
  const folder = await response.json();
  await page.reload();
  const tile = page.locator(`[data-gallery-card="collection"][data-collection-id="${folder.id}"]`);
  await expect(tile).toHaveCount(1);
  const tileDelete = tile.getByRole("button", { name: "Delete collection Card delete folder" });
  const dialog = page.locator("#collection-delete-dialog");

  // Activate the restored delete button via keyboard. A mouse click on the
  // hover-revealed button is reset mid-click when the confirm dialog
  // round-trip shifts focus/scroll, leaving the full-face open button to
  // intercept the pointer. Focus + Enter fires the same native click the
  // delegated handler acts on.
  await tileDelete.focus();
  await expect(tileDelete).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveAttribute("open", "");
  await expect(dialog).toContainText("Delete ‘Card delete folder’ and everything inside?");
  await dialog.locator(".dialog-actions").getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(tile).toHaveCount(1);

  await tileDelete.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveAttribute("open", "");
  await dialog.getByRole("button", { name: "Delete everything", exact: true }).click();
  await expect(tile).toHaveCount(0);
  await expect(page.locator("#toast-region")).toContainText("Collection and its contents were deleted.");
});

test("photo viewer download control downloads the image being viewed", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("photo viewer download");
  const acceptedResponse = await generateAndExpectAccepted(page);
  const accepted = await acceptedResponse.json();
  const card = page.locator(`.gallery-card[data-generation-id="${accepted.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/);
  await card.locator(".card-media").click();

  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  const viewerDownload = photoViewer.locator(".photo-viewer-toolbar .download-button");
  await expect(viewerDownload).toHaveAttribute("aria-label", "Download current image");
  const imageSrc = await photoViewer.locator(".photo-viewer-media img").getAttribute("src");
  await expect(viewerDownload).toHaveAttribute("href", imageSrc);

  await page.mouse.move(80, 80);
  const downloadPromise = page.waitForEvent("download");
  await viewerDownload.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).not.toBe("");
});

test("photo viewer offers generate and batch progress without leaving the viewer", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("viewer dock baseline");
  const baselineResponse = await generateAndExpectAccepted(page);
  const baseline = await baselineResponse.json();
  const baselineCard = page.locator(`.gallery-card[data-generation-id="${baseline.id}"]`);
  await expect(baselineCard).toHaveClass(/status-succeeded/);
  await baselineCard.locator(".card-media").click();

  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  const viewerGenerate = photoViewer.locator("#photo-generate-button");
  await expect(viewerGenerate).toBeVisible();
  await expect(viewerGenerate).toBeEnabled();
  const activityWidget = photoViewer.locator(".photo-viewer-activity-host .generation-activity");
  await expect(activityWidget).toHaveCount(0);

  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations" &&
      response.request().method() === "POST",
  );
  // The dock fades on idle; re-trigger activity so the control is clickable.
  await page.mouse.move(80, 80);
  await viewerGenerate.click();
  const response = await responsePromise;
  expect(response.status(), await response.text()).toBe(201);
  const queued = await response.json();
  await expect(photoViewer).toHaveAttribute("open", "");
  // The batch is in flight: the top-bar-style progress widget reports in the viewer.
  await expect(activityWidget).toBeVisible();
  await expect(activityWidget).toHaveAttribute("role", "progressbar");

  // Controls fade on idle while the progress keeps reporting.
  await expect(photoViewer).not.toHaveClass(/controls-visible/, { timeout: 6000 });
  await expect(viewerGenerate).toHaveCSS("opacity", "0");
  await expect(activityWidget).toHaveCSS("opacity", "1");

  // After the batch resolves, the widget clears following the top bar's grace period.
  const queuedCard = page.locator(`.gallery-card[data-generation-id="${queued.id}"]`);
  await expect(queuedCard).toHaveClass(/status-succeeded/);
  await expect(activityWidget).toHaveCount(0, { timeout: 15000 });
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
  await expect(page.locator("#service-banner")).toContainText("Checking ComfyUI runtimes");
  const generateButton = page.getByRole("button", { name: "Generate" });
  await expect(generateButton).toBeEnabled();
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("service status is still pending");
  await expect(generateButton).toBeEnabled();

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
  await page.route("**/api/generations?limit=24&collection_id=", async (route) => {
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
    await deleteSelectedCard(page, card);
    await expect(card).toHaveCount(0);
  } finally {
    releaseGallery();
    await page.unrouteAll({ behavior: "wait" });
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
  await page.route("**/api/generations?limit=24&collection_id=", async (route) => {
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

    await deleteSelectedCard(page, card);
    await expect(card).toHaveCount(0);
  } finally {
    releaseGallery();
    await page.unrouteAll({ behavior: "wait" });
    await producer?.close();
  }
});

test("generation source selection is singular and transactional", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Krea 2 NSFW V4");

  const trigger = page.locator("#workflow-source");
  await trigger.click();
  const dialog = page.locator("#source-picker-dialog");
  const workflow = dialog.locator("[data-source-workflow-choice]");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Generation source" })).toBeVisible();
  await expect(dialog.locator("table")).toHaveCount(0);
  await expect(dialog.getByText(/Primary|Include|Rating|Technologies/, { exact: true })).toHaveCount(0);

  const genericOption = workflow.locator("option").filter({ hasText: "Generic Landscape" });
  const genericKey = await genericOption.getAttribute("value");
  expect(genericKey).toBeTruthy();
  await workflow.selectOption(genericKey);
  await expect(dialog.locator(".source-workflow-select strong")).toHaveText("Generic Landscape");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(trigger).toContainText("Krea 2 NSFW V4");

  await trigger.click();
  await expect(dialog.locator("[data-source-workflow-choice]")).not.toHaveValue(genericKey);
  await dialog.locator("[data-source-workflow-choice]").selectOption(genericKey);
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(trigger).toHaveAttribute("data-source-key", genericKey);
  await expect(trigger).toContainText("Generic Landscape");

  await selectPublishedSource(page, "Krea 2 NSFW V4");
});


test("tiered checkpoint choices reorder, persist, and fan out", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("checkpoint tier lighthouse");

  const loraSection = page.getByRole("button", { name: "LoRAs", exact: true });
  if (await loraSection.getAttribute("aria-expanded") !== "true") await loraSection.click();
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("1.25");
  await page.getByRole("button", { name: "Reorder Beta" }).press("ArrowUp");
  const submittedStack = [{ id: "b", strength: 1.25 }, { id: "a", strength: 0 }];
  const trigger = page.locator("#workflow-source");
  await trigger.click();
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-checkpoint-tier]")).toHaveCount(4);
  await expect(dialog.locator(".checkpoint-tier-rail > strong")).toHaveText([
    "Top picks",
    "Preferred",
    "Occasional",
    "Unsorted",
  ]);
  await expect(dialog.locator(".checkpoint-tier-occasional .checkpoint-tier-empty")).toHaveText(
    "Drop checkpoints here",
  );

  const v4Card = dialog
    .locator("[data-checkpoint-card]")
    .filter({ hasText: "Moody Krea 2 V4 INT8 ConvRot" });
  const topGrid = dialog.locator(".checkpoint-tier-top_picks .checkpoint-tier-grid");
  await v4Card.locator("[data-checkpoint-drag-handle]").dragTo(topGrid);
  await expect(topGrid).toContainText("Moody Krea 2 V4 INT8 ConvRot");
  await expect(dialog.locator(".checkpoint-tier-unsorted")).not.toContainText(
    "Moody Krea 2 V4 INT8 ConvRot",
  );

  await dialog.getByRole("button", { name: "Select all", exact: true }).click();
  await expect(dialog.locator("[data-source-selection-count]")).toHaveText("5 of 5 selected");
  const preferenceSave = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/preferences" &&
      response.request().method() === "PUT",
  );
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  const savedBody = (await preferenceSave).request().postDataJSON();
  expect(savedBody.checkpoint_tiers).toBeTruthy();
  await expect(trigger).toContainText("5 checkpoints selected");

  await trigger.click();
  await expect(dialog.locator(".checkpoint-tier-top_picks")).toContainText(
    "Moody Krea 2 V4 INT8 ConvRot",
  );
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  const generationRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/generations/batch" && request.method() === "POST") {
      generationRequests.push(...request.postDataJSON().items);
    }
  });
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page.locator("#toast-region")).toContainText("5 generations queued.");
  await expect.poll(() => generationRequests.length).toBe(5);
  expect(
    generationRequests
      .map((request) => request.parameters.checkpoint)
      .sort((first, second) => first.localeCompare(second)),
  ).toEqual(["cutie_x_int8", "tyjr_mxfp8", "v4_bf16", "v4_int8", "v5_bf16"]);
  expect(new Set(generationRequests.map((request) => request.parameters.seed)).size).toBe(1);
  for (const request of generationRequests) expect(request.parameters.loras).toEqual(submittedStack);
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("0.25");
  for (const request of generationRequests) expect(request.parameters.loras).toEqual(submittedStack);
  expect(
    generationRequests.every(
      (request) =>
        typeof request.parameters.checkpoint === "string" &&
        !Array.isArray(request.parameters.checkpoint),
    ),
  ).toBe(true);
});


test("checkpoint search is non-destructive and Clear all prevents Apply", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");

  await page.locator("#workflow-source").click();
  const dialog = page.locator("#source-picker-dialog");
  const search = dialog.getByRole("searchbox", { name: "Search checkpoints" });
  await search.fill("TYJR");
  await expect(dialog.locator("[data-checkpoint-card]")).toHaveCount(1);
  await expect(dialog.locator("[data-checkpoint-drag-handle]")).toBeDisabled();
  await search.fill("");
  await expect(dialog.locator("[data-checkpoint-card]")).toHaveCount(5);

  await dialog.getByRole("button", { name: "Clear all", exact: true }).click();
  await expect(dialog.locator("[data-source-selection-count]")).toHaveText("0 of 5 selected");
  await expect(dialog.getByRole("button", { name: "Apply", exact: true })).toBeDisabled();

  const unsortedToggle = dialog
    .locator(".checkpoint-tier-unsorted")
    .getByRole("checkbox", { name: /every checkpoint in Unsorted/ });
  if (await unsortedToggle.isEnabled()) await unsortedToggle.check();
  const topToggle = dialog
    .locator(".checkpoint-tier-top_picks")
    .getByRole("checkbox", { name: /every checkpoint in Top picks/ });
  if (await topToggle.isEnabled()) await topToggle.check();
  await expect(dialog.getByRole("button", { name: "Apply", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
});


test("checkpoint tier preference persists across reload and supports keyboard moves", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");

  await page.locator("#workflow-source").click();
  let dialog = page.locator("#source-picker-dialog");
  let tyjrHandle = dialog
    .locator("[data-checkpoint-card]")
    .filter({ hasText: "Moody Krea 2 TYJR MXFP8" })
    .locator("[data-checkpoint-drag-handle]");
  await tyjrHandle.focus();
  await tyjrHandle.press("Alt+ArrowUp");
  tyjrHandle = dialog
    .locator("[data-checkpoint-card]")
    .filter({ hasText: "Moody Krea 2 TYJR MXFP8" })
    .locator("[data-checkpoint-drag-handle]");
  await tyjrHandle.press("Alt+ArrowUp");
  await expect(dialog.locator(".checkpoint-tier-preferred")).toContainText(
    "Moody Krea 2 TYJR MXFP8",
  );

  const saved = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/preferences" &&
      response.request().method() === "PUT" &&
      Object.values(response.request().postDataJSON()?.checkpoint_tiers || {})
        .flatMap((selectors) => Object.values(selectors))
        .some((tiers) => tiers.preferred?.includes("tyjr_mxfp8")),
  );
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  expect((await saved).ok()).toBe(true);

  const preferencesLoaded = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/preferences" &&
      response.request().method() === "GET",
  );
  await page.reload();
  const preferences = await (await preferencesLoaded).json();
  expect(
    Object.values(preferences.checkpoint_tiers)
      .flatMap((selectors) => Object.values(selectors))
      .some((tiers) => tiers.preferred?.includes("tyjr_mxfp8")),
  ).toBe(true);

  await expect(page.locator("#workflow-source")).toBeEnabled();
  await page.locator("#workflow-source").click();
  dialog = page.locator("#source-picker-dialog");
  // Reload can select the workflow from retained history; inspect the workflow
  // whose tier preference this test saved, independently of earlier journeys.
  const reloadedWorkflow = dialog.locator("[data-source-workflow-choice]");
  const moodyKey = await reloadedWorkflow.locator("option")
    .filter({ hasText: "Moody Krea 2 Mix V4" }).getAttribute("value");
  await reloadedWorkflow.selectOption(moodyKey);
  await expect(dialog.locator(".checkpoint-tier-preferred")).toContainText(
    "Moody Krea 2 TYJR MXFP8",
  );
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
});


test("gallery defaults to request initiation order when the page arrives unsorted", async ({
  page,
}) => {
  await page.route("**/api/events?**", (route) => route.abort());
  await page.route("**/api/generations?limit=24&collection_id=", async (route) => {
    const generation = (id, acceptedAt, status, sourceKey, sourceName) => ({
      id,
      accepted_at: acceptedAt,
      status,
      source_key: sourceKey,
      workflow_display_name: sourceName,
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
          generation("oldest", "2026-07-14T12:00:00Z", "succeeded", "colored-source", "Colored Source"),
          generation("newest-active", "2026-07-14T12:02:00Z", "running", "other-source", "Other Source"),
          generation("previous", "2026-07-14T12:01:00Z", "succeeded", "colored-source", "Colored Source"),
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

test("empty checkpoint tiers retain their drop area and vertical rail label", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await page.locator("#workflow-source").click();

  const occasional = page.locator("#source-picker-dialog .checkpoint-tier-occasional");
  await expect(occasional.locator(".checkpoint-tier-empty")).toHaveText("Drop checkpoints here");
  await expect(
    occasional.getByRole("checkbox", { name: /every checkpoint in Occasional/ }),
  ).toBeDisabled();
  expect((await occasional.boundingBox())?.height).toBeGreaterThanOrEqual(90);
  expect(
    await occasional
      .locator(".checkpoint-tier-rail > strong")
      .evaluate((element) => getComputedStyle(element).writingMode),
  ).toBe("vertical-rl");
});


test("generation source trigger shows only workflow and checkpoint count", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");

  const trigger = page.locator("#workflow-source");
  await trigger.click();
  const picker = page.locator("#source-picker-dialog");
  const selectAll = picker.getByRole("button", { name: "Select all", exact: true });
  if (await selectAll.isEnabled()) await selectAll.click();
  await picker.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(trigger.locator("#generation-source-value")).toHaveText("Moody Krea 2 Mix V4");
  await expect(trigger.locator(".source-picker-current small")).toHaveText(
    /checkpoints? selected/,
  );
  await expect(trigger.locator(".source-color-dot")).toHaveCount(0);
  await expect(trigger).not.toContainText(/sources selected|generations planned|Available/);
  expect(await trigger.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(false);
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
  const columnThinkingMode = columnAssistant.locator("#prompt-assistant-thinking-mode");
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
  const focusedThinkingMode = dialog.locator("#prompt-editor-thinking-mode");
  await expect(focusedPrompt).toHaveValue("draft that should remain");
  await expect(focusedDirection).toHaveValue("column direction");
  await expect(focusedCreateMode).toBeChecked();
  await expect(focusedThinkingMode).toBeChecked();
  await expect(columnThinkingMode).toBeChecked();
  await expect(focusedThinkingMode).toBeHidden();
  await dialog.locator(".prompt-preprocessor summary").click();
  await focusedPrompt.fill("this canceled draft should not be applied");
  await focusedDirection.fill("canceled direction");
  await focusedRefineMode.check();
  await focusedThinkingMode.uncheck();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(prompt).toHaveValue("draft that should remain");
  await expect(columnDirection).toHaveValue("column direction");
  await expect(columnCreateMode).toBeChecked();
  await expect(columnThinkingMode).toBeChecked();
  await expect(openEditor).toBeFocused();

  const longPrompt = Array.from({ length: 60 }, () => "cinematic detail").join(" ");
  await openEditor.click();
  await expect(focusedDirection).toHaveValue("column direction");
  await expect(focusedCreateMode).toBeChecked();
  await expect(focusedThinkingMode).toBeChecked();
  await focusedPrompt.fill(longPrompt);
  await expect(dialog.locator("[data-prompt-word-count]")).toHaveText("120 words");
  await expect(dialog.locator("[data-prompt-character-count]")).toHaveText(
    `${longPrompt.length.toLocaleString()} characters`,
  );
  await focusedDirection.fill("focused assistant direction");
  await dialog.locator(".prompt-preprocessor summary").click();
  await focusedThinkingMode.uncheck();
  await dialog.locator(".prompt-preprocessor summary").click();
  const focusedCompositionRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST",
  );
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  expect((await focusedCompositionRequest).postDataJSON()).toMatchObject({
    mode: "create",
    prompt: longPrompt,
    creative_direction: "focused assistant direction",
    think: false,
  });
  const composedPrompt =
    "focused assistant direction, detailed photographic rendering, soft natural light, " +
    "shallow depth of field, high detail";
  await expect(focusedPrompt).toHaveValue(composedPrompt);
  await expect(prompt).toHaveValue("draft that should remain");
  await expect(columnDirection).toHaveValue("column direction");
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(dialog).not.toHaveAttribute("open", "");
  await expect(prompt).toHaveValue(composedPrompt);
  await expect(columnDirection).toHaveValue("focused assistant direction");
  await expect(columnCreateMode).toBeChecked();
  await expect(columnThinkingMode).not.toBeChecked();
  await expect(openEditor).toBeFocused();
  await openEditor.click();
  await expect(focusedThinkingMode).not.toBeChecked();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
});

test("clipboard paste replaces the prompt text in both prompt surfaces", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("existing draft");
  await page.evaluate(() => navigator.clipboard.writeText("clipboard one"));
  await page.getByRole("button", { name: "Replace prompt with clipboard contents" }).click();
  await expect(prompt).toHaveValue("clipboard one");

  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  const dialog = page.locator("#prompt-editor-dialog");
  await expect(dialog).toHaveAttribute("open", "");
  await page.evaluate(() => navigator.clipboard.writeText("clipboard two"));
  await dialog.getByRole("button", { name: "Paste", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "Prompt editor" })).toHaveValue("clipboard two");
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
    window.setTimeout = (handler, delay, ...args) => {
      const servicePoll =
        delay === 10_000 && String(handler).includes("refreshServices");
      return setTimeout(handler, servicePoll ? 100 : delay, ...args);
    };
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

  const sourceBoard = sourceDialog.locator(".checkpoint-tier-board");
  const sourceBoardScrollTop = await sourceBoard.evaluate((board) => {
    board.style.height = "36px";
    board.scrollTop = 24;
    window.__sourceBoardBeforeChangedServicePoll = board;
    return board.scrollTop;
  });
  expect(sourceBoardScrollTop).toBeGreaterThan(0);
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
        window.__sourceBoardBeforeChangedServicePoll ===
        document.querySelector("#source-picker-dialog .checkpoint-tier-board"),
    ),
  ).toBe(true);
  await expect(sourceBoard).toHaveJSProperty("scrollTop", sourceBoardScrollTop);
  expect(workflowCatalogRequests).toBe(catalogRequestsBeforePoll);

  const deferredCatalogRefresh = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/workflows" &&
      response.request().method() === "GET",
  );
  await sourceDialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await deferredCatalogRefresh;
  await expect(page.getByRole("button", { name: "Generate" })).toBeEnabled();
  await page.unrouteAll({ behavior: "wait" });
});

test("published Krea source exposes choice controls, strict outputs, and the authored result hierarchy", async ({
  page,
}) => {
  test.setTimeout(90_000);
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

  const seedRandom = page.getByLabel("Random seed", { exact: true });
  const seedValue = page.getByLabel("Seed value", { exact: true });
  await seedRandom.focus();
  await expect(generationPanel.getByRole("tooltip")).toHaveCount(0);
  await seedRandom.blur();
  await expect(seedRandom).toBeChecked();
  await expect(seedValue).toBeDisabled();
  await seedRandom.uncheck();
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
  const generation = await generationResponse.json();
  const generationRequest = generationResponse.request().postDataJSON();
  expect(generationRequest.parameters).toMatchObject({
    width: 1024,
    height: 1600,
    lora: "knp_v3_1",
    lora_strength: 0.7,
  });
  expect(JSON.stringify(generationRequest)).not.toMatch(/safetensors|options_json|binding/i);

  const card = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/, { timeout: 30_000 });
  await expect(card.locator(".batch-count")).toHaveText("2");
  await clickGalleryControl(card.locator(".card-details-button"));

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
  await expect(prototypes).toHaveCount(0);
  await expect(comparisons).toHaveCount(0);
  await expect(additionalImages).toHaveCount(0);
  await expect(detailDialog.locator("a.artifact-download")).toHaveCount(2);

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
  test.setTimeout(90_000);
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
  await expect(blockerCard).toHaveClass(/status-running/, { timeout: 40_000 });

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
  await deleteSelectedCard(page, blockerCard);
  await expect(blockerCard).toHaveCount(0);
});

test("working card reserves final aspect ratio and cancels in place", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Krea 2 NSFW V4");
  await page.getByRole("spinbutton", { name: "Width", exact: true }).fill("384");
  await page.getByRole("spinbutton", { name: "Height", exact: true }).fill("512");
  // Own the viewer's older-image fixture rather than relying on a previous test.
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("viewer navigation reference");
  const reference = await (await generateAndExpectAccepted(page)).json();
  await expect(page.locator(`.gallery-card[data-generation-id="${reference.id}"]`)).toHaveClass(/status-succeeded/);
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow cancellation sample");
  const generation = await (await generateAndExpectAccepted(page)).json();

  const card = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
  await expect(card).toHaveClass(/status-running/);
  await expect(card.locator(".card-media img")).toBeVisible();
  await expect(card.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  const generationId = await card.getAttribute("data-generation-id");

  // Cancel while the sample is running, before spending time on viewer navigation.
  await card.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(card.locator(".media-status")).toContainText("Cancelled generation", {
    timeout: 40_000,
  });
  await card.locator(".card-media").click();
  const photoViewer = page.locator("#photo-viewer");
  await expect(photoViewer).toHaveAttribute("open", "");
  await expect(photoViewer.getByRole("img")).toHaveAttribute("alt", /Cancelled generation/);
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
  const previewBox = await dropzone.locator(".image-input-selection img").boundingBox();
  const detailsBox = await dropzone.locator(".image-input-details").boundingBox();
  const browseBox = await dropzone.locator(".image-input-browse").boundingBox();
  const removeBox = await dropzone.locator(".image-input-remove").boundingBox();
  expect(previewBox).not.toBeNull();
  expect(detailsBox).not.toBeNull();
  expect(browseBox).not.toBeNull();
  expect(removeBox).not.toBeNull();
  expect(detailsBox.y).toBeGreaterThanOrEqual(previewBox.y + previewBox.height);
  expect(browseBox.y).toBeGreaterThanOrEqual(detailsBox.y + detailsBox.height);
  expect(Math.abs(browseBox.y - removeBox.y)).toBeLessThan(1);
  expect(Math.abs(browseBox.width - removeBox.width)).toBeLessThan(1);
  expect(Math.abs(browseBox.height - removeBox.height)).toBeLessThan(1);

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
  await clickGalleryControl(editedCard.getByRole("button", { name: "Recall settings" }));
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

test("Prompt Assistant submits the live create mode and generation preserves control elements", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const createMode = page.getByRole("radio", {
    name: "New Prompt from Creative Direction",
  });
  const applyCreativeDirection = page.locator(
    '#prompt-assistant [data-action="compose-prompt"]',
  );
  const thinkingMode = page.locator("#prompt-assistant-thinking-mode");
  const generate = page.locator("#generate-button");
  const composedPrompt =
    "a crimson fox beneath moonlit pines, detailed photographic rendering, soft natural " +
    "light, shallow depth of field, high detail";

  await prompt.fill("the prompt that must be replaced");
  await direction.fill("a crimson fox beneath moonlit pines");
  await createMode.check();
  await expect(thinkingMode).toBeChecked();
  await page.locator("#prompt-assistant .prompt-preprocessor summary").click();
  await thinkingMode.uncheck();
  await page.locator("#prompt-assistant .prompt-preprocessor summary").click();
  await page.evaluate(() => {
    const promptElement = document.querySelector('[data-control-id="prompt"]');
    const directionElement = document.querySelector("#creative-direction");
    const thinkingElement = document.querySelector("#prompt-assistant-thinking-mode");
    promptElement.style.height = "280px";
    directionElement.style.height = "170px";
    window.__stablePromptElement = promptElement;
    window.__stableDirectionElement = directionElement;
    window.__stableThinkingElement = thinkingElement;
  });

  let composePayload;
  let releaseComposition;
  const compositionGate = new Promise((resolve) => {
    releaseComposition = resolve;
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    composePayload = route.request().postDataJSON();
    await compositionGate;
    await route.continue();
  });

  const compositionRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST",
  );
  await applyCreativeDirection.click();
  await compositionRequest;
  expect(composePayload).toEqual({
    mode: "create",
    prompt: "the prompt that must be replaced",
    creative_direction: "a crimson fox beneath moonlit pines",
    think: false,
    instructions: await page.locator("#prompt-assistant-instructions").inputValue(),
  });
  await expect(applyCreativeDirection).toBeDisabled();
  await expect(applyCreativeDirection).toHaveText("Applying…");
  expect(
    await page.evaluate(() => ({
      promptStable:
        document.querySelector('[data-control-id="prompt"]') ===
        window.__stablePromptElement,
      directionStable:
        document.querySelector("#creative-direction") === window.__stableDirectionElement,
      thinkingStable:
        document.querySelector("#prompt-assistant-thinking-mode") ===
        window.__stableThinkingElement,
      thinkingEnabled: window.__stableThinkingElement.checked,
      promptHeight: window.__stablePromptElement.style.height,
      directionHeight: window.__stableDirectionElement.style.height,
    })),
  ).toEqual({
    promptStable: true,
    directionStable: true,
    thinkingStable: true,
    thinkingEnabled: false,
    promptHeight: "280px",
    directionHeight: "170px",
  });

  releaseComposition();
  await expect(prompt).toHaveValue(composedPrompt);
  await expect(applyCreativeDirection).toBeEnabled();
  expect(
    await page.evaluate(() => ({
      promptStable:
        document.querySelector('[data-control-id="prompt"]') ===
        window.__stablePromptElement,
      directionStable:
        document.querySelector("#creative-direction") === window.__stableDirectionElement,
      thinkingStable:
        document.querySelector("#prompt-assistant-thinking-mode") ===
        window.__stableThinkingElement,
      thinkingEnabled: window.__stableThinkingElement.checked,
      promptHeight: window.__stablePromptElement.style.height,
      directionHeight: window.__stableDirectionElement.style.height,
    })),
  ).toEqual({
    promptStable: true,
    directionStable: true,
    thinkingStable: true,
    thinkingEnabled: false,
    promptHeight: "280px",
    directionHeight: "170px",
  });

  let releaseGeneration;
  const generationGate = new Promise((resolve) => {
    releaseGeneration = resolve;
  });
  await page.route("**/api/generations", async (route) => {
    if (route.request().method() === "POST") await generationGate;
    await route.continue();
  });
  const generationResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations" &&
      response.request().method() === "POST",
  );
  await generate.click();
  await expect(generate).toHaveText("Queueing…");
  expect(
    await page.evaluate(() =>
      document.querySelector('[data-control-id="prompt"]') === window.__stablePromptElement &&
      document.querySelector("#creative-direction") === window.__stableDirectionElement &&
      document.querySelector("#prompt-assistant-thinking-mode") ===
        window.__stableThinkingElement,
    ),
  ).toBe(true);
  releaseGeneration();
  expect((await generationResponse).status()).toBe(201);
  await expect(generate).toHaveText("Generate");
  expect(
    await page.evaluate(() => ({
      promptStable:
        document.querySelector('[data-control-id="prompt"]') ===
        window.__stablePromptElement,
      directionStable:
        document.querySelector("#creative-direction") === window.__stableDirectionElement,
      thinkingStable:
        document.querySelector("#prompt-assistant-thinking-mode") ===
        window.__stableThinkingElement,
      thinkingEnabled: window.__stableThinkingElement.checked,
      promptHeight: window.__stablePromptElement.style.height,
      directionHeight: window.__stableDirectionElement.style.height,
    })),
  ).toEqual({
    promptStable: true,
    directionStable: true,
    thinkingStable: true,
    thinkingEnabled: false,
    promptHeight: "280px",
    directionHeight: "170px",
  });
});

test("Creative Direction shows a gold-while-composing and green-when-applied prompt border signal", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const createMode = page.getByRole("radio", {
    name: "New Prompt from Creative Direction",
  });
  const applyCreativeDirection = page.locator(
    '#prompt-assistant [data-action="compose-prompt"]',
  );
  // Directions are unique to this test: successful create runs are recorded in
  // the assistant history, and the assistant rejects a repeated direction as a
  // duplicate of a recorded output (earlier specs use other directions).
  const firstDirection = "a silver heron over reed beds";
  const composedPrompt =
    "a silver heron over reed beds, detailed photographic rendering, soft natural " +
    "light, shallow depth of field, high detail";
  const composeRequest = (predicate) =>
    page.waitForRequest((request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST" && predicate(),
    );

  // Move the pointer off the prompt control so :hover styling cannot shadow
  // the base border color when asserting the "normal" state.
  const settlePointer = () => page.mouse.move(4, 4);

  const mainSignalStatus = () =>
    page.evaluate(() => {
      const element = document.querySelector('[data-control-id="prompt"]');
      if (!element) return null;
      if (element.classList.contains("is-direction-composing")) return "composing";
      if (element.classList.contains("is-direction-applied")) return "applied";
      return "idle";
    });
  const dialogSignalStatus = () =>
    page.evaluate(() => {
      const element = document.querySelector(
        "#prompt-editor-dialog[open] #prompt-editor-textarea",
      );
      if (!element) return null;
      if (element.classList.contains("is-direction-composing")) return "composing";
      if (element.classList.contains("is-direction-applied")) return "applied";
      return "idle";
    });

  // 1. While a composition request is in flight, the prompt border pulses gold.
  let releaseComposition;
  const compositionGate = new Promise((resolve) => {
    releaseComposition = resolve;
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await compositionGate;
    await route.continue();
  });

  await prompt.fill("the prompt that must be replaced");
  await direction.fill(firstDirection);
  await createMode.check();
  await page.locator("#prompt-assistant .prompt-preprocessor summary").click();
  await page.locator("#prompt-assistant-thinking-mode").uncheck();
  await page.locator("#prompt-assistant .prompt-preprocessor summary").click();

  const inFlightRequest = composeRequest(() => true);
  await applyCreativeDirection.click();
  await inFlightRequest;
  await expect.poll(mainSignalStatus).toBe("composing");

  releaseComposition();
  await expect(prompt).toHaveValue(composedPrompt);
  await expect.poll(mainSignalStatus).toBe("applied");
  await expect(prompt).toHaveCSS("border-color", "rgb(102, 198, 154)");

  // 2. Editing the prompt immediately clears the applied signal.
  await prompt.click();
  await page.keyboard.press("End");
  await page.keyboard.type(" more");
  await expect.poll(mainSignalStatus).toBe("idle");
  await settlePointer();
  await expect(prompt).toHaveCSS("border-color", "rgb(40, 54, 71)");

  // 3. Re-applying re-arms the green signal; queueing a generation resets it.
  //    The assistant rejects a second creation for the same direction as a
  //    duplicate of a recorded output, so vary the direction.
  const secondDirection = "a lighthouse in a storm at dusk";
  const secondComposedPrompt =
    "a lighthouse in a storm at dusk, detailed photographic rendering, soft natural " +
    "light, shallow depth of field, high detail";
  await direction.fill(secondDirection);
  await applyCreativeDirection.click();
  await expect(prompt).toHaveValue(secondComposedPrompt);
  await expect.poll(mainSignalStatus).toBe("applied");
  await expect(prompt).toHaveCSS("border-color", "rgb(102, 198, 154)");
  await generateAndExpectAccepted(page);
  await expect.poll(mainSignalStatus).toBe("idle");
  await settlePointer();
  await expect(prompt).toHaveCSS("border-color", "rgb(40, 54, 71)");

  // 4. A failed composition flashes gold and then returns to normal styling.
  const failureMessage = "The prompt model is temporarily unavailable.";
  let releaseFailure;
  const failureGate = new Promise((resolve) => {
    releaseFailure = resolve;
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await failureGate;
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "prompt_assistant_unavailable",
          message: failureMessage,
          fields: {},
        },
      }),
    });
  });
  const failedRequest = composeRequest(() => true);
  await applyCreativeDirection.click();
  await failedRequest;
  await expect.poll(mainSignalStatus).toBe("composing");
  releaseFailure();
  await expect(page.locator("#prompt-assistant-error")).toHaveText(failureMessage);
  await expect(page.locator("#toast-region .toast.error")).toHaveText(failureMessage);
  await expect.poll(mainSignalStatus).toBe("idle");
  await settlePointer();
  await expect(prompt).toHaveCSS("border-color", "rgb(40, 54, 71)");

  // 5. The focused prompt editor shows the same signal locally, and applying the
  //    committed draft transfers it to the main prompt field. Each composition
  //    needs a fresh direction: the assistant rejects a repeated direction as a
  //    duplicate of a recorded output.
  const thirdDirection = "an abandoned greenhouse full of ferns";
  const thirdComposedPrompt =
    "an abandoned greenhouse full of ferns, detailed photographic rendering, soft " +
    "natural light, shallow depth of field, high detail";
  const fourthDirection = "a harbor town at first light";
  const fourthComposedPrompt =
    "a harbor town at first light, detailed photographic rendering, soft natural " +
    "light, shallow depth of field, high detail";

  await page.unroute("**/api/prompt-assistant/compose");
  const dialog = page.locator("#prompt-editor-dialog");
  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  await expect(dialog).toBeVisible();
  const dialogPrompt = dialog.locator("#prompt-editor-textarea");
  await expect(dialogPrompt).toHaveValue(secondComposedPrompt);
  const dialogDirection = dialog.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  await dialogDirection.fill(thirdDirection);

  let releaseDialogComposition;
  const dialogCompositionGate = new Promise((resolve) => {
    releaseDialogComposition = resolve;
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await dialogCompositionGate;
    await route.continue();
  });
  const dialogInFlightRequest = composeRequest(() => true);
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  await dialogInFlightRequest;
  await expect.poll(dialogSignalStatus).toBe("composing");
  expect(await mainSignalStatus()).toBe("idle");

  releaseDialogComposition();
  await expect(dialogPrompt).toHaveValue(thirdComposedPrompt);
  await expect.poll(dialogSignalStatus).toBe("applied");
  await expect(dialogPrompt).toHaveCSS("border-color", "rgb(102, 198, 154)");

  // Editing the draft clears the dialog signal without touching the main field.
  await dialogPrompt.click();
  await page.keyboard.press("End");
  await page.keyboard.type(" more");
  await expect.poll(dialogSignalStatus).toBe("idle");
  await settlePointer();
  await expect(dialogPrompt).toHaveCSS("border-color", "rgb(59, 79, 103)");
  expect(await mainSignalStatus()).toBe("idle");

  // Re-composing the edited draft re-arms the green dialog signal.
  await dialogDirection.fill(fourthDirection);
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  await expect(dialogPrompt).toHaveValue(fourthComposedPrompt);
  await expect.poll(dialogSignalStatus).toBe("applied");

  // Applying the committed draft transfers the signal to the main prompt field.
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(prompt).toHaveValue(fourthComposedPrompt);
  await expect.poll(mainSignalStatus).toBe("applied");
  await expect(prompt).toHaveCSS("border-color", "rgb(102, 198, 154)");

  // Queueing a generation resets the main field to normal styling.
  await generateAndExpectAccepted(page);
  await expect.poll(mainSignalStatus).toBe("idle");
  await settlePointer();
  await expect(prompt).toHaveCSS("border-color", "rgb(40, 54, 71)");
});

test("Prompt Assistant failures remain visible with the pre-processor collapsed", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const failureMessage = "Prompt Assistant could not produce a changed prompt after retrying.";
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "prompt_refinement_unchanged",
          message: failureMessage,
          fields: {},
        },
      }),
    });
  });

  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("a portrait");
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  await direction.fill("use warm window light");
  await page.getByRole("button", { name: "Apply Creative Direction" }).click();

  const panelError = page.locator("#prompt-assistant-error");
  await expect(panelError).toBeVisible();
  await expect(panelError).toHaveText(failureMessage);
  await expect(page.locator("#prompt-assistant-thinking-mode")).toHaveAttribute(
    "aria-describedby",
    "prompt-assistant-error",
  );

  await direction.fill("use dramatic side light");
  await expect(panelError).toBeHidden();

  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  const dialog = page.locator("#prompt-editor-dialog");
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  const editorError = dialog.locator("#prompt-editor-assistant-error");
  await expect(editorError).toBeVisible();
  await expect(editorError).toHaveText(failureMessage);
  await expect(dialog.locator("#prompt-editor-thinking-mode")).toHaveAttribute(
    "aria-describedby",
    "prompt-editor-assistant-error",
  );
});

test("auto-generate applies enabled Creative Direction before every generation and queues only when idle", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const useCreativeDirection = page.getByRole("checkbox", {
    name: "Use Creative Direction",
  });
  const generate = page.locator("#generate-button");
  const applyCreativeDirection = page.locator(
    '#prompt-assistant [data-action="compose-prompt"]',
  );
  const automaticControlLayout = await page
    .locator(".auto-generation-options")
    .evaluate((row) => {
      const controls = row.querySelectorAll(
        ".auto-generation-switch, .auto-generation-checkbox",
      );
      const rectangles = [...controls].map((control) => {
        const bounds = control.getBoundingClientRect();
        return { left: bounds.left, top: bounds.top, width: bounds.width };
      });
      return {
        controls: rectangles,
        rowWidth: row.getBoundingClientRect().width,
        sameRow:
          rectangles.length === 2 && Math.abs(rectangles[0].top - rectangles[1].top) < 2,
      };
    });
  expect(automaticControlLayout).toMatchObject({ sameRow: true });
  const sequence = [];
  const generationRequests = [];
  let releaseFirstComposition;
  const firstCompositionGate = new Promise((resolve) => {
    releaseFirstComposition = resolve;
  });
  let firstCompositionHeld = false;
  let releaseFirstGeneration;
  const firstGenerationGate = new Promise((resolve) => {
    releaseFirstGeneration = resolve;
  });
  let firstGenerationHeld = false;

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/prompt-assistant/compose" && request.method() === "POST") {
      sequence.push("compose");
    }
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      sequence.push("generate");
      generationRequests.push(request);
    }
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    if (route.request().method() === "POST" && !firstCompositionHeld) {
      firstCompositionHeld = true;
      await firstCompositionGate;
    }
    await route.continue();
  });
  await page.route("**/api/generations", async (route) => {
    if (route.request().method() === "POST" && !firstGenerationHeld) {
      firstGenerationHeld = true;
      await firstGenerationGate;
    }
    await route.continue();
  });

  await prompt.fill("slow auto lighthouse");
  await direction.fill("cinematic blue hour");
  const firstCompositionPromise = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST",
  );
  const firstRequestPromise = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  await useCreativeDirection.check();
  await autoGenerate.check();
  await firstCompositionPromise;

  await expect(applyCreativeDirection).toBeDisabled();
  await expect(applyCreativeDirection).toHaveText("Applying…");
  await expect(applyCreativeDirection).toHaveAttribute("aria-busy", "true");
  await expect(prompt).toBeEnabled();
  await applyCreativeDirection.evaluate((button) => button.click());
  await page.waitForTimeout(100);
  expect(sequence).toEqual(["compose"]);

  releaseFirstComposition();
  const firstRequest = await firstRequestPromise;

  await expect(autoGenerate).toBeChecked();
  await expect(useCreativeDirection).toBeChecked();
  await expect(generate).toBeDisabled();
  await expect(applyCreativeDirection).toBeEnabled();
  await expect(applyCreativeDirection).toHaveText("Apply Creative Direction");
  await expect(applyCreativeDirection).not.toHaveAttribute("aria-busy", "true");
  await expect(prompt).toBeEnabled();
  await expect(page.locator("#workflow-source")).toBeEnabled();
  expect(sequence.slice(0, 2)).toEqual(["compose", "generate"]);
  expect(firstRequest.postDataJSON().parameters.prompt).toBe(
    "slow auto lighthouse, cinematic blue hour",
  );
  expect(firstRequest.postDataJSON().prompt_assistant_run_id).toBeTruthy();

  await prompt.fill("slow updated auto controls");
  const firstResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations" &&
      response.request().method() === "POST",
  );
  releaseFirstGeneration();
  expect((await firstResponsePromise).status()).toBe(201);

  await page.waitForTimeout(500);
  expect(generationRequests).toHaveLength(1);
  const secondRequest = await page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST" &&
      request !== firstRequest,
  );

  expect(sequence.slice(0, 4)).toEqual(["compose", "generate", "compose", "generate"]);
  expect(secondRequest.postDataJSON().parameters.prompt).toBe(
    "slow updated auto controls, cinematic blue hour",
  );
  expect(secondRequest.postDataJSON().prompt_assistant_run_id).toBeTruthy();

  const thirdRequest = await page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST" &&
      request !== firstRequest &&
      request !== secondRequest,
  );
  await autoGenerate.uncheck();

  expect(sequence.slice(0, 6)).toEqual([
    "compose",
    "generate",
    "compose",
    "generate",
    "compose",
    "generate",
  ]);
  expect(thirdRequest.postDataJSON().parameters.prompt).toBe(
    "slow updated auto controls, cinematic blue hour, cinematic blue hour",
  );
  expect(thirdRequest.postDataJSON().prompt_assistant_run_id).toBeTruthy();
  await page.waitForTimeout(500);
  expect(generationRequests).toHaveLength(3);
  await expect(generate).toBeEnabled();
  await page.unroute("**/api/prompt-assistant/compose");
  await page.unroute("**/api/generations");
});

test("auto-generate keeps targeting the enabled folder while browsing elsewhere", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const created = [];
  for (const name of ["Auto pin source", "Auto pin browse"]) {
    const response = await page.request.post("/api/collections", {
      headers: { "X-CSRF-Token": session.csrf_token },
      data: { name },
    });
    expect(response.status()).toBe(201);
    created.push(await response.json());
  }
  const [sourceFolder, browseFolder] = created;
  await page.reload();
  const folderCard = (id) =>
    page.locator(`[data-gallery-card="collection"][data-collection-id="${id}"]`);

  await selectPublishedSource(page, "Generic Landscape");
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("pinned auto lighthouse");

  await folderCard(sourceFolder.id)
    .getByRole("button", { name: /Open collection/ })
    .click();
  await expect(page).toHaveURL(/#\/c\//u);

  const generationRequests = [];
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST"
    ) generationRequests.push(request);
  });
  const firstResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations" &&
      response.request().method() === "POST",
  );
  await page.locator("#auto-generate").check();
  const first = await (await firstResponsePromise).json();
  expect(first.collection_id).toBe(sourceFolder.id);
  await expect(
    page.locator(`.gallery-card[data-generation-id="${first.id}"]`),
  ).toHaveClass(/status-succeeded/u);

  // Browse to a different folder while auto-generate keeps running.
  const secondResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations" &&
      response.request().method() === "POST",
  );
  await page.getByRole("link", { name: "Home" }).click();
  await folderCard(browseFolder.id)
    .getByRole("button", { name: /Open collection/ })
    .click();
  await expect(page).toHaveURL(/#\/c\//u);

  const second = await (await secondResponsePromise).json();
  expect(generationRequests[0].postDataJSON().collection_id).toBe(sourceFolder.id);
  expect(generationRequests[1].postDataJSON().collection_id).toBe(sourceFolder.id);
  expect(second.collection_id).toBe(sourceFolder.id);
  // The image belongs to the pinned folder, not the one being browsed.
  await expect(
    page.locator(`.gallery-card[data-generation-id="${second.id}"]`),
  ).toHaveCount(0);

  const activity = page.locator("#generation-activity-host");
  await activity.hover();
  await expect(activity.locator(".activity-tooltip")).toBeVisible();
  await expect(activity.locator(".activity-tooltip")).toContainText(
    `Auto-generation is targeting ${sourceFolder.name}`,
  );

  // The pinned image lands in the source folder when the user returns to it.
  await page.getByRole("link", { name: "Home" }).click();
  await folderCard(sourceFolder.id)
    .getByRole("button", { name: /Open collection/ })
    .click();
  await expect(
    page.locator(`.gallery-card[data-generation-id="${second.id}"]`),
  ).toHaveClass(/status-succeeded/u);

  // Manual generation follows the folder on screen once auto-generate is off.
  await page.locator("#auto-generate").uncheck();
  await page.getByRole("link", { name: "Home" }).click();
  await folderCard(browseFolder.id)
    .getByRole("button", { name: /Open collection/ })
    .click();
  const manual = await (await generateAndExpectAccepted(page)).json();
  expect(manual.collection_id).toBe(browseFolder.id);
});

test("auto-generate retries one recoverable composition without parallel requests and queues once", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const useCreativeDirection = page.getByRole("checkbox", {
    name: "Use Creative Direction",
  });
  let composeCalls = 0;
  let generationCalls = 0;
  let activeRequests = 0;
  let maximumActiveRequests = 0;
  const relevantRequests = new Set();
  const trackFinished = (request) => {
    if (!relevantRequests.delete(request)) return;
    activeRequests -= 1;
  };
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() === "POST" &&
      ["/api/prompt-assistant/compose", "/api/generations"].includes(path)
    ) {
      relevantRequests.add(request);
      activeRequests += 1;
      maximumActiveRequests = Math.max(maximumActiveRequests, activeRequests);
      if (path === "/api/generations") generationCalls += 1;
    }
  });
  page.on("requestfinished", trackFinished);
  page.on("requestfailed", trackFinished);
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    composeCalls += 1;
    if (composeCalls === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "ollama_output_budget_exhausted",
            message: "Prompt Assistant exhausted its output-token budget.",
            fields: {},
            details: {},
          },
        }),
      });
      return;
    }
    await route.continue();
  });

  await prompt.fill("retry lighthouse");
  await direction.fill("cinematic blue hour");
  await useCreativeDirection.check();
  await autoGenerate.check();
  await expect(page.locator("#auto-generate-status")).toContainText(
    "Retrying Auto-generate in 1 second",
  );
  await page.waitForTimeout(500);
  expect(composeCalls).toBe(1);
  expect(generationCalls).toBe(0);

  const generationRequest = await page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  await autoGenerate.uncheck();

  expect(composeCalls).toBe(2);
  expect(generationCalls).toBe(1);
  expect(maximumActiveRequests).toBe(1);
  expect(generationRequest.postDataJSON().parameters.prompt).toBe(
    "retry lighthouse, cinematic blue hour",
  );
  expect(generationRequest.postDataJSON().prompt_assistant_run_id).toBeTruthy();
  await page.waitForTimeout(500);
  expect(generationCalls).toBe(1);
  await page.unroute("**/api/prompt-assistant/compose");
});

test("turning Auto-generate off cancels a pending Prompt Assistant retry", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  let composeCalls = 0;
  let generationCalls = 0;
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST"
    ) {
      generationCalls += 1;
    }
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    composeCalls += 1;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "ollama_generate_unavailable",
          message: "Prompt Assistant is temporarily unavailable.",
          fields: {},
          details: {},
        },
      }),
    });
  });

  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(
    "cancelled retry lighthouse",
  );
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill(
    "storm light",
  );
  await page.getByRole("checkbox", { name: "Use Creative Direction" }).check();
  await autoGenerate.check();
  await expect(page.locator("#auto-generate-status")).toContainText(
    "Retrying Auto-generate",
  );
  await autoGenerate.uncheck();
  await expect(page.locator("#auto-generate-status")).toBeHidden();
  await page.waitForTimeout(1_300);

  expect(composeCalls).toBe(1);
  expect(generationCalls).toBe(0);
  await page.unroute("**/api/prompt-assistant/compose");
});

test("changing Creative Direction invalidates backoff and composes the fresh fingerprint", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const compositionBodies = [];
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    compositionBodies.push(route.request().postDataJSON());
    if (compositionBodies.length === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "ollama_output_budget_exhausted",
            message: "Prompt Assistant exhausted its output-token budget.",
            fields: {},
            details: {},
          },
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(
    "fresh fingerprint lighthouse",
  );
  await direction.fill("stale blue hour");
  await page.getByRole("checkbox", { name: "Use Creative Direction" }).check();
  await autoGenerate.check();
  await expect(page.locator("#auto-generate-status")).toContainText(
    "Retrying Auto-generate",
  );

  const freshComposition = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST" &&
      request.postDataJSON().creative_direction === "fresh golden hour",
  );
  await direction.fill("fresh golden hour");
  await freshComposition;
  const generationRequest = await page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  await autoGenerate.uncheck();
  await page.waitForTimeout(1_200);

  expect(compositionBodies).toHaveLength(2);
  expect(compositionBodies[0].creative_direction).toBe("stale blue hour");
  expect(compositionBodies[1].creative_direction).toBe("fresh golden hour");
  expect(generationRequest.postDataJSON().parameters.prompt).toBe(
    "fresh fingerprint lighthouse, fresh golden hour",
  );
  await page.unroute("**/api/prompt-assistant/compose");
});

test("exhausted automatic composition pauses visibly and explicit retry restores operation", async ({
  page,
}) => {
  test.setTimeout(45_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  let composeCalls = 0;
  let allowSuccess = false;
  let generationCalls = 0;
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST"
    ) {
      generationCalls += 1;
    }
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    composeCalls += 1;
    if (allowSuccess) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "ollama_output_budget_exhausted",
          message: "Prompt Assistant exhausted all output budgets.",
          fields: {},
          details: {},
        },
      }),
    });
  });

  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(
    "paused retry lighthouse",
  );
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill(
    "violet dusk",
  );
  await page.getByRole("checkbox", { name: "Use Creative Direction" }).check();
  await autoGenerate.check();

  const paused = page.locator("#auto-generate-status");
  await expect(paused).toHaveAttribute("role", "alert", { timeout: 10_000 });
  await expect(paused).toContainText("Auto-generate paused");
  await expect(autoGenerate).not.toBeChecked();
  expect(composeCalls).toBe(3);
  expect(generationCalls).toBe(0);

  allowSuccess = true;
  const generationRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  await page.getByRole("button", { name: "Retry Auto-generate" }).click();
  await generationRequest;
  await autoGenerate.uncheck();

  expect(composeCalls).toBe(4);
  expect(generationCalls).toBe(1);
  await expect(paused).toBeHidden();
  await page.unroute("**/api/prompt-assistant/compose");
});

test("auto-generate leaves a populated Creative Direction unused when its control is disabled", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const useCreativeDirection = page.getByRole("checkbox", {
    name: "Use Creative Direction",
  });
  const sequence = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/prompt-assistant/compose" && request.method() === "POST") {
      sequence.push("compose");
    }
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      sequence.push("generate");
    }
  });

  await prompt.fill("unmodified lighthouse prompt");
  await direction.fill("this direction must be ignored");
  await expect(useCreativeDirection).not.toBeChecked();
  const generationRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  await autoGenerate.check();
  const request = await generationRequest;
  await autoGenerate.uncheck();

  expect(sequence).toEqual(["generate"]);
  expect(request.postDataJSON().parameters.prompt).toBe("unmodified lighthouse prompt");
  expect(request.postDataJSON().prompt_assistant_run_id).toBeUndefined();
});

test("auto-generate skips an empty enabled Creative Direction without clearing the control", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const useCreativeDirection = page.getByRole("checkbox", {
    name: "Use Creative Direction",
  });
  const sequence = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/prompt-assistant/compose" && request.method() === "POST") {
      sequence.push("compose");
    }
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      sequence.push("generate");
    }
  });

  await prompt.fill("plain empty-direction prompt");
  await useCreativeDirection.check();
  const generationRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  await autoGenerate.check();
  const request = await generationRequest;
  await autoGenerate.uncheck();

  expect(sequence).toEqual(["generate"]);
  expect(request.postDataJSON().parameters.prompt).toBe("plain empty-direction prompt");
  expect(request.postDataJSON().prompt_assistant_run_id).toBeUndefined();
  await expect(useCreativeDirection).toBeChecked();
});

test("auto-generate applies a Creative Direction restored without an input event before queueing", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("background lighthouse");

  const sequence = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/prompt-assistant/compose" && request.method() === "POST") {
      sequence.push("compose");
    }
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      sequence.push("generate");
    }
  });
  const generationRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );

  await page.evaluate(() => {
    const direction = document.querySelector("#creative-direction");
    const autoGenerate = document.querySelector("#auto-generate");
    const useCreativeDirection = document.querySelector(
      "#auto-generate-creative-direction",
    );
    direction.value = "cinematic dusk";
    useCreativeDirection.checked = true;
    autoGenerate.checked = true;
    autoGenerate.dispatchEvent(new Event("change", { bubbles: true }));
  });

  const request = await generationRequest;
  await page.evaluate(() => {
    const autoGenerate = document.querySelector("#auto-generate");
    autoGenerate.checked = false;
    autoGenerate.dispatchEvent(new Event("change", { bubbles: true }));
  });

  expect(sequence.slice(0, 2)).toEqual(["compose", "generate"]);
  expect(request.postDataJSON().parameters.prompt).toBe(
    "background lighthouse, cinematic dusk",
  );
  expect(request.postDataJSON().prompt_assistant_run_id).toBeTruthy();
});

test("a manual Creative Direction composition can prepare the next auto-generate request", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const applyCreativeDirection = page.locator(
    '#prompt-assistant [data-action="compose-prompt"]',
  );
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const sequence = [];
  let releaseComposition;
  const compositionGate = new Promise((resolve) => {
    releaseComposition = resolve;
  });

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/prompt-assistant/compose" && request.method() === "POST") {
      sequence.push("compose");
    }
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      sequence.push("generate");
    }
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await compositionGate;
    await route.continue();
  });

  await prompt.fill("manual preparation lighthouse");
  await direction.fill("storm-lit horizon");
  const compositionRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST",
  );
  await applyCreativeDirection.click();
  await compositionRequest;
  await autoGenerate.check();
  const generationRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  releaseComposition();

  const request = await generationRequest;
  await autoGenerate.uncheck();

  expect(sequence.slice(0, 2)).toEqual(["compose", "generate"]);
  expect(request.postDataJSON().parameters.prompt).toBe(
    "manual preparation lighthouse, storm-lit horizon",
  );
  expect(request.postDataJSON().prompt_assistant_run_id).toBeTruthy();
  await page.unroute("**/api/prompt-assistant/compose");
});

test("auto-generate reevaluates controls changed while Creative Direction is composing", async ({
  page,
}) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", {
    name: "Creative Direction",
    exact: true,
  });
  const autoGenerate = page.getByRole("switch", { name: "Auto-generate" });
  const useCreativeDirection = page.getByRole("checkbox", {
    name: "Use Creative Direction",
  });
  const sequence = [];
  let releaseComposition;
  const compositionGate = new Promise((resolve) => {
    releaseComposition = resolve;
  });

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/prompt-assistant/compose" && request.method() === "POST") {
      sequence.push("compose");
    }
    if (url.pathname === "/api/generations" && request.method() === "POST") {
      sequence.push("generate");
    }
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    await compositionGate;
    await route.continue();
  });

  await prompt.fill("live control lighthouse");
  await direction.fill("temporary blue hour");
  const compositionRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/prompt-assistant/compose" &&
      request.method() === "POST",
  );
  await useCreativeDirection.check();
  await autoGenerate.check();
  await compositionRequest;
  await direction.fill("");
  const generationRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/generations" &&
      request.method() === "POST",
  );
  releaseComposition();

  const request = await generationRequest;
  await autoGenerate.uncheck();

  expect(sequence.slice(0, 2)).toEqual(["compose", "generate"]);
  expect(request.postDataJSON().parameters.prompt).toBe("live control lighthouse");
  expect(request.postDataJSON().prompt_assistant_run_id).toBeUndefined();
  await page.unroute("**/api/prompt-assistant/compose");
});

test("toolbar and nested folder activity survive reload and reflect auto mode", async ({ page }, testInfo) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const collectionResponse = await page.request.post("/api/collections", {
    headers: { "X-CSRF-Token": session.csrf_token },
    data: { name: "Generation progress" },
  });
  expect(collectionResponse.status()).toBe(201);
  const collection = await collectionResponse.json();
  let activity = {
    run: { id: "progress-run", total_count: 10, resolved_count: 6, remaining_count: 4,
      succeeded_count: 6, failed_count: 0, cancelled_count: 0, completed_at: null },
    remaining_count: 4,
    collection_remaining_counts: { [collection.id]: 4 },
    collection_generation_counts: { [collection.id]: 2 },
  };
  await page.route("**/api/generation-activity", (route) => route.fulfill({ json: activity }));
  await page.reload();
  const progress = page.locator("#generation-activity-host");
  const folder = page.locator(`[data-gallery-card="collection"][data-collection-id="${collection.id}"]`);
  await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "60");
  await expect(folder.locator(".collection-count")).toContainText("4 remaining");
  await progress.getByRole("progressbar").focus();
  await expect(progress.locator(".activity-tooltip")).toBeVisible();
  await expect(progress.locator(".activity-tooltip")).toContainText("6 of 10 resolved");
  await page.locator("#gallery-scale").focus();
  await page.screenshot({ path: testInfo.outputPath("generation-progress-desktop.png") });
  await page.locator("#auto-generate").check();
  await expect(progress).toContainText("Auto active");
  await expect(progress.getByRole("progressbar")).toHaveCount(0);
  await page.locator("#auto-generate").uncheck();
  await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "60");
  activity = { ...activity, run: { ...activity.run, total_count: 20, remaining_count: 14 },
    remaining_count: 14, collection_remaining_counts: { [collection.id]: 14 } };
  await page.reload();
  await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
  await expect(folder.locator(".collection-count")).toContainText("14 remaining");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(progress).toBeInViewport();
  await expect(page.locator(".account-menu")).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("generation-progress-mobile.png"), animations: "disabled" });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(folder.locator(".activity-spinner")).toHaveCSS("animation-iteration-count", "1");
  activity = { ...activity, run: { ...activity.run, resolved_count: 20, remaining_count: 0,
    succeeded_count: 19, cancelled_count: 1, completed_at: new Date().toISOString() },
    remaining_count: 0, collection_remaining_counts: {} };
  await page.reload();
  await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  await expect(folder.locator(".collection-remaining")).toHaveCount(0);
  await expect(progress.getByRole("progressbar")).toHaveCount(0, { timeout: 7000 });
});

test("mixed selection copies independently, moves originals, and deletes only the copies", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  const session = await (await page.request.get("/api/auth/session")).json();
  const folders = [];
  for (const name of ["Bulk source", "Bulk destination"]) {
    const response = await page.request.post("/api/collections", {
      headers: { "X-CSRF-Token": session.csrf_token }, data: { name },
    });
    expect(response.status()).toBe(201);
    folders.push(await response.json());
  }
  const [source, destination] = folders;
  const folderCard = (id) => page.locator(`[data-gallery-card="collection"][data-collection-id="${id}"]`);
  const imageCard = (id) => page.locator(`[data-gallery-card="generation"][data-generation-id="${id}"]`);
  await page.reload();
  await folderCard(source.id).getByRole("button", { name: /Open collection/ }).click();
  await selectPublishedSource(page, "Generic Landscape");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("bulk folder lighthouse");
  const inside = await (await generateAndExpectAccepted(page)).json();
  await expect(imageCard(inside.id)).toHaveClass(/status-succeeded/);
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("bulk unfiled lighthouse");
  const outside = await (await generateAndExpectAccepted(page)).json();
  await expect(imageCard(outside.id)).toHaveClass(/status-succeeded/);

  async function selectPair(folderId, imageId) {
    await clickGalleryControl(folderCard(folderId).locator(".card-select-button"));
    await imageCard(imageId).locator(".card-select-button").click();
    await expect(page.locator("#gallery-selection-toolbar")).toContainText("2 selected");
  }
  async function transferSelection(operation, target) {
    await page.getByRole("button", { name: "Move / Copy…" }).click();
    const dialog = page.locator("#gallery-transfer-dialog");
    if (target) await dialog.getByRole("radio", { name: target, exact: true }).check();
    const responsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/gallery/transfer" && response.request().method() === "POST");
    await dialog.getByRole("button", { name: `${operation} here`, exact: true }).click();
    const response = await responsePromise;
    expect(response.status(), await response.text()).toBe(200);
    await expect(dialog).not.toHaveAttribute("open", "");
    await expect(page.locator("#gallery-selection-toolbar")).toBeHidden();
    return response.json();
  }

  await selectPair(source.id, outside.id);
  await page.getByRole("button", { name: "Add to Favorites", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add to Favorites", exact: true })).toBeDisabled();
  await expect(folderCard(source.id)).toHaveClass(/is-favorited/);
  await expect(imageCard(outside.id)).toHaveClass(/is-favorited/);
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("2 selected");
  expect((await (await page.request.get(`/api/generations/${inside.id}`)).json()).is_favorite).toBe(false);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download selection", exact: true }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("gallery-selection.zip");
  expect(await download.failure()).toBeNull();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("2 selected");
  const copied = await transferSelection("Copy");
  expect(copied.generation_ids).toHaveLength(2);
  expect(copied.collection_ids).toHaveLength(1);
  const copiedDetails = await Promise.all(copied.generation_ids.map(async (id) =>
    (await page.request.get(`/api/generations/${id}`)).json()));
  const copiedOutside = copiedDetails.find((item) => item.collection_id === null);
  const copiedInside = copiedDetails.find((item) => item.collection_id === copied.collection_ids[0]);
  expect(copiedInside).toBeTruthy();
  await expect(imageCard(copiedOutside.id)).toHaveCount(1);
  await expect(folderCard(copied.collection_ids[0])).toHaveCount(1);
  await expect(imageCard(outside.id)).toHaveCount(1);
  await expect(folderCard(source.id)).toHaveCount(1);

  await selectPair(source.id, outside.id);
  const moved = await transferSelection("Move", destination.name);
  expect(moved.generation_ids).toEqual([outside.id]);
  expect(moved.collection_ids).toEqual([source.id]);
  await expect(imageCard(outside.id)).toHaveCount(0);
  await expect(folderCard(source.id)).toHaveCount(0);
  await expect(imageCard(copiedOutside.id)).toHaveCount(1);

  await selectPair(copied.collection_ids[0], copiedOutside.id);
  await page.getByRole("button", { name: "Delete…", exact: true }).click();
  const deletion = page.locator("#gallery-delete-dialog");
  await expect(deletion).toContainText("2 generations");
  await deletion.getByRole("button", { name: "Delete 2 items", exact: true }).click();
  await expect(imageCard(copiedOutside.id)).toHaveCount(0);
  await expect(folderCard(copied.collection_ids[0])).toHaveCount(0);
  for (const image of [inside, outside]) {
    const response = await page.request.get(`/api/generations/${image.id}`);
    expect(response.status()).toBe(200);
    const detail = await response.json();
    expect((await page.request.get(detail.display_artifact.content_url)).status()).toBe(200);
  }
  await deleteSelectedCard(page, folderCard(destination.id));
  await expect(folderCard(destination.id)).toHaveCount(0);
});

test("prompt pre-processor starts collapsed, keeps per-mode edits, and sends focused drafts", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");
  const panel = page.locator("#prompt-assistant");
  const disclosure = panel.locator(".prompt-preprocessor");
  const instructions = panel.getByRole("textbox", { name: "Prompt pre-processor", exact: true });
  const create = panel.getByRole("radio", { name: "New Prompt from Creative Direction" });
  const refine = panel.getByRole("radio", { name: "Refine Current Prompt" });
  const defaults = await (await page.request.get("/api/prompt-assistant/status")).json();
  await expect(panel.locator("[data-prompt-instructions]")).toBeHidden();
  await expect(panel.locator("#prompt-assistant-thinking-mode")).toBeHidden();
  await expect(panel.locator("#prompt-assistant-thinking-mode")).toBeChecked();
  await expect(panel.getByRole("button", { name: "Apply Creative Direction" })).toBeVisible();
  await disclosure.locator("summary").click();
  await refine.check();
  await expect(instructions).toHaveValue(defaults.default_instructions.refine);
  await instructions.fill("Refine using one concise sentence in French.");
  await create.check();
  await expect(instructions).toHaveValue(defaults.default_instructions.create);
  await instructions.fill("Create a cinematic image prompt using one sentence.");
  await refine.check();
  await expect(instructions).toHaveValue("Refine using one concise sentence in French.");
  await create.check();
  await expect(instructions).toHaveValue("Create a cinematic image prompt using one sentence.");

  await page.reload();
  await expect(panel.locator("[data-prompt-instructions]")).toBeHidden();
  await disclosure.locator("summary").click();
  await create.check();
  await expect(instructions).toHaveValue("Create a cinematic image prompt using one sentence.");
  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  const dialog = page.locator("#prompt-editor-dialog");
  const draftDisclosure = dialog.locator(".prompt-preprocessor");
  const draft = dialog.getByRole("textbox", { name: "Prompt pre-processor", exact: true });
  await expect(dialog.locator("[data-prompt-instructions]")).toBeHidden();
  await expect(dialog.locator("#prompt-editor-thinking-mode")).toBeHidden();
  await expect(dialog.locator("#prompt-editor-thinking-mode")).toBeChecked();
  await expect(dialog.getByRole("button", { name: "Apply Creative Direction" })).toBeVisible();
  await draftDisclosure.locator("summary").click();
  await expect(draft).toHaveValue("Create a cinematic image prompt using one sentence.");
  await draft.fill("This canceled instruction must stay in the dialog.");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(instructions).toHaveValue("Create a cinematic image prompt using one sentence.");

  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  await expect(dialog.locator("[data-prompt-instructions]")).toBeHidden();
  await draftDisclosure.locator("summary").click();
  await draft.fill("");
  await draftDisclosure.locator("summary").click();
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  await expect(draft).toBeFocused();
  await draft.fill("Create a cinematic image prompt under forty words.");
  await draftDisclosure.locator("summary").click();
  await dialog.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("a lighthouse at blue hour");
  const composing = page.waitForRequest((request) =>
    new URL(request.url()).pathname === "/api/prompt-assistant/compose" && request.method() === "POST");
  await dialog.getByRole("button", { name: "Apply Creative Direction" }).click();
  expect((await composing).postDataJSON()).toMatchObject({
    mode: "create", instructions: "Create a cinematic image prompt under forty words.",
  });
  await expect(dialog.getByRole("textbox", { name: "Prompt editor" })).toHaveValue(/a lighthouse at blue hour/);
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(instructions).toHaveValue("Create a cinematic image prompt under forty words.");
  await panel.getByRole("button", { name: "Reset to default" }).click();
  await expect(instructions).toHaveValue(defaults.default_instructions.create);
  await refine.check();
  await expect(instructions).toHaveValue("Refine using one concise sentence in French.");
  await panel.getByRole("button", { name: "Reset to default" }).click();
  await expect(instructions).toHaveValue(defaults.default_instructions.refine);
  await instructions.fill("");
  await disclosure.locator("summary").click();
  await panel.getByRole("button", { name: "Apply Creative Direction" }).click();
  await expect(instructions).toBeFocused();
  await panel.getByRole("button", { name: "Reset to default" }).click();
  await expect(instructions).toHaveValue(defaults.default_instructions.refine);
});
