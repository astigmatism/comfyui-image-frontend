import assert from "node:assert/strict";
import test from "node:test";
import {
  defaultRerunFolderName,
  promptRerunDraft,
  promptRerunMarkup,
  promptRerunPlannedTotal,
  promptRerunRequest,
  retargetRerunDraft,
  updateRerunDraft,
  validatePromptRerunDraft,
} from "../src/prompt-rerun.mjs";
import { rerunToolState } from "../src/gallery-selection.mjs";

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
  draft = updateRerunDraft(draft, "rerun_checkpoint", "b", { checked: false });
  assert.deepEqual(draft.selections.checkpoint, ["c"]);
  assert.equal(draft.values.checkpoint, "c");
  draft = updateRerunDraft(draft, "rerun_checkpoint", "a", { checked: true });
  assert.deepEqual(draft.selections.checkpoint, ["a", "c"], "selection keeps published order");
  draft = updateRerunDraft(draft, "rerun_seed", "original");
  assert.equal(draft.seedMode, "original");
  assert.equal(draft.quantity, 1);
  draft = updateRerunDraft(draft, "rerun_width", "1000");
  assert.equal(draft.values.width, 1000);
  draft = updateRerunDraft(draft, "rerun_lora_enabled", true, { controlId: "loras", loraId: "x", restoreStrength: 0 });
  assert.equal(draft.values.loras.find((entry) => entry.id === "x").strength, 1);
  draft = updateRerunDraft(draft, "rerun_lora_enabled", false, { controlId: "loras", loraId: "y" });
  assert.equal(draft.values.loras.find((entry) => entry.id === "y").strength, 0);
  draft = updateRerunDraft(draft, "rerun_lora_enabled", true, { controlId: "loras", loraId: "y", restoreStrength: 0.8 });
  assert.equal(draft.values.loras.find((entry) => entry.id === "y").strength, 0.8);
  draft = updateRerunDraft(draft, "rerun_folder", "My folder");
  assert.equal(draft.folderName, "My folder");
  assert.equal(panel.parameters.width, 832);
});

test("validation explains every reason submit is blocked", () => {
  let draft = promptRerunDraft(panel);
  assert.deepEqual(validatePromptRerunDraft(draft, preview), {});
  draft = updateRerunDraft(draft, "rerun_folder", "   ");
  draft = updateRerunDraft(draft, "rerun_width", "1001");
  draft = updateRerunDraft(draft, "rerun_checkpoint", "b", { checked: false });
  draft = updateRerunDraft(draft, "rerun_checkpoint", "c", { checked: false });
  draft = updateRerunDraft(draft, "rerun_lora_strength", "3", { controlId: "loras", loraId: "y" });
  const errors = validatePromptRerunDraft(draft, { ...preview, unique_prompt_count: 0 });
  assert.deepEqual(Object.keys(errors).sort(), ["checkpoints", "folder", "loras", "prompts", "width"]);
  const keep = updateRerunDraft(promptRerunDraft(panel), "rerun_width", "1001");
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
  assert.match(html, /a &lt;b&gt;/);
  assert.match(html, /Alpha &lt;A&gt;/);
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
