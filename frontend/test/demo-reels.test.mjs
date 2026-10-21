import assert from "node:assert/strict";
import test from "node:test";
import { demoReelGalleryMarkup, demoReelPath, demoReelSettingsMarkup, expandDemoReelPrompt, moveDemoReelSlot } from "../src/demo-reels.mjs";
import { createActivityReporter } from "../src/user-activity.mjs";

test("slot prompts expand every subject placeholder literally and slots reorder without edits", () => {
  assert.equal(expandDemoReelPrompt("{subject}, portrait of {subject}", "$& Alpha"), "$& Alpha, portrait of $& Alpha");
  const slots = [{ id: "a", prompt: "first" }, { id: "b", prompt: "second" }];
  assert.deepEqual(moveDemoReelSlot(slots, "b", 0), [slots[1], slots[0]]);
  assert.deepEqual(slots.map((slot) => slot.id), ["a", "b"]);
  assert.equal(moveDemoReelSlot(slots, "b", -1), slots);
  assert.equal(demoReelPath("krea2", "lr1_a"), "/api/demo-reels/krea2/lr1_a");
});

test("shared gallery preserves slot order, escapes prompts, and grants removal only to admins", () => {
  const generation = { id: "g", is_favorite: false, display_artifact: { content_url: "/image", thumbnail_url: "/thumb" } };
  const view = { label: "Alpha", subject: "<subject>", slots: [{ id: "s", title: "Portrait", prompt: "{subject}" }], entries: [{ id: "e", slot_id: "s", checkpoint_label: "A model", state: "ready", generation }], completed: 1, expected: 1, status: "complete", next_offset: null };
  const user = demoReelGalleryMarkup(view);
  assert.match(user, /&lt;subject&gt;/);
  for (const action of ["photo", "details", "recall", "favorite", "copy"]) assert.match(user, new RegExp(`data-demo-action="${action}"`));
  for (const action of ["delete", "move", "regenerate"]) assert.doesNotMatch(user, new RegExp(`data-demo-action="${action}"`));
  assert.match(demoReelGalleryMarkup({ ...view, can_manage: true }), /data-demo-action="regenerate"/);
});

test("settings keep random seeds implicit and offer independent prompt save and cancel", () => {
  const markup = demoReelSettingsMarkup({ slots: [{ id: "s", title: "", prompt: "{subject}" }], recipes: { krea2: { source_key: "source", strength: 1, parameters: {} } }, strengths: {}, libraries: [{ key: "krea2", label: "Krea", sources: [{ source_key: "source", label: "Workflow" }] }] }, { source: { interface: { inputs: [{ id: "seed", type: "seed" }, { id: "width", type: "integer", semantic_role: "width", default: 512 }] } } });
  assert.doesNotMatch(markup, /data-demo-parameter="seed"/);
  assert.match(markup, /new random seed/);
  assert.match(markup, /data-demo-slot-handle/);
  assert.match(markup, /data-demo-action="cancel-settings"/);
  assert.match(markup, /data-demo-resolution="width"/);
});

test("settings report shared generation progress and pause state", () => {
  const config = { slots: [], recipes: {}, progress: { completed: 2, expected: 8, states: { running: 1 } } };
  assert.match(demoReelSettingsMarkup(config), /2 of 8 images complete · generating/);
  assert.match(demoReelSettingsMarkup({ ...config, paused: true }), /2 of 8 images complete · paused/);
});

test("activity sends immediately after quiet time, coalesces bursts, and ignores unauthenticated input", async () => {
  let now = 0, sends = 0, ready = false;
  const scheduled = new Map(); let next = 0;
  const reporter = createActivityReporter({ send: async () => { sends++; }, ready: () => ready, now: () => now, schedule: (fn) => { scheduled.set(++next, fn); return next; }, cancel: (id) => scheduled.delete(id) });
  reporter.activity(); assert.equal(sends, 0);
  ready = true; reporter.activity(); await Promise.resolve(); assert.equal(sends, 1);
  reporter.activity(); reporter.activity(); assert.equal(sends, 1);
  now = 5000;
  for (const [id, fn] of [...scheduled]) { scheduled.delete(id); await fn(); }
  assert.equal(sends, 2);
  now = 305000; reporter.activity(); await Promise.resolve(); assert.equal(sends, 3);
  reporter.dispose();
});
