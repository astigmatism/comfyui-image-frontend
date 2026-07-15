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
}

export function getCsrfToken() {
  return csrfToken;
}

export async function api(path, options = {}) {
  const {
    deadlineMs,
    operation = "Request",
    signal: callerSignal,
    method: requestedMethod = "GET",
    ...fetchOptions
  } = options;
  const method = requestedMethod.toUpperCase();
  const headers = new Headers(fetchOptions.headers || {});
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

  const deadline = composeDeadlineSignal(method, callerSignal, deadlineMs);
  try {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...fetchOptions,
      method,
      headers,
      signal: deadline.signal,
    });
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
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
    throw exception;
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
