import { expect, test } from "@playwright/test";

test.describe.configure({ timeout: 60_000 });

async function enter(page) {
  const anonymous = await (await page.request.get("/api/auth/session")).json();
  for (const password of ["E2EAdminPermanent123!", "E2EAdminTemporary123!"]) {
    const response = await page.request.post("/api/auth/login", {
      headers: { "X-CSRF-Token": anonymous.csrf_token }, data: { username: "admin", password },
    });
    if (!response.ok()) continue;
    const session = await response.json();
    if (session.user.must_change_password) await page.request.post("/api/auth/password", {
      headers: { "X-CSRF-Token": session.csrf_token }, data: { new_password: "E2EAdminPermanent123!" },
    });
    await page.goto("/");
    await expect(page.locator("#workflow-source")).toBeEnabled();
    await page.locator("#generation-quantity").fill("1");
    await page.locator("#generation-quantity").blur();
    await page.getByRole("textbox", { name: "Prompt", exact: true }).fill("reliability lighthouse");
    await expect(page.locator("#generate-button")).toBeEnabled();
    return;
  }
  throw new Error("Fixture login failed");
}

test("lost submission responses survive retry exhaustion and reload without duplicate acceptance", async ({ page }, testInfo) => {
  await enter(page);
  const accepted = [];
  const requests = [];
  let receiptsAvailable = false;
  await page.route("**/api/generation-submissions/*", async (route) => {
    if (receiptsAvailable) return route.continue();
    await route.fulfill({ status: 503, json: { error: { code: "service_busy", message: "Temporarily unavailable" } } });
  });
  await page.route(/\/api\/generations(?:\/batch)?$/, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    requests.push({ key: route.request().headers()["idempotency-key"], body: route.request().postData() });
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    const result = await response.json();
    accepted.push(result.items ? result.items.map((item) => item.generation?.id) : [result.id]);
    await route.abort("failed");
  });
  await page.locator("#generate-button").click();
  await expect(page.locator("#generate-button")).toHaveText("Reconnecting…");
  await expect(page.locator("#generate-button .button-spinner")).toBeVisible();
  await expect(page.locator(".submission-recovery")).toHaveCount(0);
  expect(requests).toHaveLength(5);
  expect(new Set(requests.map((item) => item.key)).size).toBe(1);
  expect(new Set(requests.map((item) => item.body)).size).toBe(1);
  expect(new Set(accepted.map((ids) => JSON.stringify(ids))).size).toBe(1);
  await expect(page.locator("#generate-button")).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("submission-reconnecting.png"), fullPage: true });
  await page.reload();
  await expect(page.locator("#generate-button")).toHaveText("Reconnecting…");
  const receipt = page.waitForResponse((response) => response.url().includes("/api/generation-submissions/") && response.ok());
  receiptsAvailable = true;
  expect((await receipt).ok()).toBe(true);
  await expect(page.locator(".submission-recovery")).toHaveCount(0);
  await expect(page.locator("#generate-button")).toBeEnabled();
  expect(requests).toHaveLength(5);
});

test("failed gallery thumbnails have a working visible retry action", async ({ page }, testInfo) => {
  await enter(page);
  const generated = page.waitForResponse((response) => /\/api\/generations(?:\/batch)?$/.test(response.url()) && response.request().method() === "POST");
  await page.locator("#generate-button").click();
  const result = await (await generated).json();
  const id = result.id || result.items[0].generation.id;
  const card = page.locator(`.gallery-card[data-generation-id="${id}"]`);
  await expect(card).toHaveClass(/status-succeeded/, { timeout: 40_000 });
  let active = 0;
  let maximum = 0;
  let failThumbnails = true;
  await page.route("**/api/artifacts/*/thumbnail", async (route) => {
    if (!failThumbnails) return route.continue();
    active += 1;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, 50));
    active -= 1;
    await route.fulfill({ status: 503, headers: { "Retry-After": "0" }, json: { error: { code: "service_busy", message: "Busy" } } });
  });
  await page.reload();
  await expect(async () => {
    await card.scrollIntoViewIfNeeded();
    await expect(card).toBeInViewport();
  }).toPass();
  const retry = card.getByRole("button", { name: "Retry unavailable thumbnail" });
  await expect(retry).toBeVisible();
  expect(maximum).toBeLessThanOrEqual(4);
  await page.screenshot({ path: testInfo.outputPath("thumbnail-retry.png") });
  await retry.click();
  failThumbnails = false;
  await expect(retry).toHaveCount(0);
  await expect(card.locator("img[data-thumbnail-src]")).toHaveAttribute("src", /^blob:/);
  await expect.poll(() => card.locator("img[data-thumbnail-src]").evaluate((image) => image.complete && image.naturalWidth > 0)).toBe(true);
});

test("re-rendered gallery cards keep their loaded thumbnail without refetching", async ({ page }) => {
  await enter(page);
  const thumbnailRequests = {};
  await page.route("**/api/artifacts/*/thumbnail", async (route) => {
    const url = route.request().url();
    thumbnailRequests[url] = (thumbnailRequests[url] || 0) + 1;
    await new Promise((resolve) => setTimeout(resolve, 30));
    await route.continue();
  });
  const generated = page.waitForResponse((response) => /\/api\/generations(?:\/batch)?$/.test(response.url()) && response.request().method() === "POST");
  await page.locator("#generate-button").click();
  const result = await (await generated).json();
  const id = result.id || result.items[0].generation.id;
  const card = page.locator(`.gallery-card[data-generation-id="${id}"]`);
  await expect(card).toHaveClass(/status-succeeded/, { timeout: 40_000 });
  // Earlier journeys can leave the gallery at full-card scale; folders precede
  // images, and offscreen thumbnails are intentionally released/not requested.
  const image = card.locator("img[data-thumbnail-src]");
  // Late group metadata can move the card after a single scroll. Keep the
  // target visible until the initial thumbnail has actually decoded.
  await expect.poll(async () => {
    await card.scrollIntoViewIfNeeded();
    return image.evaluate((img) => img.getAttribute("src")?.startsWith("blob:") && img.complete && img.naturalWidth > 0);
  }).toBe(true);
  const thumbnailURL = new URL(await image.getAttribute("data-thumbnail-src"), page.url()).href;
  const count = () => thumbnailRequests[thumbnailURL] || 0;
  const firstCount = count();
  expect(firstCount).toBeGreaterThan(0);
  // Favoriting preserves the loaded image through reconciliation, with no
  // blank frame or new thumbnail request.
  // Click through the card's delegated handler: the favorite button lives in a
  // pointer-events hover overlay that is timing-sensitive for synthetic pointers.
  await card.getByRole("button", { name: "Add to Favorites" }).evaluate((button) => button.click());
  await expect(card.getByRole("button", { name: "Remove from Favorites" })).toHaveCount(1);
  const rerenderedImage = card.locator("img[data-thumbnail-src]");
  await expect(rerenderedImage).toHaveAttribute("src", /^blob:/);
  await expect.poll(() => rerenderedImage.evaluate((img) => img.complete && img.naturalWidth > 0)).toBe(true);
  expect(count()).toBe(firstCount);
});
