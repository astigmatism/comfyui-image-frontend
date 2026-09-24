import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  let loginSession = await (await page.request.get("/api/auth/session")).json();
  let response = await page.request.post("/api/auth/login", { headers: { "X-CSRF-Token": loginSession.csrf_token }, data: { username: "admin", password: "E2EAdminPermanent123!" } });
  if (!response.ok()) response = await page.request.post("/api/auth/login", { headers: { "X-CSRF-Token": loginSession.csrf_token }, data: { username: "admin", password: "E2EAdminTemporary123!" } });
  expect(response.ok(), await response.text()).toBeTruthy();
  let session = await response.json();
  if (session.user.must_change_password) {
    await page.request.post("/api/auth/password", { headers: { "X-CSRF-Token": session.csrf_token }, data: { new_password: "E2EAdminPermanent123!" } });
    session = await (await page.request.get("/api/auth/session")).json();
  }
  const username = `prompt.${Date.now()}.${Math.floor(Math.random() * 10000)}`;
  const created = await page.request.post("/api/admin/users", { headers: { "X-CSRF-Token": session.csrf_token }, data: { username, temporary_password: "PromptTemporary123!" } });
  expect(created.ok()).toBeTruthy();
  await page.request.post("/api/auth/logout", { headers: { "X-CSRF-Token": session.csrf_token } });
  loginSession = await (await page.request.get("/api/auth/session")).json();
  session = await (await page.request.post("/api/auth/login", { headers: { "X-CSRF-Token": loginSession.csrf_token }, data: { username, password: "PromptTemporary123!" } })).json();
  await page.request.post("/api/auth/password", { headers: { "X-CSRF-Token": session.csrf_token }, data: { new_password: "PromptPermanent123!" } });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Generate", exact: true })).toBeEnabled();
  await page.getByRole("switch", { name: "Use Prompt Generation" }).check();
  await expect(page.getByRole("textbox", { name: "Subject name", exact: true })).toBeVisible();
});

test("approved sections, standalone prompt, and every image in a batch", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await expect(page.locator('[data-control-section="prompt-generation"] .control-section-trigger')).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#prompt-generation-source option")).toHaveCount(2);
  await page.getByRole("switch", { name: "Use Prompt Generation" }).uncheck();
  await page.getByRole("switch", { name: "Use Creative Direction" }).check();
  const create = page.getByRole("radio", { name: "New Prompt from Creative Direction", exact: true });
  await create.check();
  await page.getByRole("switch", { name: "Use Prompt Generation" }).check();
  await expect(create).toBeDisabled();
  await page.getByRole("switch", { name: "Use Prompt Generation" }).uncheck();
  await expect(create).toBeChecked();
  await page.getByRole("switch", { name: "Use Prompt Generation" }).check();
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("Mira");
  await page.getByRole("button", { name: "Generate prompt", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(/Mira explores a quiet forest/);
  await page.getByRole("switch", { name: "Use Creative Direction" }).check();
  await expect(page.getByRole("radio", { name: "New Prompt from Creative Direction", exact: true })).toBeDisabled();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("Warm sunlight");
  await expect(page.locator("#prompt-pipeline-flow")).toHaveText("Prompt generation → Refine → Image");
  await page.setViewportSize({ width: 1440, height: 1100 });
  await expect(async () => {
    await page.locator("#prompt-generation-source").scrollIntoViewIfNeeded();
    await expect(page.locator("#prompt-generation-source")).toBeInViewport();
  }).toPass();
  await page.screenshot({ path: testInfo.outputPath("prompt-generation-desktop.png"), fullPage: true });
  await page.getByRole("textbox", { name: "Generation quantity" }).fill("2");
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page.locator(".gallery-card.status-succeeded")).toHaveCount(2);
  expect(errors).toEqual([]);
});

test("local edits survive refresh before remote saving", async ({ page }) => {
  await page.route("**/api/preferences", async (route) => {
    if (route.request().method() === "PUT") return route.abort("failed");
    return route.continue();
  });
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("Local subject");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("My unsynced draft");
  await page.getByRole("textbox", { name: "Generation quantity" }).fill("3");
  await page.getByRole("checkbox", { name: "Random prompt seed" }).uncheck();
  await page.getByRole("textbox", { name: "Prompt seed value" }).fill("123");
  await page.reload();
  await expect(page.getByRole("switch", { name: "Use Prompt Generation" })).toBeChecked();
  await expect(page.getByRole("textbox", { name: "Subject name", exact: true })).toHaveValue("Local subject");
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue("My unsynced draft");
  await expect(page.getByRole("textbox", { name: "Generation quantity" })).toHaveValue("3");
  await expect(page.getByRole("textbox", { name: "Prompt seed value" })).toHaveValue("123");
});

test("quantity and model selections share one generated prompt", async ({ page }) => {
  test.setTimeout(60_000);
  await page.locator("#workflow-source").click();
  const dialog = page.locator("#source-picker-dialog");
  const choices = dialog.locator("[data-source-workflow-choice]");
  const source = await choices.locator("option").filter({ hasText: "Moody Krea 2 Mix V4" }).getAttribute("value");
  await choices.selectOption(source);
  await dialog.getByRole("button", { name: "Clear all", exact: true }).click();
  await dialog.locator('[data-checkpoint-card] input[type="checkbox"]').nth(0).check();
  await dialog.locator('[data-checkpoint-card] input[type="checkbox"]').nth(1).check();
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await page.getByRole("textbox", { name: "Generation quantity" }).fill("2");
  const acceptance = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/generation-preparations" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  const response = await acceptance;
  expect(response.status()).toBe(202);
  const submitted = response.request().postDataJSON().items;
  expect(submitted).toHaveLength(4);
  expect(new Set(submitted.map((item) => item.generation.parameters.checkpoint)).size).toBe(2);
  const group = (await response.json()).id;
  await expect.poll(async () => (await (await page.request.get(`/api/generation-preparations/${group}`)).json()).items.filter((item) => item.status === "accepted").length).toBe(4);
  const result = await (await page.request.get(`/api/generation-preparations/${group}`)).json();
  expect(result.items.filter((item) => item.status === "accepted")).toHaveLength(4);
  expect(new Set(result.items.map((item) => item.prompt_run_id)).size).toBe(1);
  expect(new Set(result.items.map((item) => item.raw_prompt)).size).toBe(1);
  expect(new Set(result.items.map((item) => item.generation?.checkpoint_label)).size).toBe(2);
});

test("late results preserve a draft across reload and offer Use latest", async ({ page }) => {
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("slow Mira");
  await page.getByRole("button", { name: "Generate prompt", exact: true }).click();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("Keep this draft");
  await page.reload();
  await expect(page.getByRole("button", { name: "Use latest prompt" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue("Keep this draft");
  await page.getByRole("button", { name: "Use latest prompt" }).click();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(/slow Mira explores/);
});

test("enabling auto generation expands controls and respects the image limit", async ({ page }) => {
  await page.getByRole("textbox", { name: "Generation quantity" }).fill("2");
  await page.getByRole("textbox", { name: "Generation quantity" }).blur();
  const autoSection = page.locator('[data-control-section="auto-generation"]');
  await autoSection.locator(".control-section-trigger").click();
  await page.getByRole("spinbutton", { name: "Images queued limit" }).fill("2");
  await autoSection.locator(".control-section-trigger").click();
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  await expect(autoSection.locator(".control-section-trigger")).toHaveAttribute("aria-expanded", "true");
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).revision).toBeGreaterThan(0);
  await page.reload();
  await expect(page.locator(".gallery-card.status-succeeded")).toHaveCount(2);
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).status).toBe("completed");
  const items = (await (await page.request.get("/api/generations")).json()).items;
  const details = await Promise.all(items.map(async (item) => (await page.request.get(`/api/generations/${item.id}`)).json()));
  expect(details).toHaveLength(2);
  expect(details[0].final_prompt).toBe(details[1].final_prompt);
  await expect(autoSection.locator("select, details, pre")).toHaveCount(0);
  await expect(autoSection).not.toContainText("Latest automatic prompt");
});

test("automatic limit edits synchronize without Apply and survive refresh", async ({ page }) => {
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("slow Mira");
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).enabled).toBe(true);
  await page.getByRole("spinbutton", { name: "Images queued limit" }).fill("3");
  await page.getByRole("spinbutton", { name: "Images queued limit" }).blur();
  await page.reload();
  await expect(page.getByRole("spinbutton", { name: "Images queued limit" })).toHaveValue("3");
  await expect(page.getByRole("button", { name: "Apply to auto generation", exact: true })).toHaveCount(0);
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).snapshot.max_generations).toBe(3);
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).uncheck();
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).enabled).toBe(false);
});
