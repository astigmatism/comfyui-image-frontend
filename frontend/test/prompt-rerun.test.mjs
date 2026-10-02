import assert from "node:assert/strict";
import test from "node:test";
import {
  defaultRerunFolderName,
  promptRerunDraft,
  promptRerunMarkup,
  promptRerunPlannedTotal,
  promptRerunRequest,
  promptRerunSummaryMarkup,
  promptRerunSubmitLabel,
  retargetRerunDraft,
  updateRerunDraft,
  validatePromptRerunDraft,
} from "../src/prompt-rerun.mjs";
import { rerunToolState } from "../src/gallery-selection.mjs";
import { promptRerunProgressMarkup } from "../src/prompt-rerun-progress.mjs";

const revision = { publication_id: "p", workflow_sha256: "w", api_sha256: "a", manifest_sha256: "m" };
const contract = {
  inputs: [
    { id: "prompt", type: "string", semantic_role: "positive_prompt", default: "" },
    { id: "width", type: "integer", semantic_role: "width", default: 1024, minimum: 16, maximum: 2048, step: 8 },
    { id: "height", type: "integer", semantic_role: "height", default: 1024, minimum: 16, maximum: 2048, step: 8 },
    { id: "seed", type: "seed", semantic_role: "seed", default: 0, default_mode: "random", minimum: 0, maximum: 100 },
    { id: "checkpoint", type: "choice", semantic_role: "checkpoint", default: "a", choices: [{ value: "a", label: "Alpha <A>" }, { value: "b", label: "Beta" }, { value: "c", label: "Gamma" }] },
    { id: "loras", type: "lora_stack", label: "LoRAs", minimum: 0, maximum: 2, step: 0.05, items: [{ id: "x", label: "Ex" }, { id: "y", label: "Why" }], default: [{ id: "x", strength: 0 }, { id: "y", strength: 0 }] },
    { id: "steps", type: "integer", default: 20 },
  ],
};
const source = { source_key: "moody", display_name: "Moody", available: true, revision, interface: contract };
const preview = { generation_count: 5, prompt_count: 4, unique_prompt_count: 3, duplicate_count: 1, skipped_count: 1, prompts: [{ generation_id: "g1", excerpt: "a <b>", width: 512, height: 768 }] };
const panel = {
  source,
  parameters: { prompt: "panel prompt", width: 832, height: 1216, steps: 30, checkpoint: "b", loras: [{ id: "y", strength: 0.8 }, { id: "x", strength: 0 }], seed: "random" },
  selections: { checkpoint: ["b", "c"] },
  quantity: 2,
  collectionId: "folder-1",
  now: Date.UTC(2026, 0, 5, 12),
};

test("Creative Direction is an isolated opt-in refinement draft with separate prompt and image counts", () => {
  const assistant = { creativeDirection: "cinematic light", mode: "create", think: false,
    defaultInstructions: { refine: "Default refine" }, instructionOverrides: { refine: "Keep the subject" } };
  let draft = promptRerunDraft({ ...panel, promptAssistant: assistant });
  assert.equal(draft.refine, false);
  assert.equal(draft.creativeDirection, "cinematic light");
  assert.equal(draft.instructions, "Keep the subject");
  assert.equal(draft.think, false);
  assert.equal(promptRerunRequest(draft, {}).refinement, undefined);
  draft = updateRerunDraft(draft, "rerun_refine", true);
  assert.equal(draft.sectionOpen["creative-direction"], true);
  draft = updateRerunDraft(draft, "rerun_direction", "at dusk");
  draft = updateRerunDraft(draft, "rerun_instructions", "Preserve composition");
  assert.equal(assistant.creativeDirection, "cinematic light");
  assert.equal(assistant.instructionOverrides.refine, "Keep the subject");
  assert.deepEqual(promptRerunRequest(draft, {}).refinement, {
    creative_direction: "at dusk", instructions: "Preserve composition", think: false,
  });
  assert.match(promptRerunSummaryMarkup(draft, preview), /Refine 3 prompts · Generate up to 12 images/);
  assert.equal(promptRerunSubmitLabel(draft, preview), "Refine & Queue");
  assert.ok(validatePromptRerunDraft({ ...draft, creativeDirection: " " }, preview).direction);
  assert.ok(validatePromptRerunDraft({ ...draft, instructions: " " }, preview).instructions);
  assert.match(promptRerunMarkup({ ...draft, instructions: " " }, preview), /class="prompt-preprocessor" open/);
  const markup = promptRerunMarkup(draft, preview);
  assert.match(markup, /Thinking mode/);
  assert.doesNotMatch(markup, /New Prompt from Creative Direction|name="assistant-mode"/);
  assert.equal(retargetRerunDraft(draft, source).creativeDirection, "at dusk");
});

test("rerun progress separates prompt completion from image completion and escapes retained text", () => {
  const run = { id: "r", status: "processing", prompt_count: 2, queued_count: 3, planned_count: 6,
    counts: { waiting: 0, refining: 1, ready: 0, finished: 1, failed: 0, cancelled: 0 },
    items: [{ id: "g", status: "refining", original_prompt: "<img src=x>", prompt: "refined <b>" }] };
  const markup = promptRerunProgressMarkup([run]);
  assert.match(markup, /Stop remaining/);
  assert.match(markup, /3 of 6 images queued/);
  assert.match(markup, /&lt;img src=x&gt;/);
  const completed = promptRerunProgressMarkup([{ ...run, status: "completed" }]);
  assert.match(completed, /All prompts processed/);
  assert.match(completed, /Queued images finish independently/);
  assert.doesNotMatch(completed, /Stop remaining/);
});

test("the draft starts from the control panel and does not alias its values", () => {
  const draft = promptRerunDraft(panel);
  assert.equal(draft.sourceKey, "moody");
  assert.equal(draft.values.width, 832);
  assert.deepEqual(draft.selections, { checkpoint: ["b", "c"] });
  assert.equal(draft.quantity, 2);
  assert.equal(draft.parentCollectionId, "folder-1");
  assert.match(draft.folderName, /^Prompt Re-run 2026-01-0[45]$/);
  draft.values.loras[0].strength = 2;
  assert.equal(panel.parameters.loras[0].strength, 0.8);
  assert.equal(defaultRerunFolderName(source, { checkpoint: ["a"] }), "Alpha <A> re-run");
});

test("the planned total multiplies prompts, checkpoints and the count per prompt", () => {
  const draft = promptRerunDraft(panel);
  assert.equal(promptRerunPlannedTotal(draft, preview), 3 * 2 * 2);
  assert.equal(promptRerunPlannedTotal({ ...draft, skipDuplicates: false }, preview), 4 * 2 * 2);
  assert.equal(promptRerunPlannedTotal(draft, null), 0);
});

test("the request omits the panel prompt and seed and sends one variant per checkpoint", () => {
  const draft = promptRerunDraft(panel);
  const body = promptRerunRequest(draft, { generation_ids: ["g1"], collection_ids: ["f1"] });
  assert.deepEqual(body.generation_ids, ["g1"]);
  assert.deepEqual(body.collection_ids, ["f1"]);
  assert.equal(body.parent_collection_id, "folder-1");
  assert.equal(body.source_key, "moody");
  assert.deepEqual(body.revision, revision);
  assert.equal(Object.hasOwn(body.parameters, "prompt"), false);
  assert.equal(Object.hasOwn(body.parameters, "seed"), false);
  assert.equal(body.parameters.steps, 30);
  assert.deepEqual(body.parameters.loras, [{ id: "y", strength: 0.8 }, { id: "x", strength: 0 }]);
  assert.deepEqual(body.model_variants, [{ checkpoint: "b" }, { checkpoint: "c" }]);
  assert.equal(body.quantity, 2);
  assert.equal(body.seed_mode, "random");
  assert.equal(body.keep_original_resolution, false);
  assert.equal(body.skip_duplicates, true);
});

test("form updates edit only the draft and keep dependent settings consistent", () => {
  let draft = promptRerunDraft(panel);
  draft = updateRerunDraft(draft, "rerun_seed", "original");
  assert.equal(draft.seedMode, "original");
  assert.equal(draft.quantity, 1);
  draft = updateRerunDraft(draft, "rerun_keep_resolution", true);
  assert.equal(draft.keepOriginalResolution, true);
  draft = updateRerunDraft(draft, "rerun_skip_duplicates", false);
  assert.equal(draft.skipDuplicates, false);
  draft = updateRerunDraft(draft, "rerun_folder", "My folder");
  assert.equal(draft.folderName, "My folder");
  assert.equal(panel.parameters.width, 832);
});

test("validation explains every reason submit is blocked", () => {
  let draft = promptRerunDraft(panel);
  assert.deepEqual(validatePromptRerunDraft(draft, preview), {});
  draft = updateRerunDraft(draft, "rerun_folder", "   ");
  draft.values.width = 1001;
  draft.selections.checkpoint = [];
  draft.values.loras[0].strength = 3;
  const errors = validatePromptRerunDraft(draft, { ...preview, unique_prompt_count: 0 });
  assert.deepEqual(Object.keys(errors).sort(), ["checkpoints", "folder", "loras", "prompts", "width"]);
  const keep = promptRerunDraft(panel);
  keep.values.width = 1001;
  assert.equal(validatePromptRerunDraft(updateRerunDraft(keep, "rerun_keep_resolution", true), preview).width, undefined);
  const large = updateRerunDraft(promptRerunDraft(panel), "rerun_quantity", "16");
  assert.match(validatePromptRerunDraft(large, { ...preview, unique_prompt_count: 9 }).total, /288 planned generations exceeds the 256-item limit/);
});

test("switching source reconciles saved values and drops unsupported seed reuse", () => {
  const plain = { source_key: "plain", available: true, revision, interface: { inputs: [contract.inputs[0], contract.inputs[1], contract.inputs[2]] } };
  let draft = updateRerunDraft(promptRerunDraft(panel), "rerun_seed", "original");
  draft = retargetRerunDraft(draft, plain, { width: 640, unknown: 1 });
  assert.equal(draft.sourceKey, "plain");
  assert.equal(draft.values.width, 640);
  assert.equal(draft.values.height, 1024);
  assert.equal(Object.hasOwn(draft.values, "unknown"), false);
  assert.equal(draft.seedMode, "random");
  assert.deepEqual(promptRerunRequest(draft, {}).model_variants, [{}]);
});

test("markup escapes text, reflects state and disables submit until valid", () => {
  const draft = promptRerunDraft(panel);
  const loading = promptRerunMarkup(draft, null, { sources: [source] });
  assert.match(loading, /data-rerun-action="submit" disabled/);
  const html = promptRerunMarkup({ ...draft, folderName: "<script>" }, preview, { sources: [source, { source_key: "text", output_kind: "text", display_name: "Text" }], parentName: "A & B" });
  assert.match(html, /value="&lt;script&gt;"/);
  assert.doesNotMatch(html, /a &lt;b&gt;|rerun-prompts|name="rerun_checkpoint"|name="rerun_source"/);
  assert.match(html, /id="rerun-workflow-source"/);
  assert.match(html, /data-lora-open data-control-context="rerun"/);
  assert.match(html, /data-resolution-grid/);
  assert.match(html, /data-resolution-preset/);
  assert.match(html, /id="rerun-control-width"/);
  assert.match(html, /id="rerun-control-section-resolution-trigger"[^>]+aria-expanded="false"/);
  assert.match(html, /Created inside A &amp; B/);
  assert.match(html, /3 prompts from 5 generations · 1 duplicate skipped · 1 without a prompt/);
  assert.match(html, /Queue 12 generations/);
  assert.doesNotMatch(html, /value="text"/, "text sources are not offered");
  assert.doesNotMatch(html, /data-rerun-action="submit" disabled/);
  const blocked = promptRerunMarkup(updateRerunDraft(draft, "rerun_quantity", "16"), { ...preview, unique_prompt_count: 20 }, { sources: [source] });
  assert.match(blocked, /data-rerun-action="submit" disabled/);
  assert.match(blocked, /exceeds the 256-item limit/);
});

test("the toolbar tool explains when Prompt Re-run is unavailable", () => {
  assert.deepEqual(rerunToolState({ count: 2 }), { disabled: false, title: "Re-run the selected prompts with new settings" });
  assert.equal(rerunToolState({ count: 0 }).disabled, true);
  assert.deepEqual(rerunToolState({ count: 1 }, "Turn off auto generation to re-run prompts"), { disabled: true, title: "Turn off auto generation to re-run prompts" });
  assert.equal(rerunToolState({ count: 1 }, null, true).disabled, true);
});


test("rerun drafts own section, tier, strength, image and recent-resolution state", () => {
  const initial = { ...panel, checkpointTiers: { moody: { checkpoint: { preferred: ["a"] } } }, loraMemory: { loras: { x: 0.7 } }, loraImages: { loras: { x: { image_url: "shared.png" } } }, recentResolutions: [{ width: 512, height: 768 }] };
  const draft = promptRerunDraft(initial);
  draft.checkpointTiers.moody.checkpoint.preferred.push("b");
  draft.loraMemory.loras.x = 1.2;
  draft.loraImages.loras.x.image_url = "draft.png";
  draft.recentResolutions[0].width = 1024;
  assert.deepEqual(initial.checkpointTiers.moody.checkpoint.preferred, ["a"]);
  assert.equal(initial.loraMemory.loras.x, 0.7);
  assert.equal(initial.loraImages.loras.x.image_url, "shared.png");
  assert.equal(initial.recentResolutions[0].width, 512);
  assert.deepEqual(draft.sectionOpen, {});
});

test("rerun controls reveal errors and respect original-resolution and seed modes", () => {
  const draft = promptRerunDraft(panel);
  draft.values.width = 1001;
  const invalid = promptRerunMarkup(draft, preview);
  assert.match(invalid, /id="rerun-control-section-resolution-trigger"[^>]+aria-expanded="true"/);
  const original = promptRerunMarkup(updateRerunDraft({ ...draft, keepOriginalResolution: true }, "rerun_seed", "original"), preview);
  assert.match(original, /Original sizes/);
  assert.match(original, /data-resolution-disabled="true"/);
  assert.match(original, /name="rerun_quantity"[^>]+max="1"[^>]+disabled/);
  assert.doesNotMatch(original, /Use a multiple/);
});

test("composite resolution sources use the shared editor and validate values", () => {
  const compositeSource = { ...source, interface: { inputs: [contract.inputs[0], { id: "size", type: "resolution", label: "Resolution", default: { width: 512, height: 768 }, constraints: { minimum_width: 64, maximum_width: 2048, minimum_height: 64, maximum_height: 2048, multiple: 64 } }] } };
  const draft = promptRerunDraft({ ...panel, source: compositeSource });
  const html = promptRerunMarkup(draft, preview);
  assert.match(html, /id="rerun-control-size-width"/);
  assert.match(html, /data-resolution-grid data-control-id="size"/);
  assert.doesNotMatch(html, /rerun_keep_resolution/);
  draft.values.size.width = 1001;
  assert.ok(validatePromptRerunDraft(draft, preview).size);
});
