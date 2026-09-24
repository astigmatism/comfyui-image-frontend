import assert from "node:assert/strict";
import test from "node:test";
import { createThumbnailScheduler } from "../src/thumbnails.mjs";

const tick = () => new Promise(setImmediate);
function fixture(options = {}) {
  const requests = [], revoked = [];
  let serial = 0;
  const scheduler = createThumbnailScheduler({
    load: (url, signal) => new Promise((resolve, reject) => requests.push({ url, signal, resolve, reject })),
    decode: async () => {}, createURL: () => `blob:${++serial}`, revokeURL: (url) => revoked.push(url),
    ...options,
  });
  return { scheduler, requests, revoked };
}

test("visible thumbnails precede preloads and shared subscribers reuse decoded results", async () => {
  const { scheduler, requests, revoked } = fixture({ limit: 1 });
  const preload = scheduler.subscribe("/near", () => {}, { priority: 1 });
  const results = [];
  const visible = scheduler.subscribe("/visible", (result) => results.push(result));
  const duplicate = scheduler.subscribe("/visible", (result) => results.push(result));
  await tick();
  assert.deepEqual(requests.map((request) => request.url), ["/visible"]);
  requests[0].resolve(new Blob(["image"]));
  await tick();
  assert.equal(results.length, 2);
  assert.equal(results[0].url, results[1].url);
  visible(); duplicate();
  const again = scheduler.subscribe("/visible", (result) => results.push(result));
  await tick();
  assert.equal(results[2].url, results[0].url);
  assert.equal(requests.filter((request) => request.url === "/visible").length, 1);
  assert.deepEqual(revoked, []);
  again(); preload(); scheduler.dispose();
  requests[1].resolve(new Blob(["obsolete"]));
  await tick();
  assert.deepEqual(revoked, ["blob:1"]);
  assert.deepEqual(scheduler.snapshot(), { active: 0, queued: 0, retained: 0 });
});

test("idle cache evicts the least recently used entries without evicting active consumers", async () => {
  const { scheduler, requests, revoked } = fixture({ maxIdleEntries: 2 });
  const stops = [];
  for (const url of ["/a", "/b", "/c", "/pinned"]) stops.push(scheduler.subscribe(url, () => {}));
  await tick();
  for (const request of requests) request.resolve(new Blob(["image"]));
  await tick();
  stops[0](); stops[1]();
  const reuse = scheduler.subscribe("/a", () => {});
  await tick();
  reuse(); stops[2]();
  assert.deepEqual(revoked, ["blob:2"]);
  assert.equal(scheduler.snapshot().retained, 3);
  scheduler.dispose();
  assert.deepEqual(new Set(revoked), new Set(["blob:1", "blob:2", "blob:3", "blob:4"]));
});

test("idle bytes and idle expiration are bounded even when no more images are requested", async () => {
  let time = 0, timer;
  const { scheduler, requests, revoked } = fixture({ maxIdleBytes: 6, idleMs: 60,
    now: () => time, setTimer: (callback) => { timer = callback; return 1; }, clearTimer: () => { timer = null; } });
  const first = scheduler.subscribe("/a", () => {});
  const second = scheduler.subscribe("/b", () => {});
  await tick();
  requests.forEach((request) => request.resolve(new Blob(["1234"])));
  await tick();
  first(); time = 10; second();
  assert.deepEqual(revoked, ["blob:1"]);
  assert.equal(scheduler.snapshot().retained, 1);
  time = 70; timer();
  assert.deepEqual(revoked, ["blob:1", "blob:2"]);
  assert.equal(scheduler.snapshot().retained, 0);
  scheduler.dispose();
});

test("obsolete requests cannot publish into a new subscription for the same URL", async () => {
  const { scheduler, requests, revoked } = fixture();
  const results = [];
  const stop = scheduler.subscribe("/a", (value) => results.push(["old", value]));
  await tick(); stop();
  scheduler.subscribe("/a", (value) => results.push(["new", value]));
  await tick();
  assert.equal(requests[0].signal.aborted, true);
  requests[0].resolve(new Blob(["old"]));
  requests[1].resolve(new Blob(["new"]));
  await tick();
  assert.equal(results.length, 1);
  assert.equal(results[0][0], "new");
  scheduler.dispose();
  assert.deepEqual(revoked, ["blob:1"]);
});

test("decode errors revoke invalid blobs and can be retried without duplicate work", async () => {
  let fail = true;
  const { scheduler, requests, revoked } = fixture({ decode: async () => { if (fail) throw new Error("Invalid image"); } });
  const results = [];
  scheduler.subscribe("/a", (result) => results.push(result));
  await tick(); requests[0].resolve(new Blob(["invalid"])); await tick();
  assert.match(results[0].error.message, /Invalid/);
  assert.deepEqual(revoked, ["blob:1"]);
  fail = false;
  scheduler.retry("/a"); scheduler.retry("/a");
  await tick();
  assert.equal(requests.length, 2);
  requests[1].resolve(new Blob(["valid"])); await tick();
  assert.equal(results[1].url, "blob:2");
  scheduler.dispose();
});

test("disposal aborts active work and removes queued and cached work", async () => {
  const { scheduler, requests } = fixture({ limit: 1 });
  scheduler.subscribe("/a", () => assert.fail("Disposed listener called"));
  scheduler.subscribe("/b", () => assert.fail("Disposed listener called"));
  await tick(); scheduler.dispose();
  assert.equal(requests[0].signal.aborted, true);
  requests[0].resolve(new Blob(["obsolete"])); await tick();
  assert.deepEqual(scheduler.snapshot(), { active: 0, queued: 0, retained: 0 });
  assert.throws(() => scheduler.subscribe("/c", () => {}), /disposed/);
});


test("cancellation during decode revokes the blob and suppresses late completion", async () => {
  let finishDecode;
  const { scheduler, requests, revoked } = fixture({ decode: () => new Promise((resolve) => { finishDecode = resolve; }) });
  const stop = scheduler.subscribe("/a", () => assert.fail("Cancelled image was published"));
  await tick(); requests[0].resolve(new Blob(["image"])); await tick();
  stop();
  assert.deepEqual(revoked, ["blob:1"]);
  finishDecode(); await tick();
  assert.deepEqual(scheduler.snapshot(), { active: 0, queued: 0, retained: 0 });
  scheduler.dispose();
});
