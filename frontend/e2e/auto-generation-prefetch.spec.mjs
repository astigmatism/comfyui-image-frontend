import { expect, test } from "@playwright/test";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function signIn(page) {
  const loginSession = await (await page.request.get("/api/auth/session")).json();
  for (const password of ["E2EAdminPermanent123!", "E2EAdminTemporary123!"]) {
    const response = await page.request.post("/api/auth/login", {
      headers: { "X-CSRF-Token": loginSession.csrf_token },
      data: { username: "admin", password },
    });
    if (response.ok()) {
      if (password === "E2EAdminTemporary123!") {
        const session = await (await page.request.get("/api/auth/session")).json();
        const changed = await page.request.post("/api/auth/password", {
          headers: { "X-CSRF-Token": session.csrf_token },
          data: { new_password: "E2EAdminPermanent123!" },
        });
        expect(changed.ok()).toBe(true);
      }
      break;
    }
  }
  expect((await (await page.request.get("/api/auth/session")).json()).authenticated).toBe(true);
  await page.goto("/");
  await expect(page.locator("#workflow-source")).toBeEnabled();
}

async function selectSource(page, name) {
  await page.locator("#workflow-source").click();
  const dialog = page.locator("#source-picker-dialog");
  const select = dialog.locator("[data-source-workflow-choice]");
  const value = await select.locator("option").filter({ hasText: name }).getAttribute("value");
  await select.selectOption(value);
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.locator("#workflow-source")).toHaveAttribute("data-source-key", value);
}

// Keep real application startup, contracts and controls. Only the two slow
// services and their event delivery are controlled, independently, by each test.
async function setup(page, { source = "Generic Landscape", mode = "refine" } = {}) {
  const compositions = [];
  const submissions = [];
  const jobs = [];
  let activityGate = null;
  let activeCompositions = 0;
  let maximumActiveCompositions = 0;
  await page.addInitScript(() => {
    window.EventSource = class extends EventTarget {
      constructor() {
        super();
        window.prefetchEvents = this;
        queueMicrotask(() => this.onopen?.());
      }
      close() { this.closed = true; }
    };
  });
  const json = (route, body, status = 200) => route.fulfill({
    status, contentType: "application/json", body: JSON.stringify(body),
  });
  await page.route("**/api/prompt-assistant/compose", async (route) => {
    const response = deferred();
    compositions.push({ payload: route.request().postDataJSON(), response });
    activeCompositions += 1;
    maximumActiveCompositions = Math.max(maximumActiveCompositions, activeCompositions);
    const result = await response.promise;
    activeCompositions -= 1;
    await json(route, result.body, result.status);
  });
  await page.route(/\/api\/generations(?:\/batch)?(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== "POST") {
      await json(route, { items: jobs, next_cursor: null });
      return;
    }
    const payload = route.request().postDataJSON();
    const response = deferred();
    submissions.push({ items: payload.items || [payload], batch: Boolean(payload.items), response });
    await json(route, await response.promise, 201);
  });
  await page.route(/\/api\/generations\/prefetch-\d+$/, async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    await json(route, jobs.find((job) => job.id === id));
  });
  await page.route("**/api/generation-activity", async (route) => {
    if (activityGate) await activityGate.promise;
    await json(route, {
      remaining_count: jobs.filter((job) => job.status === "queued").length,
      run: null, collection_remaining_counts: {}, collection_generation_counts: {},
    });
  });
  await signIn(page);
  await selectSource(page, source);
  const section = page.getByRole("button", { name: "Creative Direction", exact: true });
  if ((await section.getAttribute("aria-expanded")) !== "true") await section.click();
  const prompt = page.getByRole("textbox", { name: "Prompt", exact: true });
  const direction = page.getByRole("textbox", { name: "Creative Direction", exact: true });
  await prompt.fill("starting landscape");
  await direction.fill("cinematic light");
  await page.locator(`[name=assistant-mode][value=${mode}]`).check();
  await page.locator("#auto-generate-creative-direction").check();
  await expect(page.locator('[data-action="compose-prompt"]')).toBeEnabled();
  await expect(page.locator("#prompt-assistant-instructions")).toBeEnabled();

  async function emit(type, id) {
    await page.evaluate(({ type, id }) => {
      if (!window.prefetchEvents?.closed) window.prefetchEvents.dispatchEvent(
        new MessageEvent(type, { data: JSON.stringify({ generation_id: id }) }),
      );
    }, { type, id });
  }
  return {
    compositions, submissions, jobs, prompt, direction,
    auto: page.locator("#auto-generate"),
    creative: page.locator("#auto-generate-creative-direction"),
    maximumActiveCompositions: () => maximumActiveCompositions,
    holdActivity() { activityGate = deferred(); },
    releaseActivity() { activityGate?.resolve(); activityGate = null; },
    async compose(index, promptText = `prepared prompt ${index + 1}`, error = null) {
      await expect.poll(() => compositions.length).toBe(index + 1);
      compositions[index].response.resolve({
        status: error ? 503 : 200,
        body: error ? { error: { code: error, message: "Controlled composition failure", fields: {} } }
          : { prompt: promptText, composition_id: `composition-${index + 1}`, model: "controlled-model" },
      });
    },
    async accept(index, { failLast = false } = {}) {
      await expect.poll(() => submissions.length).toBe(index + 1);
      const submission = submissions[index];
      const items = submission.items.map((item, itemIndex) => {
        if (failLast && itemIndex === submission.items.length - 1) {
          return { error: { code: "queue_unavailable", message: "Controlled queue failure", fields: {} } };
        }
        const job = {
          id: `prefetch-${jobs.length + 1}`, status: "queued",
          workflow_display_name: source, comfyui_instance_id: item.comfyui_instance_id,
          comfyui_instance_label: "Primary", source_key: item.source_key,
          collection_id: item.collection_id, accepted_at: new Date().toISOString(),
          artifact_count: 0, image_count: 0, final_artifact_count: 0, artifacts: [],
        };
        jobs.push(job);
        return { generation: job };
      });
      submission.response.resolve(submission.batch ? { items } : items[0].generation);
    },
    async finish(job = jobs[0], status = "succeeded") {
      job.status = status;
      await emit("generation.terminal", job.id);
      await expect(page.locator(`.gallery-card[data-generation-id="${job.id}"]`)).toHaveClass(new RegExp(`status-${status}`));
    },
    async notify() { if (jobs[0]) await emit("generation.queued", jobs[0].id); },
  };
}

for (const batch of [false, true]) {
  test(`prepares exactly one prompt after ${batch ? "quantity × checkpoint batch" : "single image"} acceptance without waiting for activity`, async ({ page }) => {
    const app = await setup(page, { source: batch ? "Moody Krea 2 Mix V4" : "Generic Landscape", mode: batch ? "create" : "refine" });
    if (batch) {
      await page.locator("#workflow-source").click();
      const dialog = page.locator("#source-picker-dialog");
      await dialog.getByRole("button", { name: "Select all", exact: true }).click();
      await dialog.getByRole("button", { name: "Apply", exact: true }).click();
      await page.locator("#generation-quantity").fill("2");
      await page.locator("#generation-quantity").blur();
    }
    await app.auto.check();
    await app.compose(0);
    await expect.poll(() => app.submissions.length).toBe(1);
    expect(app.compositions).toHaveLength(1);
    const first = app.submissions[0].items;
    expect(first).toHaveLength(batch ? 10 : 1);
    expect(first.every((item) => item.parameters.prompt === "prepared prompt 1")).toBe(true);
    expect(first.filter((item) => item.prompt_assistant_run_id)).toHaveLength(1);
    app.holdActivity();
    await app.accept(0);
    await expect.poll(() => app.compositions.length).toBe(2);
    expect(app.jobs.every((job) => job.status === "queued")).toBe(true);
    await expect(app.prompt).toHaveClass(/is-direction-composing/);
    await app.compose(1);
    await expect(app.prompt).toHaveValue("prepared prompt 2");
    await expect(app.prompt).toHaveClass(/is-direction-applied/);
    app.releaseActivity();
    await app.notify();
    await expect(page.locator("#generation-activity-host")).toContainText("Next prompt ready.");
    expect(app.compositions).toHaveLength(2);
    expect(app.submissions).toHaveLength(1);
    const currentJobs = [...app.jobs];
    for (const job of currentJobs.slice(0, -1)) {
      await app.finish(job);
      expect(app.submissions).toHaveLength(1);
    }
    await app.finish(currentJobs.at(-1));
    await expect.poll(() => app.submissions.length).toBe(2);
    expect(app.compositions).toHaveLength(2);
    expect(app.submissions[1].items.every((item) => item.parameters.prompt === "prepared prompt 2")).toBe(true);
    expect(app.submissions[1].items[0].prompt_assistant_run_id).toBe("composition-2");
    await app.accept(1);
    await expect.poll(() => app.compositions.length).toBe(3);
    expect(app.maximumActiveCompositions()).toBe(1);
    await app.auto.uncheck();
    await app.compose(2);
  });
}

test("image completion waits for the existing composition, then submits once", async ({ page }) => {
  const app = await setup(page);
  await app.auto.check();
  await app.compose(0);
  await app.accept(0);
  await expect.poll(() => app.compositions.length).toBe(2);
  await app.finish();
  await expect(page.locator("#generation-activity-host")).toContainText("0 generations remaining");
  expect(app.submissions).toHaveLength(1);
  expect(app.compositions).toHaveLength(2);
  await app.compose(1);
  await expect.poll(() => app.submissions.length).toBe(2);
  expect(app.submissions[1].items[0].prompt_assistant_run_id).toBe("composition-2");
  await app.auto.uncheck();
  await app.accept(1);
});

test("enabling automation during existing jobs prepares immediately and image-only edits reuse it", async ({ page }) => {
  const app = await setup(page);
  await page.locator("#generate-button").click();
  await app.accept(0);
  await app.auto.check();
  await app.compose(0);
  await expect(app.prompt).toHaveValue("prepared prompt 1");
  await page.locator("#generation-quantity").fill("3");
  await page.locator("#generation-quantity").blur();
  await app.notify();
  expect(app.compositions).toHaveLength(1);
  expect(app.submissions).toHaveLength(1);
  await app.finish();
  await expect.poll(() => app.submissions.length).toBe(2);
  expect(app.submissions[1].items).toHaveLength(3);
  expect(app.submissions[1].items[0].prompt_assistant_run_id).toBe("composition-1");
  await app.auto.uncheck();
  await app.accept(1);
});

for (const control of ["auto", "creative"]) {
  test(`rapid ${control} off/on ignores an old response with identical inputs`, async ({ page }) => {
    const app = await setup(page);
    await app.auto.check();
    await app.compose(0);
    await app.accept(0);
    await expect.poll(() => app.compositions.length).toBe(2);
    await app[control].uncheck();
    await app[control].check();
    expect(app.compositions).toHaveLength(2);
    await app.compose(1, "obsolete prompt", control === "auto" ? "invalid_prompt" : null);
    await expect.poll(() => app.compositions.length).toBe(3);
    await expect(app.auto).toBeChecked();
    await expect(app.prompt).toHaveValue("prepared prompt 1");
    await app.compose(2, "fresh prompt");
    await expect(app.prompt).toHaveValue("fresh prompt");
    expect(app.maximumActiveCompositions()).toBe(1);
    expect(app.submissions).toHaveLength(1);
    await app.auto.uncheck();
  });
}

test("edits invalidate in-flight and ready prompts while images continue", async ({ page }) => {
  const app = await setup(page);
  await app.auto.check();
  await app.compose(0);
  await app.accept(0);
  await expect.poll(() => app.compositions.length).toBe(2);
  // Returning to the same text must still invalidate an old request.
  await app.direction.fill("temporary direction");
  await app.direction.fill("cinematic light");
  await app.compose(1, "obsolete prompt");
  await expect.poll(() => app.compositions.length).toBe(3);
  await expect(app.prompt).toHaveValue("prepared prompt 1");
  await app.compose(2);
  await expect(app.prompt).toHaveValue("prepared prompt 3");
  await app.prompt.fill("my latest prompt");
  await expect.poll(() => app.compositions.length).toBe(4);
  expect(app.compositions[3].payload.prompt).toBe("my latest prompt");
  // Browser-restored text without an input event also supersedes a request.
  await app.direction.evaluate((input) => { input.value = "restored direction"; });
  await app.compose(3, "another obsolete prompt");
  await expect.poll(() => app.compositions.length).toBe(5);
  expect(app.compositions[4].payload.creative_direction).toBe("restored direction");
  await app.compose(4, "latest preparation");
  await expect(app.prompt).toHaveValue("latest preparation");
  expect(app.submissions).toHaveLength(1);
  expect(app.maximumActiveCompositions()).toBe(1);
  await app.auto.uncheck();
});

test("prefetch retries while images run and exhausted retries pause without cancelling jobs", async ({ page }) => {
  const app = await setup(page);
  await app.auto.check();
  await app.compose(0);
  await app.accept(0);
  for (const index of [1, 2, 3]) {
    await app.compose(index, "", "ollama_generate_timeout");
  }
  await expect(app.auto).not.toBeChecked();
  await expect(page.locator("#auto-generate-status")).toContainText("Auto-generate paused");
  expect(app.jobs[0].status).toBe("queued");
  expect(app.submissions).toHaveLength(1);
  await page.getByRole("button", { name: "Retry Auto-generate" }).click();
  await app.compose(4, "recovered preparation");
  await expect(app.prompt).toHaveValue("recovered preparation");
  expect(app.submissions).toHaveLength(1);
  await app.finish();
  await expect.poll(() => app.submissions.length).toBe(2);
  expect(app.submissions[1].items[0].prompt_assistant_run_id).toBe("composition-5");
  await app.auto.uncheck();
  await app.accept(1);
});

test("partial batch acceptance does not prefetch while accepted jobs remain", async ({ page }) => {
  const app = await setup(page);
  await page.locator("#generation-quantity").fill("2");
  await page.locator("#generation-quantity").blur();
  await app.auto.check();
  await app.compose(0);
  await app.accept(0, { failLast: true });
  await expect(page.locator(".form-error.summary")).toContainText("Queued 1 of 2");
  await app.notify();
  await expect(page.locator("#generation-activity-host")).toContainText("1 generations remaining");
  expect(app.compositions).toHaveLength(1);
  expect(app.submissions).toHaveLength(1);
  await app.auto.uncheck();
});

test("mode, thinking, instructions and source changes invalidate preparation", async ({ page }) => {
  const app = await setup(page);
  await app.auto.check();
  await app.compose(0);
  await app.accept(0);
  await expect.poll(() => app.compositions.length).toBe(2);
  await page.locator('[name=assistant-mode][value=create]').check();
  await app.compose(1, "obsolete refine result");
  await expect.poll(() => app.compositions.length).toBe(3);
  expect(app.compositions[2].payload.mode).toBe("create");
  await app.compose(2);
  await expect(app.prompt).toHaveValue("prepared prompt 3");
  await page.locator("#prompt-assistant .prompt-preprocessor summary").click();
  await page.locator("#prompt-assistant-thinking-mode").uncheck();
  await expect.poll(() => app.compositions.length).toBe(4);
  expect(app.compositions[3].payload.think).toBe(false);
  await app.compose(3);
  await expect(app.prompt).toHaveValue("prepared prompt 4");
  await page.locator("#prompt-assistant-instructions").fill("Use vivid colors.");
  await expect.poll(() => app.compositions.length).toBe(5);
  expect(app.compositions[4].payload.instructions).toBe("Use vivid colors.");
  await selectSource(page, "Krea 2 NSFW V4");
  await selectSource(page, "Generic Landscape");
  await app.compose(4, "obsolete source result");
  await expect.poll(() => app.compositions.length).toBe(6);
  await expect(app.prompt).toHaveValue("prepared prompt 4");
  await app.compose(5, "current source result");
  await expect(app.prompt).toHaveValue("current source result");
  expect(app.submissions).toHaveLength(1);
  expect(app.maximumActiveCompositions()).toBe(1);
  await app.auto.uncheck();
});

test("turning Creative Direction off discards ready provenance and keeps queued images", async ({ page }) => {
  const app = await setup(page);
  await app.auto.check();
  await app.compose(0);
  await app.accept(0);
  await app.compose(1);
  await expect(app.prompt).toHaveValue("prepared prompt 2");
  await app.creative.uncheck();
  await app.prompt.fill("plain prompt after disabling");
  await app.finish();
  await expect.poll(() => app.submissions.length).toBe(2);
  expect(app.submissions[1].items[0].parameters.prompt).toBe("plain prompt after disabling");
  expect(app.submissions[1].items[0].prompt_assistant_run_id).toBeUndefined();
  expect(app.compositions).toHaveLength(2);
  await app.auto.uncheck();
  await app.accept(1);
});

test("signing out discards an in-flight preparation and its late response", async ({ page }) => {
  const app = await setup(page);
  await app.auto.check();
  await app.compose(0);
  await app.accept(0);
  await expect.poll(() => app.compositions.length).toBe(2);
  await page.locator(".account-menu summary").click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await app.compose(1, "late response from the previous session");
  await signIn(page);
  await expect(app.auto).not.toBeChecked();
  await expect(app.prompt).toHaveValue("prepared prompt 1");
  expect(app.submissions).toHaveLength(1);
  await app.creative.check();
  await app.auto.check();
  await app.compose(2, "new session preparation");
  await expect(app.prompt).toHaveValue("new session preparation");
  expect(app.submissions).toHaveLength(1);
  await app.auto.uncheck();
});
