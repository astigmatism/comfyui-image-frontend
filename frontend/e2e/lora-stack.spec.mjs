import { expect, test } from "@playwright/test";

async function mount(page) {
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.goto("/");
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { loraStackMarkup } = await import(new URL("./lora-stack.mjs", base));
    const { installLoraManager } = await import(new URL("./lora-manager.mjs", base));
    const control = { id: "loras", type: "lora_stack", label: "LoRAs", items: [
      { id: "a", label: "Alpha", trigger_word: "AlphaCharacter", description: "Use AlphaCharacter in the prompt." },
      { id: "b", label: "Beta" }, { id: "c", label: "Gamma" },
    ], default: [{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }], minimum: 0, maximum: 2, step: 0.05 };
    window.stack = structuredClone(control.default);
    window.memory = {};
    window.images = Object.fromEntries(control.items.map(({ id }) => [id, { id, version: "a".repeat(64), image_url: null }]));
    window.posts = [];
    window.subject = "Original subject";
    window.renderLoras = () => { document.querySelector("#lora-summary").innerHTML = loraStackMarkup(control, window.stack, window.images); };
    const root = document.querySelector("#app");
    root.innerHTML = `<main style="padding:16px;max-width:600px"><label>Subject name <input id="subject_name" value="Original subject"></label><section class="control-section is-expanded"><div class="control-section-header"><button class="control-section-trigger" aria-expanded="true">LoRAs</button><span class="control-section-status">0 active</span><div class="prompt-field-actions control-section-actions"><button type="button" class="icon-button prompt-editor-launch" data-lora-open data-lora-control-id="loras" aria-label="Open LoRA manager"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5" /></svg></button></div></div><div class="control-section-body"><div id="lora-summary"></div></div></section></main><dialog id="lora-manager-dialog" class="lm-dialog" aria-labelledby="lora-manager-title"></dialog>`;
    window.renderLoras();
    installLoraManager(root, {
      api: async (path, options) => {
        if (!options) return { items: Object.values(window.images) };
        const changes = JSON.parse(options.body.get("changes"));
        window.posts.push(changes);
        if (window.forceConflict) { const error = new Error("Image changed."); error.code = "lora_image_conflict"; throw error; }
        for (const change of changes) window.images[change.id] = { id: change.id, version: "b".repeat(64), image_url: change.action === "set" ? "/sample-image.webp" : null };
        return { items: Object.values(window.images) };
      },
      context: () => ({ control, sourceKey: "source", sourceName: "Sample workflow", values: window.stack, memory: window.memory, images: window.images }),
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
  await dialog(page).getByRole("checkbox", { name: "Enable Beta" }).check();
  await dialog(page).getByRole("spinbutton", { name: "Beta strength" }).fill("1.25");
  await dialog(page).getByRole("spinbutton", { name: "Beta strength" }).press("Tab");
  await dialog(page).getByRole("button", { name: "Reorder Beta" }).press("ArrowUp");
  await expect(dialog(page).locator(".lm-row").first()).toHaveAttribute("data-lora-id", "b");
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator(".lm-summary-item")).toHaveCount(0);
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }]);

  await open(page);
  await dialog(page).getByRole("checkbox", { name: "Enable Alpha" }).check();
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
  await dialog(page).getByRole("checkbox", { name: "Enable Alpha" }).check();
  await expect(dialog(page).getByRole("spinbutton", { name: "Alpha strength" })).toHaveValue("1.00");
});

test("staged image upload is saved only on Apply and conflicts keep the draft", async ({ page }) => {
  await mount(page);
  await open(page);
  // The image button identifies the row for the hidden local file picker.
  await dialog(page).getByRole("button", { name: "Add image for Beta" }).click();
  await dialog(page).locator("[data-lora-file]").setInputFiles({ name: "sample.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a", "hex") });
  await expect(dialog(page).getByRole("button", { name: "Change image for Beta" })).toBeVisible();
  await dialog(page).getByRole("button", { name: "Cancel", exact: true }).click();
  expect(await page.evaluate(() => window.posts.length)).toBe(0);
  await open(page);
  await dialog(page).getByRole("button", { name: "Add image for Beta" }).click();
  await dialog(page).locator("[data-lora-file]").setInputFiles({ name: "sample.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a", "hex") });
  await page.evaluate(() => { window.forceConflict = true; });
  await dialog(page).getByRole("button", { name: "Apply" }).click();
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).getByRole("alert")).toContainText("changed while this manager was open");
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }]);
});

test("manager fits a narrow viewport with all rows and actions reachable", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
  const page = await context.newPage();
  await mount(page);
  await open(page);
  await expect(dialog(page).locator(".lm-row")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(dialog(page).getByRole("button", { name: "Apply" })).toBeVisible();
  await context.close();
});
