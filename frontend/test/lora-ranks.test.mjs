import assert from "node:assert/strict";
import { test } from "node:test";
import { excludedLoraRanks, galleryFilterActive, galleryGenerationMatches, galleryViewParameters, loraRankFilterMarkup } from "../src/gallery-view.mjs";
import { loraRank, lowestLoraRank, migrateInterfaceState, migrateLoraStrengthMemory, moveLoraRank, normalizeLoraRanks, stepLoraRank } from "../src/lib.mjs";
import { loraManagerMarkup, loraRankControlMarkup } from "../src/lora-manager.mjs";
import { galleryCardMarkup, photoViewerMarkup } from "../src/render.mjs";

const identity = (index) => `lr1_${index.toString(16).padStart(64, "0")}`;
const ranks = { A: [identity(1)], D: [identity(4)], F: [identity(5)] };

test("LoRA ranks keep one tier per shared identity and default to C", () => {
  assert.deepEqual(normalizeLoraRanks({ A: [identity(1), "cp1_" + "0".repeat(64), identity(1)], Z: [identity(2)] }), { A: [identity(1)], B: [], C: [], D: [], F: [] });
  assert.equal(loraRank(identity(9), ranks), "C");
  assert.equal(loraRank(identity(5), ranks), "F");
  assert.deepEqual(moveLoraRank(ranks, identity(1), "B").B, [identity(1)]);
  assert.deepEqual(moveLoraRank(ranks, identity(1), "B").A, []);
  assert.deepEqual(stepLoraRank(ranks, identity(9), -1), { from: "C", to: "B", ranks: { A: [identity(1)], B: [identity(9)], C: [], D: [identity(4)], F: [identity(5)] } });
  assert.equal(stepLoraRank(ranks, identity(1), -1), null);
  assert.equal(lowestLoraRank([{ lora_identity: identity(1) }, { lora_identity: identity(4) }], ranks), "D");
  assert.equal(lowestLoraRank([], ranks), null);
});

test("the LoRA rank filter hides images that used any excluded LoRA, like the server", () => {
  const state = { excludedLoraRanks: ["F", "C"], loraTiers: ranks, favoritesMode: "all" };
  assert.deepEqual(excludedLoraRanks(state), ["C", "F"]);
  assert.equal(galleryFilterActive(state), true);
  assert.equal(galleryGenerationMatches(state, { loras: [{ lora_identity: identity(1) }] }), true);
  assert.equal(galleryGenerationMatches(state, { loras: [{ lora_identity: identity(1) }, { lora_identity: identity(5) }] }), false);
  assert.equal(galleryGenerationMatches(state, { lora_identities: [identity(9)] }), false);
  assert.equal(galleryGenerationMatches(state, { loras: [] }), true);
  const parameters = galleryViewParameters(state);
  assert.deepEqual(parameters.getAll("excluded_lora_ranks"), ["C", "F"]);
  assert.match(loraRankFilterMarkup(state), /data-lora-rank="F" aria-label="Show LoRA rank F" aria-pressed="false"/);
  assert.match(loraRankFilterMarkup(state), /LoRA · 2 hidden/);
});

test("cards show the lowest LoRA rank and the photo viewer offers per-LoRA arrows", () => {
  const generation = {
    id: "g1", status: "succeeded", workflow_display_name: "Moody Krea2 Minimal v1",
    loras: [{ lora_identity: identity(1), label: "Alpha", strength: 1 }, { lora_identity: identity(4), label: "Delta", strength: 0.5 }],
  };
  const card = galleryCardMarkup(generation, { loraTiers: ranks });
  assert.match(card, /card-lora-rank/);
  assert.match(card, /checkpoint-tier-D/);
  assert.match(card, /Alpha · A/);
  assert.doesNotMatch(galleryCardMarkup({ ...generation, loras: [] }, { loraTiers: ranks }), /card-lora-rank/);
  const viewer = photoViewerMarkup(generation, {}, "fill", "hold", { loraTiers: ranks });
  assert.match(viewer, /aria-label="LoRAs used"/);
  assert.match(viewer, new RegExp(`data-action="rank-lora" data-lora-identity="${identity(4)}" data-rank-step="1"`));
  assert.match(viewer, /Alpha is already at the highest rank A/);
});

test("the LoRA manager shows each row's shared rank with immediate arrows", () => {
  const control = { id: "loras", minimum: 0, maximum: 2, step: 0.05, default: [{ id: "a", strength: 0 }], items: [{ id: "a", label: "Alpha", lora_identity: identity(4) }] };
  const markup = loraManagerMarkup({ control, values: control.default, loraTiers: ranks });
  assert.match(markup, /aria-label="LoRA rank D"/);
  assert.match(markup, /Raise Alpha from D to C/);
  assert.match(markup, /Shared LoRA library/);
  assert.equal(loraRankControlMarkup("not-an-identity", "Alpha", ranks), "");
  assert.match(loraRankControlMarkup(identity(4), "Alpha", ranks, { identity: identity(4), status: "saving" }), /Saving…/);
});

test("switching workflows carries LoRA picks through the shared library", () => {
  const stack = { id: "loras", type: "lora_stack", semantic_role: "lora", items: [{ id: "a" }, { id: "b" }], default: [{ id: "a", strength: 0 }, { id: "b", strength: 0 }], minimum: 0, maximum: 2, step: 0.05 };
  const prompt = { id: "prompt", type: "string", semantic_role: "positive_prompt", default: "" };
  const contract = { inputs: [prompt, stack] };
  const previous = { prompt: "carried prompt", loras: [{ id: "b", strength: 1 }, { id: "a", strength: 0 }] };
  const own = { prompt: "", loras: [{ id: "a", strength: 0.7 }, { id: "b", strength: 0 }] };
  const switched = migrateInterfaceState(contract, contract, previous, ["prompt", "loras"], own, [], { rejectInvalid: true });
  assert.deepEqual(switched.values.loras, previous.loras);
  assert.equal(switched.values.prompt, "carried prompt");
  assert.deepEqual(new Set(switched.explicitInputIds), new Set(["prompt", "loras"]));
});

test("LoRA picks follow the shared identity when library item IDs differ", () => {
  const stack = (items) => ({ id: "loras", type: "lora_stack", semantic_role: "lora", items, default: items.map(({ id }) => ({ id, strength: 0 })), minimum: 0, maximum: 2, step: 0.05 });
  const source = { inputs: [stack([{ id: "a", lora_identity: identity(1) }, { id: "b", lora_identity: identity(2) }])] };
  const target = { inputs: [stack([{ id: "renamed_b", lora_identity: identity(2) }, { id: "a", lora_identity: identity(1) }, { id: "only_here", lora_identity: identity(3) }])] };
  const own = { loras: [{ id: "only_here", strength: 0.5 }, { id: "renamed_b", strength: 0 }, { id: "a", strength: 0 }] };
  const previous = { loras: [{ id: "b", strength: 1.25 }, { id: "a", strength: 0 }] };
  const { values } = migrateInterfaceState(target, source, previous, ["loras"], own, [], { rejectInvalid: true });
  // Carried order and strengths; a LoRA only the destination publishes starts at its default.
  assert.deepEqual(values.loras, [{ id: "renamed_b", strength: 1.25 }, { id: "a", strength: 0 }, { id: "only_here", strength: 0 }]);
  const memory = migrateLoraStrengthMemory(target, source, previous, { loras: { a: 0.7, gone: 1 } }, { loras: { only_here: 0.4 } });
  assert.deepEqual(memory, { loras: { a: 0.7 } });
});

test("a stack with nothing in common keeps the destination's own LoRA picks and memory", () => {
  const stack = (ids, offset) => ({ id: "loras", type: "lora_stack", semantic_role: "lora", items: ids.map((id, index) => ({ id, lora_identity: identity(offset + index) })), default: ids.map((id) => ({ id, strength: 0 })), minimum: 0, maximum: 2, step: 0.05 });
  const source = { inputs: [stack(["a", "b"], 10)] };
  const target = { inputs: [stack(["x", "y"], 20)] };
  const own = { loras: [{ id: "y", strength: 0.6 }, { id: "x", strength: 0 }] };
  const previous = { loras: [{ id: "b", strength: 1 }, { id: "a", strength: 0 }] };
  const { values } = migrateInterfaceState(target, source, previous, ["loras"], own, ["loras"], { rejectInvalid: true });
  assert.deepEqual(values.loras, own.loras);
  const memory = migrateLoraStrengthMemory(target, source, previous, { loras: { a: 1 } }, { loras: { x: 0.3 } });
  assert.deepEqual(memory, { loras: { x: 0.3 } });
});
