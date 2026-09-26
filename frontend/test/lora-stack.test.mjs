import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import {
  loraStackError,
  loraStackMarkup,
  loraDefaultPositiveStrength,
  strongestLoraTrigger,
  moveLora,
} from "../src/lora-stack.mjs";
import { loraManagerMarkup } from "../src/lora-manager.mjs";
import { parametersForRequest, overwriteWithRecall, clientValidate } from "../src/lib.mjs";

const control = { id: "loras", type: "lora_stack", label: "LoRAs", semantic_role: "lora", required: false, advanced: false, group: "LoRAs", order: 150, items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }], minimum: 0, maximum: 2, step: 0.05, default: [{ id: "a", strength: 0 }, { id: "b", strength: 0 }] };

test("LoRA identity survives ordering, request snapshots and exact recall", () => {
  const original = [{ id: "a", strength: 0.05 }, { id: "b", strength: 2 }];
  const values = { loras: moveLora(original, "b", 0) };
  assert.equal(values.loras[0].strength, 2);
  const contract = { inputs: [control] };
  const requested = parametersForRequest(contract, values);
  values.loras[0].strength = 0;
  assert.equal(requested.loras[0].strength, 2);
  const recalled = overwriteWithRecall({}, { source_key: "source", parameters: requested });
  assert.deepEqual(recalled.parameters.loras, [{ id: "b", strength: 2 }, { id: "a", strength: 0.05 }]);
  assert.deepEqual(original, [{ id: "a", strength: 0.05 }, { id: "b", strength: 2 }]);
  assert.deepEqual(clientValidate(contract, requested), {});
});

test("a newly enabled LoRA starts at a valid positive workflow strength", () => {
  const limited = { ...control, maximum: 0.8, step: 0.1 };
  assert.equal(loraDefaultPositiveStrength(control), 1);
  assert.equal(loraDefaultPositiveStrength(limited), 0.1);
});

test("LoRA client validation rejects malformed entries", () => {
  for (const strength of [null, "1", true, NaN, Infinity, 1e-12, -0.05, 2.05, 0.051]) {
    assert.ok(loraStackError(control, [{ id: "a", strength }, { id: "b", strength: 0 }]));
  }
  for (const value of [null, [], "[]", [{ id: "a", strength: 0 }, { id: "a", strength: 0 }]]) assert.ok(loraStackError(control, value));
});

test("control panel summarizes only enabled LoRAs in application order", () => {
  const rows = [{ id: "b", strength: 0.65 }, { id: "a", strength: 1.5 }];
  const markup = loraStackMarkup(control, rows);
  assert.match(markup, /lm-summary-list/);
  assert.ok(markup.indexOf("Beta") < markup.indexOf("Alpha"));
  assert.doesNotMatch(markup, /data-lora-solo|data-lora-mixer|data-lora-range|data-lora-number/);

  const empty = loraStackMarkup(control, control.default);
  assert.match(empty, /No LoRAs enabled/);
  assert.doesNotMatch(empty, /lm-summary-item/);
});

test("manager lists all LoRAs with enable, strength, image and reorder controls", () => {
  const withUsage = structuredClone(control);
  withUsage.items[0].description = 'Use: <character> & "style". <script>alert(1)</script>';
  const rows = [{ id: "b", strength: 0 }, { id: "a", strength: 1.25 }];
  const markup = loraManagerMarkup({ control: withUsage, values: rows, memory: { b: 0.65 }, sourceName: "Example workflow" });
  assert.ok(markup.indexOf('data-lora-id="b"') < markup.indexOf('data-lora-id="a"'));
  for (const id of ["a", "b"]) {
    assert.match(markup, new RegExp(`data-lora-toggle="${id}"`));
    assert.match(markup, new RegExp(`data-lora-range="${id}"`));
    assert.match(markup, new RegExp(`data-lora-number="${id}"`));
    assert.match(markup, new RegExp(`data-lora-image-change="${id}"`));
    assert.match(markup, /data-lora-handle/);
  }
  assert.match(markup, /data-lora-image-remove="a"/);
  assert.doesNotMatch(markup, /type="checkbox"/);
  assert.match(markup, /0\.65/);
  assert.match(markup, /Example workflow/);
  assert.match(markup, /Use: &lt;character&gt; &amp; &quot;style&quot;/);
  assert.doesNotMatch(markup, /<script>/);
  assert.doesNotMatch(markup, /Quick pick|Mix &amp; adjust|data-lora-solo/);
});

test("strongest enabled LoRA supplies only its verified trigger", () => {
  const withTrigger = structuredClone(control);
  withTrigger.items[0].trigger_word = '<Alpha & "Character">';
  const rows = [{ id: "a", strength: 1 }, { id: "b", strength: 0 }];
  const markup = loraManagerMarkup({ control: withTrigger, values: rows });
  assert.match(markup, /&lt;Alpha &amp; &quot;Character&quot;&gt;/);
  assert.match(loraManagerMarkup({ control: withTrigger, values: rows, subjectAvailable: false }), /Subject unchanged · Prompt Generation subject is unavailable/);
  assert.doesNotMatch(markup, /<Alpha/);
  assert.equal(strongestLoraTrigger(withTrigger, rows).triggerWord, '<Alpha & "Character">');
  assert.equal(strongestLoraTrigger(withTrigger, [{ id: "b", strength: 1.5 }, { id: "a", strength: 1 }]).triggerWord, null);
  assert.equal(strongestLoraTrigger(withTrigger, [{ id: "b", strength: 1 }, { id: "a", strength: 1 }]).triggerWord, null);
  assert.equal(strongestLoraTrigger(withTrigger, [{ id: "a", strength: 0 }, { id: "b", strength: 0 }]).triggerWord, null);
});

const publisherSource = await readFile(new URL("../../comfyui_extension/comfyui-image-frontend-interface/web/publication.js", import.meta.url), "utf8");
const { validatePublication, derivePublishedMetadata } = await import(`data:text/javascript;base64,${Buffer.from(publisherSource).toString("base64")}`);
function fixture() {
  const meta = { instance_uuid: "12345678-1234-4234-8234-123456789abc", parameter_id: "loras", label: "LoRAs", description: "Ordered stack", semantic_role: "lora", required: false, advanced: false, group: "LoRAs", order: 150 };
  const api = {
    1: { class_type: "CIFTextParameter", inputs: { ...meta, parameter_id: "prompt", instance_uuid: "12345678-1234-4234-8234-123456789abd", semantic_role: "positive_prompt", value: "test" } },
    2: { class_type: "CIFLoraStack", inputs: { ...meta, model: ["5", 0], catalog_json: JSON.stringify(control.items.map((item) => ({ ...item, filename: `private/${item.id}.safetensors` }))), value: JSON.stringify(control.default), minimum: 0, maximum: 2, step: 0.05 } },
    3: { class_type: "FakeSampler", inputs: { model: ["2", 0], prompt: ["1", 0] } },
    4: { class_type: "FakeOutput", inputs: { images: ["3", 0] } },
    5: { class_type: "UNETLoader", inputs: { unet_name: "model.safetensors" } },
  };
  const workflow = { nodes: Object.entries(api).map(([id, node]) => ({ id: Number(id), type: node.class_type, properties: { cif_contract_schema: "comfyui-image-frontend.interface/v1" } })) };
  const defs = { FakeOutput: { output_node: true }, LoraLoaderModelOnly: { input: { required: { lora_name: [["private/a.safetensors", "private/b.safetensors"]] } } } };
  return { api, workflow, defs };
}

test("publisher exposes only public stack metadata and configurable inventory", () => {
  const { api, workflow, defs } = fixture();
  const catalog = JSON.parse(api[2].inputs.catalog_json);
  catalog[0].description = "Use: AlphaCharacter in your prompt.";
  catalog[0].trigger_word = "AlphaCharacter";
  api[2].inputs.catalog_json = JSON.stringify(catalog);
  const validation = validatePublication(workflow, api, defs);
  assert.deepEqual(validation.errors, []);
  const stack = validation.inputs.find((input) => input.type === "lora_stack");
  assert.deepEqual(stack.items, [{ ...control.items[0], description: catalog[0].description, trigger_word: "AlphaCharacter" }, control.items[1]]);
  assert.deepEqual(stack.default, control.default);
  assert.doesNotMatch(JSON.stringify(stack), /filename|catalog_json|private\//);
  const metadata = derivePublishedMetadata(workflow, api, validation);
  assert.equal(metadata.technical_inventory.loras[0].usage, "public_stack");
  assert.equal(metadata.technical_inventory.loras[0].items[0].trigger_word, "AlphaCharacter");
  assert.doesNotMatch(JSON.stringify(metadata.technical_inventory.loras), /private\//);
});

test("publisher rejects duplicate IDs, nonzero defaults, missing files and invalid constraints", () => {
  for (const mutate of [
    (api) => { api[2].inputs.value = '[{"id":"a","strength":0},{"id":"a","strength":0}]'; },
    (api) => { api[2].inputs.value = '[{"id":"a","strength":1},{"id":"b","strength":0}]'; },
    (api) => { api[2].inputs.minimum = -1; },
    (api, defs) => { defs.LoraLoaderModelOnly.input.required.lora_name = [[]]; },
    ...[null, "", " ", 1, {}, "x".repeat(1001)].map((description) => (api) => {
      const catalog = JSON.parse(api[2].inputs.catalog_json);
      catalog[0].description = description;
      api[2].inputs.catalog_json = JSON.stringify(catalog);
    }),
    ...[null, "", " ", 1, {}, "x".repeat(121)].map((trigger_word) => (api) => {
      const catalog = JSON.parse(api[2].inputs.catalog_json);
      catalog[0].trigger_word = trigger_word;
      api[2].inputs.catalog_json = JSON.stringify(catalog);
    }),
  ]) {
    const { api, workflow, defs } = fixture();
    mutate(api, defs);
    assert.ok(validatePublication(workflow, api, defs).errors.length);
  }
});

test("recalling records predating LoRA controls restores an all-zero stack", () => {
  const contract = { inputs: [control] };
  const state = { parameters: { loras: [{ id: "b", strength: 2 }, { id: "a", strength: 1 }] } };
  const recalled = overwriteWithRecall(state, { source_available: false, parameters: { prompt: "old" }, input_definitions: [] }, contract);
  assert.deepEqual(recalled.parameters.loras, control.default);
});
