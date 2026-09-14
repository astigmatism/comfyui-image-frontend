import assert from "node:assert/strict";
import test from "node:test";

import { ApiTimeoutError, api, setCsrfToken } from "../src/api.mjs";

function jsonResponse(payload, init = {}) {
  return new Response(JSON.stringify(payload), {
    status: init.status || 200,
    headers: { "content-type": "application/json", ...(init.headers || {}) },
  });
}

test("blob downloads retain CSRF headers and surface JSON errors instead of downloading them", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; setCsrfToken(null); });
  setCsrfToken("download-token");
  globalThis.fetch = async (_path, options) => {
    assert.equal(options.headers.get("X-CSRF-Token"), "download-token");
    assert.equal(options.responseType, undefined);
    return new Response("zip bytes", { headers: { "content-type": "application/zip" } });
  };
  const options = { method: "POST", body: JSON.stringify({ generation_ids: ["g"] }), responseType: "blob" };
  const result = await api("/api/gallery/download", options);
  assert.equal(result.type, "application/zip");
  assert.equal(await result.text(), "zip bytes");
  globalThis.fetch = async () => jsonResponse({ error: { code: "download_empty", message: "No images are available." } }, { status: 409 });
  await assert.rejects(api("/api/gallery/download", options), { code: "download_empty", message: "No images are available." });
});

function stalledJsonResponse(signal, { abortDelayMs = 0 } = {}) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('{"partial":'));
        signal.addEventListener(
          "abort",
          () => {
            setTimeout(
              () => controller.error(signal.reason || new DOMException("Aborted.", "AbortError")),
              abortDelayMs,
            );
          },
          { once: true },
        );
      },
    }),
    { headers: { "content-type": "application/json" } },
  );
}

test("named GET deadlines report the logical operation", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = (_path, options) =>
    new Promise((_resolve, reject) => {
      options.signal.addEventListener(
        "abort",
        () => reject(new DOMException("The operation was aborted.", "AbortError")),
        { once: true },
      );
    });

  await assert.rejects(
    api("/private-path", { deadlineMs: 10, operation: "Gallery history" }),
    (error) => {
      assert.ok(error instanceof ApiTimeoutError);
      assert.equal(error.code, "request_timeout");
      assert.equal(error.operation, "Gallery history");
      assert.equal(error.timeoutMs, 10);
      assert.equal(error.message, "Gallery history timed out after 10 ms.");
      assert.doesNotMatch(error.message, /private-path/);
      return true;
    },
  );
});

test("caller abort signals remain authoritative and are not mislabeled as timeouts", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = (_path, options) =>
    new Promise((_resolve, reject) => {
      options.signal.addEventListener(
        "abort",
        () =>
          setTimeout(
            () => reject(new DOMException("Caller cancelled.", "AbortError")),
            25,
          ),
        { once: true },
      );
    });
  const controller = new AbortController();
  const request = api("/api/example", {
    signal: controller.signal,
    deadlineMs: 10,
    operation: "Example request",
  });
  controller.abort();

  await assert.rejects(request, (error) => {
    assert.equal(error.name, "AbortError");
    assert.notEqual(error.code, "request_timeout");
    return true;
  });
});

test("GET deadlines cover a stalled JSON response body", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (_path, options) => stalledJsonResponse(options.signal);

  await assert.rejects(
    api("/api/body-stall", { deadlineMs: 10, operation: "Session request" }),
    (error) => {
      assert.ok(error instanceof ApiTimeoutError);
      assert.equal(error.operation, "Session request");
      assert.equal(error.message, "Session request timed out after 10 ms.");
      return true;
    },
  );
});

test("caller abort remains authoritative after response headers arrive", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  let markHeadersReady;
  const headersReady = new Promise((resolve) => {
    markHeadersReady = resolve;
  });
  globalThis.fetch = async (_path, options) => {
    const response = stalledJsonResponse(options.signal, { abortDelayMs: 25 });
    markHeadersReady();
    return response;
  };
  const controller = new AbortController();
  const request = api("/api/example", {
    signal: controller.signal,
    deadlineMs: 10,
    operation: "Example request",
  });
  await headersReady;
  controller.abort(new DOMException("Caller cancelled.", "AbortError"));

  await assert.rejects(request, (error) => {
    assert.equal(error.name, "AbortError");
    assert.notEqual(error.code, "request_timeout");
    return true;
  });
});

test("mutating requests are sent exactly once without an implicit deadline or retry", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
    setCsrfToken(null);
  });
  setCsrfToken("csrf-value");
  let calls = 0;
  globalThis.fetch = async (_path, options) => {
    calls += 1;
    assert.equal(options.method, "POST");
    assert.equal(options.signal, undefined);
    assert.equal(options.headers.get("X-CSRF-Token"), "csrf-value");
    return jsonResponse(
      { error: { code: "temporarily_unavailable", message: "Try later.", fields: {} } },
      { status: 503 },
    );
  };

  await assert.rejects(
    api("/api/generations", {
      method: "POST",
      body: JSON.stringify({ prompt: "one request only" }),
      deadlineMs: 1,
      operation: "Generation submission",
    }),
    (error) => error.code === "temporarily_unavailable",
  );
  assert.equal(calls, 1);
});

test("API response Date calibrates the countdown clock without changing payloads", async (context) => {
  const { serverClockOffset } = await import("../src/server-clock.mjs");
  const originalFetch = globalThis.fetch;
  setCsrfToken(null);
  context.after(() => { globalThis.fetch = originalFetch; setCsrfToken(null); });
  const start = Date.parse("2026-09-14T12:00:00Z");
  context.mock.method(Date, "now", () => start + 10_500);
  globalThis.fetch = async () => jsonResponse({ ok: true }, {
    headers: { date: new Date(start).toUTCString() },
  });
  assert.deepEqual(await api("/api/auth/session"), { ok: true });
  assert.equal(serverClockOffset(), 10_000);
});
