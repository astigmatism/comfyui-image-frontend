import { expect, test } from "@playwright/test";

async function mount(page) {
  await page.route("**/app.mjs", (route) => route.fulfill({ contentType: "text/javascript", body: "export {};" }));
  await page.goto("/");
  await page.evaluate(async () => {
    const base = document.querySelector('script[type="module"]').src;
    const { loraStackMarkup, installLoraControls } = await import(new URL("./lora-stack.mjs", base));
    window.stack = [{ id: "a", strength: 0 }, { id: "b", strength: 0 }, { id: "c", strength: 0 }];
    const control = { id: "loras", items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }, { id: "c", label: "Gamma" }], default: window.stack, minimum: 0, maximum: 2, step: 0.05 };
    const root = document.querySelector("#app");
    root.innerHTML = `<main style="width:min(100%,600px);padding:16px"><fieldset class="field semantic-fieldset"><legend>LoRAs</legend>${loraStackMarkup(control, window.stack)}</fieldset></main>`;
    installLoraControls(root, { read: () => window.stack, write: (id, value) => { window.stack = structuredClone(value); } });
  });
}

test("dragging and keyboard movement preserve slider and exact strengths", async ({ page }) => {
  await mount(page);
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("0.65");
  await expect(page.getByRole("slider", { name: "Beta strength slider" })).toHaveValue("0.65");
  await page.getByRole("button", { name: "Reorder Beta" }).dragTo(page.locator('[data-lora-id="a"]'));
  await expect(page.locator(".lora-row").first()).toHaveAttribute("data-lora-id", "b");
  await page.getByRole("button", { name: "Reorder Beta" }).press("ArrowDown");
  await expect(page.locator(".lora-row").nth(1)).toHaveAttribute("data-lora-id", "b");
  await page.getByRole("slider", { name: "Beta strength slider" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue("0.7");
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "a", strength: 0 }, { id: "b", strength: 0.7 }, { id: "c", strength: 0 }]);
  await expect(page.locator('[data-lora-status]')).toContainText("position 2 of 3");
});

test("touch movement retains zero rows and input identity at phone width", async ({ browser }, testInfo) => {
  const context = await browser.newContext({ hasTouch: true, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await mount(page);
  await page.getByRole("button", { name: "Move Gamma up" }).tap();
  await page.getByRole("button", { name: "Move Gamma up" }).tap();
  await expect(page.locator(".lora-row").first()).toHaveAttribute("data-lora-id", "c");
  expect(await page.evaluate(() => window.stack)).toEqual([{ id: "c", strength: 0 }, { id: "a", strength: 0 }, { id: "b", strength: 0 }]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("lora-controls-phone.png") });
  await context.close();
});
