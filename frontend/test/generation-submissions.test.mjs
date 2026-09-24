import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";
globalThis.crypto ||= webcrypto;
import { submitGeneration, recoverSubmission, createSubmissionRecovery, pendingSubmission, pendingPromptJobs, setSubmissionOwner } from "../src/generation-submissions.mjs";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

for (const path of ["/api/prompt-generations", "/api/generation-preparations"]) test(`${path}: lost reply recovers the original job and context without resubmitting`, async (context) => {
  const original = { fetch: globalThis.fetch, localStorage: globalThis.localStorage, sessionStorage: globalThis.sessionStorage };
  context.after(() => { Object.assign(globalThis, original); setSubmissionOwner(null); });
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  setSubmissionOwner("owner");
  let key;
  let posts = 0;
  globalThis.fetch = async (_url, options) => {
    posts += 1;
    key ??= options.headers.get("Idempotency-Key");
    assert.equal(options.headers.get("Idempotency-Key"), key);
    assert.equal(pendingSubmission().key, key);
    throw new TypeError("Lost reply after acceptance");
  };
  await assert.rejects(submitGeneration(path, { parameters: {} }, { source: "image", generator: "text" }), { code: "submission_status_unknown" });
  const requests = posts;
  const result = path.endsWith("preparations") ? { id: "group", items: [{ id: "item", status: "preparing" }] } : { id: "text-run", status: "running" };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, `/api/generation-submissions/${key}`);
    assert.equal(options.method, "GET");
    return new Response(JSON.stringify({ result }), { headers: { "content-type": "application/json" } });
  };
  const recovered = await recoverSubmission();
  assert.deepEqual(recovered.result, result);
  assert.equal(posts, requests);
  assert.equal(pendingSubmission(), null);
  assert.equal(pendingPromptJobs()[0].source, "image");
  assert.equal(pendingPromptJobs()[0].generator, "text");
  setSubmissionOwner("other");
  assert.deepEqual(pendingPromptJobs(), []);
});

function recoveryFixture(context) {
  const original = { fetch: globalThis.fetch, localStorage: globalThis.localStorage, sessionStorage: globalThis.sessionStorage };
  globalThis.localStorage = storage();
  globalThis.sessionStorage = storage();
  setSubmissionOwner("owner");
  const pending = { ownerId: "owner", key: "original-key", path: "/api/generations", body: '{"prompt":"original"}' };
  sessionStorage.setItem("cif.pending-generation.owner", JSON.stringify(pending));
  const controller = new AbortController();
  const timers = new Map();
  const delays = [];
  const recovered = [];
  const errors = [];
  const changes = [];
  const recovery = createSubmissionRecovery({
    signal: controller.signal,
    onRecovered: (value) => recovered.push(value),
    onError: (error) => errors.push(error),
    onChange: (value) => changes.push(value),
    setTimer: (callback, delay) => { const id = Symbol(); timers.set(id, callback); delays.push(delay); return id; },
    clearTimer: (id) => timers.delete(id),
  });
  context.after(() => { controller.abort(); Object.assign(globalThis, original); setSubmissionOwner(null); });
  const tick = async () => {
    assert.equal(timers.size, 1);
    const [id, callback] = timers.entries().next().value;
    timers.delete(id);
    await callback();
  };
  return { pending, controller, timers, delays, recovered, errors, changes, recovery, tick };
}

const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });

test("automatic recovery checks the receipt then replays the frozen request only once", async (context) => {
  const f = recoveryFixture(context);
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push([url, options.method]);
    if (options.method === "GET") return response({}, 404);
    assert.equal(options.headers.get("Idempotency-Key"), f.pending.key);
    assert.equal(options.body, f.pending.body);
    f.recovery.start(); // Reentrant triggers cannot launch a second recovery.
    return response({ id: "accepted" });
  };
  f.recovery.start();
  f.recovery.start();
  await f.tick();
  assert.deepEqual(requests.map(([, method]) => method), ["GET", "POST"]);
  assert.equal(f.recovered[0].result.id, "accepted");
  assert.equal(pendingSubmission(), null);
  assert.equal(f.timers.size, 0);
  assert.equal(f.changes.at(-1), false);
});

test("automatic recovery backs off to thirty seconds and clears uncertainty after a receipt", async (context) => {
  const f = recoveryFixture(context);
  globalThis.fetch = async () => response({}, 401);
  f.recovery.start();
  for (let i = 0; i < 7; i += 1) await f.tick();
  assert.deepEqual(f.delays, [0, 1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  assert.equal(pendingSubmission().key, f.pending.key);
  assert.deepEqual(f.errors, []);
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.method, "GET");
    return response({ result: { id: "already-accepted" } });
  };
  f.recovery.start({ immediate: true });
  assert.equal(f.timers.size, 1);
  await f.tick();
  assert.equal(f.recovered[0].result.id, "already-accepted");
  assert.equal(f.timers.size, 0);
  assert.equal(f.changes.at(-1), false);
});

for (const stop of ["abort", "account change"]) for (const status of [404, 410]) test(`recovery ignores HTTP ${status} and preserves the receipt after ${stop}`, async (context) => {
  const f = recoveryFixture(context);
  let finishRead;
  let reads = 0;
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.method, "GET", "A stopped session must never replay a POST");
    reads += 1;
    return await new Promise((resolve) => { finishRead = () => resolve(response({}, status)); });
  };
  f.recovery.start();
  const running = f.tick();
  await new Promise((resolve) => setImmediate(resolve));
  if (stop === "abort") f.controller.abort();
  else setSubmissionOwner("another-owner");
  finishRead();
  await running;
  assert.equal(reads, 1);
  assert.equal(f.timers.size, 0);
  assert.deepEqual(f.recovered, []);
  assert.deepEqual(f.errors, []);
  setSubmissionOwner("owner");
  assert.equal(pendingSubmission().key, f.pending.key);
});

test("an unavailable receipt reports a terminal error and stops retrying", async (context) => {
  const f = recoveryFixture(context);
  globalThis.fetch = async () => response({ error: { code: "submission_result_unavailable", message: "Request is no longer available." } }, 410);
  f.recovery.start();
  await f.tick();
  assert.equal(f.errors[0].code, "submission_result_unavailable");
  assert.equal(f.timers.size, 0);
  assert.equal(pendingSubmission(), null);
});

test("reconnection during a healthy in-flight submission neither reports uncertainty nor duplicates it", async (context) => {
  const f = recoveryFixture(context);
  sessionStorage.removeItem("cif.pending-generation.owner");
  let finishPost;
  let calls = 0;
  globalThis.fetch = async (_url, options) => {
    calls += 1;
    assert.equal(options.method, "POST");
    return await new Promise((resolve) => { finishPost = () => resolve(response({ id: "healthy" })); });
  };
  const submitted = submitGeneration("/api/generations", { prompt: "healthy" });
  f.recovery.start({ immediate: true });
  await f.tick();
  assert.deepEqual(f.changes, []);
  finishPost();
  await submitted;
  await f.tick();
  assert.deepEqual(f.changes, [false]);
  assert.equal(f.timers.size, 0);
  assert.equal(calls, 1);
});
