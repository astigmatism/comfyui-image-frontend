import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

async function signIn(page, username, password) {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function setForcedPassword(page, password) {
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password", { exact: true }).fill(password);
  await page.getByLabel("Confirm new password").fill(password);
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.locator(".gallery-viewport")).toBeVisible();
}

async function signInAdminWithCurrentFixturePassword(page) {
  let responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/auth/login" &&
      response.request().method() === "POST",
  );
  await signIn(page, "admin", "E2EAdminPermanent123!");
  if ((await responsePromise).ok()) return;

  responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/auth/login" &&
      response.request().method() === "POST",
  );
  await signIn(page, "admin", "E2EAdminTemporary123!");
  expect((await responsePromise).ok()).toBe(true);
  await setForcedPassword(page, "E2EAdminPermanent123!");
}

async function selectPublishedSource(page, name) {
  const selector = page.locator("#workflow-source");
  await selector.click();
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog).toBeVisible();
  const workflow = dialog.locator("[data-source-workflow-choice]");
  const option = workflow.locator("option").filter({ hasText: name });
  await expect(option).toHaveCount(1);
  const value = await option.getAttribute("value");
  expect(value).toBeTruthy();
  await workflow.selectOption(value);
  // These journeys exercise one image request; other specs persist model fan-out.
  const checkpoints = dialog.locator('[data-checkpoint-card] input[type="checkbox"]');
  if (await checkpoints.count()) {
    await dialog.getByRole("button", { name: "Clear all", exact: true }).click();
    await checkpoints.first().check();
  }
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(selector).toHaveAttribute("data-source-key", value);
  await expect(selector).toBeFocused();
}

test("control bar values, active source, and section states persist across reload", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");

  const sectionTrigger = (key) =>
    page.locator(`[data-control-section="${key}"] .control-section-trigger`);

  // Establish the starting state explicitly: this account's settings are shared
  // with earlier browser journeys, even though this is a fresh browser context.
  for (const [key, expanded] of [["prompt", true], ["seed", true], ["resolution", true],
    ["creative-direction", false], ["group-loras", false]]) {
    if ((await sectionTrigger(key).getAttribute("aria-expanded")) !== String(expanded)) {
      await sectionTrigger(key).click();
    }
  }
  await expect(sectionTrigger("prompt")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("seed")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("resolution")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("creative-direction")).toHaveAttribute("aria-expanded", "false");
  await expect(sectionTrigger("group-loras")).toHaveAttribute("aria-expanded", "false");

  // Change the control values.
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("persistence lighthouse");
  await page.getByLabel("Random seed", { exact: true }).uncheck();
  await page.getByLabel("Seed value", { exact: true }).fill("987654321");
  await page.getByRole("spinbutton", { name: "Width", exact: true }).fill("1280");
  await page.getByRole("spinbutton", { name: "Height", exact: true }).fill("960");

  // LoRA strength lives in the collapsed LoRAs section.
  await sectionTrigger("group-loras").click();
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("1.25");

  // Creative Direction lives in its own collapsed section.
  await sectionTrigger("creative-direction").click();
  await page.getByRole("textbox", { name: "Creative Direction", exact: true }).fill("dusk harbor");

  // Flip the seed section against its default while Creative Direction stays open.
  await sectionTrigger("seed").click();
  await expect(sectionTrigger("seed")).toHaveAttribute("aria-expanded", "false");
  await expect(sectionTrigger("creative-direction")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("group-loras")).toHaveAttribute("aria-expanded", "true");

  await expect(page.locator(".shared-settings-status")).toContainText("Settings saved across devices");
  await page.reload();

  // The active source, its values, and the section states all come back.
  await expect(page.locator("#workflow-source")).toContainText("Moody Krea 2 Mix V4");
  await expect(page.getByRole("textbox", { name: "Prompt", exact: true })).toHaveValue(
    "persistence lighthouse",
  );
  await expect(page.getByLabel("Random seed", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Seed value", { exact: true })).toHaveValue("987654321");
  await expect(page.getByRole("spinbutton", { name: "Width", exact: true })).toHaveValue("1280");
  await expect(page.getByRole("spinbutton", { name: "Height", exact: true })).toHaveValue("960");
  await expect(page.getByRole("textbox", { name: "Creative Direction", exact: true })).toHaveValue(
    "dusk harbor",
  );

  // Section preferences persist: the seed section stays collapsed, and the
  // sections we opened (Creative Direction, LoRAs) stay expanded. LoRAs also
  // keeps its strength value.
  await expect(sectionTrigger("seed")).toHaveAttribute("aria-expanded", "false");
  await expect(sectionTrigger("creative-direction")).toHaveAttribute("aria-expanded", "true");
  await expect(sectionTrigger("group-loras")).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue(
    "1.25",
  );
});

test("republished LoRA membership is reconciled on load, submission, and persistence", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await expect(page.locator(".shared-settings-status")).toContainText("Settings saved across devices");
  const sourceKey = await page.locator("#workflow-source").getAttribute("data-source-key");
  const source = await (await page.request.get(`/api/workflows/${sourceKey}`)).json();
  const stack = source.interface.inputs.find((input) => input.type === "lora_stack");
  const oldIds = ["a", "b", "c", "d", "e"];
  const newIds = Array.from({ length: 11 }, (_, index) => `new_${index}`);
  let publishedIds = [...oldIds, ...newIds];
  const savedStack = [...oldIds].reverse().map((id, index) => ({ id, strength: index * 0.25 }));
  const publishedInterface = () => ({
    ...source.interface,
    inputs: source.interface.inputs.map((input) => input.id !== stack.id ? input : {
      ...input,
      items: publishedIds.map((id) => ({ id, label: `LoRA ${id}` })),
      default: publishedIds.map((id) => ({ id, strength: 0 })),
    }),
  });
  await page.route(`**/api/workflows/${sourceKey}`, async (route) => {
    await route.fulfill({ json: { ...source, interface: publishedInterface() } });
  });
  let seedStaleSettings = true;
  await page.route("**/api/preferences", async (route) => {
    if (route.request().method() !== "GET" || !seedStaleSettings) return route.continue();
    seedStaleSettings = false;
    const response = await route.fetch();
    const preferences = await response.json();
    // Reproduce the incident's stale five-item value already saved with a new snapshot.
    preferences.settings.sources[sourceKey].interface = publishedInterface();
    preferences.settings.sources[sourceKey].values[stack.id] = savedStack;
    await route.fulfill({ response, json: preferences });
  });
  const submissions = [];
  await page.route("**/api/generations", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    submissions.push(route.request().postDataJSON());
    // Capture the payload with a definitive rejection, avoiding submission recovery.
    // The fake backend retains its original two-item publication.
    await route.fulfill({ status: 422, json: {
      error: { code: "captured_submission", message: "Submission captured for this test.", fields: {} },
    } });
  });

  for (const removedId of [null, "b"]) {
    if (removedId) publishedIds = publishedIds.filter((id) => id !== removedId);
    const expected = [
      ...savedStack.filter(({ id }) => id !== removedId),
      ...newIds.map((id) => ({ id, strength: 0 })),
    ];
    await page.reload();
    const loraSection = page.getByRole("button", { name: "LoRAs", exact: true });
    if (await loraSection.getAttribute("aria-expanded") !== "true") await loraSection.click();
    await expect(page.locator(".lora-row")).toHaveCount(expected.length);
    expect(await page.locator(".lora-row").evaluateAll((rows) => rows.map((row) => ({
      id: row.dataset.loraId,
      strength: Number(row.querySelector('input[type="number"]').value),
    })))).toEqual(expected);
    await expect(page.getByText("Include every LoRA exactly once.", { exact: true })).toHaveCount(0);
    const generate = page.getByRole("button", { name: "Generate", exact: true });
    await expect(generate).toBeEnabled();
    await page.getByRole("textbox", { name: "Prompt", exact: true }).fill(`reconciled ${expected.length} LoRAs`);
    await expect.poll(async () => {
      const preferences = await (await page.request.get("/api/preferences")).json();
      return preferences.settings.sources[sourceKey].values[stack.id];
    }).toEqual(expected);
    const previousSubmissions = submissions.length;
    await generate.click();
    await expect.poll(() => submissions.length).toBe(previousSubmissions + 1);
    expect(submissions.at(-1).parameters[stack.id]).toEqual(expected);
    await expect(page.getByText("Submission captured for this test.", { exact: true })).toBeVisible();
  }
});

test("recall reconciles against the recalled source's LoRA catalog", async ({ page }) => {
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  const sourceKey = await page.locator("#workflow-source").getAttribute("data-source-key");
  const source = await (await page.request.get(`/api/workflows/${sourceKey}`)).json();
  const stack = source.interface.inputs.find((input) => input.type === "lora_stack");
  const loraSection = page.getByRole("button", { name: "LoRAs", exact: true });
  if (await loraSection.getAttribute("aria-expanded") !== "true") await loraSection.click();
  await page.getByRole("spinbutton", { name: "Beta strength", exact: true }).fill("1.25");
  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("recall the original LoRA catalog");
  const accepted = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/generations" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  const response = await accepted;
  expect(response.status()).toBe(201);
  const submittedStack = response.request().postDataJSON().parameters[stack.id];
  const generation = await response.json();
  const card = page.locator(`.gallery-card[data-generation-id="${generation.id}"]`);
  await expect(card).toHaveClass(/status-succeeded/);

  await page.route("**/api/workflows/*", async (route) => {
    const response = await route.fetch();
    const other = await response.json();
    if (other.source_key !== sourceKey) other.interface.inputs.push({
      ...stack,
      items: [{ id: "a", label: "Alpha" }, { id: "c", label: "Gamma" }],
      default: [{ id: "a", strength: 0 }, { id: "c", strength: 0 }],
    });
    await route.fulfill({ response, json: other });
  });
  await selectPublishedSource(page, "Generic Landscape");
  await expect(page.locator('.lora-row[data-lora-id="c"]')).toHaveCount(1);
  await card.scrollIntoViewIfNeeded();
  const recall = card.getByRole("button", { name: "Recall settings", exact: true });
  await recall.focus();
  await recall.press("Enter");
  await expect(page.locator("#workflow-source")).toHaveAttribute("data-source-key", sourceKey);
  await expect(page.getByRole("spinbutton", { name: "Beta strength", exact: true })).toHaveValue("1.25");
  expect(await page.locator(".lora-row").evaluateAll((rows) => rows.map((row) => ({
    id: row.dataset.loraId,
    strength: Number(row.querySelector('input[type="number"]').value),
  })))).toEqual(submittedStack);
});
