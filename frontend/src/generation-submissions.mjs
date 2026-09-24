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
  return Object.assign(new Error("Submission status unknown. Check status or resume the original submission."), {
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

async function send(pending, deadlineMs = 60_000, previouslyUncertain = false) {
  try {
    const result = await api(pending.path, {
      method: "POST", body: pending.body, submissionKey: pending.key, deadlineMs,
      operation: "Generation submission",
    });
    validateResult(pending, result);
    finish(pending, result);
    return result;
  } catch (error) {
    if (error.code === "submission_status_unknown" || previouslyUncertain || error.submissionUncertain || isTransientError(error)
        || error.code === "request_timeout" || error.name === "AbortError") {
      throw unknown(error);
    }
    finish(pending);
    throw error;
  }
}

export async function submitGeneration(path, payload, context = null) {
  if (!ownerId) throw new Error("Sign in before submitting a generation.");
  if (active || pendingSubmission()) throw unknown();
  const pending = { ownerId, key: crypto.randomUUID(), path, body: JSON.stringify(payload), context };
  // Persist before sending; inability to persist must prevent an uncertain acceptance.
  sessionStorage.setItem(prefix + ownerId, JSON.stringify(pending));
  active = true;
  try {
    return await send(pending);
  } finally {
    active = false;
  }
}

export async function recoverSubmission({ resume = false } = {}) {
  const pending = pendingSubmission();
  if (!pending) return null;
  if (active) throw unknown();
  active = true;
  const deadline = Date.now() + 60_000;
  try {
    try {
      const receipt = await api(`/api/generation-submissions/${encodeURIComponent(pending.key)}`, {
        deadlineMs: 10_000, operation: "Submission status",
      });
      validateResult(pending, receipt?.result);
      finish(pending, receipt.result);
      return { pending, result: receipt.result };
    } catch (error) {
      if (error.status === 410) { finish(pending); throw error; }
      if (error.status !== 404) throw unknown(error);
    }
    if (!resume) return { pending, result: null };
    return { pending, result: await send(pending, Math.max(1, deadline - Date.now()), true) };
  } finally {
    active = false;
  }
}

function validateResult(pending, result) {
  const valid = pending.path === "/api/generation-preparations"
    ? typeof result?.id === "string" && Array.isArray(result?.items) && result.items.every((item) => item.id && item.status)
    : pending.path.endsWith("/batch")
    ? Array.isArray(result?.items) && result.items.every((item) => item?.generation?.id || item?.error?.code)
    : typeof result?.id === "string";
  if (!valid) throw new TypeError("The submission response was incomplete.");
}
