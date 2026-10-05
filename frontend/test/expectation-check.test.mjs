import assert from "node:assert/strict";
import test from "node:test";
import {
  ATTEMPT_STATUS_LABELS,
  EXPECTATION_NOTES,
  MISSING_EXPECTATIONS_MESSAGE,
  automationOn,
  checkIsActive,
  createExpectationCheck,
  deleteAttemptsLabel,
  expectationCheckBusy,
  expectationDialogMarkup,
  expectationPanelPresentation,
  expectationPipeline,
  expectationSettingsErrors,
  expectationSettingsPayload,
  expectationSnapshotPayload,
  expectationStatusPresentation,
  expectationsActive,
  expectationsMarkup,
  failedAttemptGenerationIds,
  olderCheck,
  parseExpectations,
  reusableAttempt,
  shouldAutoApply,
} from "../src/expectation-check.mjs";

const EXPECTATIONS = ["The keeper wears a red raincoat", "A lit lighthouse beam is visible"];

function attempt(number, status, overrides = {}) {
  const scored = ["passed", "not_met"].includes(status);
  const scores = overrides.scores || (status === "passed" ? [96, 88] : [40, 90]);
  return {
    number,
    status,
    prompt: status === "composing" ? null : `prompt ${number}`,
    composition_id: `run-${number}`,
    generation: ["composing", "ready"].includes(status)
      ? null
      : { id: `gen-${number}`, status: "succeeded", thumbnail_url: `/api/artifacts/a${number}/thumbnail`, content_url: `/api/artifacts/a${number}/content` },
    score: scored ? Math.min(...scores) : null,
    passed: scored ? status === "passed" : null,
    results: scored
      ? EXPECTATIONS.map((expectation, index) => ({ expectation, score: scores[index], met: scores[index] >= 80, observation: `seen ${index + 1}` }))
      : [],
    summary: scored ? "Reviewer summary" : null,
    error: null,
    ...overrides,
  };
}

function check(status, attempts, overrides = {}) {
  return {
    id: "check-1",
    status,
    purpose: "apply",
    mode: "refine",
    expectations: EXPECTATIONS,
    threshold: 80,
    max_attempts: 5,
    starting_prompt: "a lighthouse keeper",
    collection_id: null,
    planned_count: 1,
    attempts,
    best_attempt: null,
    final_prompt: null,
    final_composition_id: null,
    queued: { generation_ids: [], errors: [] },
    error: null,
    created_at: "2026-10-04T10:00:00Z",
    updated_at: "2026-10-04T10:00:05Z",
    completed_at: null,
    ...overrides,
  };
}

const running = () => check("generating", [attempt(1, "not_met"), attempt(2, "generating")]);
const passed = (overrides = {}) =>
  check("passed", [attempt(1, "not_met"), attempt(2, "not_met"), attempt(3, "passed")], {
    final_prompt: "prompt 3",
    best_attempt: 3,
    completed_at: "2026-10-04T10:02:00Z",
    ...overrides,
  });
const notMet = () =>
  check("not_met", [attempt(1, "not_met", { scores: [10, 90] }), attempt(2, "not_met", { scores: [70, 86] }), attempt(3, "not_met", { scores: [50, 60] })], {
    best_attempt: 2,
    threshold: 90,
    max_attempts: 3,
    completed_at: "2026-10-04T10:02:00Z",
  });

test("expectations parse one per line exactly like the server", () => {
  assert.deepEqual(parseExpectations("- red coat\r\n\n  2. beam  \n* storm\n\u2022 waves\n3) rain"), ["red coat", "beam", "storm", "waves", "rain"]);
  assert.deepEqual(parseExpectations(null), []);
  const valid = { text: "a\nb", threshold: 80, maxAttempts: 5 };
  assert.deepEqual(expectationSettingsErrors(valid), {});
  assert.equal(expectationSettingsErrors({ ...valid, text: " \n" }).expectations, MISSING_EXPECTATIONS_MESSAGE);
  assert.match(expectationSettingsErrors({ ...valid, text: Array.from({ length: 13 }, (_, i) => `e${i}`).join("\n") }).expectations, /at most 12/);
  assert.match(expectationSettingsErrors({ ...valid, text: "x".repeat(301) }).expectations, /300 characters/);
  assert.ok(expectationSettingsErrors({ ...valid, threshold: 0 }).threshold);
  assert.ok(expectationSettingsErrors({ ...valid, maxAttempts: 11 }).maxAttempts);
  assert.deepEqual(expectationSettingsPayload({ enabled: true, text: "a\nb", threshold: 85, maxAttempts: 4 }), { enabled: true, text: "a\nb", threshold: 85, max_attempts: 4 });
  assert.deepEqual(expectationSnapshotPayload({ enabled: false, text: "- a\nb", threshold: 85, maxAttempts: 4 }), { enabled: true, items: ["a", "b"], threshold: 85, max_attempts: 4 });
});

test("a check starts only with vision available and automation off", () => {
  const state = { expectations: { enabled: true }, promptAssistant: { visionAvailable: true } };
  assert.equal(expectationsActive(state), true);
  assert.equal(expectationsActive({ ...state, promptAssistant: { visionAvailable: false } }), false);
  assert.equal(expectationsActive({ ...state, expectations: { enabled: false } }), false);
  assert.equal(expectationsActive({ ...state, autoGenerate: true }), false);
  assert.equal(automationOn({ pendingAutoEnabled: true }), true);
  assert.equal(automationOn({ pendingAutoEnabled: false }), false);
  assert.equal(checkIsActive(running()), true);
  assert.equal(checkIsActive(passed()), false);
  assert.equal(checkIsActive(null), false);
  assert.equal(expectationCheckBusy({ expectationCheckStarting: true }), true);
  assert.equal(shouldAutoApply("a cat", "a cat"), true);
  assert.equal(shouldAutoApply("a cat", "a cat, edited"), false);
  assert.equal(shouldAutoApply(null, "a cat"), false);
});

test("attempt images offered for deletion keep the passing and best attempts", () => {
  assert.deepEqual(failedAttemptGenerationIds(passed()), ["gen-1", "gen-2"]);
  assert.equal(deleteAttemptsLabel(passed()), "Delete failed attempts (2)");
  assert.deepEqual(failedAttemptGenerationIds(notMet()), ["gen-1", "gen-3"]);
  assert.equal(deleteAttemptsLabel(notMet()), "Delete other attempts (2)");
  // The running probe is never offered.
  assert.deepEqual(failedAttemptGenerationIds(running()), ["gen-1"]);
  assert.equal(reusableAttempt(passed()).number, 3);
  assert.equal(reusableAttempt(notMet()).number, 2);
  const stopped = check("stopped", [attempt(1, "not_met"), attempt(2, "stopped")]);
  assert.equal(reusableAttempt(stopped).number, 2);
  assert.deepEqual(failedAttemptGenerationIds(check("passed", [attempt(1, "not_met", { generation: null }), attempt(2, "passed")])), []);
});

test("the sidebar block reports availability, notes, and the Apply label", () => {
  const base = {
    expectations: { enabled: true, text: "red coat\nbeam", threshold: 80, maxAttempts: 5 },
    promptAssistant: { visionAvailable: true },
  };
  const ready = expectationPanelPresentation(base);
  assert.equal(ready.badge, "On · 2");
  assert.equal(ready.composeLabel, "Apply & verify");
  assert.equal(ready.toggleDisabled, false);
  assert.deepEqual(ready.notes, { vision: false, auto: false, "prompt-generation": false });

  const unavailable = expectationPanelPresentation({ ...base, promptAssistant: { visionAvailable: false } });
  assert.equal(unavailable.badge, "Unavailable");
  assert.equal(unavailable.toggleDisabled, true);
  assert.equal(unavailable.fieldsDisabled, true);
  assert.equal(unavailable.notes.vision, true);
  assert.equal(unavailable.composeLabel, "Apply Creative Direction");

  const auto = expectationPanelPresentation({ ...base, autoGenerate: true });
  assert.equal(auto.notes.auto, true);
  assert.equal(auto.composeLabel, "Apply Creative Direction");
  assert.equal(expectationPanelPresentation({ ...base, promptGeneration: { enabled: true } }).notes["prompt-generation"], true);
  const busy = expectationPanelPresentation({ ...base, expectationCheck: running() });
  assert.equal(busy.toggleDisabled, true);
  assert.equal(busy.fieldsDisabled, true);

  const markup = expectationsMarkup({ ...base, expectations: { ...base.expectations, text: "<b>bold</b>" }, promptAssistant: { visionAvailable: false } });
  assert.match(markup, /<details class="prompt-expectations" data-expectations open>/);
  assert.match(markup, /id="expectations-enabled" type="checkbox" {2}disabled/);
  assert.match(markup, /data-expectations-note="vision" >/);
  assert.match(markup, /data-expectations-note="auto" hidden>/);
  assert.match(markup, /The Creative Direction model can&#039;t inspect images right now/);
  assert.match(EXPECTATION_NOTES.auto, /Not applied during Auto-generate/);
  assert.match(markup, /&lt;b&gt;bold&lt;\/b&gt;<\/textarea>/);
  assert.doesNotMatch(markup, /<b>bold<\/b>/);
});

test("the status line summarizes every check state", () => {
  assert.equal(expectationStatusPresentation(null).hidden, true);
  assert.equal(expectationStatusPresentation(running(), { dismissed: true }).hidden, true);
  const active = expectationStatusPresentation(running());
  assert.match(active.inner, /Verifying expectations/);
  assert.match(active.inner, /Attempt 2 of 5 · Generating image/);
  assert.doesNotMatch(active.inner, /dismiss-expectation-status/);
  const done = expectationStatusPresentation(passed(), { currentPrompt: "prompt 3" });
  assert.equal(done.className, "expectation-status is-passed");
  assert.match(done.inner, /Passed · 88\/100/);
  assert.match(done.inner, /Prompt applied/);
  assert.match(done.inner, /dismiss-expectation-status/);
  assert.match(expectationStatusPresentation(passed(), { currentPrompt: "edited" }).inner, /Qualified prompt ready/);
  const generated = expectationStatusPresentation(passed({ purpose: "generate", planned_count: 3, queued: { generation_ids: ["q1", "q2"], errors: [] } }));
  assert.match(generated.inner, /2 more images queued/);
  assert.match(expectationStatusPresentation(notMet()).inner, /Not met · best 70\/100/);
  assert.match(expectationStatusPresentation(check("failed", [attempt(1, "failed")], { error: { code: "vision_unavailable", message: "No vision." } })).inner, /Check failed[\s\S]*No vision\./);
  assert.match(expectationStatusPresentation(check("stopped", [attempt(1, "stopped")])).inner, /Stopped at attempt 1/);
});

test("the Generate pipeline shows the vision stage and the live phase", () => {
  const state = {
    expectations: { enabled: true, text: "a", maxAttempts: 4 },
    promptAssistant: { visionAvailable: true, mode: "refine" },
    autoGenerateCreativeDirection: true,
  };
  assert.deepEqual(expectationPipeline(state).stages.map(([, label]) => label), ["Refine", "Vision check (≤4)", "Image"]);
  assert.equal(expectationPipeline(state).active, null);
  assert.equal(expectationPipeline({ ...state, autoGenerateCreativeDirection: false }), null);
  assert.equal(expectationPipeline({ ...state, promptGeneration: { enabled: true } }), null);
  assert.equal(expectationPipeline({ ...state, autoGenerate: true }), null);
  assert.equal(expectationPipeline({ ...state, expectationCheck: running() }).active, "image");
  const evaluating = check("evaluating", [attempt(1, "evaluating")]);
  assert.equal(expectationPipeline({ ...state, expectationCheck: evaluating }).active, "vision");
  const composing = check("composing", [attempt(1, "composing")]);
  assert.equal(expectationPipeline({ ...state, autoGenerateCreativeDirection: false, expectationCheck: composing }).active, "creative_direction");
});

test("the dialog renders a running check with Stop and the latest scores", () => {
  const html = expectationDialogMarkup(running(), { sourceName: "Moody Krea2" });
  assert.match(html, /Verify expectations <span class="expectation-pill "><span class="activity-spinner"[^>]*><\/span>Generating image/);
  assert.match(html, /Creative Direction · Refine · Apply · pass score 80 · up to 5 attempts/);
  assert.match(html, /Attempt 2 of 5<\/strong> · Generating one image with Moody Krea2/);
  assert.match(html, /Latest scores · attempt 1/);
  assert.match(html, /aria-current="step"/);
  assert.match(html, /data-expectation-action="stop"/);
  assert.match(html, />Hide</);
  assert.doesNotMatch(html, /delete-failed/);
  assert.ok(html.indexOf('data-attempt-number="2"') < html.indexOf('data-attempt-number="1"'));
  assert.match(html, /src="\/api\/artifacts\/a1\/thumbnail"/);
  const pending = expectationDialogMarkup(check("composing", [attempt(1, "composing")]));
  assert.match(pending, /<h3 class="expectation-section-title" id="expectation-scoreboard-title">Expectations<\/h3>/);
  assert.match(pending, /aria-label="Not scored yet"/);
  assert.match(pending, /Composing a prompt from your Creative Direction and expectations/);
  const revising = expectationDialogMarkup(check("composing", [attempt(1, "not_met"), attempt(2, "composing")]));
  assert.match(revising, /Revising the prompt from attempt 1&#039;s feedback/);
});

test("passed, not met, failed, and stopped dialogs offer the right actions", () => {
  const applied = expectationDialogMarkup(passed(), { currentPrompt: "prompt 3" });
  assert.match(applied, /Passed on attempt 3 with 88\/100\.<\/strong> The qualified prompt was applied to the Prompt field\./);
  assert.match(applied, /Delete failed attempts \(2\)/);
  assert.match(applied, /data-expectation-action="use-prompt" data-attempt="3" disabled>[\s\S]*Prompt applied/);
  const edited = expectationDialogMarkup(passed(), { currentPrompt: "my own edit" });
  assert.match(edited, /Your Prompt field changed during the check/);
  assert.match(edited, /data-attempt="3" >Use this prompt</);
  const generated = expectationDialogMarkup(passed({ purpose: "generate", planned_count: 3, queued: { generation_ids: ["q1", "q2"], errors: [] } }));
  assert.match(generated, /Generate · 3 images/);
  assert.match(generated, /first of your 3; <strong>2 more<\/strong> were queued/);

  const best = expectationDialogMarkup(notMet());
  assert.match(best, /Expectations weren't met after 3 attempts\.<\/strong> The best was attempt 2 at 70\/100 \(pass score 90\)/);
  assert.match(best, /Best scores · attempt 2/);
  assert.match(best, /class="expectation-step is-not-met is-best"/);
  assert.match(best, /class="button primary" data-expectation-action="use-prompt" data-attempt="2" >Use best prompt \(70\)/);
  assert.match(best, /Delete other attempts \(2\)/);

  const failed = expectationDialogMarkup(
    check("failed", [attempt(1, "not_met"), attempt(2, "failed", { error: { code: "vision_unavailable", message: "The model rejected the image." } })], {
      error: { code: "vision_unavailable", message: "The model rejected the image." },
    }),
  );
  assert.match(failed, /The check stopped with an error on attempt 2\.<\/strong> The model rejected the image\./);
  assert.match(failed, /<p class="prompt-assistant-error" role="alert">The model rejected the image\.<\/p>/);
  assert.match(failed, /Use latest prompt/);

  const stopped = expectationDialogMarkup(check("stopped", [attempt(1, "stopped", { generation: null })]));
  assert.match(stopped, /You stopped this check during attempt 1/);
  assert.match(stopped, /is-pending is-empty[^>]*><span>Stopped/);
  const cancelled = expectationDialogMarkup(
    check("stopped", [attempt(1, "stopped")], { error: { code: "expectation_check_stopped", message: "The attempt image was cancelled." } }),
  );
  assert.match(cancelled, /The check stopped during attempt 1\.<\/strong> The attempt image was cancelled\./);
});

test("the dialog escapes model text and marks deleted images", () => {
  const hostile = attempt(1, "not_met", { prompt: "<img src=x onerror=alert(1)>", generation: null, summary: "<script>x</script>" });
  hostile.results[0].observation = "<b>coat</b>";
  const html = expectationDialogMarkup(check("not_met", [hostile], { best_attempt: 1, max_attempts: 1 }));
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;b&gt;coat&lt;\/b&gt;/);
  assert.match(html, /<span>Image deleted<\/span>/);
  assert.equal(ATTEMPT_STATUS_LABELS.ready, "Queueing image");
});

test("late reads never move a check backwards", () => {
  const current = running();
  assert.equal(olderCheck(current, { ...current, updated_at: "2026-10-04T10:00:01Z" }), true);
  assert.equal(olderCheck(current, { ...current, updated_at: "2026-10-04T10:00:09Z" }), false);
  assert.equal(olderCheck(current, { ...current, id: "older", created_at: "2026-10-04T09:00:00Z" }), true);
  assert.equal(olderCheck(null, current), false);
});

const settle = async () => {
  for (let index = 0; index < 4; index += 1) await new Promise((resolve) => setImmediate(resolve));
};

function fakeDialog() {
  const listeners = {};
  return {
    open: false,
    innerHTML: "",
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
      this.onclose?.();
    },
    querySelector() {
      return null;
    },
    contains() {
      return false;
    },
    addEventListener(type, listener) {
      listeners[type] = listener;
    },
    removeEventListener(type) {
      delete listeners[type];
    },
    click(action, attempt) {
      listeners.click?.({
        target: {
          closest: (selector) => (selector === "[data-expectation-action]" ? { dataset: { expectationAction: action, ...(attempt ? { attempt: String(attempt) } : {}) } } : null),
        },
        preventDefault() {},
      });
    },
  };
}

function harness(responses, overrides = {}) {
  const calls = [];
  const timers = [];
  const changes = [];
  const dialog = fakeDialog();
  const api = async (path, options = {}) => {
    calls.push({ path, method: options.method || "GET" });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return typeof next === "function" ? next() : next;
  };
  const controller = createExpectationCheck(dialog, {
    api,
    setTimer: (callback, delay) => {
      timers.push({ callback, delay });
      return timers.length;
    },
    clearTimer: () => {},
    onChange: (next, previous) => changes.push([next?.status ?? null, previous?.status ?? null]),
    currentPrompt: () => "prompt 3",
    ...overrides,
  });
  return { calls, timers, changes, dialog, controller };
}

test("the controller follows a check, polls while active, and stops polling when done", async () => {
  const { calls, timers, changes, dialog, controller } = harness([{ check: running() }, { check: passed() }]);
  await controller.refresh();
  assert.deepEqual(calls.map(({ path }) => path), ["/api/prompt-assistant/checks/latest"]);
  assert.equal(controller.current().status, "generating");
  assert.equal(timers.at(-1).delay, 3000);
  assert.equal(controller.open(), true);
  assert.equal(dialog.open, true);
  assert.match(dialog.innerHTML, /Generating image/);
  const polled = timers.length;
  timers.at(-1).callback();
  await settle();
  assert.equal(controller.current().status, "passed");
  assert.deepEqual(changes.at(-1), ["passed", "generating"]);
  assert.match(dialog.innerHTML, /Passed on attempt 3/);
  assert.equal(timers.length, polled, "terminal checks are not polled");
});

test("a null latest read never hides an active check, and track opens a new one", async () => {
  const { controller, dialog } = harness([{ check: null }]);
  controller.track(running(), { open: true });
  assert.equal(dialog.open, true);
  await controller.refresh();
  assert.equal(controller.current().status, "generating");
  assert.equal(harness([]).controller.open(), false);
});

test("stop, prompt reuse, and deleting failed attempts call their dependencies", async () => {
  const applied = [];
  const deleted = [];
  const notices = [];
  const stoppedCheck = check("stopped", [attempt(1, "not_met"), attempt(2, "stopped")], { updated_at: "2026-10-04T10:00:09Z" });
  const { calls, dialog, controller } = harness([stoppedCheck, { check: passed({ updated_at: "2026-10-04T10:05:00Z" }) }], {
    applyPrompt: (prompt, item) => applied.push([prompt, item.number]),
    deleteGenerations: async (ids) => deleted.push(ids),
    notify: (message) => notices.push(message),
  });
  controller.track(running(), { open: true });
  dialog.click("stop");
  await settle();
  assert.deepEqual(calls[0], { path: "/api/prompt-assistant/checks/check-1/stop", method: "POST" });
  assert.equal(controller.current().status, "stopped");
  assert.match(dialog.innerHTML, /You stopped this check during attempt 2/);
  assert.ok(notices.includes("Check stopped."));

  dialog.click("use-prompt", 2);
  assert.deepEqual(applied, [["prompt 2", 2]]);

  dialog.click("delete-failed");
  assert.match(dialog.innerHTML, /Delete 2 attempt images from the gallery\?/);
  dialog.click("cancel-delete");
  assert.doesNotMatch(dialog.innerHTML, /from the gallery\?/);
  dialog.click("delete-failed");
  dialog.click("confirm-delete");
  await settle();
  assert.deepEqual(deleted, [["gen-1", "gen-2"]]);
  assert.ok(notices.includes("2 attempt images were deleted."));
  assert.equal(calls.at(-1).path, "/api/prompt-assistant/checks/latest");

  dialog.click("close");
  assert.equal(dialog.open, false);
});

test("a disposed controller ignores late responses", async () => {
  let release;
  const { controller } = harness([() => new Promise((resolve) => { release = resolve; })]);
  const pending = controller.refresh();
  controller.dispose();
  release({ check: running() });
  await pending;
  assert.equal(controller.current(), null);
});
