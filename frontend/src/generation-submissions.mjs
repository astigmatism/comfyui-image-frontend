import { api, isTransientError } from "./api.mjs";

const prefix = "cif.pending-generation.";
let ownerId = null;
let active = false;

export function setSubmissionOwner(id) {
  ownerId = id || null;
}

export function pendingSubmission() {
  if (!ownerId) return null;
  const saved = sessionStorage.getItem(prefix + ownerId);
  if (!saved) return null;
  const pending = JSON.parse(saved);
  if (pending.ownerId !== ownerId || !pending.key || typeof pending.body !== "string"
      || !["/api/generations", "/api/generations/batch", "/api/prompt-generations", "/api/generation-preparations"].includes(pending.path)) {
    throw new Error("The saved submission cannot be resumed. Keep this tab open and check generation history.");
  }
  return pending;
}

export function clearSubmissionStorage() {
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith(prefix)) sessionStorage.removeItem(key);
  }
  ownerId = null;
}

function unknown(cause) {
  return Object.assign(new Error("Reconnecting to your generation request…"), {
    code: "submission_status_unknown", cause,
  });
}

export function pendingPromptJobs() {
  if (!ownerId) return [];
  const saved = JSON.parse(localStorage.getItem("cif.prompt-jobs." + ownerId) || "[]");
  if (!Array.isArray(saved)) throw new Error("Saved prompt requests could not be read.");
  return saved;
}

export function finishPromptJob(id) {
  localStorage.setItem("cif.prompt-jobs." + ownerId, JSON.stringify(pendingPromptJobs().filter((job) => job.id !== id)));
}

function finish(pending, result) {
  if (result && ["/api/prompt-generations", "/api/generation-preparations"].includes(pending.path)) {
    const storageKey = "cif.prompt-jobs." + pending.ownerId;
    try {
      const jobs = JSON.parse(localStorage.getItem(storageKey) || "[]");
      const payload = JSON.parse(pending.body);
      const context = pending.context || { source: payload.items?.[0]?.generation.source_key };
      if (!jobs.some((job) => job.id === result.id)) jobs.push({ ...context, id: result.id, path: pending.path, seen: [] });
      localStorage.setItem(storageKey, JSON.stringify(jobs));
    } catch (error) { throw unknown(error); }
  }
  // A response from the previous account must never clear another account's receipt.
  sessionStorage.removeItem(prefix + pending.ownerId);
}

function checkOwner(pending, signal) {
  signal?.throwIfAborted();
  if (pending.ownerId !== ownerId) throw new DOMException("Account changed.", "AbortError");
}

async function send(pending, deadlineMs = 60_000, previouslyUncertain = false, signal) {
  try {
    checkOwner(pending, signal);
    const result = await api(pending.path, {
      method: "POST", body: pending.body, submissionKey: pending.key, deadlineMs,
      operation: "Generation submission", signal,
    });
    checkOwner(pending, signal);
    validateResult(pending, result);
    finish(pending, result);
    return result;
  } catch (error) {
    if (signal?.aborted || pending.ownerId !== ownerId || error.code === "submission_status_unknown" || previouslyUncertain || error.submissionUncertain || isTransientError(error)
        || error.code === "request_timeout" || error.name === "AbortError") {
      throw unknown(error);
    }
    finish(pending);
    throw error;
  }
}

export async function submitGeneration(path, payload, context = null, { signal } = {}) {
  if (!ownerId) throw new Error("Sign in before submitting a generation.");
  signal?.throwIfAborted();
  if (active || pendingSubmission()) throw unknown();
  const pending = { ownerId, key: crypto.randomUUID(), path, body: JSON.stringify(payload), context };
  // Persist before sending; inability to persist must prevent an uncertain acceptance.
  sessionStorage.setItem(prefix + ownerId, JSON.stringify(pending));
  active = true;
  try {
    return await send(pending, 60_000, false, signal);
  } finally {
    active = false;
  }
}

export async function recoverSubmission({ resume = false, signal } = {}) {
  const pending = pendingSubmission();
  if (!pending) return null;
  if (active) throw unknown();
  active = true;
  const deadline = Date.now() + 60_000;
  try {
    checkOwner(pending, signal);
    try {
      const receipt = await api(`/api/generation-submissions/${encodeURIComponent(pending.key)}`, {
        deadlineMs: 10_000, operation: "Submission status", signal,
      });
      checkOwner(pending, signal);
      validateResult(pending, receipt?.result);
      finish(pending, receipt.result);
      return { pending, result: receipt.result };
    } catch (error) {
      checkOwner(pending, signal);
      if (error.status === 410) { finish(pending); throw error; }
      if (error.status !== 404) throw unknown(error);
    }
    if (!resume) return { pending, result: null };
    return { pending, result: await send(pending, Math.max(1, deadline - Date.now()), true, signal) };
  } finally {
    active = false;
  }
}

// Recovery has its own backoff, beyond an individual request's bounded retries.
// Callbacks belong to this account/session and never run after it is stopped.
export function createSubmissionRecovery({ signal, onRecovered, onError, onChange,
  setTimer = setTimeout, clearTimer = clearTimeout }) {
  const owner = ownerId;
  let timer = null;
  let busy = false;
  let delay = 1000;
  const stopped = () => signal.aborted || owner !== ownerId;
  const schedule = (wait) => {
    if (!stopped() && timer === null) timer = setTimer(() => run(), wait);
  };
  const run = async () => {
    timer = null;
    if (stopped() || busy) return;
    // An original POST may still be finishing as the application reconnects.
    if (active) { schedule(1000); return; }
    const pending = pendingSubmission();
    if (!pending) { onChange(false); return; }
    busy = true;
    onChange(true);
    try {
      const recovered = await recoverSubmission({ resume: true, signal });
      if (!stopped() && recovered?.result) await onRecovered(recovered);
    } catch (error) {
      if (!stopped() && error.code !== "submission_status_unknown") onError(error, pending);
    } finally {
      busy = false;
      if (!stopped()) {
        const pending = pendingSubmission();
        onChange(Boolean(pending));
        if (pending) { schedule(delay); delay = Math.min(delay * 2, 30_000); }
        else delay = 1000;
      }
    }
  };
  signal.addEventListener("abort", () => { clearTimer(timer); timer = null; }, { once: true });
  return {
    start({ immediate = false } = {}) {
      if (stopped() || busy || !pendingSubmission()) return;
      if (active) { schedule(1000); return; }
      if (immediate) { clearTimer(timer); timer = null; }
      schedule(0);
      onChange(true);
    },
  };
}

function validateResult(pending, result) {
  const valid = pending.path === "/api/generation-preparations"
    ? typeof result?.id === "string" && Array.isArray(result?.items) && result.items.every((item) => item.id && item.status)
    : pending.path.endsWith("/batch")
    ? Array.isArray(result?.items) && result.items.every((item) => item?.generation?.id || item?.error?.code)
    : typeof result?.id === "string";
  if (!valid) throw new TypeError("The submission response was incomplete.");
}
