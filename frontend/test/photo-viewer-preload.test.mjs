import assert from "node:assert/strict";
import test from "node:test";
import { photoViewerPreloadArtifact, createPhotoViewerPreloader } from "../src/photo-viewer-preload.mjs";
const items = [0, 1, 2].map((id) => ({ id, display_artifact: { kind: "image", width: 2048, height: 2048, content_url: `/image/${id}` } }));

test("preloads only the immediate neighbor in the navigation direction", () => {
  assert.equal(photoViewerPreloadArtifact(items, 1), items[2].display_artifact);
  assert.equal(photoViewerPreloadArtifact(items, 1, "newer"), items[0].display_artifact);
  assert.equal(photoViewerPreloadArtifact(items, 2), null);
  assert.equal(photoViewerPreloadArtifact(items, 0, "newer"), null);
  assert.equal(photoViewerPreloadArtifact(items, "missing"), null);
});

test("preload size budget rejects oversized, missing and invalid dimensions", () => {
  for (const size of [{ width: 8192, height: 8192 }, { width: null }, { width: 0 }, { width: -1 }, { width: Infinity }, { height: NaN }]) {
    assert.equal(photoViewerPreloadArtifact([items[0], { ...items[1], display_artifact: { ...items[1].display_artifact, ...size } }], 0), null);
  }
  assert.ok(photoViewerPreloadArtifact([items[0], { ...items[1], display_artifact: { ...items[1].display_artifact, width: 4096, height: 2048 } }], 0));
});

test("short visits cancel speculative timers and repeated progress does not postpone them", () => {
  const calls = [];
  let timer;
  const preloader = createPhotoViewerPreloader((artifact) => calls.push(artifact), {
    setTimer(callback) { timer = callback; return 1; }, clearTimer() { timer = null; },
  });
  preloader.update(items[1].display_artifact);
  const first = timer;
  preloader.update(items[1].display_artifact); assert.equal(timer, first);
  preloader.update(items[2].display_artifact); assert.notEqual(timer, first);
  preloader.dispose(); assert.equal(timer, null);
  assert.ok(calls.every((value) => value === null));
  preloader.update(items[1].display_artifact); timer();
  assert.equal(calls.at(-1), items[1].display_artifact); preloader.dispose();
});
