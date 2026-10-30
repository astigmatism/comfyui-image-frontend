import { expect, test } from "@playwright/test";

async function mount(page, { triggerWord = "AlphaCharacter", description = "Use AlphaCharacter in the prompt.", thumbnails = [] } = {}) {
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.goto("/");
  await page.evaluate(async ({ triggerWord, description, thumbnails }) => {
    const base = document.querySelector('script[type="module"]').src;
    const { loraStackMarkup, installLoraStackControls } = await import(new URL("./lora-stack.mjs", base));
    const { installLoraManager } = await import(new URL("./lora-manager.mjs", base));
    const control = { id: "loras", type: "lora_stack", label: "LoRAs", items: [
      { id: "a", label: "Alpha", trigger_word: triggerWord, description, lora_identity: `lr1_${"a".repeat(64)}` },
      { id: "b", label: "Beta" }, { id: "c", label: "Gamma" },
    ], default: [{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }], minimum: 0, maximum: 2, step: 0.05 };
    window.stack = structuredClone(control.default);
    window.memory = {};
    const thumb = "data:image/gif;base64,R0lGODlhAQABAIAAAP8AAAAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw==";
    window.images = Object.fromEntries(control.items.map(({ id }) => [id, { id, version: "a".repeat(64), image_url: thumbnails.includes(id) ? thumb : null }]));
    window.serverImages = structuredClone(window.images);
    window.notifications = [];
    window.copies = [];
    window.toasts = [];
    window.extraToasts = [];
    window.sourceKey = "source";
    window.revision = { publication_id: "one" };
    window.subject = "Original subject";
    window.renderLoras = () => { document.querySelector("#lora-summary").innerHTML = loraStackMarkup(control, window.stack, window.images, { editable: true }); };
    const root = document.querySelector("#app");
    root.innerHTML = `<main style="padding:16px;max-width:600px"><label>Subject name <input id="subject_name" value="Original subject"></label><section class="control-section is-expanded"><div class="control-section-header"><button class="control-section-trigger" aria-expanded="true">LoRAs</button><span class="control-section-status">0 active</span><div class="prompt-field-actions control-section-actions"><button type="button" class="icon-button prompt-editor-launch" data-lora-open data-lora-control-id="loras" aria-label="Open LoRA manager"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div></div><div class="control-section-body"><div id="lora-summary"></div></div></section></main><dialog id="lora-manager-dialog" class="lm-dialog" aria-labelledby="lora-manager-title"></dialog>`;
    window.renderLoras();
    window.installLoraStackControls = installLoraStackControls;
    installLoraStackControls(root, {
      context: () => ({ control, sourceKey: window.sourceKey, values: window.stack, memory: window.memory }),
      apply: (_id, values, memory) => { window.stack = structuredClone(values); window.memory = structuredClone(memory); },
      copyText: async (text) => {
        if (window.copyFails) throw new Error("Clipboard access is unavailable in this browser.");
        window.copies.push(text);
      },
      notify: (message, kind) => window.toasts.push({ message, kind }),
    });
    window.manager = installLoraManager(root, {
      api: async (path, options) => {
        if (path.includes("/lora-groups/")) return { library_key: "krea2", revision: 0, groups: [{ id: "uncategorized", name: "Uncategorized", system: true }], assignments: {} };
        // A LoRA's picture is derived from its Demo Reel, so reading the picture
        // metadata is the only image request the manager may make; a write is a bug.
        if (options) throw new Error(`Unexpected ${options.method || "write"} request to ${path}`);
        if (window.failLoad) throw new Error("Offline");
        return { items: structuredClone(Object.values(window.serverImages)) };
      },
      context: (_id, owner) => owner === "rerun" ? null : ({ control, sourceKey: window.sourceKey, publicationRevision: window.revision, sourceName: "Sample workflow", values: window.stack, memory: window.memory, images: window.images }),
      notify: (message) => window.notifications.push(message),
      onImages: (_source, _id, images) => { window.images = images; window.renderLoras(); },
      apply: (_id, values, memory) => {
        window.stack = structuredClone(values);
        window.memory = structuredClone(memory);
        const strongest = values.reduce((best, row) => row.strength > 0 && (!best || row.strength > best.strength) ? row : best, null);
        if (strongest?.id === "a") window.subject = "AlphaCharacter";
        document.querySelector("#subject_name").value = window.subject;
        window.renderLoras();
      },
    });
  }, { triggerWord, description, thumbnails });
}

const dialog = (page) => page.locator("#lora-manager-dialog");

// A character LoRA publishes its whole look as the trigger; the field allows 1000 characters.
const TRIGGER_PHRASE =
  "1oandve, short, straight black hair with bangs, pale skin, green eyes, " +
  "accentuated with black winged eyeliner, light pink lipstick";

async function open(page) {
  await page.getByRole("button", { name: "Open LoRA manager" }).click();
  await expect(dialog(page)).toBeVisible();
}

test("smaller desktop dialog and thumbnail slider reflow cards without changing selection", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mount(page, { thumbnails: ["a"] });
  await open(page);
  const box = await dialog(page).boundingBox();
  expect(box.width).toBeCloseTo((1440 - 48) * .75, -1);
  expect(box.height).toBeCloseTo((1000 - 32) * .75, -1);
  const slider = dialog(page).getByRole("slider", { name: "LoRA thumbnail size" });
  const thumbnail = dialog(page).getByRole("button", { name: "Toggle Alpha" });
  await thumbnail.click();
  const defaultWidth = (await thumbnail.boundingBox()).width;
  await slider.fill("50");
  expect((await thumbnail.boundingBox()).width).toBeLessThan(defaultWidth);
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveValue("55");
  await expect(slider).toHaveAttribute("aria-valuetext", "55%");
  await expect(thumbnail).toHaveAttribute("aria-pressed", "true");
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await mount(page, { thumbnails: ["a"] });
  await open(page);
  await expect(slider).toHaveValue("55");
  await expect(thumbnail).toHaveAttribute("aria-pressed", "false");
  await page.setViewportSize({ width: 390, height: 844 });
  await slider.fill("50");
  const smallMobile = (await thumbnail.boundingBox()).width;
  await slider.fill("150");
  expect((await thumbnail.boundingBox()).width).toBeGreaterThan(smallMobile);
  expect(await dialog(page).evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await expect(dialog(page).getByRole("button", { name: "Apply", exact: true })).toBeInViewport();
});

const LONG_DESCRIPTION =
  "Use for editorial portrait work: keep the face structure, hair line and eye colour exactly as trained, " +
  "paint no extra background detail, and keep the skin matte so later retouching stays reversible. " +
  "Works best beside a neutral character LoRA when the subject is framed from the chest up at 3:4.";

test("a cap-length trigger phrase wraps in the footer instead of scrolling the dialog sideways", async ({ page }) => {
  const phrase = TRIGGER_PHRASE.repeat(8).slice(0, 1000);
  await mount(page, { triggerWord: phrase, description: LONG_DESCRIPTION });
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  const preview = dialog(page).locator("[data-lora-subject-preview]");
  await expect(preview).toContainText("1oandve");
  const geometry = await dialog(page).evaluate((el) => {
    const subject = el.querySelector("[data-lora-subject-preview]");
    const style = getComputedStyle(subject);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
    return {
      dialogScrollWidth: el.scrollWidth,
      dialogClientWidth: el.clientWidth,
      previewLines: Math.round(subject.getBoundingClientRect().height / lineHeight),
      previewRight: subject.getBoundingClientRect().right,
      dialogRight: el.getBoundingClientRect().right,
      viewportWidth: window.innerWidth,
    };
  });
  expect(geometry.previewLines).toBeGreaterThan(2);
  expect(geometry.previewRight).toBeLessThanOrEqual(geometry.dialogRight);
  expect(geometry.dialogScrollWidth).toBe(geometry.dialogClientWidth);
  expect(geometry.dialogClientWidth).toBeLessThanOrEqual(geometry.viewportWidth - 48);
  const apply = await dialog(page).getByRole("button", { name: "Apply" }).boundingBox();
  const frame = await dialog(page).boundingBox();
  expect(apply.x + apply.width).toBeLessThanOrEqual(frame.x + frame.width - 1);
  // Clamping may hide the tail of the phrase, never the phrase itself.
  await expect(preview).toHaveAttribute("title", `Subject on Apply: ${phrase}`);

  await page.setViewportSize({ width: 420, height: 780 });
  const narrow = await dialog(page).evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  expect(narrow.scroll).toBe(narrow.client);
});

test("gallery drafts selection and order while strengths remain in the sidebar", async ({ page }) => {
  await mount(page);
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await expect(dialog(page).getByRole("spinbutton")).toHaveCount(0);
  await dialog(page).getByRole("button", { name: "Reorder Beta" }).press("ArrowUp");
  await expect(dialog(page).locator(".lm-order-chip").first()).toHaveAttribute("data-lora-order-id", "b");
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }]);

  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await expect(dialog(page).locator("[data-lora-subject-preview]")).toContainText("AlphaCharacter");
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(dialog(page)).not.toBeVisible();
  await expect(page.getByLabel("Subject name")).toHaveValue("AlphaCharacter");
  await expect(page.locator(".lm-summary-item")).toHaveCount(1);
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 1 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }]);

  await open(page);
  await dialog(page).getByRole("button", { name: "All off" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("spinbutton", { name: "Alpha strength", exact: true })).toHaveValue("1.00");
});

test("thumbnail selection preserves scrolling and the card menu does not toggle selection", async ({ page }) => {
  await mount(page, { thumbnails: ["a", "b", "c"] });
  await page.addStyleTag({ content: "#lora-manager-dialog { height: 330px; min-height: 0; }" });
  await open(page);
  const manager = dialog(page);
  const content = manager.locator(".lm-dialog-content");
  const toggle = manager.getByRole("button", { name: "Toggle Gamma" });
  await expect(manager.locator('input[type="checkbox"]')).toHaveCount(0);
  await toggle.scrollIntoViewIfNeeded();
  expect(await content.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(await content.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await manager.getByRole("button", { name: "Actions for Gamma" }).click();
  await manager.getByRole("button", { name: "Usage details", exact: true }).click();
  await manager.getByRole("button", { name: "Back to gallery" }).click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(await content.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});

async function imageMenu(page, label = "Beta") {
  await dialog(page).getByRole("button", { name: `Actions for ${label}`, exact: true }).click();
  return dialog(page).locator("[data-lora-popover]");
}

test("a card menu has no thumbnail actions because every picture comes from the Demo Reel", async ({ page }) => {
  await mount(page, { thumbnails: ["a"] });
  await open(page);
  for (const label of ["Alpha", "Beta"]) {
    const menu = await imageMenu(page, label);
    await expect(menu.getByRole("button", { name: "Usage details", exact: true })).toBeVisible();
    await expect(menu.getByRole("button", { name: /thumbnail/i })).toHaveCount(0);
    // A LoRA whose reel has not produced a picture yet offers the reel itself instead.
    await expect(menu.getByRole("button", { name: "Demo Reel", exact: true })).toHaveCount(label === "Beta" ? 1 : 0);
  }
  await expect(dialog(page).locator('input[type="file"], [data-lora-file]')).toHaveCount(0);
  await expect(dialog(page).locator(".lm-image-status")).toHaveCount(0);
  await expect(dialog(page).locator(".lm-footer-context small")).toHaveText("A LoRA shows its Demo Reel’s first picture.");
  expect(await page.evaluate(() => window.notifications)).toEqual([]);
});

test("a failed picture read keeps selection working and retries on demand", async ({ page }) => {
  await mount(page, { thumbnails: ["a"] });
  await page.evaluate(() => { window.failLoad = true; });
  await open(page);
  await expect(dialog(page).getByRole("alert")).toContainText("Images could not be loaded");
  const menu = await imageMenu(page);
  await expect(menu.getByRole("button", { name: /thumbnail/i })).toHaveCount(0);
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.locator(".lm-summary-item")).toHaveCount(1);
  await open(page);
  await expect(dialog(page).getByRole("alert")).toContainText("Images could not be loaded");
  await page.evaluate(() => { window.failLoad = false; window.serverImages.a.image_url = "/from-the-demo-reel.webp"; });
  await dialog(page).getByRole("button", { name: "Reload images" }).click();
  await expect(dialog(page).getByRole("alert")).toHaveCount(0);
  await expect(dialog(page).locator('[data-lora-id="a"] img')).toHaveAttribute("src", "/from-the-demo-reel.webp");
});

test("sidebar edits step by 0.5, accept fine values, disable and restore remembered strengths", async ({ page }) => {
  await mount(page);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await expect(dialog(page).getByRole("button", { name: "Toggle Alpha" })).toHaveAttribute("aria-pressed", "true");
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  const strength = page.getByRole("spinbutton", { name: "Alpha strength", exact: true });
  await strength.fill("0.65");
  await strength.press("Enter");
  await expect(strength).toHaveValue("0.65");
  await strength.press("ArrowUp");
  await expect(strength).toHaveValue("1.15");
  await expect(strength).toBeFocused();
  await page.getByRole("button", { name: "Increase Alpha strength" }).click();
  await expect(strength).toHaveValue("1.65");
  await page.getByRole("button", { name: "Increase Alpha strength" }).click();
  await expect(strength).toHaveValue("2.00");
  await expect(page.getByRole("button", { name: "Increase Alpha strength" })).toBeDisabled();
  await strength.fill("0.63");
  await strength.press("Enter");
  await expect(strength).toHaveAttribute("aria-invalid", "true");
  expect(await page.evaluate(() => window.stack[0].strength)).toBe(2);
  await strength.press("Escape");
  await expect(strength).toHaveValue("2.00");
  await strength.fill("0.15");
  await strength.press("Tab");
  expect(await page.evaluate(() => window.stack[0].strength)).toBe(0.15);
  await expect(page.getByRole("button", { name: "Increase Alpha strength" })).toBeFocused();
  await strength.press("ArrowDown");
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Disable Beta" }).click();
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  await expect(page.getByLabel("Subject name")).toHaveValue("AlphaCharacter");
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("spinbutton", { name: "Alpha strength", exact: true })).toHaveValue("0.15");
  expect(await page.evaluate(() => window.stack.map((entry) => entry.id))).toEqual(["a", "b", "c"]);
});

test("typing zero and tabbing moves focus out of the removed row", async ({ page }) => {
  await mount(page);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  const alpha = page.getByRole("spinbutton", { name: "Alpha strength", exact: true });
  await alpha.fill("0");
  await alpha.press("Tab");
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toBeFocused();
  await expect(alpha).toHaveCount(0);
  await page.getByRole("button", { name: "Disable Beta" }).focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("button", { name: "Open LoRA manager" })).toBeFocused();
});

const copyTile = (page, name) => page.getByRole("button", { name: `Copy trigger word for ${name}`, exact: true });

test("a loaded LoRA's icon copies its trigger word, or its title when none is published", async ({ page }) => {
  await mount(page);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  const before = await page.evaluate(() => structuredClone(window.stack));

  await copyTile(page, "Alpha").click();
  expect(await page.evaluate(() => window.copies)).toEqual(["AlphaCharacter"]);
  expect(await page.evaluate(() => window.toasts.at(-1))).toEqual({ message: "Trigger word for Alpha copied.", kind: "success" });
  await expect(page.locator('[data-lora-summary-id="a"] .lm-summary-thumb')).toHaveClass(/is-copied/);

  await copyTile(page, "Beta").click();
  expect(await page.evaluate(() => window.copies)).toEqual(["AlphaCharacter", "Beta"]);
  expect(await page.evaluate(() => window.toasts.at(-1))).toEqual({
    message: "No trigger word is published for Beta, so its title was copied.", kind: "success",
  });

  await copyTile(page, "Beta").focus();
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.copies)).toEqual(["AlphaCharacter", "Beta", "Beta"]);

  // The panel's installation also hears clicks bubbling out of a list owned by
  // another installation, where its own context is deliberately null: it must stay
  // silent rather than report a copy it did not make.
  await page.evaluate(() => {
    window.installLoraStackControls(document.querySelector("#app"), {
      context: () => null,
      apply: () => {},
      copyText: async () => { throw new Error("This listener does not own the row."); },
      notify: (message, kind) => window.extraToasts.push({ message, kind }),
    });
  });
  await copyTile(page, "Alpha").click();
  expect(await page.evaluate(() => window.copies)).toEqual(["AlphaCharacter", "Beta", "Beta", "AlphaCharacter"]);
  expect(await page.evaluate(() => window.extraToasts)).toEqual([]);

  // Copying reads the published catalog only: the stack is exactly as it was.
  expect(await page.evaluate(() => window.stack)).toEqual(before);

  const copied = await page.evaluate(() => window.copies.length);
  await page.evaluate(() => { window.copyFails = true; });
  await copyTile(page, "Alpha").click();
  expect(await page.evaluate(() => window.copies.length)).toBe(copied);
  expect(await page.evaluate(() => window.toasts.at(-1))).toEqual({
    message: "Could not copy to the clipboard. Clipboard access is unavailable in this browser.", kind: "error",
  });
  expect(await page.evaluate(() => window.stack)).toEqual(before);
  await expect(page.locator(".lm-summary-thumb.is-copied")).toHaveCount(0);
});

test("an icon click commits a typed strength and changes nothing else", async ({ page }) => {
  await mount(page);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  const strength = page.getByRole("spinbutton", { name: "Alpha strength", exact: true });
  await strength.fill("0.65");
  await copyTile(page, "Alpha").click();
  await expect(strength).toHaveValue("0.65");
  expect(await page.evaluate(() => [window.copies, window.stack[0].strength])).toEqual([["AlphaCharacter"], 0.65]);
});

test("manager fits a narrow viewport with all rows and actions reachable", async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  const page = await context.newPage();
  await mount(page);
  await open(page);
  await expect(dialog(page).locator(".lm-card")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(dialog(page).getByRole("button", { name: "Apply" })).toBeVisible();
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).screenshot({ path: testInfo.outputPath("lora-manager-mobile.png"), animations: "disabled" });
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("button", { name: "Disable Alpha" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await context.close();
});

test("enabling ignores remembered strengths that the current workflow rejects", async ({ page }) => {
  await mount(page);
  await page.evaluate(() => { window.memory = { a: 99, b: .63 }; });
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Apply", exact: true }).click();
  expect(await page.evaluate(() => window.stack.filter((entry) => entry.strength > 0))).toEqual([{ id: "a", strength: 1 }, { id: "b", strength: 1 }]);
});

test("the application strip supports mouse, keyboard, and touch reordering independently of the gallery", async ({ page }) => {
  await mount(page);
  await open(page);
  for (const name of ["Alpha", "Beta", "Gamma"]) await dialog(page).getByRole("button", { name: `Toggle ${name}` }).click();
  const order = dialog(page).locator(".lm-order-chip");
  const handle = (name) => dialog(page).getByRole("button", { name: `Reorder ${name}`, exact: true });
  await handle("Gamma").dragTo(handle("Alpha"));
  await expect(order.first()).toHaveAttribute("data-lora-order-id", "c");
  await handle("Gamma").press("ArrowRight");
  await expect(order.nth(1)).toHaveAttribute("data-lora-order-id", "c");
  const session = await page.context().newCDPSession(page);
  await session.send("Emulation.setTouchEmulationEnabled", { enabled: true });
  const from = await handle("Gamma").boundingBox();
  const to = await handle("Beta").boundingBox();
  await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: from.x + from.width / 2, y: from.y + from.height / 2 }] });
  await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: to.x + to.width / 2, y: to.y + to.height / 2 }] });
  await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await expect(order.nth(2)).toHaveAttribute("data-lora-order-id", "c");
  expect(await dialog(page).locator(".lm-card").evaluateAll((cards) => cards.map((card) => card.dataset.loraId))).toEqual(["a", "b", "c"]);
  await session.detach();
});
