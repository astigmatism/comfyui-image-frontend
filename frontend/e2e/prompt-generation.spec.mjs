import { expect, test } from "@playwright/test";

async function waitForAcceptedImages(page) {
  // Stopping automation preserves accepted work on the shared fake runtime.
  await expect.poll(async () => {
    const { items } = await (await page.request.get("/api/generations")).json();
    return items.every((item) => !["queued", "dispatching", "running", "cancel_requested"].includes(item.status));
  }, { timeout: 30_000 }).toBe(true);
}

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
  test.setTimeout(60_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await expect(page.locator('[data-control-section="prompt-generation"] .control-section-trigger')).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator('[data-control-section="prompt-generation"]')).not.toContainText("Subject entry is independent");
  await expect(page.locator("#prompt-generation-source option")).toHaveCount(2);
  await page.getByRole("switch", { name: "Use Prompt Generation" }).uncheck();
  await page.getByRole("switch", { name: "Use Creative Direction" }).check();
  await expect(page.locator("#prompt-assistant-thinking-mode")).toBeVisible();
  await expect(page.locator("#prompt-assistant .prompt-preprocessor input[type=checkbox]")).toHaveCount(0);
  await expect(page.locator("#prompt-assistant")).not.toContainText("Prompt Generation supplies the starting prompt");
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
  // The fake runtime completes the two images serially. Give each completion
  // its own assertion window after prompt generation/preparation.
  await expect(page.locator(".gallery-card.status-succeeded").first()).toBeAttached();
  await expect(page.locator(".gallery-card.status-succeeded")).toHaveCount(2);
  expect(errors).toEqual([]);
});

async function controlledAutomaticPipeline(page) {
  // Keep stage boundaries deterministic while exercising the real app and SSE handlers.
  // Backend integration tests separately hold the real composition/acceptance boundary.
  await page.addInitScript(() => {
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(...args) { super(...args); window.automaticEvents = this; }
    };
  });
  let auto = { enabled: false, status: "off", revision: 0 };
  const edits = [];
  await page.route("**/api/auto-generation", async (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON();
      auto = { ...auto, enabled: payload.enabled, snapshot: payload.snapshot || auto.snapshot,
        revision: auto.revision + 1, status: payload.enabled ? "preparing" : "off",
        progress: payload.enabled ? { revision: auto.revision + 1, cycle_id: "controlled-cycle",
          cycle_created_at: "2026-09-24T01:00:00Z", active_stages: ["prompt_generation"],
          raw_prompt: null, refined_prompt: null } : null };
    }
    await route.fulfill({ json: auto });
  });
  await page.route("**/api/auto-generation/apply", async (route) => {
    edits.push(route.request().postDataJSON());
    await route.fulfill({ json: auto });
  });
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Subject name", exact: true })).toBeVisible();
  await page.getByRole("switch", { name: "Use Creative Direction" }).check();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("Warm sunlight");
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  await expect(page.locator('[data-pipeline-stage="prompt_generation"]')).toHaveClass(/is-active/);
  return {
    edits,
    async deliver(progress) {
      auto = { ...auto, progress: { ...auto.progress, ...progress } };
      await page.evaluate(() => window.automaticEvents.dispatchEvent(new Event("auto_generation.updated")));
    },
  };
}

test("automatic pipeline displays raw then refined prompts and highlights each stage", async ({ page }, testInfo) => {
  const pipeline = await controlledAutomaticPipeline(page);
  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const flow = page.locator("#prompt-pipeline-flow");
  await expect(flow).toHaveText("Repeat · Prompt generation → Refine → Image");
  await pipeline.deliver({ raw_prompt: "A lighthouse above a quiet sea.", active_stages: ["creative_direction"] });
  await expect(prompt).toHaveValue("A lighthouse above a quiet sea.");
  const refine = page.locator('[data-pipeline-stage="creative_direction"]');
  await expect(refine).toHaveClass(/is-active/);
  await expect(refine).toHaveAttribute("aria-label", "Refine (active)");
  await expect(refine).toHaveCSS("color", "rgb(91, 156, 245)");
  await expect(refine).toHaveCSS("text-decoration-line", "underline");
  await prompt.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("automatic-refining-preview.png"), fullPage: true });
  await pipeline.deliver({ refined_prompt: "A lighthouse above a quiet sea, in warm sunlight.", active_stages: ["image"] });
  await expect(prompt).toHaveValue("A lighthouse above a quiet sea, in warm sunlight.");
  await expect(page.locator('[data-pipeline-stage="image"]')).toHaveClass(/is-active/);
  await expect(refine).not.toHaveClass(/is-active/);
  expect(pipeline.edits).toEqual([]);
  await page.evaluate(() => window.automaticEvents.onerror(new Event("error")));
  await expect(flow.locator(".is-active")).toHaveCount(0);
  await pipeline.deliver({});
  await expect(page.locator('[data-pipeline-stage="image"]')).toHaveClass(/is-active/);
  await prompt.fill("Preserve this draft when stopped");
  await pipeline.deliver({ cycle_id: "next-cycle", cycle_created_at: "2026-09-24T02:00:00Z",
    raw_prompt: "Next generated prompt", refined_prompt: null, active_stages: ["creative_direction"] });
  await expect(page.getByRole("button", { name: "Use latest prompt" })).toBeVisible();
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).uncheck();
  await expect(flow.locator(".is-active")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Use latest prompt" })).toHaveCount(0);
  await expect(prompt).toHaveValue("Preserve this draft when stopped");
});

test("automatic preview survives delayed startup and protects edited drafts across reload", async ({ page }) => {
  const pipeline = await controlledAutomaticPipeline(page);
  await pipeline.deliver({ raw_prompt: "The complete generated lighthouse prompt.", active_stages: ["creative_direction"] });
  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await expect(prompt).toHaveValue("The complete generated lighthouse prompt.");
  let releaseSources;
  const held = new Promise((resolve) => { releaseSources = resolve; });
  await page.route("**/api/workflows/*", async (route) => { await held; await route.continue(); });
  const progressRead = page.waitForResponse("**/api/auto-generation");
  await page.reload();
  await progressRead;
  releaseSources();
  await expect(prompt).toHaveValue("The complete generated lighthouse prompt.");
  await expect(page.locator('[data-pipeline-stage="creative_direction"]')).toHaveClass(/is-active/);
  await prompt.fill("Keep my own draft");
  await pipeline.deliver({ refined_prompt: "The refined lighthouse prompt.", active_stages: ["image"] });
  await expect(page.getByRole("button", { name: "Use latest prompt" })).toBeVisible();
  await expect(prompt).toHaveValue("Keep my own draft");
  await page.reload();
  await expect(prompt).toHaveValue("Keep my own draft");
  await page.getByRole("button", { name: "Use latest prompt" }).click();
  await expect(prompt).toHaveValue("The refined lighthouse prompt.");
  expect(pipeline.edits).toEqual([]);
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).uncheck();
});

test("automatic generated prompts are refined once and shared by the accepted images", async ({ page }) => {
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("Beacon keeper");
  await page.getByRole("switch", { name: "Use Creative Direction" }).check();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("Warm sunlight");
  await page.getByRole("textbox", { name: "Generation quantity" }).fill("2");
  await page.locator('[data-control-section="auto-generation"] .control-section-trigger').click();
  await page.getByRole("spinbutton", { name: "Images queued limit" }).fill("2");
  await page.getByRole("spinbutton", { name: "Images queued limit" }).blur();
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).status).toBe("completed");
  const auto = await (await page.request.get("/api/auto-generation")).json();
  expect(auto.progress.raw_prompt).toContain("Beacon keeper explores");
  expect(auto.progress.refined_prompt).toBe(`${auto.progress.raw_prompt}, Warm sunlight`);
  expect(auto.latest_prompt).toBe(auto.progress.refined_prompt);
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(auto.latest_prompt);
  const { items } = await (await page.request.get("/api/generations")).json();
  expect(items).toHaveLength(2);
  for (const item of items) {
    const detail = await (await page.request.get(`/api/generations/${item.id}`)).json();
    expect(detail.final_prompt).toBe(auto.latest_prompt);
  }
  await waitForAcceptedImages(page);
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

test("auto-generation toggles preserve expansion and respect the image limit", async ({ page }) => {
  await page.getByRole("textbox", { name: "Generation quantity" }).fill("2");
  await page.getByRole("textbox", { name: "Generation quantity" }).blur();
  const autoSection = page.locator('[data-control-section="auto-generation"]');
  await autoSection.locator(".control-section-trigger").click();
  await page.getByRole("spinbutton", { name: "Images queued limit" }).fill("2");
  await autoSection.locator(".control-section-trigger").click();
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  await expect(autoSection.locator(".control-section-trigger")).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#auto-generate-status")).toHaveCount(0);
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).revision).toBeGreaterThan(0);
  await page.reload();
  await expect(page.locator(".gallery-card.status-succeeded")).toHaveCount(2);
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).status).toBe("completed");
  await expect(page.locator("#generate-button")).toHaveText("Generate");
  await expect(page.locator("#generate-button .button-spinner")).toHaveCount(0);
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
  const autoTrigger = page.locator('[data-control-section="auto-generation"] .control-section-trigger');
  await expect(autoTrigger).toHaveAttribute("aria-expanded", "false");
  await autoTrigger.click();
  await page.getByRole("spinbutton", { name: "Images queued limit" }).fill("3");
  await page.getByRole("spinbutton", { name: "Images queued limit" }).blur();
  await page.reload();
  await expect(page.getByRole("spinbutton", { name: "Images queued limit" })).toHaveValue("3");
  await expect(page.getByRole("button", { name: "Apply to auto generation", exact: true })).toHaveCount(0);
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).snapshot.max_generations).toBe(3);
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).uncheck();
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).enabled).toBe(false);
  await expect(autoTrigger).toHaveAttribute("aria-expanded", "true");
});

test("prompt submission keeps its spinner across polling and removes helper rows", async ({ page }) => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/prompt-generations", async (route) => {
    const response = await route.fetch();
    await held;
    await route.fulfill({ response });
  });
  const button = page.locator('[data-action="generate-prompt"]');
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("Patient Mira");
  await button.click();
  await expect(button).toHaveText("Submitting…");
  await expect(button).toHaveAttribute("aria-busy", "true");
  // Cross the 1.5-second prompt polling interval before the POST resolves.
  await page.waitForTimeout(1800);
  await expect(button).toHaveText("Submitting…");
  await expect(button).toBeDisabled();
  await expect(button.locator(".button-spinner")).toBeVisible();
  await expect(page.locator(".submission-recovery, .prompt-pipeline-status")).toHaveCount(0);
  await expect(page.locator(".prompt-generation-inputs > .control-block").last()).toHaveCSS("border-bottom-width", "0px");
  release();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(/Patient Mira explores/);
  await expect(button).toHaveText("Generate prompt");
  await expect(button).toBeEnabled();
  await expect(button.locator(".button-spinner")).toHaveCount(0);
});

for (const automatic of [false, true]) test(`prompt queues behind an image with auto-generation ${automatic ? "on" : "off"}`, async ({ page }) => {
  test.setTimeout(60_000);
  await page.getByRole("switch", { name: "Use Prompt Generation" }).uncheck();
  await page.locator('[data-control-section="prompt-generation"] .control-section-trigger').click();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow image before manual prompt");
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("slow manual Mira");
  const main = page.locator("#generate-button");
  if (automatic) await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  else await main.click();
  await expect(page.locator(".gallery-card.status-running").first()).toBeVisible();
  const button = page.locator('[data-action="generate-prompt"]');
  await button.click();
  await expect(button).toHaveText("Waiting for ComfyUI…");
  await expect(button.locator(".button-spinner")).toBeVisible();
  if (automatic) {
    await expect(main).toHaveText("Auto Generating");
    await expect(main).toBeDisabled();
    await expect(main.locator(".button-spinner")).toBeVisible();
    await page.getByRole("switch", { name: "Auto-generate", exact: true }).uncheck();
  }
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(/slow manual Mira explores/);
  await expect(button).toBeEnabled();
  await expect(button).toHaveText("Generate prompt");
  await expect(main).toHaveText("Generate");
  await waitForAcceptedImages(page);
});

test("automatic batch edits stay silent and preserve Generate feedback across reload", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.getByRole("switch", { name: "Use Prompt Generation" }).uncheck();
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("slow automatic layout check");
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).check();
  const main = page.locator("#generate-button");
  await expect(main).toHaveText("Auto Generating");
  await page.locator('.gallery-card .card-media[data-action="open-photo"]').first().click();
  await expect(page.locator("#photo-generate-button")).toHaveText("Auto Generating");
  await expect(page.locator("#photo-generate-button")).toBeDisabled();
  await expect(page.locator("#photo-generate-button .button-spinner")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(main).toHaveText("Auto Generating");
  await expect(main).toBeDisabled();
  const source = page.locator("#workflow-source");
  await expect(source).toBeEnabled();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/auto-generation/apply", async (route) => {
    await held;
    await route.continue();
  });
  const before = await source.boundingBox();
  const saving = page.waitForRequest("**/api/auto-generation/apply");
  await page.locator("#generation-quantity").fill("2");
  await page.locator("#generation-quantity").blur();
  await expect(page.locator("#automation-status-host")).toBeHidden();
  expect((await source.boundingBox()).y).toBe(before.y);
  await saving;
  await expect(page.locator("#automation-status-host")).toBeEmpty();
  expect((await source.boundingBox()).y).toBe(before.y);
  await expect(main).toHaveText("Auto Generating");
  await expect(main.locator(".button-spinner")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("auto-generating-button.png"), fullPage: true });
  release();
  await expect.poll(async () => (await (await page.request.get("/api/auto-generation")).json()).snapshot.quantity).toBe(2);
  expect((await source.boundingBox()).y).toBe(before.y);
  await page.getByRole("switch", { name: "Auto-generate", exact: true }).uncheck();
  await expect(main).toHaveText("Generate");
  await expect(main).toBeEnabled();
  await expect(main.locator(".button-spinner")).toHaveCount(0);
  await waitForAcceptedImages(page);
});

test("rejected prompt requests show the real error and restore the button", async ({ page }) => {
  await page.route("**/api/prompt-generations", (route) => route.fulfill({ status: 422,
    json: { error: { code: "invalid_subject", message: "Choose a valid subject." } },
  }));
  const button = page.locator('[data-action="generate-prompt"]');
  await button.click();
  await expect(page.locator(".prompt-generation-body [role=alert]")).toHaveText("Choose a valid subject.");
  await expect(button).toHaveText("Generate prompt");
  await expect(button).toBeEnabled();
  await expect(button).toHaveAttribute("aria-busy", "false");
});

test("lost prompt replies recover automatically after reload without losing the result during source loading", async ({ page }) => {
  test.setTimeout(60_000);
  const posts = [];
  let receiptsAvailable = false;
  await page.route("**/api/generation-submissions/*", async (route) => {
    if (receiptsAvailable) return route.continue();
    await route.fulfill({ status: 503, json: { error: { code: "service_busy", message: "Temporary connection failure" } } });
  });
  await page.route("**/api/prompt-generations", async (route) => {
    posts.push({ key: route.request().headers()["idempotency-key"], body: route.request().postData() });
    await route.fetch();
    await route.abort("failed");
  });
  await page.getByRole("textbox", { name: "Subject name", exact: true }).fill("Recovered Mira");
  await page.locator('[data-action="generate-prompt"]').click();
  await expect(page.locator('[data-action="generate-prompt"]')).toHaveText("Reconnecting…");
  expect(posts).toHaveLength(5);
  let releaseSources;
  const sourcesHeld = new Promise((resolve) => { releaseSources = resolve; });
  await page.route("**/api/workflows/*", async (route) => { await sourcesHeld; await route.continue(); });
  const recovered = page.waitForResponse((response) => response.url().includes("/api/generation-submissions/") && response.ok());
  receiptsAvailable = true;
  await page.reload();
  await recovered;
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage)
    .filter((key) => key.startsWith("cif.prompt-jobs."))
    .flatMap((key) => JSON.parse(localStorage.getItem(key))).length)).toBe(1);
  releaseSources();
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(/Recovered Mira explores/);
  await expect(page.locator('[data-action="generate-prompt"]')).toHaveText("Generate prompt");
  await expect(page.locator('[data-action="generate-prompt"]')).toBeEnabled();
  await expect(page.locator("#prompt-generation-source")).toBeEnabled();
  expect(posts).toHaveLength(5);
  expect(new Set(posts.map((post) => post.key)).size).toBe(1);
  expect(new Set(posts.map((post) => post.body)).size).toBe(1);
  await expect(page.locator(".submission-recovery, .prompt-pipeline-status")).toHaveCount(0);
});
