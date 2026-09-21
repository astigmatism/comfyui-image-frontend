import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";
import { api } from "../src/api.mjs";
import { createThumbnailScheduler } from "../src/thumbnails.mjs";
import { setSubmissionOwner, submitGeneration, recoverSubmission, pendingSubmission, clearSubmissionStorage } from "../src/generation-submissions.mjs";

function json(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json", ...headers } });
}
function setup(context) {
  const fetch = globalThis.fetch;
  const storage = globalThis.sessionStorage;
  const crypto = globalThis.crypto;
  const values = {};
  globalThis.sessionStorage = Object.assign(values, {
    getItem: (key) => values[key] || null,
    setItem: (key, value) => { values[key] = value; },
    removeItem: (key) => { delete values[key]; },
  });
  globalThis.crypto = webcrypto;
  context.after(() => {
    clearSubmissionStorage();
    globalThis.fetch = fetch;
    globalThis.sessionStorage = storage;
    globalThis.crypto = crypto;
  });
  setSubmissionOwner("account-one");
}

test("safe reads retry transient responses but never authorization or other mutations", async (context) => {
  setup(context);
  let calls = 0;
  globalThis.fetch = async () => ++calls < 4 ? json({}, 503) : json({ ok: true });
  assert.deepEqual(await api("/api/read"), { ok: true });
  assert.equal(calls, 4);
  for (const status of [400, 401, 403, 404, 409, 422]) {
    calls = 0;
    globalThis.fetch = async () => { calls += 1; return json({}, status); };
    await assert.rejects(api("/api/read"), { status });
    assert.equal(calls, 1);
  }
  calls = 0;
  globalThis.fetch = async () => { calls += 1; return json({}, 503); };
  await assert.rejects(api("/api/preferences", { method: "PUT" }), { status: 503 });
  assert.equal(calls, 1);
});

test("Retry-After cannot exceed the caller's overall deadline", async (context) => {
  setup(context);
  for (const body of ["{}", "incomplete JSON"]) {
    let calls = 0;
    globalThis.fetch = async () => {
      calls += 1;
      return new Response(body, { status: 503, headers: { "content-type": "application/json", "Retry-After": "10" } });
    };
    const started = Date.now();
    await assert.rejects(api("/api/read", { deadlineMs: 30 }), { code: "request_timeout" });
    assert.equal(calls, 1);
    assert.ok(Date.now() - started < 500);
  }
});

test("lost replies retry the same frozen body and UUID", async (context) => {
  setup(context);
  const seen = [];
  const payload = { parameters: { seed: "random", prompt: "original" } };
  globalThis.fetch = async (_url, options) => {
    seen.push([options.headers.get("Idempotency-Key"), options.body]);
    assert.equal(options.headers.get("X-CIF-Generation-Protocol"), "3");
    payload.parameters.prompt = "changed after click";
    if (seen.length < 3) throw new TypeError("lost reply");
    return json({ id: "accepted-once" });
  };
  assert.deepEqual(await submitGeneration("/api/generations", payload), { id: "accepted-once" });
  assert.equal(new Set(seen.map(([key]) => key)).size, 1);
  assert.equal(new Set(seen.map(([, body]) => body)).size, 1);
  assert.equal(JSON.parse(seen[0][1]).parameters.prompt, "original");
  assert.equal(pendingSubmission(), null);
});

test("an exhausted submission retains its identity across reload and account changes", async (context) => {
  setup(context);
  let attempts = 0;
  globalThis.fetch = async () => { attempts += 1; throw new TypeError("offline"); };
  await assert.rejects(submitGeneration("/api/generations", { parameters: {} }), { code: "submission_status_unknown" });
  assert.equal(attempts, 5);
  const original = pendingSubmission();
  await assert.rejects(submitGeneration("/api/generations", {}), { code: "submission_status_unknown" });
  setSubmissionOwner("account-two");
  assert.equal(pendingSubmission(), null);
  setSubmissionOwner("account-one");
  assert.equal(pendingSubmission().key, original.key);
  const seen = [];
  globalThis.fetch = async (url, options) => {
    seen.push([url, options.method]);
    if (options.method === "GET") return json({}, 404);
    assert.equal(options.headers.get("Idempotency-Key"), original.key);
    assert.equal(options.body, original.body);
    return json({ id: "original-result" });
  };
  assert.equal((await recoverSubmission()).result, null);
  assert.equal(seen.length, 1);
  assert.equal((await recoverSubmission({ resume: true })).result.id, "original-result");
  assert.deepEqual(seen.map(([, method]) => method), ["GET", "GET", "POST"]);
  assert.equal(pendingSubmission(), null);
});

test("a receipt after a lost response resolves without another POST", async (context) => {
  setup(context);
  sessionStorage.setItem("cif.pending-generation.account-one", JSON.stringify({
    ownerId: "account-one", key: webcrypto.randomUUID(), path: "/api/generations", body: "{}",
  }));
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.method, "GET");
    return json({ result: { id: "existing" } });
  };
  assert.equal((await recoverSubmission({ resume: true })).result.id, "existing");
  assert.equal(pendingSubmission(), null);
});

test("a rejection after an uncertain attempt cannot discard its original key", async (context) => {
  setup(context);
  let attempts = 0;
  globalThis.fetch = async () => {
    if (++attempts === 1) throw new TypeError("accepted response lost");
    return json({}, 401);
  };
  await assert.rejects(submitGeneration("/api/generations", {}), { code: "submission_status_unknown" });
  assert.equal(attempts, 2);
  const original = pendingSubmission();
  globalThis.fetch = async (_url, options) => json({}, options.method === "GET" ? 404 : 422);
  await assert.rejects(recoverSubmission({ resume: true }), { code: "submission_status_unknown" });
  assert.equal(pendingSubmission().key, original.key);
  globalThis.fetch = async () => json({ result: { id: "accepted-original" } });
  assert.equal((await recoverSubmission()).result.id, "accepted-original");
  assert.equal(pendingSubmission(), null);
});

test("thumbnail scheduler bounds work, deduplicates, cancels, and revokes images", async () => {
  const pending = [];
  const revoked = [];
  let serial = 0;
  const scheduler = createThumbnailScheduler({
    load: (url, signal) => new Promise((resolve, reject) => {
      pending.push({ url, signal, resolve });
      signal.addEventListener("abort", () => reject(signal.reason));
    }),
    createURL: () => `blob:${++serial}`, revokeURL: (url) => revoked.push(url),
  });
  const stop = [];
  const results = [];
  for (let index = 0; index < 8; index += 1) stop.push(scheduler.subscribe(`/${index}`, (value) => results.push(value)));
  const duplicate = scheduler.subscribe("/0", (value) => results.push(value));
  await new Promise(setImmediate);
  assert.deepEqual(scheduler.snapshot(), { active: 4, queued: 4, retained: 8 });
  assert.equal(pending.length, 4);
  pending[0].resolve(new Blob(["image"]));
  await new Promise(setImmediate);
  assert.equal(results.length, 2);
  assert.equal(results[0].url, results[1].url);
  stop[0]();
  assert.equal(revoked.length, 0);
  duplicate();
  assert.deepEqual(revoked, ["blob:1"]);
  for (const release of stop.slice(1)) release();
  await new Promise(setImmediate);
  assert.deepEqual(scheduler.snapshot(), { active: 0, queued: 0, retained: 0 });
  assert.ok(pending.slice(1).every((request) => request.signal.aborted));
});
