// TLS-edge regression tests.
//
// These run against the REAL TLS edge (see playwright.tls.config.mjs) and
// verify the reason the edge exists: because the appliance is only reachable
// over https://, the browser treats it as a secure context and exposes the
// secure-context APIs the product depends on (clipboard paste + microphone).
//
// Over plain http:// on a LAN address those APIs are undefined, which is
// exactly the failure this project guards against.
import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

// --- minimal app helpers (mirrored from principal-journeys.spec.mjs) --------

async function signIn(page, username, password) {
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
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

async function setForcedPassword(page, password) {
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password", { exact: true }).fill(password);
  await page.getByLabel("Confirm new password").fill(password);
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.locator(".gallery-viewport")).toBeVisible();
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

// --- tests ------------------------------------------------------------------

test("TLS edge exposes a secure context with clipboard and microphone APIs", async ({
  page,
}) => {
  await page.goto("/");

  // The secure-context flag is set by the scheme, so it is available as soon as
  // the (login) page loads over https://.
  await expect
    .poll(() => page.evaluate(() => window.isSecureContext), {
      message: "window.isSecureContext must be true over the TLS edge",
    })
    .toBe(true);

  const apis = await page.evaluate(() => ({
    clipboardReadText:
      typeof navigator.clipboard !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.readText,
    mediaDevicesGetUserMedia:
      typeof navigator.mediaDevices !== "undefined" &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia,
  }));
  expect(apis.clipboardReadText, "navigator.clipboard.readText must be a function").toBe(
    "function",
  );
  expect(
    apis.mediaDevicesGetUserMedia,
    "navigator.mediaDevices.getUserMedia must be a function",
  ).toBe("function");
});

test("clipboard paste replaces the prompt text over the TLS edge", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  await prompt.fill("existing draft");
  await page.evaluate(() => navigator.clipboard.writeText("clipboard one"));
  await page.getByRole("button", { name: "Replace prompt with clipboard contents" }).click();
  await expect(prompt).toHaveValue("clipboard one");

  await page.getByRole("button", { name: "Open focused prompt editor" }).click();
  const dialog = page.locator("#prompt-editor-dialog");
  await expect(dialog).toHaveAttribute("open", "");
  await page.evaluate(() => navigator.clipboard.writeText("clipboard two"));
  await dialog.getByRole("button", { name: "Paste", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "Prompt editor" })).toHaveValue(
    "clipboard two",
  );
});

test("voice input reaches the recording flow over the TLS edge", async ({ page }) => {
  // The browser has no real microphone in CI, so getUserMedia / MediaRecorder
  // are stubbed to return a deterministic stream. This still exercises the real
  // recording flow end-to-end: the mic button must be enabled (a secure context
  // exposes navigator.mediaDevices), and clicking it must drive the recorder to
  // a transcript.
  await page.addInitScript(() => {
    const stream = { getTracks: () => [{ stop() {} }] };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => stream },
    });
    class FakeMediaRecorder {
      static isTypeSupported() {
        return true;
      }

      constructor(_stream, options = {}) {
        this.mimeType = options.mimeType || "audio/webm";
        this.state = "inactive";
        this.ondataavailable = null;
        this.onerror = null;
        this.onstop = null;
      }

      start() {
        this.state = "recording";
      }

      stop() {
        this.state = "inactive";
        queueMicrotask(() => {
          this.ondataavailable?.({
            data: new Blob(["deterministic microphone audio"], { type: this.mimeType }),
          });
          this.onstop?.();
        });
      }
    }
    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: FakeMediaRecorder,
    });
  });

  await page.goto("/");
  await signInAdminWithCurrentFixturePassword(page);
  await selectPublishedSource(page, "Generic Landscape");

  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const promptMic = page.locator('[data-speech-target="control-prompt"]');

  // A secure context is what makes the microphone button usable at all.
  await expect(promptMic).toBeEnabled();

  await prompt.fill("blue sky");
  await prompt.evaluate((element) => element.setSelectionRange(4, 4));
  await promptMic.click();
  await expect(promptMic).toHaveAttribute("aria-label", "Stop recording for Prompt");
  await expect(promptMic).toHaveClass(/is-recording/);
  await promptMic.click();
  await expect(prompt).toHaveValue("blue transcribed speech sky");
});
