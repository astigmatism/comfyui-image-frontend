// Creative Direction expectations: a server-owned check composes a prompt,
// generates one probe image, scores every expectation with vision, and revises
// the prompt until every expectation reaches the pass score or attempts run
// out. This module holds the pure rules, the sidebar and dialog markup, and the
// dialog controller; the browser only starts, watches, stops, and reuses checks.
import { EXPECTATION_LIMITS, escapeHtml, normalizeExpectationSettings } from "./lib.mjs";

export const ACTIVE_CHECK_STATUSES = new Set(["composing", "generating", "evaluating"]);
export const TERMINAL_CHECK_STATUSES = new Set(["passed", "not_met", "failed", "stopped"]);
const ACTIVE_ATTEMPT_STATUSES = new Set(["composing", "ready", "generating", "evaluating"]);
export const EXPECTATION_POLL_MS = 3000;
export const EXPECTATIONS_SECTION_KEY = "creative-direction-expectations";
// Panel inputs that only affect manual checks, never automation.
export const EXPECTATION_INPUT_IDS = Object.freeze([
  "expectations-enabled",
  "creative-direction-expectations",
  "expectations-threshold",
  "expectations-attempts",
]);

export const EXPECTATION_NOTES = Object.freeze({
  vision: "The Creative Direction model can't inspect images right now, so expectations can't be verified. Apply Creative Direction still works without the check.",
  auto: "Not applied during Auto-generate. Each cycle runs Creative Direction without the vision check.",
  promptGeneration: "Not applied to Generate while Prompt Generation is on. Apply & verify still works.",
});
export const MISSING_EXPECTATIONS_MESSAGE = "Add at least one expectation or turn off Verify with vision.";

export const ICONS = Object.freeze({
  check: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m3.5 8.5 3 3 6-7" /></svg>',
  cross: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4.5 4.5 7 7m0-7-7 7" /></svg>',
  alert: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 2.5 14 13H2L8 2.5Z" /><path d="M8 6.5v3M8 11.4v.1" /></svg>',
  notMet: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="6" /><path d="M8 4.8v3.8M8 11v.1" /></svg>',
  stop: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="4" y="4" width="8" height="8" rx="1.5" /></svg>',
  dot: '<svg class="expectation-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="1.5" /></svg>',
});

const SPINNER = '<span class="activity-spinner" aria-hidden="true"></span>';
export const CHECK_STATUS_LABELS = Object.freeze({
  composing: "Verifying",
  generating: "Verifying",
  evaluating: "Verifying",
  passed: "Passed",
  not_met: "Not met",
  failed: "Failed",
  stopped: "Stopped",
});
export const ATTEMPT_STATUS_LABELS = Object.freeze({
  composing: "Composing",
  ready: "Queueing image",
  generating: "Generating image",
  evaluating: "Checking with vision",
  passed: "Passed",
  not_met: "Not met",
  failed: "Error",
  stopped: "Stopped",
});
const STATUS_CLASS = { passed: "is-passed", not_met: "is-not-met", failed: "is-failed", stopped: "is-stopped" };

const plural = (count, singular, pluralForm = `${singular}s`) => `${count} ${count === 1 ? singular : pluralForm}`;
const attributeValue = (value) => String(value).replace(/["\\]/gu, "\\$&");

// ---------- Pure rules ----------

// One expectation per nonblank line; list bullets and numbering are dropped
// exactly as the server does (domain/expectations.py).
export function parseExpectations(text) {
  return String(text ?? "")
    .split(/\r\n|\r|\n/u)
    .map((line) => line.replace(/^\s*(?:[-*\u2022]|\d+[.)])\s*/u, "").trim())
    .filter(Boolean);
}

export function expectationSettingsErrors(settings) {
  const errors = {};
  const text = String(settings?.text ?? "");
  const items = parseExpectations(text);
  if (!items.length) errors.expectations = MISSING_EXPECTATIONS_MESSAGE;
  else if (items.length > EXPECTATION_LIMITS.maxItems) errors.expectations = `Use at most ${EXPECTATION_LIMITS.maxItems} expectations.`;
  else if (items.some((item) => item.length > EXPECTATION_LIMITS.maxLength)) errors.expectations = `Keep each expectation to ${EXPECTATION_LIMITS.maxLength} characters or fewer.`;
  else if (text.length > EXPECTATION_LIMITS.maxTextLength) errors.expectations = `Keep the expectations under ${EXPECTATION_LIMITS.maxTextLength.toLocaleString("en-US")} characters.`;
  const threshold = settings?.threshold;
  if (!Number.isInteger(threshold) || threshold < 1 || threshold > EXPECTATION_LIMITS.maxThreshold) errors.threshold = "Use a pass score from 1 to 100.";
  const attempts = settings?.maxAttempts;
  if (!Number.isInteger(attempts) || attempts < 1 || attempts > EXPECTATION_LIMITS.maxAttempts) errors.maxAttempts = `Use 1 to ${EXPECTATION_LIMITS.maxAttempts} attempts.`;
  return errors;
}

// Preference shape (settings.creative_direction_expectations).
export function expectationSettingsPayload(settings) {
  const normalized = normalizeExpectationSettings(settings);
  return { enabled: normalized.enabled, text: normalized.text, threshold: normalized.threshold, max_attempts: normalized.maxAttempts };
}

// Snapshot recorded on every generation a check queues, so recall restores it.
export function expectationSnapshotPayload(settings) {
  const normalized = normalizeExpectationSettings(settings);
  return { enabled: true, items: parseExpectations(normalized.text), threshold: normalized.threshold, max_attempts: normalized.maxAttempts };
}

export function automationOn(state) {
  return Boolean(state?.autoGenerate || state?.automation?.enabled || state?.pendingAutoEnabled === true);
}

// Apply & verify (and Generate with Use Creative Direction) start a check.
export function expectationsActive(state) {
  return Boolean(state?.expectations?.enabled && state?.promptAssistant?.visionAvailable === true && !automationOn(state));
}

export function checkIsActive(check) {
  return Boolean(check && ACTIVE_CHECK_STATUSES.has(check.status));
}

export function expectationCheckBusy(state) {
  return checkIsActive(state?.expectationCheck) || Boolean(state?.expectationCheckStarting);
}

// A passing prompt replaces the Prompt field only if the user left it untouched.
export function shouldAutoApply(startPrompt, currentPrompt) {
  return typeof startPrompt === "string" && String(currentPrompt ?? "") === startPrompt;
}

export function currentAttempt(check) {
  return check?.attempts?.at(-1) || null;
}

export function passedAttempt(check) {
  return check?.attempts?.find((item) => item.status === "passed") || null;
}

// The best attempt is only singled out when the check did not pass.
function highlightedBest(check) {
  return check?.status === "not_met" ? check.best_attempt ?? null : null;
}

export function scoredAttempt(check) {
  if (!check) return null;
  const best = highlightedBest(check);
  const highlighted = best ? check.attempts?.find((item) => item.number === best) : null;
  if (highlighted?.results?.length) return highlighted;
  return [...(check.attempts || [])].reverse().find((item) => item.results?.length) || null;
}

// Images of attempts that did not pass. A check that was not met keeps its best
// attempt; a running attempt is never offered for deletion.
export function failedAttemptGenerationIds(check) {
  if (!check) return [];
  const best = highlightedBest(check);
  return (check.attempts || [])
    .filter((item) => item.generation?.id && item.status !== "passed" && item.number !== best && !ACTIVE_ATTEMPT_STATUSES.has(item.status))
    .map((item) => item.generation.id);
}

export function deleteAttemptsLabel(check, count = failedAttemptGenerationIds(check).length) {
  return check?.status === "not_met" ? `Delete other attempts (${count})` : `Delete failed attempts (${count})`;
}

// Whose prompt the footer offers: the passing one, the best one, or the latest one.
export function reusableAttempt(check) {
  if (!check) return null;
  if (check.status === "passed") return passedAttempt(check);
  if (check.status === "not_met") {
    const best = check.attempts?.find((item) => item.number === check.best_attempt);
    if (best?.prompt) return best;
  }
  return [...(check.attempts || [])].reverse().find((item) => item.prompt) || null;
}

// ---------- Sidebar ----------

export function expectationPanelPresentation(state) {
  const settings = normalizeExpectationSettings(state?.expectations);
  const vision = state?.promptAssistant?.visionAvailable;
  const unavailable = vision === false;
  const running = expectationCheckBusy(state);
  const on = settings.enabled && !unavailable;
  const auto = automationOn(state);
  const count = parseExpectations(settings.text).length;
  const sectionOpen = state?.controlSectionOpen?.[EXPECTATIONS_SECTION_KEY];
  return {
    settings,
    on,
    unavailable,
    running,
    count,
    open: typeof sectionOpen === "boolean" ? sectionOpen : settings.enabled,
    toggleDisabled: vision !== true || running,
    fieldsDisabled: !on || running,
    badge: on ? `On · ${count}` : unavailable ? "Unavailable" : "Off",
    countText: `${count} of ${EXPECTATION_LIMITS.maxItems} expectations`,
    notes: {
      vision: unavailable,
      auto: on && auto,
      "prompt-generation": on && !auto && Boolean(state?.promptGeneration?.enabled),
    },
    composeLabel: expectationsActive(state) ? "Apply & verify" : "Apply Creative Direction",
  };
}

const NOTE_COPY = { vision: EXPECTATION_NOTES.vision, auto: EXPECTATION_NOTES.auto, "prompt-generation": EXPECTATION_NOTES.promptGeneration };

export function expectationsMarkup(state) {
  const view = expectationPanelPresentation(state);
  const fields = view.fieldsDisabled ? "disabled" : "";
  return `<details class="prompt-expectations" data-expectations ${view.open ? "open" : ""}>
    <summary><span>Expectations</span><span class="prompt-expectations-badge ${view.on ? "is-on" : ""}" data-expectations-badge>${escapeHtml(view.badge)}</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6" /></svg></summary>
    <div class="prompt-expectations-content">
      <label class="prompt-expectations-toggle ${view.unavailable ? "is-disabled" : ""}"><input id="expectations-enabled" type="checkbox" ${view.on ? "checked" : ""} ${view.toggleDisabled ? "disabled" : ""} /> Verify with vision</label>
      ${Object.entries(NOTE_COPY).map(([key, copy]) => `<p class="prompt-expectations-note" role="note" data-expectations-note="${key}" ${view.notes[key] ? "" : "hidden"}>${escapeHtml(copy)}</p>`).join("")}
      <textarea id="creative-direction-expectations" rows="5" maxlength="${EXPECTATION_LIMITS.maxTextLength}" aria-label="Expectations, one per line" placeholder="One expectation per line, for example:&#10;Exactly two people are visible&#10;The subject wears a red coat" ${fields}>${escapeHtml(view.settings.text)}</textarea>
      <p class="prompt-expectations-meta"><span data-expectations-count>${escapeHtml(view.countText)}</span><span>One per line</span></p>
      <div class="prompt-expectations-limits">
        <label class="field compact"><span>Pass score</span><input id="expectations-threshold" type="number" inputmode="numeric" min="1" max="${EXPECTATION_LIMITS.maxThreshold}" step="1" value="${view.settings.threshold}" ${fields} /></label>
        <label class="field compact"><span>Max attempts</span><input id="expectations-attempts" type="number" inputmode="numeric" min="1" max="${EXPECTATION_LIMITS.maxAttempts}" step="1" value="${view.settings.maxAttempts}" ${fields} /></label>
      </div>
      <p class="prompt-expectations-hint">Each attempt makes one image with the first selected model, scores every expectation from 0 to 100 by looking at it, and revises the prompt until every expectation reaches the pass score.</p>
    </div>
  </details>`;
}

export function expectationStatusPresentation(check, { dismissed = false, currentPrompt = null } = {}) {
  if (!check?.id || dismissed) return { hidden: true, className: "expectation-status", inner: "" };
  const current = currentAttempt(check);
  const best = scoredAttempt(check);
  const final = passedAttempt(check);
  let icon = SPINNER;
  let title = "Verifying expectations";
  let detail = current ? `Attempt ${current.number} of ${check.max_attempts} · ${ATTEMPT_STATUS_LABELS[current.status] || current.status}` : "Starting";
  if (check.status === "passed") {
    const queued = check.queued?.generation_ids?.length || 0;
    icon = ICONS.check;
    title = final?.score !== null && final?.score !== undefined ? `Passed · ${final.score}/100` : "Passed";
    detail = check.purpose === "generate" && check.planned_count > 1
      ? `${plural(queued, "more image", "more images")} queued`
      : check.final_prompt && currentPrompt === check.final_prompt ? "Prompt applied" : "Qualified prompt ready";
  } else if (check.status === "not_met") {
    icon = ICONS.notMet;
    title = best?.score !== null && best?.score !== undefined ? `Not met · best ${best.score}/100` : "Not met";
    detail = `Pass score ${check.threshold}`;
  } else if (check.status === "failed") {
    icon = ICONS.alert;
    title = "Check failed";
    detail = check.error?.message || current?.error?.message || "The check stopped with an error.";
  } else if (check.status === "stopped") {
    icon = ICONS.stop;
    title = current ? `Stopped at attempt ${current.number}` : "Stopped";
    detail = "Images already queued will finish";
  }
  const terminal = TERMINAL_CHECK_STATUSES.has(check.status);
  return {
    hidden: false,
    className: `expectation-status ${STATUS_CLASS[check.status] || ""}`.trim(),
    inner: `<span class="expectation-status-icon" aria-hidden="true">${icon}</span>
    <span class="expectation-status-text"><span>${escapeHtml(title)}</span><small>${escapeHtml(detail)}</small></span>
    <button type="button" class="button low" data-action="view-expectation-check" aria-haspopup="dialog" aria-controls="expectation-check-dialog">View</button>
    ${terminal ? '<button type="button" class="icon-button" data-action="dismiss-expectation-status" aria-label="Dismiss expectation check status">×</button>' : ""}`,
  };
}

export function expectationStatusMarkup(check, options = {}) {
  const view = expectationStatusPresentation(check, options);
  return `<div id="expectation-status" class="${view.className}" role="status" aria-live="polite" ${view.hidden ? "hidden" : ""}>${view.inner}</div>`;
}

export function expectationPipelineStage(check) {
  const attempt = currentAttempt(check);
  if (!checkIsActive(check) || !attempt) return null;
  if (attempt.status === "generating") return "image";
  if (attempt.status === "evaluating") return "vision";
  return "creative_direction";
}

// Generate's pipeline once Use Creative Direction runs a vision check, and the
// live stage while any check runs. Null keeps the ordinary pipeline.
export function expectationPipeline(state) {
  if (state?.autoGenerate) return null;
  const running = checkIsActive(state?.expectationCheck);
  const planned = expectationsActive(state) && Boolean(state?.autoGenerateCreativeDirection) && !state?.promptGeneration?.enabled;
  if (!running && !planned) return null;
  const check = running ? state.expectationCheck : null;
  const mode = check ? check.mode : state?.promptAssistant?.mode;
  const attempts = check ? check.max_attempts : normalizeExpectationSettings(state?.expectations).maxAttempts;
  return {
    stages: [
      ["creative_direction", mode === "create" && !state?.promptGeneration?.enabled ? "Create" : "Refine", ""],
      ["vision", `Vision check (≤${attempts})`, " is-vision-stage"],
      ["image", "Image", ""],
    ],
    active: expectationPipelineStage(check),
  };
}

// ---------- Dialog ----------

export function expectationScoreRowMarkup(result, threshold) {
  const score = Math.max(0, Math.min(100, Number(result.score) || 0));
  const fillClass = score >= threshold ? "is-met" : score < 50 ? "is-low" : "";
  return `<li class="expectation-score-row">
    <span class="expectation-mark ${result.met ? "is-met" : "is-unmet"}" role="img" aria-label="${result.met ? "Met" : "Not met"}">${result.met ? ICONS.check : ICONS.cross}</span>
    <span class="expectation-score-text">${escapeHtml(result.expectation)}</span>
    <span class="expectation-bar" role="img" aria-label="Score ${score} of 100, pass score ${threshold}"><span class="expectation-bar-fill ${fillClass}" style="width:${score}%"></span><span class="expectation-bar-threshold" style="left:calc(${threshold}% - 1px)"></span></span>
    <span class="expectation-score-value">${score}</span>
  </li>`;
}

export function expectationPendingScoreRowMarkup(expectation) {
  return `<li class="expectation-score-row"><span class="expectation-mark is-pending" role="img" aria-label="Not scored yet">${ICONS.dot}</span><span class="expectation-score-text">${escapeHtml(expectation)}</span><span class="expectation-bar" aria-hidden="true"></span><span class="expectation-score-value">—</span></li>`;
}

export function expectationStepperMarkup(check) {
  const steps = [];
  const running = checkIsActive(check);
  const latest = currentAttempt(check);
  const best = highlightedBest(check);
  for (let number = 1; number <= check.max_attempts; number += 1) {
    const item = check.attempts.find((entry) => entry.number === number);
    const current = running && item === latest;
    const stateClass = !item ? ""
      : current ? "is-current"
        : item.status === "passed" ? "is-passed"
          : best === number ? "is-not-met is-best"
            : item.status === "not_met" ? "is-not-met"
              : ["failed", "stopped"].includes(item.status) ? "is-failed" : "";
    const label = item ? `${(ATTEMPT_STATUS_LABELS[item.status] || item.status).toLowerCase()}${best === number ? ", best" : ""}` : "not started";
    if (number > 1) steps.push('<li class="expectation-step-connector" aria-hidden="true"></li>');
    steps.push(`<li class="expectation-step ${stateClass}" aria-label="Attempt ${number}: ${escapeHtml(label)}"${current ? ' aria-current="step"' : ""}>${item?.status === "passed" ? ICONS.check : number}</li>`);
  }
  return `<ol class="expectation-stepper" aria-label="Attempts">${steps.join("")}</ol>`;
}

function attemptImageDeleted(item) {
  return !item.generation && Boolean(item.prompt) && (["passed", "not_met"].includes(item.status) || (item.score !== null && item.score !== undefined));
}

function attemptPromptOpen(item, check, view) {
  const saved = view.detailsOpen?.get?.(item.number);
  if (typeof saved === "boolean") return saved;
  const current = checkIsActive(check) && item === currentAttempt(check);
  return current || item.status === "passed" || highlightedBest(check) === item.number;
}

export function expectationAttemptMarkup(item, check, view = {}) {
  const current = checkIsActive(check) && item === currentAttempt(check);
  const best = highlightedBest(check) === item.number;
  const classes = ["expectation-attempt", current ? "is-current" : "", item.status === "passed" ? "is-passed" : "", best ? "is-best" : ""].filter(Boolean).join(" ");
  const pillClass = item.status === "passed" ? "is-passed" : item.status === "not_met" ? "is-not-met" : ["failed", "stopped"].includes(item.status) ? "is-failed" : "";
  const thumbnail = item.generation?.thumbnail_url;
  const placeholder = attemptImageDeleted(item) ? "Image deleted"
    : item.status === "stopped" ? "Stopped"
      : item.status === "failed" ? "No score"
        : ATTEMPT_STATUS_LABELS[item.status] || item.status;
  const media = thumbnail
    ? `<button type="button" class="expectation-attempt-media" data-expectation-action="open-image" data-attempt="${item.number}" aria-label="View attempt ${item.number} image"><img src="${escapeHtml(thumbnail)}" alt="Attempt ${item.number} image" decoding="async" /></button>`
    : `<div class="expectation-attempt-media is-pending${current ? "" : " is-empty"}" aria-hidden="true">${current ? '<span class="activity-spinner"></span>' : ""}<span>${escapeHtml(placeholder)}</span></div>`;
  const results = item.results?.length
    ? `<ul class="expectation-results">${item.results.map((result) => `<li class="expectation-result"><span class="expectation-mark ${result.met ? "is-met" : "is-unmet"}" role="img" aria-label="${result.met ? "Met" : "Not met"}">${result.met ? ICONS.check : ICONS.cross}</span><span class="expectation-result-copy"><strong>${escapeHtml(result.expectation)}</strong>${result.observation ? `<span>${escapeHtml(result.observation)}</span>` : ""}</span><span class="expectation-result-score">${escapeHtml(result.score)}</span></li>`).join("")}</ul>`
    : "";
  const prompt = item.prompt
    ? `<details class="expectation-attempt-prompt" data-attempt-prompt="${item.number}" ${attemptPromptOpen(item, check, view) ? "open" : ""}><summary>Prompt</summary><p>${escapeHtml(item.prompt)}</p></details>`
    : "";
  const message = item.status === "failed" ? item.error?.message || check.error?.message : "";
  const score = item.score !== null && item.score !== undefined ? `<span class="expectation-attempt-score"><b>${escapeHtml(item.score)}</b> / 100</span>` : "";
  return `<li class="${classes}" data-attempt-number="${item.number}">
    ${media}
    <div class="expectation-attempt-body">
      <div class="expectation-attempt-heading">
        <h3>Attempt ${item.number}</h3>
        <span class="expectation-pill ${pillClass}">${current ? SPINNER : ""}${escapeHtml(best ? "Best" : ATTEMPT_STATUS_LABELS[item.status] || item.status)}</span>
        ${score}
      </div>
      ${prompt}
      ${results}
      ${item.summary ? `<p class="expectation-summary">“${escapeHtml(item.summary)}”</p>` : ""}
      ${message ? `<p class="prompt-assistant-error" role="alert">${escapeHtml(message)}</p>` : ""}
    </div>
  </li>`;
}

export function expectationBannerMarkup(check, view = {}) {
  const final = passedAttempt(check);
  const best = scoredAttempt(check);
  const current = currentAttempt(check);
  const banner = (tone, icon, body) => `<p class="expectation-banner ${tone}"><span class="expectation-banner-icon" aria-hidden="true">${icon}</span><span>${body}</span></p>`;
  if (check.status === "passed") {
    const queued = check.queued?.generation_ids?.length || 0;
    const failures = check.queued?.errors || [];
    let tail;
    if (check.purpose === "generate") {
      tail = check.planned_count > 1
        ? `That image is the first of your ${check.planned_count}; <strong>${queued} more</strong> ${queued === 1 ? "was" : "were"} queued with the qualified prompt.`
        : "That image is the one you asked for.";
      if (failures.length) tail += ` ${plural(failures.length, "image")} couldn't be queued: ${escapeHtml(failures[0]?.message || "Unknown error.")}`;
    } else {
      tail = check.final_prompt && view.currentPrompt === check.final_prompt
        ? "The qualified prompt was applied to the Prompt field."
        : "Your Prompt field changed during the check, so it was left as it is. Use this prompt to apply the qualified one.";
    }
    const score = final?.score !== null && final?.score !== undefined ? ` with ${final.score}/100` : "";
    return banner("is-passed", ICONS.check, `<strong>Passed on attempt ${final?.number ?? check.best_attempt}${score}.</strong> ${tail}`);
  }
  if (check.status === "not_met") {
    const reason = check.error?.message ? `${escapeHtml(check.error.message)} ` : "";
    const bestCopy = best ? `The best was attempt ${best.number} at ${best.score}/100 (pass score ${check.threshold}). ` : "";
    return banner("is-not-met", ICONS.notMet, `<strong>Expectations weren't met after ${plural(check.attempts.length, "attempt")}.</strong> ${reason}${bestCopy}Nothing else was queued.`);
  }
  if (check.status === "failed") {
    const message = check.error?.message || current?.error?.message || "";
    return banner("is-failed", ICONS.alert, `<strong>The check stopped with an error on attempt ${current?.number ?? 1}.</strong> ${escapeHtml(message)} Images already generated are kept.`);
  }
  if (check.status === "stopped") {
    return check.error?.message
      ? banner("is-stopped", ICONS.stop, `<strong>The check stopped during attempt ${current?.number ?? 1}.</strong> ${escapeHtml(check.error.message)}`)
      : banner("is-stopped", ICONS.stop, `<strong>You stopped this check during attempt ${current?.number ?? 1}.</strong> An attempt image that was already queued will still finish.`);
  }
  return "";
}

export function expectationFooterMarkup(check, view = {}) {
  const busy = view.busy || null;
  if (view.confirmDelete) {
    const count = failedAttemptGenerationIds(check).length;
    return `<div class="expectation-confirm" role="alert"><span>Delete ${plural(count, "attempt image", "attempt images")} from the gallery?</span></div>
      <div class="expectation-footer-buttons"><button type="button" class="button secondary" data-expectation-action="cancel-delete" ${busy ? "disabled" : ""}>Cancel</button><button type="button" class="button destructive" data-expectation-action="confirm-delete" ${busy ? "disabled" : ""}>${busy === "deleting" ? "Deleting…" : "Delete"}</button></div>`;
  }
  if (checkIsActive(check)) {
    return `<p class="expectation-footer-note">Runs on the server — closing this window doesn't stop it.</p>
      <div class="expectation-footer-buttons"><button type="button" class="button destructive" data-expectation-action="stop" ${busy ? "disabled" : ""}>${busy === "stopping" ? "Stopping…" : "Stop"}</button><button type="button" class="button secondary" data-expectation-action="close">Hide</button></div>`;
  }
  const failed = failedAttemptGenerationIds(check);
  const deleteButton = failed.length
    ? `<button type="button" class="button low" data-expectation-action="delete-failed" ${busy ? "disabled" : ""}>${escapeHtml(deleteAttemptsLabel(check, failed.length))}</button>`
    : "";
  const reusable = reusableAttempt(check);
  let use = "";
  if (reusable?.prompt) {
    const applied = view.currentPrompt === reusable.prompt;
    const tone = check.status === "not_met" ? "primary" : "secondary";
    const label = check.status === "passed" ? "Use this prompt"
      : check.status === "not_met" ? `Use best prompt${reusable.score !== null && reusable.score !== undefined ? ` (${reusable.score})` : ""}`
        : "Use latest prompt";
    use = `<button type="button" class="button ${tone}" data-expectation-action="use-prompt" data-attempt="${reusable.number}" ${applied ? "disabled" : ""}>${applied ? `${ICONS.check} Prompt applied` : escapeHtml(label)}</button>`;
  }
  const note = {
    passed: "Attempt images stay in the gallery until you delete them.",
    not_met: "Adjust the expectations, pass score, or direction and try again.",
    failed: "Fix the cause, then start again with Apply & verify.",
    stopped: "Start again with Apply & verify when you're ready.",
  }[check.status] || "";
  return `<p class="expectation-footer-note">${escapeHtml(note)}</p>
    <div class="expectation-footer-buttons">${deleteButton}${use}<button type="button" class="button ${check.status === "not_met" ? "secondary" : "primary"}" data-expectation-action="close">Close</button></div>`;
}

function phaseCopy(check, view) {
  const current = currentAttempt(check);
  if (!current) return "Starting the check";
  if (current.status === "composing") {
    return current.number === 1 ? "Composing a prompt from your Creative Direction and expectations" : `Revising the prompt from attempt ${current.number - 1}'s feedback`;
  }
  if (current.status === "ready") return "Queueing one image";
  if (current.status === "generating") return view.sourceName ? `Generating one image with ${view.sourceName}` : "Generating one image";
  if (current.status === "evaluating") return "Checking the image against each expectation";
  return "";
}

export function expectationDialogMarkup(check, view = {}) {
  if (!check) return "";
  const running = checkIsActive(check);
  const current = currentAttempt(check);
  const scored = scoredAttempt(check);
  const scoreboardTitle = !scored ? "Expectations" : highlightedBest(check) ? `Best scores · attempt ${scored.number}` : `Latest scores · attempt ${scored.number}`;
  const rows = scored
    ? scored.results.map((result) => expectationScoreRowMarkup(result, check.threshold)).join("")
    : (check.expectations || []).map(expectationPendingScoreRowMarkup).join("");
  const purpose = check.purpose === "generate" ? `Generate · ${plural(check.planned_count, "image")}` : "Apply";
  const mode = check.mode === "create" ? "Create" : "Refine";
  const pill = running
    ? `${SPINNER}${escapeHtml(ATTEMPT_STATUS_LABELS[current?.status] || "Starting")}`
    : escapeHtml(CHECK_STATUS_LABELS[check.status] || check.status);
  return `<div class="dialog-frame expectation-check-frame">
    <header class="dialog-header expectation-check-header">
      <div>
        <h2 id="expectation-check-title">Verify expectations <span class="expectation-pill ${STATUS_CLASS[check.status] || ""}">${pill}</span></h2>
        <p>Creative Direction · ${mode} · ${escapeHtml(purpose)} · pass score ${check.threshold} · up to ${plural(check.max_attempts, "attempt")}</p>
      </div>
      <button type="button" class="icon-button expectation-check-close" data-expectation-action="close" aria-label="Close expectation check">×</button>
    </header>
    <div class="expectation-check-content" tabindex="-1">
      ${expectationBannerMarkup(check, view)}
      <section class="expectation-progress" aria-label="Progress">
        ${expectationStepperMarkup(check)}
        ${running ? `<p class="expectation-phase" role="status">${SPINNER}<span><strong>Attempt ${current?.number ?? 1} of ${check.max_attempts}</strong> · ${escapeHtml(phaseCopy(check, view))}</span></p>` : ""}
      </section>
      <section aria-labelledby="expectation-scoreboard-title">
        <h3 class="expectation-section-title" id="expectation-scoreboard-title">${escapeHtml(scoreboardTitle)}</h3>
        <ul class="expectation-scoreboard">${rows}</ul>
      </section>
      <section aria-labelledby="expectation-attempts-title">
        <h3 class="expectation-section-title" id="expectation-attempts-title">Attempts · newest first</h3>
        <ol class="expectation-attempts" reversed>${[...check.attempts].reverse().map((item) => expectationAttemptMarkup(item, check, view)).join("")}</ol>
      </section>
    </div>
    <footer class="dialog-actions expectation-check-footer">${expectationFooterMarkup(check, view)}</footer>
  </div>`;
}

// A late read must not move a check backwards or replace a newer check.
export function olderCheck(current, next) {
  if (!current || !next) return false;
  const time = (value) => {
    const parsed = Date.parse(value || "");
    return Number.isFinite(parsed) ? parsed : 0;
  };
  if (current.id !== next.id) return time(next.created_at) < time(current.created_at);
  return time(next.updated_at) < time(current.updated_at);
}

// ---------- Controller ----------

// The verification dialog. It follows the account's latest check (SSE notifies,
// and it polls while a check is active), and offers stop, prompt reuse, and
// deleting the attempt images that did not pass.
export function createExpectationCheck(dialog, deps) {
  const api = deps.api;
  const setTimer = deps.setTimer || ((callback, delay) => setTimeout(callback, delay));
  const clearTimer = deps.clearTimer || ((id) => clearTimeout(id));
  const pollMs = deps.pollMs ?? EXPECTATION_POLL_MS;
  const signal = deps.signal || null;
  let check = null;
  let confirmDelete = false;
  let busy = null;
  let timer = null;
  let reading = null;
  let readAgain = false;
  let epoch = 0;
  let disposed = false;
  let lastMarkup = "";
  const detailsOpen = new Map();

  const activeElement = () => dialog.ownerDocument?.activeElement ?? globalThis.document?.activeElement ?? null;
  const view = () => ({
    confirmDelete,
    busy,
    detailsOpen,
    currentPrompt: deps.currentPrompt?.() ?? null,
    sourceName: deps.sourceName?.(check) ?? null,
  });

  function focusSelector(element) {
    if (!element || element === dialog) return null;
    if (element.dataset?.expectationAction) {
      return `[data-expectation-action="${attributeValue(element.dataset.expectationAction)}"]${element.dataset.attempt ? `[data-attempt="${attributeValue(element.dataset.attempt)}"]` : ""}`;
    }
    const prompt = element.closest?.("[data-attempt-prompt]");
    if (prompt && element.tagName === "SUMMARY") return `[data-attempt-prompt="${attributeValue(prompt.dataset.attemptPrompt)}"] > summary`;
    return ".expectation-check-content";
  }

  function render({ force = false, focus = null } = {}) {
    if (disposed || !check || (!dialog.open && !force)) return;
    const markup = expectationDialogMarkup(check, view());
    if (markup === lastMarkup && !focus) return;
    const content = dialog.querySelector?.(".expectation-check-content");
    const scrollTop = content?.scrollTop || 0;
    const focused = activeElement();
    const selector = focus || (focused && dialog.contains?.(focused) ? focusSelector(focused) : null);
    dialog.innerHTML = markup;
    lastMarkup = markup;
    const next = dialog.querySelector?.(".expectation-check-content");
    if (next) next.scrollTop = scrollTop;
    if (selector) {
      const target = dialog.querySelector?.(selector);
      (target && !target.disabled ? target : next)?.focus?.({ preventScroll: true });
    }
  }

  function schedule() {
    clearTimer(timer);
    timer = null;
    if (!disposed && !signal?.aborted && checkIsActive(check)) {
      timer = setTimer(() => {
        timer = null;
        void refresh();
      }, pollMs);
    }
  }

  function update(next) {
    if (disposed) return;
    const previous = check;
    if (olderCheck(previous, next)) return;
    if (next && previous && next.id !== previous.id) {
      detailsOpen.clear();
      confirmDelete = false;
    }
    check = next || null;
    if (JSON.stringify(previous) !== JSON.stringify(check)) {
      // The app may apply the passing prompt; render afterwards so the footer agrees.
      deps.onChange?.(check, previous);
      render();
    }
  }

  async function refresh() {
    if (disposed || signal?.aborted) return check;
    if (reading) {
      readAgain = true;
      return reading;
    }
    clearTimer(timer);
    timer = null;
    const started = epoch;
    reading = (async () => {
      try {
        const result = await api("/api/prompt-assistant/checks/latest", { signal, deadlineMs: 10_000, operation: "Expectation check" });
        if (!disposed && started === epoch && !(result?.check === null && check && checkIsActive(check))) update(result?.check ?? null);
      } catch (error) {
        if (!disposed && !signal?.aborted) deps.onError?.(error);
      } finally {
        reading = null;
        if (!disposed) {
          if (readAgain) {
            readAgain = false;
            void refresh();
          } else schedule();
        }
      }
      return check;
    })();
    return reading;
  }

  function open() {
    if (disposed || !check) return false;
    render({ force: true });
    if (!dialog.open) {
      dialog.showModal();
      dialog.querySelector?.(".expectation-check-content")?.focus?.({ preventScroll: true });
    }
    return true;
  }

  function close() {
    if (dialog.open) dialog.close();
  }

  function track(next, { open: show = false } = {}) {
    if (disposed || !next) return;
    epoch += 1;
    update(next);
    schedule();
    if (show) open();
  }

  async function stop() {
    if (!check || busy || !checkIsActive(check)) return;
    const target = check;
    busy = "stopping";
    epoch += 1;
    render();
    try {
      const result = await api(`/api/prompt-assistant/checks/${encodeURIComponent(target.id)}/stop`, { method: "POST", signal });
      busy = null;
      if (result?.id) update(result);
      render();
      deps.notify?.("Check stopped.");
    } catch (error) {
      busy = null;
      render();
      if (!signal?.aborted) deps.notify?.(error.message || "The check could not be stopped.", "error");
      void refresh();
    } finally {
      schedule();
    }
  }

  function usePrompt(number) {
    const attempt = check?.attempts?.find((item) => item.number === number);
    if (!attempt?.prompt) return;
    deps.applyPrompt?.(attempt.prompt, attempt, check);
    render({ force: true, focus: `[data-expectation-action="use-prompt"][data-attempt="${number}"]` });
  }

  async function deleteFailed() {
    const ids = failedAttemptGenerationIds(check);
    if (busy) return;
    if (!ids.length) {
      confirmDelete = false;
      render();
      return;
    }
    busy = "deleting";
    render();
    try {
      await deps.deleteGenerations(ids, check);
      deps.notify?.(`${ids.length} attempt ${ids.length === 1 ? "image was" : "images were"} deleted.`, "success");
    } catch (error) {
      if (!signal?.aborted) deps.notify?.(error.message || "The attempt images could not be deleted.", "error");
    } finally {
      busy = null;
      confirmDelete = false;
      render({ focus: ".expectation-check-content" });
      await refresh();
    }
  }

  const onClick = (event) => {
    const summary = event.target?.closest?.(".expectation-attempt-prompt > summary");
    if (summary) {
      const details = summary.parentElement;
      // Only user toggles are remembered; the defaults follow the check.
      detailsOpen.set(Number(details.dataset.attemptPrompt), !details.open);
      return;
    }
    const target = event.target?.closest?.("[data-expectation-action]");
    if (!target) return;
    const action = target.dataset.expectationAction;
    event.preventDefault?.();
    if (action === "close") close();
    else if (action === "stop") void stop();
    else if (action === "use-prompt") usePrompt(Number(target.dataset.attempt));
    else if (action === "delete-failed" && !busy) {
      confirmDelete = true;
      render({ focus: '[data-expectation-action="cancel-delete"]' });
    } else if (action === "cancel-delete" && !busy) {
      confirmDelete = false;
      render({ focus: '[data-expectation-action="delete-failed"]' });
    } else if (action === "confirm-delete") void deleteFailed();
    else if (action === "open-image") {
      const attempt = check?.attempts?.find((item) => item.number === Number(target.dataset.attempt));
      if (attempt?.generation) deps.openImage?.(attempt.generation, attempt);
    }
  };
  dialog.addEventListener("click", onClick);
  dialog.onclose = () => {
    if (!busy) confirmDelete = false;
    deps.onClose?.();
  };

  function dispose() {
    if (disposed) return;
    disposed = true;
    clearTimer(timer);
    timer = null;
    dialog.removeEventListener?.("click", onClick);
  }
  signal?.addEventListener?.("abort", dispose, { once: true });

  return {
    open,
    close,
    refresh,
    notify: () => refresh(),
    track,
    current: () => check,
    isOpen: () => Boolean(dialog.open),
    render: () => render(),
    dispose,
  };
}
