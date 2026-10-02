import { expect, test } from "@playwright/test";

async function mountSelection(page, { layout = "grouped", rerun = false } = {}) {
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.route("**/api/gallery/items?**", async (route) => route.fulfill({ json: await page.evaluate(() => ({
    generations: window.selectionFixtureState.generations,
    collection_ids: window.selectionFixtureState.collections.filter((item) => !item.parent_id).map((item) => item.id),
  })) }));
  await page.goto("/");
  await page.evaluate(async ({ layout, rerun }) => {
    const base = document.querySelector('script[type="module"]').src;
    const { galleryMarkup, shellMarkup } = await import(new URL("./render.mjs", base));
    const { bindGalleryCardHover } = await import(new URL("./gallery-hover.mjs", base));
    const { bindGallerySelection } = await import(new URL("./gallery-selection.mjs", base));
    const image = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" fill="%233c6575"/></svg>';
    const state = {
      session: { authenticated: true, user: { username: "selection", role: "user" }, app_title: "ImageGen V2" },
      galleryScale: 20, galleryLayout: layout, currentCollectionId: null, collectionsStatus: "ready", favoritesMode: "all",
      collections: [{ id: "folder", name: "Studies", parent_id: null, generation_count: 3 }, { id: "destination", name: "Archive", parent_id: null, generation_count: 0 }, { id: "child", name: "Winter", parent_id: "folder", generation_count: 1 }],
      generations: Array.from({ length: 4 }, (_, index) => ({ id: `g${index}`, collection_id: null, status: "succeeded", accepted_at: `2026-09-11T12:0${4-index}:00Z`, image_count: index === 1 ? 3 : 1, is_favorite: index === 1, display_artifact: { id: `a${index}`, kind: "image", thumbnail_url: image, content_url: image, width: 512, height: 512 } })),
    };
    window.selectionFixtureState = state;
    const root = document.querySelector("#app");
    root.innerHTML = shellMarkup(state);
    const hover = bindGalleryCardHover(root);
    window.redrawSelectionFixture = (add = false) => {
      if (add) state.generations.unshift({ ...state.generations[0], id: "new-card", accepted_at: "2026-09-11T13:00:00Z" });
      hover.preserveDuring(() => { root.querySelector("#gallery").innerHTML = galleryMarkup(state.generations, { collections: state.collections, galleryLayout: state.galleryLayout }); });
    };
    window.redrawSelectionFixture();
    window.selectionNotices = [];
    window.selectionReruns = [];
    window.selectionController = bindGallerySelection(root, {
      getState: () => state,
      openRerun: rerun ? ({ body }) => window.selectionReruns.push(body) : null,
      rerunBlocked: () => window.selectionRerunBlocked || null,
      refresh: async ({ operation, result }) => {
        if (operation !== "favorite") return;
        for (const item of state.generations) if (result.generation_ids.includes(item.id)) item.is_favorite = true;
        for (const item of state.collections) if (result.collection_ids.includes(item.id)) item.is_favorite = true;
        window.redrawSelectionFixture();
      },
      notify: (message, kind) => window.selectionNotices.push({ message, kind }),
    });
    root.querySelector("#gallery").style.setProperty("--gallery-card-min", "220px");
  }, { layout, rerun });
  if (layout === "classic") await expect(page.locator(".classic-gallery-count")).toHaveText("4 generations · 2 folders");
}

for (const layout of ["grouped", "classic"]) test(`rerun clicks survive refreshes for single, multiple and folder selections in ${layout} layout`, async ({ page }) => {
  await mountSelection(page, { layout, rerun: true });
  const toolbar = page.locator("#gallery-selection-toolbar");
  const rerun = toolbar.locator('[data-bulk-action="rerun"]');
  const selections = [
    { selector: '[data-generation-id="g0"] .card-select-button', body: { generation_ids: ["g0"], collection_ids: [] } },
    { selector: '[data-generation-id="g1"] .card-select-button', body: { generation_ids: ["g0", "g1"], collection_ids: [] } },
    { selector: '[data-collection-id="folder"] .card-select-button', body: { generation_ids: ["g0", "g1"], collection_ids: ["folder"] } },
  ];
  for (const [index, { selector }] of selections.entries()) {
    await page.locator(selector).focus();
    await page.keyboard.press("Space");
    await expect(toolbar.getByRole("status")).toHaveText(`${index + 1} selected`);
    await rerun.hover();
    await page.mouse.down();
    await rerun.evaluate((button) => { window.pressedRerun = button; });
    // Simulate incoming cards while the pointer is held down, including the
    // observer-driven selection refresh and a changed Select loaded count.
    await page.evaluate((add) => window.redrawSelectionFixture(add), index === 0);
    expect(await rerun.evaluate((button) => button === window.pressedRerun && button.isConnected)).toBe(true);
    await page.mouse.up();
    expect(await page.evaluate(() => window.selectionReruns)).toEqual(selections.slice(0, index + 1).map((selection) => selection.body));
    await expect(toolbar.getByRole("status")).toHaveAttribute("aria-label", `${index + 1} selected`);
    await expect(toolbar.getByRole("status")).toHaveAttribute("title", index === 2 ? "2 image cards · 1 folder" : `${index + 1} image card${index ? "s" : ""}`);
    await expect(rerun).toBeFocused();
  }
  await expect(toolbar.locator('[data-bulk-action="all"]')).toHaveAttribute("aria-label", layout === "classic" ? "Select all items in this view" : "Select loaded (7)");
  await expect(page.locator('[data-generation-id="new-card"] .card-select-button')).toHaveAttribute("aria-checked", "false");
});

test("unchanged toolbar refreshes preserve focus without triggering another refresh", async ({ page }) => {
  await mountSelection(page, { rerun: true });
  const select = page.locator('[data-generation-id="g0"] .card-select-button');
  await select.focus(); await select.press("Space");
  const rerun = page.locator('[data-bulk-action="rerun"]');
  await rerun.focus();
  await page.clock.install();
  await page.evaluate(() => {
    const toolbar = document.querySelector("#gallery-selection-toolbar");
    window.toolbarMutations = 0;
    new MutationObserver((records) => { window.toolbarMutations += records.length; })
      .observe(toolbar, { subtree: true, childList: true, attributes: true, characterData: true });
    window.focusRefreshes = 0;
    // Mirror app.mjs's focusout refresh. Cap it so the unfixed feedback loop
    // produces a deterministic failure instead of running indefinitely.
    document.querySelector("#app").addEventListener("focusout", () => setTimeout(() => {
      if (window.focusRefreshes >= 12) return;
      window.focusRefreshes += 1;
      window.selectionController.sync();
    }, 0));
    for (let i = 0; i < 5; i += 1) window.selectionController.sync();
  });
  await page.clock.runFor(250);
  expect(await page.evaluate(() => ({ refreshes: window.focusRefreshes, mutations: window.toolbarMutations }))).toEqual({ refreshes: 0, mutations: 0 });
  await expect(rerun).toBeFocused();
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.selectionReruns)).toEqual([{ generation_ids: ["g0"], collection_ids: [] }]);
});

test("toolbar state changes preserve buttons, focus fallback and open dialog focus", async ({ page }) => {
  await mountSelection(page, { rerun: true });
  const select = page.locator('[data-generation-id="g0"] .card-select-button');
  await select.focus(); await select.press("Space");
  const toolbar = page.locator("#gallery-selection-toolbar");
  const rerun = toolbar.locator('[data-bulk-action="rerun"]');
  await rerun.focus();
  await rerun.evaluate((button) => { window.originalRerun = button; });
  const blocked = "Turn off auto generation to re-run prompts";
  await page.evaluate((reason) => { window.selectionRerunBlocked = reason; window.selectionController.sync(); }, blocked);
  await expect(rerun).toBeDisabled();
  await expect(rerun).toHaveAttribute("title", blocked);
  await expect(toolbar.getByRole("button", { name: "Clear selection" })).toBeFocused();
  await page.evaluate(() => { window.selectionRerunBlocked = null; window.selectionController.sync(); });
  await expect(rerun).toBeEnabled();
  await expect(rerun).toHaveAttribute("title", "Re-run the selected prompts with new settings");
  expect(await rerun.evaluate((button) => button === window.originalRerun)).toBe(true);
  await toolbar.getByRole("button", { name: "Move / Copy…" }).click();
  const cancel = page.locator("#gallery-transfer-dialog").getByRole("button", { name: "Cancel", exact: true });
  await cancel.focus();
  await page.evaluate(() => { window.selectionRerunBlocked = "Wait for the current submission to finish"; window.selectionController.sync(); });
  await expect(cancel).toBeFocused();
  await cancel.click();
  await expect(toolbar.getByRole("button", { name: "Move / Copy…" })).toBeFocused();
  await toolbar.getByRole("button", { name: "Clear selection" }).click();
  await expect(toolbar).toBeHidden();
  await expect(rerun).toBeDisabled();
  await expect(rerun).toHaveAttribute("title", "Select images or folders to re-run their prompts");
});

for (const cardSelector of ['[data-gallery-card="generation"][data-generation-id="g0"]', '[data-gallery-card="collection"][data-collection-id="folder"]']) {
  test(`hover toolkit starts selection and the last deselection restores the gallery: ${cardSelector}`, async ({ page }) => {
    await mountSelection(page);
    const toolkits = page.locator("#gallery .card-actions, #gallery .collection-tile-actions");
    const card = page.locator(cardSelector);
    const toolkit = card.locator('[role="group"]');
    const checkbox = toolkit.locator(".card-select-button");
    for (const controls of await toolkits.all()) await expect(controls).toHaveCSS("opacity", "0");
    await card.scrollIntoViewIfNeeded();
    // Scrolling cancels hover intent; begin the mouse movement after it settles.
    await card.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await card.hover();
    await expect(toolkit).toHaveCSS("opacity", "1");
    // Individual delete is rightmost; the select checkbox sits directly to its left.
    const before = await checkbox.boundingBox();
    const del = await toolkit.locator(".delete-generation-button, .delete-collection-button").boundingBox();
    const tools = await toolkit.boundingBox();
    expect(del.x + del.width).toBeCloseTo(tools.x + tools.width, 0);
    expect(del.y + del.height).toBeCloseTo(tools.y + tools.height, 0);
    expect(before.x + before.width + 7).toBeCloseTo(del.x, 0);
    await checkbox.click();
    await expect(page.locator("#gallery-selection-toolbar")).toContainText("1 selected");
    await page.mouse.move(2, 2);
    for (const controls of await toolkits.all()) {
      await expect(controls).toHaveCSS("opacity", "1");
      await expect(controls.locator(".card-select-button")).toBeVisible();
      await expect(controls.locator(":scope > :not(.card-select-button):visible")).toHaveCount(0);
    }
    await checkbox.click();
    await expect(page.locator("#gallery-selection-toolbar")).toBeHidden();
    await page.mouse.move(2, 2);
    for (const controls of await toolkits.all()) await expect(controls).toHaveCSS("opacity", "0");
    await card.hover();
    await expect(toolkit).toHaveCSS("opacity", "1");
    await checkbox.click();
    await page.getByRole("button", { name: "Clear selection", exact: true }).click();
    await expect(page.locator("#gallery-selection-toolbar")).toBeHidden();
    await page.mouse.move(2, 2);
    for (const controls of await toolkits.all()) await expect(controls).toHaveCSS("opacity", "0");
  });
}

test("selection reveals all controls, survives incoming cards, and supports ranges and Escape", async ({ page }) => {
  await mountSelection(page);
  const initial = page.locator('[data-generation-id="g0"] .card-select-button');
  await initial.focus();
  await initial.press("Space");
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("1 selected");
  await expect(page.locator('[data-generation-id="g1"] .card-actions')).toHaveCSS("opacity", "1");
  await page.locator('[data-generation-id="g2"] .card-media').click({ modifiers: ["Shift"] });
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("3 selected");
  await expect(page.locator("#photo-viewer")).not.toHaveAttribute("open", "");
  await page.evaluate(() => window.redrawSelectionFixture(true));
  await expect(initial).toHaveAttribute("aria-checked", "true");
  await expect(page.locator('[data-generation-id="new-card"] .card-select-button')).toHaveAttribute("aria-checked", "false");
  await expect(page.locator('[data-generation-id="new-card"] .card-actions')).toHaveCSS("opacity", "1");
  await page.getByRole("button", { name: "Select loaded (7)" }).click();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("7 selected");
  await page.keyboard.press("Escape");
  await expect(page.locator("#gallery-selection-toolbar")).toBeHidden();
  await expect(page.locator(".card-select-button[aria-checked=true]")).toHaveCount(0);
});

test("move and copy share the destination picker and preserve selection on a rejected copy", async ({ page }) => {
  await mountSelection(page);
  const folder = page.getByRole("checkbox", { name: "Select collection Studies" });
  await folder.focus(); await folder.press("Space");
  await page.locator('[data-generation-id="g0"] .card-media').click();
  await page.getByRole("button", { name: "Move / Copy…" }).click();
  const dialog = page.locator("#gallery-transfer-dialog");
  await expect(dialog.getByRole("radio", { name: "Studies Inside the selection" })).toBeDisabled();
  await expect(dialog.getByRole("radio", { name: "Winter Inside the selection" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Move here" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Copy here" })).toBeEnabled();
  await dialog.getByRole("radio", { name: "Archive", exact: true }).check();
  await expect(dialog.getByRole("button", { name: "Move here" })).toBeEnabled();
  let payload;
  await page.route("**/api/gallery/transfer", async (route) => {
    payload = route.request().postDataJSON();
    await route.fulfill({ status: 409, json: { error: { message: "Wait for active generations to finish.", code: "copy_generation_active" } } });
  });
  await dialog.getByRole("button", { name: "Copy here" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Wait for active generations to finish.");
  expect(payload).toEqual({ operation: "copy", generation_ids: ["g0"], collection_ids: ["folder"], collection_id: "destination" });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(folder).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Delete…", exact: true }).click();
  const deletion = page.locator("#gallery-delete-dialog");
  await expect(deletion).toContainText("5 generations");
  await expect(deletion).toContainText("including nested folders");
  // The confirmation hugs its content instead of stretching to max-height, on desktop and phones.
  const deleteSize = () => deletion.evaluate((dialog) => ({ dialog: dialog.getBoundingClientRect(), frame: dialog.querySelector(".dialog-frame").getBoundingClientRect().height, viewportWidth: innerWidth, viewportHeight: innerHeight }));
  for (const viewport of [null, { width: 390, height: 844 }]) {
    if (viewport) await page.setViewportSize(viewport);
    const size = await deleteSize();
    expect(Math.abs(size.dialog.height - size.frame)).toBeLessThanOrEqual(2);
    expect(size.dialog.height).toBeLessThan(size.viewportHeight / 2);
    expect(size.dialog.width).toBeLessThan(size.viewportWidth);
  }
});

test("bulk favorites preserve existing favorites and selection for a ZIP download", async ({ page }) => {
  await mountSelection(page);
  const folder = page.getByRole("checkbox", { name: "Select collection Studies" });
  await folder.focus(); await folder.press("Space");
  for (const id of ["g0", "g1"]) await page.locator(`[data-generation-id="${id}"] .card-select-button`).click();
  let payload;
  await page.route("**/api/gallery/favorite", async (route) => {
    payload = route.request().postDataJSON();
    await route.fulfill({ json: payload });
  });
  const favorite = page.getByRole("button", { name: "Add to Favorites", exact: true });
  await favorite.click();
  await expect(favorite).toBeDisabled();
  expect(payload).toEqual({ generation_ids: ["g0", "g1"], collection_ids: ["folder"] });
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("3 selected");
  for (const id of ["g0", "g1"]) await expect(page.locator(`[data-gallery-card="generation"][data-generation-id="${id}"]`)).toHaveClass(/is-favorited/);
  await expect(page.locator('[data-gallery-card="collection"][data-collection-id="folder"]')).toHaveClass(/is-favorited/);
  await page.route("**/api/gallery/download", async (route) => {
    expect(route.request().postDataJSON()).toEqual(payload);
    await route.fulfill({ contentType: "application/zip", body: Buffer.from("PK\x05\x06" + "\x00".repeat(18)) });
  });
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download selection", exact: true }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("gallery-selection.zip");
  expect(await download.failure()).toBeNull();
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("3 selected");
  await page.getByRole("button", { name: "Clear selection", exact: true }).click();
  await expect(page.locator('[data-generation-id="g0"] .favorite-button')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-generation-id="g0"] .download-button')).toHaveAttribute("download", "");
});

for (const action of ["favorite", "download"]) test(`failed bulk ${action} preserves selection and allows retry`, async ({ page }) => {
  await mountSelection(page);
  const checkbox = page.locator('[data-generation-id="g0"] .card-select-button');
  await checkbox.focus(); await checkbox.press("Space");
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  await page.route(`**/api/gallery/${action}`, async (route) => {
    await pending;
    await route.fulfill({ status: 503, json: { error: { message: "Please retry this selection." } } });
  });
  const button = page.locator(`#gallery-selection-toolbar [data-bulk-action="${action}"]`);
  await button.click();
  await expect(page.locator("#gallery-selection-toolbar")).toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("button", { name: "Clear selection", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.keyboard.press("ControlOrMeta+a");
  await expect(page.locator("#gallery-selection-toolbar")).toContainText("1 selected");
  release();
  await expect(button).toBeEnabled();
  await expect(button).toBeFocused();
  await expect(checkbox).toHaveAttribute("aria-checked", "true");
  await expect.poll(() => page.evaluate(() => window.selectionNotices)).toEqual([{ message: "Please retry this selection.", kind: "error" }]);
});

test("an empty folder can be favorited but has nothing to download", async ({ page }) => {
  await mountSelection(page);
  const checkbox = page.getByRole("checkbox", { name: "Select collection Archive" });
  await checkbox.focus(); await checkbox.press("Space");
  await expect(page.getByRole("button", { name: "Download selection", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add to Favorites", exact: true })).toBeEnabled();
});

for (const width of [320, 390, 801, 850, 1000, 1024, 1201, 1440]) test(`selection toolbar and both destination buttons fit ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 });
  await mountSelection(page);
  const checkbox = page.locator('[data-generation-id="g0"] .card-select-button');
  await checkbox.focus(); await checkbox.press("Space");
  // The filter row can wrap; the gallery must follow the resulting header height.
  expect((await page.locator("#gallery-viewport").boundingBox()).y).toBeGreaterThanOrEqual((await page.locator(".topbar").boundingBox()).height);
  await expect(page.getByRole("button", { name: "Favorites", exact: true })).toBeVisible();
  await expect(page.getByRole("slider", { name: "Gallery scale" })).toBeVisible();
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { generationActivityMarkup } = await import(new URL("./render.mjs", base));
    document.querySelector("#generation-activity-host").innerHTML = generationActivityMarkup({ autoGenerate: true, submitting: true });
  });
  for (const button of await page.locator('#gallery-selection-toolbar button, .favorites-launch-button, #gallery-scale, .generation-activity, .account-menu > summary').all()) {
    await expect(button).toBeInViewport();
    const bounds = await button.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
  }
  await page.screenshot({ path: test.info().outputPath(`selection-toolbar-${width}.png`) });
  await page.getByRole("button", { name: "Move / Copy…" }).click();
  const dialog = page.locator("#gallery-transfer-dialog");
  await expect(dialog.getByRole("button", { name: "Copy here" })).toBeInViewport();
  await expect(dialog.getByRole("button", { name: "Move here" })).toBeInViewport();
});
