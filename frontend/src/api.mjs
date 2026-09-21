import { observeServerDate, resetServerClock } from "./server-clock.mjs";

let csrfToken = null;

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiTimeoutError extends Error {
  constructor(operation, timeoutMs) {
    super(`${operation} timed out after ${formatTimeout(timeoutMs)}.`);
    this.name = "ApiTimeoutError";
    this.code = "request_timeout";
    this.operation = operation;
    this.timeoutMs = timeoutMs;
  }
}

export function setCsrfToken(value) {
  csrfToken = value || null;
  if (!csrfToken) resetServerClock();
}

export function getCsrfToken() {
  return csrfToken;
}

export async function api(path, options = {}) {
  const {
    deadlineMs: requestedDeadline,
    submissionKey,
    operation = "Request",
    responseType = "json",
    signal: callerSignal,
    method: requestedMethod = "GET",
    ...fetchOptions
  } = options;
  const method = requestedMethod.toUpperCase();
  const headers = new Headers(fetchOptions.headers || {});
  headers.set("X-CIF-Generation-Protocol", "3");
  const submission = Boolean(submissionKey && method === "POST" && /^\/api\/generations(?:\/batch)?$/.test(path));
  if (submission) headers.set("Idempotency-Key", submissionKey);
  const deadlineMs = submission ? Math.min(requestedDeadline || 60_000, 60_000) : requestedDeadline;
  const attempts = submission ? 5 : SAFE_METHODS.has(method) ? 4 : 1;
  if (csrfToken && !SAFE_METHODS.has(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  if (
    fetchOptions.body &&
    !(fetchOptions.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  delete fetchOptions.headers;

  const deadline = composeDeadlineSignal(submission ? "GET" : method, callerSignal, deadlineMs);
  try {
    for (let attempt = 1; ; attempt += 1) {
      try {
        const sentAt = Date.now();
        const response = await fetch(path, {
          credentials: "same-origin",
          ...fetchOptions,
          method,
          headers,
          signal: deadline.signal,
        });
        observeServerDate(response.headers.get("date"), sentAt, Date.now());
        if (response.status === 204) return null;
        if (response.ok && responseType === "blob") return await response.blob();
        const contentType = response.headers.get("content-type") || "";
        let payload = null;
        try {
          if (contentType.includes("application/json")) payload = await response.json();
        } catch (error) {
          if (!response.ok) {
            error.status = response.status;
            error.retryAfter = response.headers.get("retry-after");
          }
          throw error;
        }
        if (response.ok) return payload;
        const error = payload?.error || {
          code: "request_failed",
          message: `Request failed with HTTP ${response.status}.`,
          fields: {},
        };
        const exception = new Error(error.message);
        exception.code = error.code;
        exception.fields = error.fields || {};
        exception.status = response.status;
        exception.details = error.details || {};
        exception.retryAfter = response.headers.get("retry-after");
        throw exception;
      } catch (error) {
        if (submission && attempt > 1) error.submissionUncertain = true;
        if (attempt >= attempts || deadline.signal?.aborted || !isTransientError(error)) throw error;
        await retryDelay(error.retryAfter, attempt, deadline.signal);
      }
    }
  } catch (error) {
    if (deadline.didTimeout()) throw new ApiTimeoutError(operation, deadlineMs);
    throw error;
  } finally {
    deadline.dispose();
  }
}

export async function upload(path, file) {
  const form = new FormData();
  form.append("file", file);
  return api(path, { method: "POST", body: form });
}

function composeDeadlineSignal(method, callerSignal, deadlineMs) {
  const useDeadline =
    SAFE_METHODS.has(method) && Number.isFinite(deadlineMs) && Number(deadlineMs) > 0;
  if (!useDeadline) {
    return { signal: callerSignal, didTimeout: () => false, dispose: () => {} };
  }

  const controller = new AbortController();
  let abortCause = null;
  const abort = (cause, reason) => {
    if (abortCause !== null) return;
    abortCause = cause;
    controller.abort(reason);
  };
  const forwardCallerAbort = () => abort("caller", callerSignal.reason);
  if (callerSignal?.aborted) forwardCallerAbort();
  else callerSignal?.addEventListener("abort", forwardCallerAbort, { once: true });

  const timer = setTimeout(() => {
    abort("timeout", new DOMException("The request deadline elapsed.", "TimeoutError"));
  }, Number(deadlineMs));
  return {
    signal: controller.signal,
    didTimeout: () => abortCause === "timeout",
    dispose: () => {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", forwardCallerAbort);
    },
  };
}

function formatTimeout(timeoutMs) {
  if (timeoutMs < 1000) return `${timeoutMs} ms`;
  const seconds = timeoutMs / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} ${seconds === 1 ? "second" : "seconds"}`;
}

export function isTransientError(error) {
  if (error?.status) return [429, 500, 502, 503, 504].includes(error.status);
  return error instanceof TypeError || error instanceof SyntaxError;
}

export function retryDelay(retryAfter, attempt, signal) {
  const seconds = retryAfter === null || retryAfter === undefined ? NaN : Number(retryAfter);
  const requested = Number.isFinite(seconds) ? Math.max(0, seconds * 1000)
    : Math.max(0, Date.parse(retryAfter) - Date.now()) || 0;
  const backoff = 150 * (2 ** (attempt - 1)) * (0.5 + Math.random());
  return new Promise((resolve, reject) => {
    let timer;
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(signal.reason || new DOMException("Aborted.", "AbortError"));
    };
    if (signal?.aborted) return abort();
    signal?.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, Math.max(requested, backoff));
  });
}
