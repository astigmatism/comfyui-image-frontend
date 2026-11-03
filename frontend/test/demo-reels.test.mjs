import assert from "node:assert/strict";
import test from "node:test";
import { demoReelGalleryMarkup, demoReelHeaderMarkup, demoReelPath, demoReelSettingsMarkup, expandDemoReelPrompt, moveDemoReelSlot } from "../src/demo-reels.mjs";
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
  // The header names the slot, draws the rule, and counts the images; the slot's
  // prompt, which settings already show in full, is a tooltip rather than a line.
  assert.match(user, /class="demo-reel-slot-copy" title="&lt;subject&gt; beside a lake">Portrait<\/span>/);
  assert.match(user, /class="prompt-group-count">1 image</);
  const header = user.slice(user.indexOf("prompt-group-header"), user.indexOf("prompt-group-grid"));
  assert.doesNotMatch(header.replace(/title="[^"]*"/, ""), /beside a lake/);
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
  // An entry without an image yet keeps its place in the grid as a card, and it is
  // the idle worker's own business to retry it: nobody is offered a button for that.
  const failed = demoReelGalleryMarkup(reelView({
    manage: true,
    entries: [{ id: "e", slot_id: "s", checkpoint_label: "A model", state: "failed", error: "Out of video memory", generation: null }],
  }));
  assert.match(failed, /data-gallery-card="demo-entry"/);
  assert.match(failed, /Out of video memory/);
  assert.doesNotMatch(failed, /data-demo-action="retry"/);
});

test("the title area carries the reel's one control and paging follows the gallery", () => {
  const actions = (markup) => {
    const header = markup.slice(markup.indexOf("<header class="), markup.indexOf("</header>") + 9);
    return [...header.matchAll(/data-demo-action="([a-z-]+)"/g)].map((match) => match[1]);
  };
  const user = demoReelGalleryMarkup(reelView());
  assert.deepEqual(actions(user), ["close"]);
  // Retrying failed images and pausing the background worker belong to the process
  // that produces them, so the reel that browses the results offers neither - and
  // pausing stays available where the reel is configured.
  assert.doesNotMatch(user, /data-demo-action="(retry|pause|resume)"/);
  assert.match(user, /data-demo-action="close"[^>]*aria-label="Close Demo Reel"/);
  const admin = demoReelGalleryMarkup(reelView({ manage: true }));
  assert.deepEqual(actions(admin), ["settings", "close"]);
  assert.match(admin, /data-demo-action="settings"[^>]*aria-label="Edit Demo Reel"/);
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

test("the reel sizes its cards from inside the dialog, beside the title rather than among the actions", () => {
  const header = (scale) => demoReelHeaderMarkup(reelView({ manage: true }), scale);
  // The control is the LoRA library's own: same range, step, accessible name, value, and
  // read-out, so the two dialogs cannot drift apart. 100% means this browser's Gallery scale.
  assert.match(header(100), /<label class="lm-scale-control"><span>Thumbnail size<\/span><input type="range" min="50" max="150" step="5" value="100" data-demo-reel-scale aria-label="Demo Reel thumbnail size" aria-valuetext="100%" \/><output data-demo-reel-scale-value>100%<\/output><\/label>/);
  assert.match(header(130), /value="130" data-demo-reel-scale aria-label="Demo Reel thumbnail size" aria-valuetext="130%" \/><output data-demo-reel-scale-value>130%<\/output>/);
  // The slider is not an action: the cluster beside it stays the reel icon and Close, which is
  // what the gallery's own header count asserts.
  const markup = header(100);
  assert.ok(markup.indexOf("data-demo-reel-scale") < markup.indexOf("lm-header-actions"));
  const actions = markup.slice(markup.indexOf("lm-header-actions"));
  assert.deepEqual([...actions.matchAll(/data-demo-action="([a-z-]+)"/g)].map((match) => match[1]), ["settings", "close"]);
  // The default is the Gallery scale itself, so a reel nobody sized draws as it always did.
  assert.match(demoReelHeaderMarkup(reelView()), /value="100"/);
});

test("a dialog closes with the application's glyph, not a multiplication sign", () => {
  // Every dialog header this change touches draws the stroked close glyph the icons beside it
  // use, sized and stroked by the same rule, instead of a text character at text size.
  const close = (markup) => markup.slice(markup.indexOf("lm-close-button"), markup.indexOf("lm-close-button") + 400);
  for (const markup of [demoReelGalleryMarkup(reelView()), demoReelGalleryMarkup(reelView({ manage: true }))]) {
    assert.match(close(markup), /aria-label="Close Demo Reel" title="Close Demo Reel"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" \/><\/svg>/);
    assert.doesNotMatch(close(markup), /×/);
  }
  const settings = demoReelSettingsMarkup({ slots: [], recipes: {}, strengths: {}, libraries: [] }, {}, {}, []);
  assert.match(settings, /class="icon-button lm-close-button" data-demo-action="cancel-settings" aria-label="Close Demo Reel settings" title="Close Demo Reel settings"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" \/><\/svg>/);
  assert.doesNotMatch(close(settings), /×/);
});

test("settings keep random seeds implicit and offer independent prompt save and cancel", () => {
  const markup = demoReelSettingsMarkup({ slots: [{ id: "s", title: "", prompt: "{subject}" }], recipes: { krea2: { source_key: "source", strength: 1, parameters: {} } }, strengths: {}, libraries: [{ key: "krea2", label: "Krea", sources: [{ source_key: "source", label: "Workflow" }] }] }, { source: { interface: { inputs: [{ id: "seed", type: "seed" }, { id: "width", type: "integer", semantic_role: "width", default: 512 }] } } });
  assert.doesNotMatch(markup, /data-demo-parameter="seed"/);
  assert.match(markup, /new random seed/);
  assert.match(markup, /data-demo-slot-handle/);
  assert.match(markup, /data-demo-action="cancel-settings"/);
  assert.match(markup, /data-demo-resolution="width"/);
});

test("every action the settings markup emits is one the editor dispatches", () => {
  const config = {
    slots: [{ id: "a", title: "Portrait", prompt: "{subject} portrait" }, { id: "b", title: "", prompt: "{subject} beside a lake" }],
    recipes: {},
    strengths: {},
    libraries: [],
  };
  const markup = demoReelSettingsMarkup(config, {}, {}, []);
  // The dispatcher in createDemoReels matches on the emitted name, so a header drawing
  // data-demo-action="slot-slot-up" renders a button that does nothing when clicked.
  const actions = [...new Set([...markup.matchAll(/data-demo-action="([a-z-]+)"/g)].map((match) => match[1]))].sort();
  assert.deepEqual(actions, ["cancel-settings", "slot-add", "slot-delete", "slot-down", "slot-up"]);
  // Each slot gets reorder buttons, disabled at the ends it cannot pass, and one delete.
  assert.match(markup, /data-demo-action="slot-up" data-demo-id="a" aria-label="Move slot 1 up" title="Move slot 1 up" disabled>/);
  assert.match(markup, /data-demo-action="slot-down" data-demo-id="a" aria-label="Move slot 1 down" title="Move slot 1 down">/);
  assert.match(markup, /data-demo-action="slot-down" data-demo-id="b" aria-label="Move slot 2 down" title="Move slot 2 down" disabled>/);
  assert.equal([...markup.matchAll(/data-demo-action="slot-delete"/g)].length, config.slots.length);
  // Duplicating a slot was an option nobody used; it is gone rather than merely hidden.
  assert.doesNotMatch(markup, /slot-slot-|data-demo-action="slot-duplicate"|Duplicate slot/);
});

test("settings pick one checkpoint for every LoRA picture and say what it does not change", () => {
  const details = { source: { interface: { inputs: [] } } };
  const config = (thumbnail_checkpoint_id) => ({
    slots: [],
    recipes: { krea2: { source_key: "source", strength: 1, parameters: {} } },
    strengths: {},
    thumbnail_checkpoint_id,
    thumbnail_checkpoint_choices: [{ id: "lr1_a", label: "Flux Dev" }, { id: "lr1_b", label: "Realvis" }],
    libraries: [{ key: "krea2", label: "Krea", sources: [{ source_key: "source", label: "Workflow" }] }],
  });

  const automatic = demoReelSettingsMarkup(config(null), details);
  assert.match(automatic, /<option value="" selected>Automatic · highest-ranked checkpoint<\/option>/);
  assert.match(automatic, /<option value="lr1_a" >Flux Dev<\/option>/);
  assert.match(automatic, /LoRA pictures use the highest-ranked checkpoint, unless a first-slot image is favorited/);

  const chosen = demoReelSettingsMarkup(config("lr1_b"), details);
  assert.match(chosen, /<option value="lr1_b" selected>Realvis<\/option>/);
  assert.doesNotMatch(chosen, /<option value="" selected>/);
  assert.match(chosen, /LoRA pictures use Realvis/);
  // The choice moves pictures only: it is stated next to what keeps happening regardless.
  assert.match(chosen, /A checkpoint ticked there gets one image per prompt slot/);
  assert.match(chosen, /A favorited first-slot image always supplies that LoRA's picture instead/);

  // A saved checkpoint that can no longer be chosen — downgraded out of A/B, or gone from the
  // installed workflows — is reported as what it is, not quietly shown as Automatic.
  const unavailable = demoReelSettingsMarkup(config("lr1_gone"), details);
  assert.match(unavailable, /<option value="lr1_gone" selected>lr1_gone · no longer available<\/option>/);
  assert.doesNotMatch(unavailable, /Automatic · highest-ranked checkpoint/);
  assert.match(unavailable, /LoRA pictures use lr1_gone, which is no longer available/);
});

test("settings say which checkpoints are developed and let the list be chosen", () => {
  const details = { source: { interface: { inputs: [] } } };
  const choices = [
    { id: "lr1_a", label: "Moody Krea2 V5 BF16", tier: "A", library_label: "Krea 2", images: 6, note: null },
    { id: "lr1_b", label: "Moody Krea2 V4 INT8", tier: "A", library_label: "Krea 2", images: 6, note: null },
    { id: "lr1_c", label: "Flux Realism Engine", tier: "B", library_label: "Flux Dev", images: 0, note: null },
  ];
  const settings = (qualifying_checkpoint_ids, thumbnail_checkpoint_id = null) => demoReelSettingsMarkup({
    slots: [{ id: "s", title: "", prompt: "{subject}" }],
    recipes: { krea2: { source_key: "source", strength: 1, parameters: {} } },
    strengths: {},
    rating_username: "admin",
    thumbnail_checkpoint_id,
    thumbnail_checkpoint_choices: choices,
    qualifying_checkpoint_ids,
    libraries: [{ key: "krea2", label: "Krea", sources: [{ source_key: "source", label: "Workflow" }] }],
  }, details);

  // The automatic state is a meaning rather than an empty list, it says so, and it has nothing to
  // restore because there is nothing to restore to.
  const automatic = settings(null);
  assert.match(automatic, /Develop these checkpoints/);
  assert.match(automatic, /3 of 3 selected · 3 images per LoRA/);
  assert.match(automatic, /Automatic · every checkpoint admin ranks A or B, including any graded later/);
  assert.match(automatic, /data-demo-action="checkpoint-auto"[^>]*hidden/);
  assert.equal([...automatic.matchAll(/data-demo-checkpoint="[^"]+"[^>]*checked/g)].length, 3);

  // A chosen list is named as one, its count follows its ticks, and the way back is offered.
  const chosen = settings(["lr1_a", "lr1_c"]);
  assert.match(chosen, /2 of 3 selected · 2 images per LoRA/);
  assert.match(chosen, /Your choice · only the checkpoints ticked below are developed/);
  assert.match(chosen, /Restore automatic · every A and B checkpoint/);
  assert.match(chosen, /data-demo-checkpoint="lr1_b" aria-label="Moody Krea2 V4 INT8, tier A, Krea 2" \/>/);
  assert.match(chosen, /row is-off">[\s\S]*?Flux Realism Engine/);
  // A ticked row says what it already has, including having nothing yet — which is a queue
  // position, not a failure.
  assert.match(chosen, /6 images/);
  assert.match(chosen, /nothing yet · next in the queue/);
  assert.match(chosen, /class="demo-reel-checkpoint-tier" data-tier="B">B</);
  assert.match(chosen, /class="demo-reel-checkpoint-family">Flux Dev</);
  const actions = [...new Set([...chosen.matchAll(/data-demo-action="(checkpoint-[a-z]+)"/g)].map((match) => match[1]))].sort();
  assert.deepEqual(actions, ["checkpoint-all", "checkpoint-auto", "checkpoint-none"]);

  // An empty list is the destructive one, and reads that way before anything is saved.
  const none = settings([]);
  assert.match(none, /0 of 3 selected · 0 images per LoRA/);
  assert.match(none, /demo-reel-checkpoints-state is-empty/);
  assert.match(none, /every existing Demo Reel image is removed for everyone/);

  // A row the server knows is producing nothing says why, in its own words rather than the
  // frontend guessing which of two reasons it is.
  const blocked = demoReelSettingsMarkup({
    slots: [], recipes: {}, strengths: {}, thumbnail_checkpoint_choices: [
      { id: "lr1_a", label: "Moody Krea2 V5 BF16", tier: "A", library_label: "Krea 2", images: 6, note: null },
      { id: "lr1_d", label: "Flux Dev 1.1 Hires", tier: "B", library_label: "Flux Dev", images: 0, note: "not in the selected workflow" },
    ],
    libraries: [],
  }, details);
  assert.match(blocked, /demo-reel-checkpoint-note is-problem">not in the selected workflow</);
  assert.doesNotMatch(blocked, /nothing yet/);
  // Nothing graded A or B is nothing to choose, and the grading choice below already says that.
  assert.doesNotMatch(
    demoReelSettingsMarkup({ slots: [], recipes: {}, strengths: {}, thumbnail_checkpoint_choices: [], libraries: [] }, details),
    /Develop these checkpoints/,
  );

  // A checkpoint left out has no image to be a picture, so the picker stops offering it and the
  // facts say which of the two reasons a saved choice has for not working.
  const leftOut = settings(["lr1_a"], "lr1_c");
  assert.match(leftOut, /<option value="lr1_c" selected>Flux Realism Engine · not being developed<\/option>/);
  assert.match(leftOut, /LoRA pictures use Flux Realism Engine, which is not being developed/);
  assert.match(leftOut, /demo-reel-checkpoint-picture" data-stale>/);
  assert.match(settings(["lr1_c"], "lr1_c"), /<option value="lr1_c" selected>Flux Realism Engine<\/option>/);
  assert.match(settings(["lr1_c"], "lr1_c"), /demo-reel-checkpoint-picture">/);
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
    thumbnail_checkpoint_choices: [{ id: "lr1_a", label: "Moody V5", tier: "A", library_label: "Krea 2", images: 1, note: null }],
    libraries: [{ key: "krea2", label: "Krea 2", sources: [{ source_key: "source", label: "Moody" }] }],
  };
  const markup = demoReelSettingsMarkup(config, { source: detail }, { libraryKey: "krea2", identity: "lr1", label: "Alpha", subject: "alpha" }, []);
  // Controls come from the workflow's own renderer, descriptions included.
  assert.match(markup, /data-control-id="steps"/);
  assert.match(markup, /More steps cost more time\./);
  // Checkpoints are chosen on their own card, so that control is explained, not offered.
  assert.doesNotMatch(markup, /data-control-id="checkpoint"/);
  assert.match(markup, /Checkpoint<\/strong><span>one image for each checkpoint ticked below/);
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
