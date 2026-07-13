let csrfToken = null;

export function setCsrfToken(value) {
  csrfToken = value || null;
}

export function getCsrfToken() {
  return csrfToken;
}

export async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    method,
    headers,
  });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
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
  }
  return payload;
}

export async function upload(path, file) {
  const form = new FormData();
  form.append("file", file);
  return api(path, { method: "POST", body: form });
}
