import { expect, test } from "@playwright/test";

async function mount(page) {
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.goto("/");
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { loraStackMarkup, installLoraStackControls } = await import(new URL("./lora-stack.mjs", base));
    const { installLoraManager } = await import(new URL("./lora-manager.mjs", base));
    const control = { id: "loras", type: "lora_stack", label: "LoRAs", items: [
      { id: "a", label: "Alpha", trigger_word: "AlphaCharacter", description: "Use AlphaCharacter in the prompt." },
      { id: "b", label: "Beta" }, { id: "c", label: "Gamma" },
    ], default: [{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }], minimum: 0, maximum: 2, step: 0.05 };
    window.stack = structuredClone(control.default);
    window.memory = {};
    window.images = Object.fromEntries(control.items.map(({ id }) => [id, { id, version: "a".repeat(64), image_url: null }]));
    window.serverImages = structuredClone(window.images);
    window.posts = [];
    window.notifications = [];
    window.sourceKey = "source";
    window.revision = { publication_id: "one" };
    window.subject = "Original subject";
    window.renderLoras = () => { document.querySelector("#lora-summary").innerHTML = loraStackMarkup(control, window.stack, window.images, { editable: true }); };
    const root = document.querySelector("#app");
    root.innerHTML = `<main style="padding:16px;max-width:600px"><label>Subject name <input id="subject_name" value="Original subject"></label><section class="control-section is-expanded"><div class="control-section-header"><button class="control-section-trigger" aria-expanded="true">LoRAs</button><span class="control-section-status">0 active</span><div class="prompt-field-actions control-section-actions"><button type="button" class="icon-button prompt-editor-launch" data-lora-open data-lora-control-id="loras" aria-label="Open LoRA manager"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div></div><div class="control-section-body"><div id="lora-summary"></div></div></section></main><dialog id="lora-manager-dialog" class="lm-dialog" aria-labelledby="lora-manager-title"></dialog>`;
    window.renderLoras();
    installLoraStackControls(root, {
      context: () => ({ control, sourceKey: window.sourceKey, values: window.stack, memory: window.memory }),
      apply: (_id, values, memory) => { window.stack = structuredClone(values); window.memory = structuredClone(memory); },
    });
    window.manager = installLoraManager(root, {
      api: async (path, options) => {
        if (!options) {
          if (window.holdLoad) await new Promise((resolve) => { window.finishLoad = resolve; });
          if (window.failLoad) throw new Error("Offline");
          return { items: structuredClone(Object.values(window.serverImages)) };
        }
        const changes = JSON.parse(options.body.get("changes"));
        window.posts.push(changes);
        if (window.holdSave) await new Promise((resolve) => { window.finishSave = resolve; });
        if (window.rejectSave) throw new Error("Upload rejected");
        if (window.forceConflict) { const error = new Error("Image changed."); error.code = "lora_image_conflict"; throw error; }
        for (const change of changes) {
          if (change.version !== window.serverImages[change.id].version) throw Object.assign(new Error("Image changed"), { code: "lora_image_conflict" });
          window.serverImages[change.id] = { id: change.id, version: String(window.posts.length).padStart(64, "0"), image_url: change.action === "set" ? `/sample-image-${window.posts.length}.webp` : null };
        }
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
  });
}

const dialog = (page) => page.locator("#lora-manager-dialog");

async function open(page) {
  await page.getByRole("button", { name: "Open LoRA manager" }).click();
  await expect(dialog(page)).toBeVisible();
}

test("manager drafts enable, strength, order and Subject until Apply", async ({ page }) => {
  await mount(page);
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("spinbutton", { name: "Beta strength" }).fill("1.25");
  await dialog(page).getByRole("spinbutton", { name: "Beta strength" }).press("Tab");
  await dialog(page).getByRole("button", { name: "Reorder Beta" }).press("ArrowUp");
  await expect(dialog(page).locator(".lm-row").first()).toHaveAttribute("data-lora-id", "b");
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
  await expect(dialog(page).getByRole("spinbutton", { name: "Alpha strength" })).toHaveValue("1.00");
});

test("row background toggles without jumping the scrolled list or hijacking strength controls", async ({ page }) => {
  await mount(page);
  await page.addStyleTag({ content: "#lora-manager-dialog { height: 330px; }" });
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
  await manager.getByRole("spinbutton", { name: "Gamma strength" }).click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(await content.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});

const sample = { name: "sample.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a", "hex") };
async function uploadImage(page, label = "Beta") {
  await dialog(page).getByRole("button", { name: new RegExp(`(?:Add|Change) image for ${label}`) }).click();
  await dialog(page).locator("[data-lora-file]").setInputFiles(sample);
}

test("thumbnail additions, replacements and removals save independently of Cancel and Escape", async ({ page }) => {
  await mount(page);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await uploadImage(page);
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  expect(await page.evaluate(() => window.posts.length)).toBe(1);
  expect(await page.evaluate(() => window.stack.every((entry) => entry.strength === 0))).toBe(true);
  await open(page);
  await expect(dialog(page).getByRole("button", { name: "Change image for Beta" })).toBeVisible();
  await uploadImage(page);
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
  await page.keyboard.press("Escape");
  await open(page);
  await dialog(page).getByRole("button", { name: "Change image for Beta" }).hover();
  await dialog(page).getByRole("button", { name: "Remove image for Beta" }).click();
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
  await dialog(page).getByRole("button", { name: "Cancel and close LoRA manager" }).click();
  await open(page);
  await expect(dialog(page).getByRole("button", { name: "Add image for Beta" })).toBeVisible();
  expect(await page.evaluate(() => window.posts.flat().map((change) => change.action))).toEqual(["set", "set", "remove"]);
  expect(await page.evaluate(() => window.posts[1][0].version)).toBe("1".padStart(64, "0"));
});

test("pending thumbnail saves survive closing and reopening without committing selection edits", async ({ page }) => {
  await mount(page);
  await open(page);
  await page.evaluate(() => { window.holdSave = true; });
  await uploadImage(page);
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saving…");
  await expect(dialog(page).getByRole("button", { name: "Change image for Beta" })).toBeDisabled();
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await open(page);
  await expect(dialog(page).getByRole("button", { name: "Toggle Alpha" })).toHaveAttribute("aria-pressed", "false");
  await page.evaluate(() => { window.holdSave = false; window.finishSave(); });
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
  expect(await page.evaluate(() => window.images.b.image_url)).toBe("/sample-image-1.webp");
});

test("conflicts refresh images without overwriting them or blocking Apply", async ({ page }) => {
  await mount(page);
  await open(page);
  await page.evaluate(() => { window.serverImages.b = { id: "b", version: "c".repeat(64), image_url: "/other-user.webp" }; });
  await uploadImage(page);
  await expect(dialog(page).getByRole("alert")).toContainText("changed elsewhere");
  await expect(dialog(page).locator('[data-lora-id="b"] img')).toHaveAttribute("src", "/other-user.webp");
  expect(await page.evaluate(() => window.posts.length)).toBe(1);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.locator(".lm-summary-item")).toHaveCount(1);
  await open(page);
  await uploadImage(page);
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
});

test("loading errors allow selection edits and a retry; rejected saves restore the confirmed image", async ({ page }) => {
  await mount(page);
  await page.evaluate(() => { window.failLoad = true; });
  await open(page);
  await expect(dialog(page).getByRole("alert")).toContainText("Images could not be loaded");
  await expect(dialog(page).getByRole("button", { name: "Add image for Beta" })).toBeDisabled();
  await dialog(page).getByRole("button", { name: "Toggle Beta" }).click();
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.locator(".lm-summary-item")).toHaveCount(1);
  await open(page);
  await page.evaluate(() => { window.failLoad = false; });
  await dialog(page).getByRole("button", { name: "Reload images" }).click();
  await uploadImage(page);
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
  await page.evaluate(() => { window.rejectSave = true; });
  await uploadImage(page);
  await expect(dialog(page).getByRole("alert")).toContainText("Upload rejected");
  await expect(dialog(page).locator('[data-lora-id="b"] img')).toHaveAttribute("src", "/sample-image-1.webp");
});

test("save failure after closing is reported, and a stale publication cannot update the current cache", async ({ page }) => {
  await mount(page);
  await open(page);
  await page.evaluate(() => { window.holdSave = true; });
  await uploadImage(page);
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await page.evaluate(() => { window.rejectSave = true; window.finishSave(); });
  await expect.poll(() => page.evaluate(() => window.notifications.length)).toBe(1);
  await page.evaluate(() => { window.rejectSave = false; });
  await open(page);
  await uploadImage(page);
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await page.evaluate(() => {
    window.revision = { publication_id: "two" };
    window.images.b.image_url = "/new-publication.webp";
    window.finishSave();
  });
  await expect.poll(() => page.evaluate(() => window.serverImages.b.image_url)).toBe("/sample-image-2.webp");
  expect(await page.evaluate(() => window.images.b.image_url)).toBe("/new-publication.webp");
});

test("edits to different thumbnails are serialized without losing either image", async ({ page }) => {
  await mount(page);
  await open(page);
  await page.evaluate(() => { window.holdSave = true; });
  await uploadImage(page, "Alpha");
  await uploadImage(page, "Beta");
  expect(await page.evaluate(() => window.posts.length)).toBe(1);
  await page.evaluate(() => { window.holdSave = false; window.finishSave(); });
  await expect(dialog(page).locator('.lm-image-status').filter({ hasText: "Saved" })).toHaveCount(2);
  expect(await page.evaluate(() => [window.images.a.image_url, window.images.b.image_url])).toEqual(["/sample-image-1.webp", "/sample-image-2.webp"]);
});

test("a background save keeps an open file chooser attached to its target", async ({ page }) => {
  await mount(page);
  await open(page);
  await page.evaluate(() => { window.holdSave = true; });
  await uploadImage(page, "Alpha");
  await dialog(page).getByRole("button", { name: "Add image for Beta" }).click();
  const fileInput = await dialog(page).locator("[data-lora-file]").elementHandle();
  await page.evaluate(() => { window.holdSave = false; window.finishSave(); });
  await expect(dialog(page).locator('[data-lora-id="a"] .lm-image-status')).toHaveText("Saved");
  expect(await fileInput.evaluate((element) => element.isConnected)).toBe(true);
  await fileInput.setInputFiles(sample);
  await expect(dialog(page).locator('[data-lora-id="b"] .lm-image-status')).toHaveText("Saved");
  expect(await page.evaluate(() => window.posts.map((changes) => changes[0].id))).toEqual(["a", "b"]);
});

test("sidebar edits step by 0.5, accept fine values, disable and restore remembered strengths", async ({ page }) => {
  await mount(page);
  await open(page);
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await expect(dialog(page).locator('[data-lora-id="a"] .lm-row-state')).toHaveText("✓ Enabled");
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
  await expect(dialog(page).getByRole("spinbutton", { name: "Alpha strength", exact: true })).toHaveValue("0.15");
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

test("manager fits a narrow viewport with all rows and actions reachable", async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  const page = await context.newPage();
  await mount(page);
  await open(page);
  await expect(dialog(page).locator(".lm-row")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(dialog(page).getByRole("button", { name: "Apply" })).toBeVisible();
  await dialog(page).getByRole("button", { name: "Toggle Alpha" }).click();
  await dialog(page).screenshot({ path: testInfo.outputPath("lora-manager-mobile.png"), animations: "disabled" });
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(page.getByRole("button", { name: "Disable Alpha" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await context.close();
});
