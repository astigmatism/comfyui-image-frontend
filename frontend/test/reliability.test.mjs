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

test("thumbnail scheduler bounds work, deduplicates, and caches blobs across re-subscribes", async () => {
  const pending = [];
  const revoked = [];
  let serial = 0;
  const scheduler = createThumbnailScheduler({
    decode: async () => {},
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
  // Completed images keep their object URLs within the idle cache budget.
  stop[0](); duplicate();
  assert.deepEqual(revoked, []);
  const resubscribed = [];
  stop[0] = scheduler.subscribe("/0", (value) => resubscribed.push(value));
  await new Promise(setImmediate);
  assert.equal(pending.length, 5); // /4 drained after /0 finished; no /0 refetch
  assert.equal(resubscribed.length, 1);
  assert.equal(resubscribed[0].url, "blob:1");
  // Reconciliation retains unchanged consumers. Work with no remaining
  // consumer is genuinely obsolete and should release its concurrency slot.
  for (const release of stop.slice(1)) release();
  await new Promise(setImmediate);
  assert.deepEqual(scheduler.snapshot(), { active: 0, queued: 0, retained: 1 });
  assert.ok(pending.slice(1).every((request) => request.signal.aborted));
  stop[0](); scheduler.dispose();
  assert.deepEqual(revoked, ["blob:1"]);
});

test("thumbnail scheduler cancels obsolete in-flight loads and a later subscriber can retry", async () => {
  const pending = [];
  const revoked = [];
  let serial = 0;
  const scheduler = createThumbnailScheduler({
    decode: async () => {},
    load: (url, signal) => new Promise((resolve, reject) => {
      pending.push({ url, signal, resolve });
      signal.addEventListener("abort", () => reject(signal.reason));
    }),
    createURL: () => `blob:${++serial}`, revokeURL: (url) => revoked.push(url),
  });
  const results = [];
  const stop = scheduler.subscribe("/image", (value) => results.push(value));
  await new Promise(setImmediate);
  stop();
  assert.equal(pending[0].signal.aborted, true);
  pending[0].resolve(new Blob(["obsolete"]));
  await new Promise(setImmediate);
  assert.equal(results.length, 0);
  assert.equal(serial, 0);
  const resubscribed = [];
  scheduler.subscribe("/image", (value) => resubscribed.push(value));
  await new Promise(setImmediate);
  assert.equal(pending.length, 2);
  pending[1].resolve(new Blob(["image"]));
  await new Promise(setImmediate);
  assert.equal(resubscribed.length, 1);
  assert.equal(resubscribed[0].url, "blob:1");
  assert.deepEqual(revoked, []);
  scheduler.dispose();
});

test("thumbnail scheduler evicts least recently used cached blobs", async () => {
  const pending = [];
  const revoked = [];
  let serial = 0;
  const scheduler = createThumbnailScheduler({
    maxIdleEntries: 3, decode: async () => {},
    load: (url, signal) => new Promise((resolve, reject) => {
      pending.push({ url, signal, resolve });
      signal.addEventListener("abort", () => reject(signal.reason));
    }),
    createURL: () => `blob:${++serial}`, revokeURL: (url) => revoked.push(url),
  });
  const stop = [];
  for (let index = 0; index < 3; index += 1) stop.push(scheduler.subscribe(`/${index}`, () => {}));
  await new Promise(setImmediate);
  pending.forEach((item) => item.resolve(new Blob(["image"])));
  await new Promise(setImmediate);
  for (const release of stop) release();
  assert.deepEqual(revoked, []);
  const touch = scheduler.subscribe("/1", () => {});
  touch();
  const fourth = scheduler.subscribe("/3", () => {});
  await new Promise(setImmediate);
  assert.deepEqual(scheduler.snapshot(), { active: 1, queued: 0, retained: 4 });
  pending[3].resolve(new Blob(["image"]));
  await new Promise(setImmediate);
  fourth();
  assert.deepEqual(revoked, ["blob:1"]); // /0 is the least recently used idle entry.
  const revived = [];
  const resub = scheduler.subscribe("/2", (value) => revived.push(value));
  await new Promise(setImmediate);
  assert.equal(pending.length, 4);
  assert.equal(revived[0].url, "blob:3");
  resub();
  const reloaded = [];
  scheduler.subscribe("/0", (value) => reloaded.push(value));
  await new Promise(setImmediate);
  assert.equal(pending.length, 5);
  assert.equal(reloaded.length, 0);
  pending.at(-1).resolve(new Blob(["image"]));
  await new Promise(setImmediate);
  assert.equal(reloaded.length, 1);
  scheduler.dispose();
});
