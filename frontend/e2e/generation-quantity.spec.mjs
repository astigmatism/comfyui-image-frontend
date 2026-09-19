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
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(selector).toHaveAttribute("data-source-key", value);
  await expect(selector).toBeFocused();
}

test("quantity stepper queues the selected number of generations and persists across reload", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const quantity = page.locator("#generation-quantity");
  const increment = page.getByRole("button", { name: "Increase generation quantity" });
  const decrement = page.getByRole("button", { name: "Decrease generation quantity" });
  const generate = page.locator("#generate-button");

  // Defaults: one generation, decrement at its floor.
  await expect(quantity).toHaveValue("1");
  await expect(decrement).toBeDisabled();
  await expect(increment).toBeEnabled();

  // The arrows change the value without typing.
  await increment.click();
  await increment.click();
  await expect(quantity).toHaveValue("3");
  await expect(decrement).toBeEnabled();
  await decrement.click();
  await expect(quantity).toHaveValue("2");
  await increment.click();
  await expect(quantity).toHaveValue("3");

  // Typed input is digit-only and clamps to the supported range on change.
  await quantity.fill("999");
  await quantity.blur();
  await expect(quantity).toHaveValue("16");
  await expect(increment).toBeDisabled();
  await quantity.fill("3");
  await quantity.blur();
  await expect(quantity).toHaveValue("3");
  await expect(increment).toBeEnabled();

  await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("quantity e2e landscape");

  const batchResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/generations/batch" &&
      response.request().method() === "POST",
  );
  await generate.click();
  await expect(generate).toHaveText("Queueing 3…");
  const response = await batchResponse;
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json();
  expect(body.items).toHaveLength(3);
  expect(body.items.every((item) => item.generation !== null)).toBe(true);
  await expect(page.locator("#toast-region")).toContainText("3 generations queued.");

  const cards = page.locator(".gallery-card");
  await expect(cards).toHaveCount(3);
  for (let index = 0; index < 3; index += 1) {
    await expect(cards.nth(index)).toHaveClass(/status-succeeded/);
  }

  // The quantity is stored per user in browser storage and survives reloads.
  await page.reload();
  await expect(page.locator(".gallery-viewport")).toBeVisible();
  await expect(page.locator("#generation-quantity")).toHaveValue("3");
});

test("quantity multiplies every selected checkpoint in the batch request", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Moody Krea 2 Mix V4");
  await page
    .getByRole("textbox", { name: "Prompt", exact: true })
    .fill("quantity fan-out lighthouse");

  const trigger = page.locator("#workflow-source");
  await trigger.click();
  const dialog = page.locator("#source-picker-dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Select all", exact: true }).click();
  await expect(dialog.locator("[data-source-selection-count]")).toHaveText("5 of 5 selected");
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(trigger).toContainText("5 checkpoints selected");

  // The account retains the first test's quantity across independent browsers.
  await page.locator("#generation-quantity").fill("1");
  await page.locator("#generation-quantity").blur();
  await page.getByRole("button", { name: "Increase generation quantity" }).click();
  await expect(page.locator("#generation-quantity")).toHaveValue("2");

  const batchRequests = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/generations/batch" && request.method() === "POST") {
      batchRequests.push(...request.postDataJSON().items);
    }
  });
  await page.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(page.locator("#generate-button")).toHaveText("Queueing 10…");
  await expect.poll(() => batchRequests.length).toBe(10);

  const checkpoints = batchRequests.map((request) => request.parameters.checkpoint);
  const expectedCheckpoints = [
    "cutie_x_int8",
    "tyjr_mxfp8",
    "v4_bf16",
    "v4_int8",
    "v5_bf16",
  ];
  expect([...checkpoints].sort()).toEqual(
    [...expectedCheckpoints, ...expectedCheckpoints].sort(),
  );
  // Each checkpoint contributes its full quantity, grouped after itself.
  expect(new Set(checkpoints).size).toBe(5);
  expect(checkpoints.filter((value) => value === checkpoints[0])).toHaveLength(2);
  // Random seeds must stay per item: the server-resolved shared seed is only
  // injected for single planned generations.
  const seedValues = batchRequests.map((request) => request.parameters.seed);
  expect(seedValues.every((value) => value === undefined || value === "random")).toBe(true);
  await expect(page.locator("#toast-region")).toContainText("10 generations queued.");
});
