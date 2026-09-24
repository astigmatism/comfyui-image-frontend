import { expect, test } from "@playwright/test";

const automationUser = `automation.${Date.now()}`;
let automationUserCreated = false;

async function signIn(page) {
  const anonymous = await (await page.request.get("/api/auth/session")).json();
  for (const password of ["E2EAdminPermanent123!", "E2EAdminTemporary123!"]) {
    const result = await page.request.post("/api/auth/login", {
      headers: { "X-CSRF-Token": anonymous.csrf_token }, data: { username: "admin", password },
    });
    if (!result.ok()) continue;
    let session = await result.json();
    if (session.user.must_change_password) {
      await page.request.post("/api/auth/password", { headers: { "X-CSRF-Token": session.csrf_token }, data: { new_password: "E2EAdminPermanent123!" } });
      session = await (await page.request.get("/api/auth/session")).json();
    }
    if (!automationUserCreated) {
      const created = await page.request.post("/api/admin/users", { headers: { "X-CSRF-Token": session.csrf_token }, data: { username: automationUser, temporary_password: "AutomationTemporary123!" } });
      expect(created.ok()).toBe(true);
      automationUserCreated = true;
    }
    await page.request.post("/api/auth/logout", { headers: { "X-CSRF-Token": session.csrf_token } });
    const fresh = await (await page.request.get("/api/auth/session")).json();
    let userLogin = await page.request.post("/api/auth/login", { headers: { "X-CSRF-Token": fresh.csrf_token }, data: { username: automationUser, password: "AutomationPermanent123!" } });
    if (!userLogin.ok()) userLogin = await page.request.post("/api/auth/login", { headers: { "X-CSRF-Token": fresh.csrf_token }, data: { username: automationUser, password: "AutomationTemporary123!" } });
    expect(userLogin.ok()).toBe(true);
    const userSession = await userLogin.json();
    if (userSession.user.must_change_password) await page.request.post("/api/auth/password", { headers: { "X-CSRF-Token": userSession.csrf_token }, data: { new_password: "AutomationPermanent123!" } });
    await page.goto("/");
    await expect(page.locator("#workflow-source")).toBeEnabled();
    await expect(page.locator("#auto-generate")).toBeEnabled();
    return;
  }
  throw new Error("Could not sign in");
}

test("shared settings and server automation survive independent browsers and no browsers", async ({ browser, playwright }, testInfo) => {
  test.setTimeout(100_000);
  const first = await browser.newContext();
  const page = await first.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await signIn(page);
  await page.locator("#generation-quantity").fill("1");
  await page.locator("#generation-quantity").blur();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow server automation lighthouse");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).blur();
  await expect(page.locator(".shared-settings-status")).toContainText("Settings saved across devices");
  const second = await browser.newContext();
  const other = await second.newPage();
  other.on("pageerror", (error) => errors.push(error.message));
  await signIn(other);
  await expect(other.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue("slow server automation lighthouse");
  await expect(page.locator('[data-control-section="auto-generation"] .control-section-body')).toHaveAttribute("inert", "");
  await expect(page.locator('[data-control-section="auto-generation"] .control-section-body')).toHaveCSS("opacity", "0");
  const browserSubmissions = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/api\/generations(?:\/batch)?$/.test(request.url())) browserSubmissions.push(request.url());
  });
  await page.locator("#auto-generate").check();
  await expect(page.locator("#auto-generate-limit")).toHaveValue("200");
  await expect(other.locator("#auto-generate")).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("auto-generation-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const panelToggle = page.getByRole("button", { name: "Open generation controls", exact: true });
  if (await panelToggle.getAttribute("aria-expanded") === "false") await panelToggle.click();
  await expect(page.locator("#auto-generate-limit")).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("auto-generation-mobile.png"), fullPage: true, animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await other.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow revised automation lighthouse");
  await other.getByRole("textbox", { name: "Prompt", exact: true }).blur();
  await expect(other.locator(".shared-settings-status")).toContainText("Settings saved across devices");
  const beforeApply = await (await other.request.get("/api/auto-generation")).json();
  expect(beforeApply.snapshot.generation.parameters.prompt).toBe("slow server automation lighthouse");
  await other.getByRole("button", { name: "Apply to auto generation", exact: true }).click();
  await expect.poll(async () => (await (await other.request.get("/api/auto-generation")).json()).snapshot.generation.parameters.prompt).toBe("slow revised automation lighthouse");
  await expect(other.getByRole("button", { name: "Apply to auto generation", exact: true })).toBeDisabled();
  await page.reload();
  await expect(page.locator("#auto-generate")).toBeChecked();
  await expect(page.locator("#generate-button")).toBeEnabled();
  await page.locator("#auto-generate-limit").fill("2");
  const changed = page.waitForResponse((response) => response.url().endsWith("/api/auto-generation/apply") && response.request().method() === "POST");
  await page.locator("#auto-generate-limit").blur();
  await page.getByRole("button", { name: "Apply to auto generation", exact: true }).click();
  expect((await changed).ok()).toBe(true);
  const observer = await playwright.request.newContext({ baseURL: `http://127.0.0.1:${process.env.CIF_E2E_PORT || "8765"}`, storageState: await first.storageState() });
  await first.close();
  await second.close();
  await expect.poll(async () => (await (await observer.get("/api/auto-generation")).json()).enabled, { timeout: 50_000 }).toBe(false);
  const ended = await (await observer.get("/api/auto-generation")).json();
  expect(ended.accepted_count).toBe(2);
  expect(ended.remaining).toBe(0);
  expect(ended.status).toBe("completed");
  expect(browserSubmissions).toEqual([]);
  expect(errors).toEqual([]);
  await observer.dispose();
});
