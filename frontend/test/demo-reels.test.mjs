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

const reelGeneration = (manage) => ({
  id: "g",
  status: "succeeded",
  workflow_display_name: "Workflow",
  checkpoint_label: "A model",
  checkpoint_id: "checkpoint",
  is_favorite: false,
  recall_available: true,
  image_count: 1,
  artifact_count: 1,
  final_artifact_count: 1,
  expected_width: 1024,
  expected_height: 768,
  accepted_at: "2026-01-01T00:00:00+00:00",
  display_artifact: { id: "artifact", kind: "image", content_url: "/image", thumbnail_url: "/thumb" },
  demo_reel: { can_manage: manage },
});

const reelView = ({ manage = false, ...overrides } = {}) => ({
  label: "Alpha",
  subject: "<subject>",
  slots: [{ id: "s", title: "Portrait", prompt: "{subject} beside a lake" }],
  entries: [{ id: "e", slot_id: "s", checkpoint_label: "A model", state: "ready", generation: reelGeneration(manage) }],
  completed: 1,
  expected: 1,
  status: "complete",
  next_offset: null,
  can_manage: manage,
  ...overrides,
});

test("the reel is the grouped gallery: one group per slot, gallery cards, no captions", () => {
  const user = demoReelGalleryMarkup(reelView());
  assert.match(user, /&lt;subject&gt; beside a lake/);
  assert.match(user, /class="prompt-group demo-reel-slot-group"/);
  assert.match(user, /class="prompt-group-grid" id="demo-reel-slot-s"/);
  assert.match(user, /data-gallery-card="generation" data-generation-id="g"/);
  assert.match(user, /--gallery-media-aspect: 1024 \/ 768/);
  // The images carry no caption strip: every action is a control on the card.
  assert.doesNotMatch(user, /demo-reel-caption|demo-reel-actions|demo-reel-grid|demo-reel-image/);
  for (const action of ["open-photo", "open-detail", "toggle-favorite", "recall"]) {
    assert.match(user, new RegExp(`data-action="${action}" data-generation-id="g"`));
  }
  assert.match(user, /class="download-button" href="\/image" download/);
  assert.match(user, /data-demo-action="rerun" data-demo-id="g"/);
  assert.match(user, /data-demo-action="copy" data-demo-id="g"/);
  for (const action of ["move", "regenerate"]) assert.doesNotMatch(user, new RegExp(`data-demo-action="${action}"`));
  assert.doesNotMatch(user, /delete-generation-button/);
  // A manager moves, regenerates, and deletes; the reel's own actions stay.
  const admin = demoReelGalleryMarkup(reelView({ manage: true }));
  for (const action of ["move", "regenerate"]) assert.match(admin, new RegExp(`data-demo-action="${action}" data-demo-id="g"`));
  assert.match(admin, /class="delete-generation-button"/);
  // An entry without an image yet keeps its place in the grid as a card.
  const failed = demoReelGalleryMarkup(reelView({
    manage: true,
    entries: [{ id: "e", slot_id: "s", checkpoint_label: "A model", state: "failed", error: "Out of video memory", generation: null }],
  }));
  assert.match(failed, /data-gallery-card="demo-entry"/);
  assert.match(failed, /Out of video memory/);
  assert.match(failed, /data-demo-action="retry"/);
  assert.doesNotMatch(demoReelGalleryMarkup(reelView({
    entries: [{ id: "e", slot_id: "s", checkpoint_label: "A model", state: "failed", error: "Out of video memory", generation: null }],
  })), /data-demo-action="retry"/);
});

test("the reel's controls live in the title area as icons and paging follows the gallery", () => {
  const user = demoReelGalleryMarkup(reelView());
  // A person browsing the reel is never offered the shared reel's switches.
  for (const action of ["settings", "retry", "pause", "resume"]) assert.doesNotMatch(user, new RegExp(`data-demo-action="${action}"(?!-)`));
  assert.match(user, /data-demo-action="close"[^>]*aria-label="Close Demo Reel"/);
  const admin = demoReelGalleryMarkup(reelView({ manage: true }));
  for (const [action, label] of [["settings", "Edit Demo Reel"], ["retry", "Retry failed images"], ["pause", "Pause background generation"]]) {
    assert.match(admin, new RegExp(`data-demo-action="${action}"[^>]*aria-label="${label}"`));
  }
  assert.match(demoReelGalleryMarkup(reelView({ manage: true, paused: true })), /data-demo-action="resume"[^>]*aria-pressed="true"/);
  // Paging is a sentinel the dialog watches; the button stays for a browser without
  // IntersectionObserver.
  assert.match(user, /data-demo-reel-sentinel hidden/);
  const paged = demoReelGalleryMarkup(reelView({ next_offset: 2 }));
  assert.match(paged, /data-demo-reel-sentinel[^>]*><button[^>]*data-demo-action="more"/);
  // Slots collapse like prompt groups, and nothing but the header moves.
  const collapsed = demoReelGalleryMarkup({ ...reelView(), collapsed: new Set(["s"]) });
  assert.match(collapsed, /class="prompt-group-collapse" data-demo-action="toggle-slot" data-demo-id="s" aria-expanded="false"/);
  assert.match(collapsed, /id="demo-reel-slot-s" hidden/);
});

test("settings keep random seeds implicit and offer independent prompt save and cancel", () => {
  const markup = demoReelSettingsMarkup({ slots: [{ id: "s", title: "", prompt: "{subject}" }], recipes: { krea2: { source_key: "source", strength: 1, parameters: {} } }, strengths: {}, libraries: [{ key: "krea2", label: "Krea", sources: [{ source_key: "source", label: "Workflow" }] }] }, { source: { interface: { inputs: [{ id: "seed", type: "seed" }, { id: "width", type: "integer", semantic_role: "width", default: 512 }] } } });
  assert.doesNotMatch(markup, /data-demo-parameter="seed"/);
  assert.match(markup, /new random seed/);
  assert.match(markup, /data-demo-slot-handle/);
  assert.match(markup, /data-demo-action="cancel-settings"/);
  assert.match(markup, /data-demo-resolution="width"/);
});

test("a LoRA's strength override belongs to the family that replaces it, and an unconfigured family still opens", () => {
  const context = { libraryKey: "krea2", identity: "lr1_a", label: "Alpha", subject: "Alpha" };
  const details = { source: { interface: { inputs: [] } } };
  const config = (recipes, strengths = {}) => ({ slots: [], recipes, strengths, libraries: [{ key: "krea2", label: "Krea", sources: [{ source_key: "source", label: "Workflow" }] }] });
  const enabled = demoReelSettingsMarkup(config({ krea2: { source_key: "source", strength: 0.8, parameters: {} } }, { krea2: { lr1_a: 1.4 } }), details, context);
  assert.match(enabled, /data-demo-lora-strength[^>]*value="1.4"/);
  assert.match(enabled, /every other LoRA in Krea uses 0\.8/);
  // A family nobody has enabled yet has no strength to replace; opening the editor
  // from a LoRA still has to draw it as off rather than fail.
  const disabled = demoReelSettingsMarkup(config({}), details, context);
  assert.match(disabled, /no images are generated for this family/);
  assert.doesNotMatch(disabled, /data-demo-lora-strength/);
});

test("recipe settings explain the workflow's own controls and mark what differs from its defaults", () => {
  const detail = { interface: { inputs: [
    { id: "prompt", type: "string", label: "Prompt", semantic_role: "positive_prompt", group: "Basic", order: 1 },
    { id: "loras", type: "lora_stack", label: "LoRAs", group: "Basic", order: 2, items: [] },
    { id: "seed", type: "seed", label: "Seed", group: "Basic", order: 3 },
    { id: "checkpoint", type: "choice", label: "Checkpoint", semantic_role: "model", group: "Basic", order: 4, default: "a", choices: [{ value: "a", label: "A" }] },
    { id: "steps", type: "integer", label: "Steps", group: "Basic", order: 5, default: 20, minimum: 1, maximum: 50, description: "More steps cost more time." },
    { id: "clip_skip", type: "integer", label: "CLIP skip", advanced: true, group: "Advanced", order: 6, default: -2 },
  ] } };
  const config = {
    slots: [],
    strengths: { krea2: { lr1: 1.2 } },
    recipes: { krea2: { source_key: "source", strength: 0.85, parameters: { steps: 34, clip_skip: -2 } } },
    libraries: [{ key: "krea2", label: "Krea 2", sources: [{ source_key: "source", label: "Moody" }] }],
  };
  const markup = demoReelSettingsMarkup(config, { source: detail }, { libraryKey: "krea2", identity: "lr1", label: "Alpha", subject: "alpha" }, []);
  // Controls come from the workflow's own renderer, descriptions included.
  assert.match(markup, /data-control-id="steps"/);
  assert.match(markup, /More steps cost more time\./);
  // Checkpoints are chosen by A/B grades, so that control is explained, not offered.
  assert.doesNotMatch(markup, /data-control-id="checkpoint"/);
  assert.match(markup, /Checkpoint<\/strong><span>one image for each A and B checkpoint/);
  assert.match(markup, /data-demo-details="advanced-krea2"/);
  // Only a value that differs from the published default is flagged, and it can be undone.
  assert.match(markup, /class="demo-reel-control is-changed"/);
  assert.match(markup, /Restore workflow default 20/);
  assert.doesNotMatch(markup, /Restore workflow default -2/);
  assert.match(markup, /1 setting differs from the workflow defaults/);
  assert.match(markup, /Krea 2 on \(strength 0\.85\)/);
  // A single LoRA's override sits with the family strength it overrides, not at
  // the top of a dialog that is otherwise shared by every LoRA.
  assert.match(markup, /data-demo-recipe="krea2"[^]*?data-demo-lora-strength[^]*?value="1\.2"/);
  assert.match(markup, /Alpha · strength/);
  assert.doesNotMatch(markup, /data-demo-action="reload-settings"|data-demo-action="slot-insert"|class="demo-reel-subject"/);
  assert.match(markup, /<p class="demo-reel-notice form-error" role="alert" data-demo-settings-error hidden>/);
  assert.doesNotMatch(markup, /Preview: /);
  assert.match(demoReelSettingsMarkup({ ...config, error: "Enter a prompt for every slot." }, { source: detail }, {}, []), /data-demo-settings-error>Enter a prompt for every slot\./);
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
