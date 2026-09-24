import { expect, test } from "@playwright/test";

async function mountCards(page) {
  // Exercise the production renderer, styles, and hover controller independently
  // of account state and generation timing; principal journeys cover real actions.
  await page.route("**/app.mjs", (route) => route.fulfill({
    contentType: "text/javascript",
    body: "export {};",
  }));
  await page.goto("/");
  await page.evaluate(async () => {
    const appUrl = document.querySelector('script[type="module"]').src;
    const { galleryMarkup } = await import(new URL("./render.mjs", appUrl));
    const { reconcileGallery } = await import(new URL("./gallery-dom.mjs", appUrl));
    const { bindGalleryCardHover } = await import(new URL("./gallery-hover.mjs", appUrl));
    const root = document.querySelector("#app");
    root.innerHTML = '<div id="gallery-viewport" style="padding:24px"><div id="gallery" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:20px;max-width:720px"></div></div>';
    const gallery = root.querySelector("#gallery");
    const hover = bindGalleryCardHover(root);
    const image = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#fff6bf"/><stop offset=".5" stop-color="#8bbbc0"/><stop offset="1" stop-color="#dae6ba"/></linearGradient></defs><path fill="url(#g)" d="M0 0h512v512H0z"/><path fill="#7f9285" d="m0 300 140-110 150 160 105-90 117 65v187H0z"/></svg>')}`;
    let generation = {
      id: "hover-image", status: "succeeded", checkpoint_label: "Moody Krea 2 V5 BF16",
      image_count: 4, recall_available: true, is_favorite: false,
      display_artifact: { id: "image-1", kind: "image", thumbnail_url: image, content_url: image, width: 512, height: 512 },
    };
    const collections = [{
      id: "hover-folder", name: "Landscapes", generation_count: 12,
      previews: Array.from({ length: 4 }, () => ({ thumbnail_url: image })),
    }];
    window.redrawHoverFixture = (changes = {}, galleryLayout = "grouped") => hover.preserveDuring(() => {
      generation = { ...generation, ...changes };
      reconcileGallery(gallery, galleryMarkup([generation], { collections, galleryLayout }));
      // Put these two card types side by side for layout inspection.
      gallery.querySelector(".collection-grid").style.display = "contents";
    });
    window.hideHoverFolder = () => hover.preserveDuring(() => reconcileGallery(gallery, galleryMarkup([generation])));
    window.redrawHoverFixture();
    window.hoverActions = [];
    root.addEventListener("click", (event) => {
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action) window.hoverActions.push(action);
    });
  });
  await page.clock.install({ time: new Date("2026-09-10T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-09-10T12:00:01Z"));
}

async function center(page, locator) {
  const box = await locator.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  return box;
}

test("cards reveal only after a settled hover, with a shared scrim and exit grace", async ({ page }) => {
  await mountCards(page);
  const image = page.locator(".gallery-card");
  const folder = page.locator(".collection-tile");
  await expect(folder.locator(".collection-caption")).toHaveText("Landscapes12");
  await expect(image.locator("footer")).toHaveCount(0);
  const imageBounds = await image.boundingBox();
  const mediaBounds = await image.locator(".card-media-frame").boundingBox();
  expect(imageBounds.height).toBe(mediaBounds.height + 2);
  for (const card of [folder, image]) {
    const before = await card.boundingBox();
    const box = await center(page, card);
    await page.clock.runFor(300);
    await page.mouse.move(box.x + box.width / 2 + 12, box.y + box.height / 2);
    await page.clock.runFor(449);
    await expect(card).not.toHaveClass(/card-controls-visible/);
    await page.mouse.move(box.x + box.width / 2 + 14, box.y + box.height / 2 + 1);
    await page.clock.runFor(1);
    await expect(card).toHaveClass(/card-controls-visible/);
    await page.clock.runFor(130);
    await expect(card.locator(".card-hover-scrim")).toHaveCSS("opacity", "1");
    await expect(card.locator('[role="group"]')).toHaveCSS("opacity", "1");
    expect(await card.boundingBox()).toEqual(before);
    await center(page, card.locator('[role="group"]'));
    await expect(card).toHaveClass(/card-controls-visible/);
    await page.mouse.move(2, 2);
    await page.clock.runFor(100);
    await expect(card).toHaveClass(/card-controls-visible/);
    await center(page, card);
    await page.clock.runFor(100);
    await expect(card).toHaveClass(/card-controls-visible/);
    await page.mouse.move(2, 2);
    await page.clock.runFor(280);
    await expect(card.locator(".card-hover-scrim")).toHaveCSS("opacity", "0");
    await expect(card.locator('[role="group"]')).toHaveCSS("pointer-events", "none");
  }
  await center(page, folder);
  await page.clock.runFor(200);
  await center(page, image);
  await page.clock.runFor(200);
  await page.mouse.move(2, 2);
  await page.clock.runFor(1000);
  await expect(page.locator(".card-controls-visible")).toHaveCount(0);
});

test("redraws preserve hover intent and keyboard focus, while scrolling cancels intent", async ({ page }) => {
  await mountCards(page);
  const card = page.locator(".gallery-card");
  await center(page, card);
  await page.clock.runFor(300);
  await page.evaluate(() => window.redrawHoverFixture({ is_favorite: true }));
  await page.clock.runFor(149);
  await expect(card).not.toHaveClass(/card-controls-visible/);
  await page.clock.runFor(131);
  await expect(card.locator(".card-actions")).toHaveCSS("opacity", "1");
  await page.evaluate(() => window.redrawHoverFixture({ is_favorite: false }));
  await expect(card).toHaveClass(/card-controls-visible/);
  // Delayed events from a closing dialog must not dismiss another card's hover.
  await page.locator("#gallery-viewport").dispatchEvent("pointerout", { pointerType: "mouse" });
  await page.clock.runFor(280);
  await expect(card).toHaveClass(/card-controls-visible/);
  await page.locator("#gallery-viewport").dispatchEvent("scroll");
  await page.clock.runFor(800);
  await expect(card).not.toHaveClass(/card-controls-visible/);
  // Pointerover caused by a stationary-pointer layout change cannot restart it.
  await card.dispatchEvent("pointerover", { pointerType: "mouse" });
  await page.clock.runFor(800);
  await expect(card).not.toHaveClass(/card-controls-visible/);

  const favorite = card.getByRole("button", { name: "Add to Favorites" });
  await favorite.focus();
  await page.clock.runFor(130);
  await expect(card.locator(".card-actions")).toHaveCSS("opacity", "1");
  await page.evaluate(() => window.redrawHoverFixture({ is_favorite: true }));
  await expect(card.getByRole("button", { name: "Remove from Favorites" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(card.getByRole("button", { name: "Recall settings" })).toBeFocused();
  await card.getByRole("button", { name: "Generation details" }).click();
  expect(await page.evaluate(() => window.hoverActions)).toEqual(["open-detail"]);
});

test("overlays fit small cards, keep status visible, and do not intercept the image", async ({ page }) => {
  await mountCards(page);
  await page.locator("#gallery").evaluate((gallery) => { gallery.style.gridTemplateColumns = "170px 170px"; });
  const card = page.locator(".gallery-card");
  await center(page, card);
  await page.clock.runFor(600);
  const frame = await card.locator(".card-media-frame").boundingBox();
  const actions = await card.locator(".card-actions").boundingBox();
  const tools = await card.locator(".card-actions > *").all();
  expect(actions.height).toBe(67);
  for (const tool of tools) {
    const box = await tool.boundingBox();
    expect(box.x).toBeGreaterThan(frame.x);
    expect(box.x + box.width).toBeLessThan(frame.x + frame.width);
    expect(box.y + box.height).toBeLessThan(frame.y + frame.height);
  }
  await card.getByRole("button", { name: "Generation details" }).click();
  expect(await page.evaluate(() => window.hoverActions)).toEqual(["open-detail"]);
  await page.mouse.move(2, 2);
  await page.clock.runFor(280);
  await expect(card.locator(".card-actions")).toHaveCSS("opacity", "0");
  await card.locator(".card-media").click({ position: { x: 70, y: 50 } });
  expect(await page.evaluate(() => window.hoverActions)).toEqual(["open-detail", "open-photo"]);

  await page.evaluate(() => window.redrawHoverFixture({
    status: "running", cancel_allowed: true,
    expected_width: 512, expected_height: 256,
    progress: { kind: "indeterminate", label: "Sampling" },
  }));
  const progress = await card.locator(".generation-progress").boundingBox();
  const cancel = await card.locator(".card-cancel-button").boundingBox();
  const activeActions = await card.locator(".card-actions").boundingBox();
  expect(progress.y + progress.height).toBeLessThan(activeActions.y);
  expect(cancel.y + cancel.height).toBeLessThanOrEqual(progress.y);
  await card.locator(".card-media").evaluate((media) => media.blur());
  await page.mouse.move(2, 2);
  await page.clock.runFor(1000);
  await expect(card.locator(".generation-progress")).toBeVisible();
  await expect(card.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await page.evaluate(() => window.redrawHoverFixture({ status: "failed_with_artifacts", cancel_allowed: false, progress: null }));
  await expect(card.locator(".media-status")).toBeVisible();
});

test("touch controls stay available and reduced motion removes fading", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, reducedMotion: "reduce" });
  const page = await context.newPage();
  await mountCards(page);
  await page.locator("#gallery").evaluate((gallery) => { gallery.style.gridTemplateColumns = "minmax(0, 1fr)"; });
  for (const card of [page.locator(".collection-tile"), page.locator(".gallery-card")]) {
    await expect(card.locator('[role="group"]')).toHaveCSS("opacity", "1");
    await expect(card.locator('[role="group"]')).toHaveCSS("pointer-events", "auto");
    for (const tool of await card.locator('[role="group"] > *').all()) {
      const box = await tool.boundingBox();
      expect(box.width).toBe(44);
      expect(box.height).toBe(44);
    }
    const duration = await card.locator(".card-hover-scrim").evaluate((scrim) => parseFloat(getComputedStyle(scrim).transitionDuration));
    expect(duration).toBeLessThan(.001);
  }
  await context.close();
});


test("retained cards moving away from the pointer lose hover intent", async ({ page }) => {
  await mountCards(page);
  const card = page.locator(".gallery-card");
  await center(page, card);
  await page.clock.runFor(600);
  await expect(card).toHaveClass(/card-controls-visible/);
  await page.evaluate(() => { window.retainedHoverCard = document.querySelector(".gallery-card"); window.hideHoverFolder(); });
  expect(await page.evaluate(() => retainedHoverCard === document.querySelector(".gallery-card"))).toBe(true);
  await expect(card).not.toHaveClass(/card-controls-visible/);
});

for (const touch of [false, true]) test(`status pills keep their original bottom inset with ${touch ? "touch" : "pointer"} controls`, async ({ browser }) => {
  test.setTimeout(90_000);
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 }, hasTouch: touch, isMobile: touch, reducedMotion: "reduce" });
  const page = await context.newPage();
  try {
    await mountCards(page);
    for (const galleryLayout of ["grouped", "classic"]) {
      for (const width of [170, 340, 900]) {
        for (const status of ["queued", "cancelled_without_artifacts", "failed_with_artifacts"]) {
          await page.evaluate(({ width, status, galleryLayout }) => {
            window.redrawHoverFixture({
              status, cancel_allowed: status === "queued", progress: null,
              expected_width: 2048, expected_height: width === 900 ? 256 : 2048,
              display_artifact: status === "failed_with_artifacts"
                ? { kind: "image", content_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E" } : null,
            }, galleryLayout);
            document.querySelector(".gallery-card").style.width = `${width}px`;
          }, { width, status, galleryLayout });
          const card = page.locator(".gallery-card");
          const measure = async () => {
            const { frame, pill, actions } = await card.evaluate((card) => ({
              frame: card.querySelector(".card-media-frame").getBoundingClientRect().toJSON(),
              pill: card.querySelector(".media-status").getBoundingClientRect().toJSON(),
              actions: card.querySelector(".card-actions").getBoundingClientRect().toJSON(),
            }));
            expect(pill.x - frame.x).toBeCloseTo(10, 1);
            expect(frame.y + frame.height - pill.y - pill.height).toBeCloseTo(10, 1);
            expect(actions.y + actions.height).toBeLessThanOrEqual(pill.y - 7);
            expect(pill.x + pill.width).toBeLessThanOrEqual(frame.x + frame.width - 9);
            return pill;
          };
          const before = await measure();
          await center(page, card);
          await page.clock.runFor(600);
          expect(await measure()).toEqual(before);
          await page.locator("#app").evaluate((root) => root.classList.add("gallery-selection-mode"));
          expect(await measure()).toEqual(before);
          await page.locator("#app").evaluate((root) => root.classList.remove("gallery-selection-mode"));
          await page.mouse.move(2, 2);
          await page.clock.runFor(280);
        }
      }
    }
  } finally { await context.close(); }
});
