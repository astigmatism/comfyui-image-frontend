import assert from "node:assert/strict";
import test from "node:test";
import { createPhotoViewerImages, decodePhoto } from "../src/photo-viewer-images.mjs";

const tick = () => new Promise(setImmediate);
const artifact = (id) => ({ id, content_url: `/images/${id}`, width: 2048, height: 1536 });
function fixture() {
  const requests = [];
  const loader = createPhotoViewerImages({ load: (value, signal) => new Promise((resolve, reject) => {
    const image = { src: value.content_url, removeAttribute() { this.src = null; } };
    requests.push({ value, signal, image, resolve: () => resolve(image), reject });
  }) });
  return { loader, requests };
}

test("same artifact reuses its decoded image, while a changed URL replaces it", async () => {
  const { loader, requests } = fixture();
  const first = loader.show(artifact("a")); await tick(); requests[0].resolve(); const image = await first;
  assert.equal(await loader.show(artifact("a")), image);
  assert.equal(requests.length, 1);
  const replacement = loader.show({ ...artifact("a"), content_url: "/images/final" });
  await tick(); assert.equal(image.src, "/images/a"); requests[1].resolve(); await replacement;
  assert.equal(image.src, null); loader.dispose();
});

test("out-of-order loads cannot replace the latest requested image", async () => {
  const { loader, requests } = fixture();
  const old = loader.show(artifact("old")); const rejected = assert.rejects(old, { name: "AbortError" });
  await tick(); const latest = loader.show(artifact("new")); await tick();
  assert.equal(requests[0].signal.aborted, true);
  requests[1].resolve(); await latest; requests[0].resolve(); await rejected;
  assert.equal(requests[0].image.src, null);
  assert.equal(loader.snapshot().current, "new:/images/new"); loader.dispose();
});

test("disposing during decode releases late results and allows a fresh viewer", async () => {
  const { loader, requests } = fixture();
  const old = loader.show(artifact("old")); const rejected = assert.rejects(old, { name: "AbortError" });
  await tick(); loader.dispose();
  const fresh = loader.show(artifact("fresh")); await tick();
  requests[0].resolve(); await rejected; requests[1].resolve(); await fresh;
  assert.equal(requests[0].image.src, null);
  assert.equal(loader.snapshot().current, "fresh:/images/fresh");
  loader.dispose(); assert.deepEqual(loader.snapshot(), { current: null, pending: null, speculative: false });
  assert.equal(requests[1].image.src, null);
});

test("a failed replacement keeps the current image and can be retried", async () => {
  const { loader, requests } = fixture();
  const first = loader.show(artifact("a")); await tick(); requests[0].resolve(); await first;
  const failure = loader.show(artifact("b")); const rejected = assert.rejects(failure, /decode/);
  await tick(); requests[1].reject(new Error("decode")); await rejected;
  assert.equal(requests[0].image.src, "/images/a");
  const retry = loader.show(artifact("b")); await tick(); requests[2].resolve(); await retry;
  assert.equal(requests[0].image.src, null); loader.dispose();
});

test("the previous image is released only after the consumer commits its replacement", async () => {
  const { loader, requests } = fixture();
  const first = loader.show(artifact("a")); await tick(); requests[0].resolve(); let displayed = await first;
  const replacement = loader.show(artifact("b"), (image) => {
    assert.equal(displayed.src, "/images/a");
    displayed = image;
  });
  await tick(); requests[1].resolve(); await replacement;
  assert.equal(displayed, requests[1].image);
  assert.equal(requests[0].image.src, null); loader.dispose();
});

test("a destination invalidated at commit keeps the displayed image intact", async () => {
  const { loader, requests } = fixture();
  const first = loader.show(artifact("a")); await tick(); requests[0].resolve(); await first;
  const replacement = loader.show(artifact("b"), () => false);
  const rejected = assert.rejects(replacement, { name: "AbortError" });
  await tick(); requests[1].resolve(); await rejected;
  assert.equal(requests[0].image.src, "/images/a");
  assert.equal(requests[1].image.src, null);
  assert.equal(loader.snapshot().current, "a:/images/a"); loader.dispose();
});

for (const ready of [false, true]) test(`preloaded image is promoted without a duplicate load (ready=${ready})`, async () => {
  const { loader, requests } = fixture();
  loader.preload(artifact("a")); await tick();
  if (ready) { requests[0].resolve(); await tick(); }
  const shown = loader.show(artifact("a")); await tick();
  if (!ready) requests[0].resolve();
  assert.equal(await shown, requests[0].image);
  assert.equal(requests.length, 1); assert.equal(loader.snapshot().pending, null); loader.dispose();
});

test("preloading retains only one neighbor and cannot displace a demand load", async () => {
  const { loader, requests } = fixture();
  loader.preload(artifact("a")); await tick(); requests[0].resolve(); await tick();
  loader.preload(artifact("b")); await tick();
  assert.equal(requests[0].image.src, null);
  const demand = loader.show(artifact("c")); await tick(); loader.preload(artifact("d"));
  assert.equal(requests.length, 3); assert.equal(requests[1].signal.aborted, true);
  requests[1].resolve(); requests[2].resolve(); await demand; await tick();
  assert.equal(requests[1].image.src, null); loader.dispose();
});

test("speculative failure is quiet and demand retries it", async () => {
  const { loader, requests } = fixture();
  loader.preload(artifact("a")); await tick(); requests[0].reject(new Error("network")); await tick();
  assert.equal(loader.snapshot().pending, null);
  loader.preload(artifact("a")); await tick(); assert.equal(requests.length, 1);
  const demand = loader.show(artifact("a")); await tick(); requests[1].resolve(); await demand; loader.dispose();
});

test("decode cancellation settles even if native decoding has not completed", async (t) => {
  let complete;
  const image = { removeAttribute() { this.src = null; }, decode: () => new Promise((resolve) => { complete = resolve; }) };
  globalThis.Image = function () { return image; };
  t.after(() => { delete globalThis.Image; });
  const controller = new AbortController();
  const pending = decodePhoto(artifact("a"), controller.signal);
  image.onload();
  controller.abort(); await assert.rejects(pending, { name: "AbortError" });
  assert.equal(image.src, null); complete(); await tick(); assert.equal(image.src, null);
});

test("cancelling an unfinished download clears callbacks without starting native decode", async (t) => {
  let decodes = 0;
  const image = { removeAttribute() { this.src = null; }, decode() { decodes++; return Promise.resolve(); } };
  globalThis.Image = function () { return image; };
  t.after(() => { delete globalThis.Image; });
  const controller = new AbortController();
  const pending = decodePhoto(artifact("a"), controller.signal);
  controller.abort(); await assert.rejects(pending, { name: "AbortError" });
  assert.equal(decodes, 0);
  assert.equal(image.src, null);
  assert.equal(image.onload, null);
  assert.equal(image.onerror, null);
});

test("a hung decode times out, clears its source, and releases its timer", async (t) => {
  const image = { removeAttribute() { this.src = null; }, decode: () => new Promise(() => {}) };
  globalThis.Image = function () { return image; };
  t.after(() => { delete globalThis.Image; });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const controller = new AbortController();
  const pending = decodePhoto(artifact("a"), controller.signal);
  image.onload();
  const rejected = assert.rejects(pending, /timed out/);
  t.mock.timers.tick(30_000); await rejected;
  assert.equal(image.src, null);
});
