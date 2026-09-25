import { expect, test } from "@playwright/test";

async function mount(page) {
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.goto("/");
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { loraStackMarkup, installLoraControls } = await import(new URL("./lora-stack.mjs", base));
    window.stack = [{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }];
    window.soloCalls = [];
    const control = { id: "loras", items: [{ id: "a", label: "Alpha", trigger_word: "AlphaCharacter", description: "Use: AlphaCharacter in your prompt (no angle brackets)." }, { id: "b", label: "Beta" }, { id: "c", label: "Gamma" }], default: window.stack, minimum: 0, maximum: 2, step: 0.05 };
    const root = document.querySelector("#app");
    root.innerHTML = '<main style="width:min(100%,600px);padding:16px;overflow:hidden"><label>Subject name <input id="subject_name" value="Original subject" /></label><fieldset class="field semantic-fieldset"><legend>LoRAs</legend><div id="lora-host"></div></fieldset></main>';
    window.renderLoras = () => { document.querySelector("#lora-host").innerHTML = loraStackMarkup(control, window.stack); };
    window.renderLoras();
    installLoraControls(root, {
      read: () => window.stack,
      write: (id, value) => { window.stack = structuredClone(value); },
      refresh: window.renderLoras,
      onSolo: (triggerWord) => {
        window.soloCalls.push(triggerWord);
        document.querySelector("#subject_name").value = triggerWord;
        return true;
      },
    });
  });
}

test("Quick Picks set one LoRA to 1 and only verified triggers update Subject name", async ({ page }) => {
  await mount(page);
  await expect(page.locator(".lora-mixer")).toHaveCount(0);
  await page.getByRole("button", { name: "Use only AlphaCharacter at strength 1" }).click();
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 1 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }]);
  await expect(page.getByLabel("Subject name")).toHaveValue("AlphaCharacter");
  await expect(page.getByRole("spinbutton", { name: "AlphaCharacter strength" })).toHaveValue("1");
  await expect(page.getByRole("button", { name: "Use only AlphaCharacter at strength 1" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("spinbutton", { name: "AlphaCharacter strength" }).fill("0.65");
  await page.getByRole("button", { name: "Use only AlphaCharacter at strength 1" }).click();
  expect(await page.evaluate(() => window.stack[0].strength)).toBe(1);

  await page.getByRole("button", { name: "Use only Beta at strength 1" }).click();
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 1 }, { id: "c", strength: 0 }]);
  await expect(page.getByLabel("Subject name")).toHaveValue("AlphaCharacter");
  expect(await page.evaluate(() => window.soloCalls)).toEqual(["AlphaCharacter", "AlphaCharacter"]);
  await expect(page.locator("[data-lora-status]")).toContainText("Subject name is unchanged");

  await page.getByRole("button", { name: "All off" }).click();
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }]);
  await expect(page.getByLabel("Subject name")).toHaveValue("AlphaCharacter");
  await expect(page.locator(".lora-active-count")).toHaveText("0 active");
});

test("mixer retains exact strengths, multiple selection, and reordering", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Mix & adjust ＋" }).click();
  await expect(page.locator(".lora-row")).toHaveCount(3);
  await expect(page.locator("[data-lora-move]")).toHaveCount(0);
  await page.getByRole("checkbox", { name: "Enable Beta" }).check();
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue("1");
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("0.65");
  await expect(page.getByRole("slider", { name: "Beta strength slider" })).toHaveValue("0.65");
  await page.getByRole("button", { name: "Reorder Beta" }).dragTo(page.locator('[data-lora-id="a"]'));
  await expect(page.locator(".lora-row").first()).toHaveAttribute("data-lora-id", "b");
  await page.getByRole("button", { name: "Reorder Beta" }).press("ArrowDown");
  await expect(page.locator(".lora-row").nth(1)).toHaveAttribute("data-lora-id", "b");
  await expect(page.locator('[data-lora-status]')).toContainText("position 2 of 3");
  await page.getByRole("slider", { name: "Beta strength slider" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue("0.7");
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0.7 }, { id: "c", strength: 0 }]);
  await page.getByRole("checkbox", { name: "Enable Gamma" }).check();
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0.7 }, { id: "c", strength: 1 }]);
  await expect(page.locator(".lora-active-count")).toHaveText("2 active");
  await expect(page.getByLabel("Subject name")).toHaveValue("Original subject");
});

test("touch movement retains zero rows and input identity at phone width", async ({ browser }, testInfo) => {
  const context = await browser.newContext({ hasTouch: true, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await mount(page);
  await page.getByRole("button", { name: "Mix & adjust ＋" }).tap();
  await page.getByRole("button", { name: "AlphaCharacter", exact: true }).tap();
  await expect(page.locator("#lora-usage-loras-a")).toBeVisible();
  const handle = await page.getByRole("button", { name: "Reorder Gamma" }).boundingBox();
  const target = await page.locator('[data-lora-id="a"]').boundingBox();
  const session = await context.newCDPSession(page);
  const x = handle.x + handle.width / 2;
  const startY = handle.y + handle.height / 2;
  const endY = target.y + target.height / 2;
  await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: startY, id: 1 }] });
  for (let i = 1; i <= 6; i++) await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: startY + (endY - startY) * i / 6, id: 1 }] });
  await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await expect(page.locator("#lora-usage-loras-a")).not.toBeVisible();
  await expect(page.locator(".lora-row").first()).toHaveAttribute("data-lora-id", "c");
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "c", strength: 0 }, { id: "a", strength: 0 }, { id: "b", strength: 0 }]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("lora-controls-phone.png") });
  await context.close();
});

test("title tooltips support hover, keyboard, dismissal, and clipped panels", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "Mix & adjust ＋" }).click();
  const title = page.getByRole("button", { name: "AlphaCharacter", exact: true });
  const tooltip = page.locator("#lora-usage-loras-a");
  await expect(tooltip).not.toBeVisible();
  await title.hover();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toHaveText("Use: AlphaCharacter in your prompt (no angle brackets).");
  await tooltip.hover();
  await expect(tooltip).toBeVisible();
  await page.getByRole("button", { name: "Reorder Gamma" }).hover();
  await expect(tooltip).not.toBeVisible();
  await title.focus();
  await expect(tooltip).toBeVisible();
  await title.press("Escape");
  await expect(tooltip).not.toBeVisible();
  await expect(title).toBeFocused();
  await title.click();
  await expect(tooltip).toBeVisible();
  await page.getByRole("button", { name: "Gamma", exact: true }).focus();
  await expect(page.locator("#lora-usage-loras-c")).toContainText("trigger words have not been verified");
  const box = await page.locator("#lora-usage-loras-c").boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize().height);
  expect(await page.locator("#lora-usage-loras-c").evaluate((el) => el.matches(":popover-open"))).toBe(true);
});
