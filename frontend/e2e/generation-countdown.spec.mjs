import { expect, test } from "@playwright/test";

test("card and slideshow countdowns age together across delayed updates and overruns", async ({ page }) => {
  // Mount the real renderer, timer updaters, and styles with deterministic event
  // delivery. Principal journeys separately exercise app wiring and auto-generation.
  await page.route("**/app.mjs", (route) => route.fulfill({
    contentType: "text/javascript", body: "export {};",
  }));
  await page.goto("/");
  await page.clock.install({ time: new Date("2026-09-14T13:00:00.500Z") });
  await page.clock.pauseAt(new Date("2026-09-14T13:00:00.500Z"));
  await page.evaluate(async () => {
    const appUrl = document.querySelector('script[type="module"]').src;
    const { galleryCardMarkup, photoViewerMarkup } = await import(new URL("./render.mjs", appUrl));
    const { refreshGenerationEtaElements, updatePhotoViewerNextIn } = await import(new URL("./generation-countdown.mjs", appUrl));
    const { observeServerDate } = await import(new URL("./server-clock.mjs", appUrl));
    const start = Date.parse("2026-09-14T12:00:00Z");
    observeServerDate(new Date(start).toUTCString(), Date.now(), Date.now());
    const root = document.querySelector("#app");
    const image = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><path fill="#183544" d="M0 0h512v512H0z"/></svg>')}`;
    const completed = {
      id: "completed", status: "succeeded", workflow_display_name: "Countdown test",
      display_artifact: { id: "image", kind: "image", content_url: image, width: 512, height: 512 },
    };
    root.innerHTML = `<div id="test-card" style="width:40vw;margin:24px"></div><dialog id="photo-viewer" class="photo-viewer" style="position:fixed;top:24px;left:55vw;width:calc(45vw - 24px);height:512px"><div class="photo-viewer-host">${photoViewerMarkup(completed, {}, "fit", "slideshow")}</div></dialog>`;
    root.querySelector("#photo-viewer").show();
    let generation;
    const tick = () => {
      refreshGenerationEtaElements(root);
      updatePhotoViewerNextIn(root, [generation], true);
    };
    window.deliverCountdown = (updated, completion) => {
      generation = {
        id: "running", status: "running", workflow_display_name: "Countdown test",
        progress: {
          kind: "node", label: "Sampling", value: 5, maximum: 10,
          updated_at: new Date(start + updated * 1000).toISOString(),
          eta: {
            remaining_seconds: Math.max(0, completion - updated),
            completion_at: new Date(start + completion * 1000).toISOString(),
            updated_at: new Date(start + updated * 1000).toISOString(),
          },
        },
      };
      root.querySelector("#test-card").innerHTML = galleryCardMarkup(generation);
      tick();
    };
    window.deliverCountdown(0, 20);
    setInterval(tick, 1000);
  });
  const card = page.locator("#test-card .generation-progress-eta");
  const badge = page.locator(".photo-viewer-next-in");
  await expect(card).toBeVisible();
  await expect(badge).toBeVisible();
  await expect(card).toHaveText("About 20s left");
  await expect(badge).toHaveText("Next in 20s");
  await page.clock.runFor(10_000);
  await expect(card).toHaveText("About 10s left");
  await expect(badge).toHaveText("Next in 10s");
  await page.evaluate(() => window.deliverCountdown(5, 20));
  await expect(card).toHaveText("About 10s left");
  await expect(badge).toHaveText("Next in 10s");
  await page.clock.runFor(10_000);
  await expect(card).toHaveText("Taking longer than expected");
  await expect(badge).toHaveText("Taking longer than expected");
  await expect(page.locator("#test-card [role=progressbar]")).toHaveAttribute("aria-valuetext", /Taking longer than expected/);
  await page.screenshot({ path: "test-results/generation-countdown-overdue.png" });
  await page.clock.runFor(10_000);
  await page.evaluate(() => window.deliverCountdown(30, 50));
  await expect(card).toBeVisible();
  await expect(badge).toBeVisible();
  await expect(card).toHaveText("About 20s left");
  await expect(badge).toHaveText("Next in 20s");
  await page.screenshot({ path: "test-results/generation-countdown.png" });
});
