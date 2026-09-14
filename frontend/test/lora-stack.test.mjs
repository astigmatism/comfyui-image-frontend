import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { loraStackError, loraStackMarkup, moveLora } from "../src/lora-stack.mjs";
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

test("LoRA client validation rejects malformed entries and keeps zero rows", () => {
  for (const strength of [null, "1", true, NaN, Infinity, 1e-12, -0.05, 2.05, 0.051]) {
    assert.ok(loraStackError(control, [{ id: "a", strength }, { id: "b", strength: 0 }]));
  }
  for (const value of [null, [], "[]", [{ id: "a", strength: 0 }, { id: "a", strength: 0 }]]) assert.ok(loraStackError(control, value));
  const markup = loraStackMarkup(control, [...control.default].reverse());
  assert.ok(markup.indexOf('data-lora-id="b"') < markup.indexOf('data-lora-id="a"'));
  assert.doesNotMatch(markup, /data-lora-move|lora-movement|arrow buttons/);
  assert.match(markup, /Reorder Beta/);
  assert.match(markup, /aria-live="polite"/);
});

test("usage tooltips escape text and distinguish unknown triggers", () => {
  const withUsage = structuredClone(control);
  withUsage.items[0].description = 'Use: <character> & "style". <script>alert(1)</script>';
  const markup = loraStackMarkup(withUsage, withUsage.default);
  assert.match(markup, /role="tooltip" popover="manual"/);
  assert.match(markup, /aria-describedby="lora-usage-loras-a"/);
  assert.match(markup, /Use: &lt;character&gt; &amp; &quot;style&quot;/);
  assert.doesNotMatch(markup, /<script>/);
  assert.match(markup, /trigger words have not been verified/);
  assert.match(markup, /no &lt;lora:\.\.\.&gt; tag is needed/);
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
  api[2].inputs.catalog_json = JSON.stringify(catalog);
  const validation = validatePublication(workflow, api, defs);
  assert.deepEqual(validation.errors, []);
  const stack = validation.inputs.find((input) => input.type === "lora_stack");
  assert.deepEqual(stack.items, [{ ...control.items[0], description: catalog[0].description }, control.items[1]]);
  assert.deepEqual(stack.default, control.default);
  assert.doesNotMatch(JSON.stringify(stack), /filename|catalog_json|private\//);
  const metadata = derivePublishedMetadata(workflow, api, validation);
  assert.equal(metadata.technical_inventory.loras[0].usage, "public_stack");
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
