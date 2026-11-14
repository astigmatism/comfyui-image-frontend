import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import {
  loraStackError,
  loraStackMarkup,
  loraDefaultPositiveStrength,
  loraEnableStrength,
  loraTriggerWord,
  subjectLoraTrigger,
  moveLora,
  steppedLoraStrength,
  formatLoraStrength,
} from "../src/lora-stack.mjs";
import { loraManagerMarkup, loraPublicationRevisionMatches } from "../src/lora-manager.mjs";
import { groupedLoras, moveEnabledLora, reconcileLoraDraft } from "../src/lora-gallery.mjs";
import { parametersForRequest, overwriteWithRecall, clientValidate } from "../src/lib.mjs";

const control = { id: "loras", type: "lora_stack", label: "LoRAs", semantic_role: "lora", required: false, advanced: false, group: "LoRAs", order: 150, items: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }], minimum: 0, maximum: 2, step: 0.05, default: [{ id: "a", strength: 0 }, { id: "b", strength: 0 }] };

test("LoRA draft rejects any changed publication hash", () => {
  const revision = { publication_id: "one", workflow_sha256: "ui", api_sha256: "api", manifest_sha256: "manifest" };
  assert.equal(loraPublicationRevisionMatches(revision, { ...revision }), true);
  for (const field of Object.keys(revision)) assert.equal(loraPublicationRevisionMatches(revision, { ...revision, [field]: "changed" }), false);
});

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

test("sidebar coarse steps preserve fine offsets and stay within the published grid", () => {
  assert.equal(formatLoraStrength(0.65), "0.65");
  assert.equal(formatLoraStrength(0.005), "0.005");
  assert.equal(steppedLoraStrength(control, 0.65, 1), 1.15);
  assert.equal(steppedLoraStrength(control, 0.65, -1), 0.15);
  assert.equal(steppedLoraStrength(control, 0.15, -1), 0);
  assert.equal(steppedLoraStrength(control, 1.85, 1), 2);
  assert.equal(steppedLoraStrength({ ...control, step: 0.3, maximum: 1.95 }, 1.5, 1), 1.8);
  assert.equal(steppedLoraStrength({ ...control, step: 0.2 }, 0.6, -1), 0.4);
  const markup = loraStackMarkup(control, [{ id: "a", strength: 0.65 }, { id: "b", strength: 0 }], {}, { editable: true });
  assert.match(markup, /data-lora-strength="a"/);
  assert.match(markup, /step="0.05"/);
  assert.match(markup, /Disable Alpha/);
  assert.doesNotMatch(markup, /Disable Beta/);
});

test("gallery sorts thumbnails independently of application order and keeps captions minimal", () => {
  const withUsage = structuredClone(control);
  withUsage.items[0].description = 'Use: <character> & "style". <script>alert(1)</script>';
  const rows = [{ id: "b", strength: 0 }, { id: "a", strength: 1.25 }];
  const markup = loraManagerMarkup({ control: withUsage, values: rows, memory: { b: 0.65 }, sourceName: "Example workflow", menuId: "a", panel: { kind: "details", id: "a" } });
  for (const id of ["a", "b"]) {
    assert.doesNotMatch(markup, new RegExp(`data-lora-range="${id}"|data-lora-number="${id}"`));
  }
  // Thumbnails are derived from the Demo Reel, so the gallery offers no upload at all.
  assert.doesNotMatch(markup, /data-lora-image-change|data-lora-image-remove|data-lora-file/);
  assert.match(markup, /data-lora-handle/);
  assert.match(markup, /Example workflow/);
  assert.match(markup, /A LoRA shows its Demo Reel/);
  assert.match(markup, /Use: &lt;character&gt; &amp; &quot;style&quot;/);
  assert.doesNotMatch(markup, /<script>/);
  assert.doesNotMatch(markup, /Quick pick|Mix &amp; adjust|data-lora-solo/);
  const gallery = loraManagerMarkup({ control, values: rows });
  assert.ok(gallery.indexOf('data-lora-id="a"') < gallery.indexOf('data-lora-id="b"'));
  assert.match(gallery, /data-lora-toggle="a"[^>]*aria-pressed="true"/);
  assert.match(gallery, /data-lora-toggle="b"[^>]*aria-pressed="false"/);
  assert.doesNotMatch(gallery, /data-lora-edit=|data-lora-delete=|data-lora-group-add/);
});

test("LoRA group headers reuse the gallery header pattern with icon-only controls", () => {
  const items = [{ id: "a", label: "Alpha", lora_identity: "one" }];
  const galleryControl = { ...control, items, default: [{ id: "a", strength: 0 }] };
  const grouping = { groups: [{ id: "style", name: "Style" }, { id: "uncategorized", name: "Uncategorized", system: true }], assignments: { one: "style" } };
  const adminController = { currentOperation: () => null, state: { library: { key: "family", eligible: true, items, members: [] }, libraries: [], pendingUploads: new Map(), uploadPercent: null } };
  const markup = loraManagerMarkup({ control: galleryControl, management: true, admin: true, adminController, libraryKey: "family", grouping });
  assert.match(markup, /<h3>Style<\/h3><span class="lm-group-rule" aria-hidden="true"><\/span><span class="lm-group-count">1 LoRA<\/span>/);
  assert.match(markup, /class="icon-button" data-lora-group-rename="style" aria-label="Rename group Style" title="Rename group"/);
  assert.match(markup, /class="icon-button" data-lora-group-up="style" aria-label="Move Style up" title="Move group up" disabled/);
  assert.match(markup, /class="icon-button" data-lora-group-down="uncategorized" aria-label="Move Uncategorized down" title="Move group down" disabled/);
  assert.doesNotMatch(markup, /data-lora-group-rename="uncategorized"|data-lora-group-delete="uncategorized"/);
  assert.doesNotMatch(markup, /class="button low" data-lora-group-(up|down|rename|delete)=|>Rename<|>Delete group</);
  assert.match(markup, /data-lora-demo-settings aria-label="Demo Reel settings" title="Demo Reel settings"><svg/);
  // The close button in that same cluster draws the application's stroked glyph rather than a
  // text character sized like a glyph, so one action keeps one picture across the dialogs.
  assert.match(markup, /data-lora-cancel aria-label="Close LoRA library" title="Close LoRA library"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18" \/><\/svg><\/button>/);
  assert.doesNotMatch(markup, /lm-close-button[^>]*>×/);
});

test("LoRAs without a thumbnail are one line in a list of their own", () => {
  const items = [{ id: "a", label: "Alpha", lora_identity: "one" }, { id: "b", label: "Beta", lora_identity: "two" }];
  const galleryControl = { ...control, items, default: items.map((item) => ({ id: item.id, strength: 0 })) };
  const values = [{ id: "a", strength: 1 }, { id: "b", strength: 0 }];
  const images = { b: { image_url: "/api/lora-library/family/identities/two/image", version: "1" } };
  const gallery = loraManagerMarkup({ control: galleryControl, values, images, libraryKey: "family" });
  const row = /<li class="lm-card lm-card-minimal is-enabled" data-lora-id="a">[\s\S]*?<\/li>/.exec(gallery)?.[0];
  assert.ok(row, "expected a minimal row for the LoRA without a thumbnail");
  assert.match(row, /class="lm-minimal-toggle" data-lora-toggle="a" aria-label="Toggle Alpha" aria-pressed="true"/);
  assert.match(row, /<span class="lm-card-name" title="Alpha">Alpha<\/span>/);
  assert.doesNotMatch(row, /lm-thumbnail|lm-caption|data-lora-demo-reel|data-lora-details/);
  assert.match(gallery, /<ul class="lm-minimal-list" aria-label="Uncategorized without thumbnails">/);
  const card = /<li class="lm-card" data-lora-id="b">[\s\S]*?<\/li>/.exec(gallery)?.[0];
  assert.match(card, /<div class="lm-caption"><span class="lm-card-name" title="Beta">Beta<\/span><button type="button" class="icon-button lm-reel-button" data-lora-demo-reel="b" aria-label="Open Demo Reel for Beta" title="Open Demo Reel"/);
});

test("the LoRA menu carries Demo Reel only where the caption has no room for it", () => {
  const items = [{ id: "a", label: "Alpha", lora_identity: "one" }, { id: "b", label: "Beta", lora_identity: "two" }];
  const galleryControl = { ...control, items, default: items.map((item) => ({ id: item.id, strength: 0 })) };
  const values = [{ id: "a", strength: 0 }, { id: "b", strength: 0 }];
  const withoutImage = loraManagerMarkup({ control: galleryControl, values, libraryKey: "family", menuId: "a" });
  assert.match(withoutImage, /data-lora-details="a">Usage details<\/button>\s*<button type="button" data-lora-demo-reel="a"\s*>Demo Reel<\/button>/);
  const actions = [...(/<div class="lm-card-popover"[\s\S]*?<\/div>/.exec(withoutImage)?.[0] ?? "").matchAll(/data-lora-([a-z-]+)="a"/g)];
  assert.deepEqual(actions.map((match) => match[1]), ["details", "demo-reel"]);
  const withImage = loraManagerMarkup({ control: galleryControl, values, images: { a: { image_url: "/api/x", version: "1" } }, libraryKey: "family", menuId: "a" });
  assert.doesNotMatch(withImage, /data-lora-demo-reel="a"\s*>Demo Reel/);
});

test("an administrator takes a LoRA out of the Demo Reel from its card menu", () => {
  const items = [{ id: "a", label: "Alpha", lora_identity: "one" }, { id: "b", label: "Beta", lora_identity: "two" }];
  const galleryControl = { ...control, items, default: items.map((item) => ({ id: item.id, strength: 0 })) };
  const values = [{ id: "a", strength: 0 }, { id: "b", strength: 0 }];
  const grouping = { groups: [{ id: "style", name: "Style" }, { id: "uncategorized", name: "Uncategorized", system: true }], assignments: { one: "style" } };
  const menu = (admin, demo_reel_enabled) => {
    const adminController = {
      currentOperation: () => null,
      state: {
        busy: false,
        library: { key: "family", eligible: true, items: items.map((item) => ({ ...item, demo_reel_enabled })), members: [] },
        libraries: [], pendingUploads: new Map(), uploadPercent: null,
      },
    };
    const markup = loraManagerMarkup({ control: galleryControl, values, libraryKey: "family", grouping, menuId: "a", admin, adminController });
    return /<div class="lm-card-popover"[\s\S]*?<\/div>/.exec(markup)?.[0] ?? "";
  };
  const actions = (markup) => [...markup.matchAll(/data-lora-([a-z-]+)="a"/g)].map((match) => match[1]);

  assert.deepEqual(actions(menu(false, true)), ["details", "demo-reel"]);
  assert.deepEqual(actions(menu(true, true)), ["details", "demo-reel", "demo-toggle", "move", "edit", "delete"]);
  assert.match(menu(true, true), /class="destructive" data-lora-demo-toggle="a"\s*>Disable Demo Reel</);
  assert.match(menu(true, false), /data-lora-demo-toggle="a"\s*>Enable Demo Reel</);
  assert.doesNotMatch(menu(true, false), /class="destructive" data-lora-demo-toggle="a"/);
});

test("grouped gallery includes empty groups and defaults missing memberships to Uncategorized", () => {
  const items = [{ id: "z", label: "Zeta", lora_identity: "one" }, { id: "a", label: "Alpha", lora_identity: "two" }];
  const grouping = { groups: [{ id: "empty", name: "Empty" }, { id: "named", name: "Named" }, { id: "uncategorized", name: "Uncategorized", system: true }], assignments: { one: "named" } };
  assert.deepEqual(groupedLoras(items, grouping).map((group) => [group.id, group.items.map((item) => item.id)]), [["empty", []], ["named", ["z"]], ["uncategorized", ["a"]]]);
});

test("weight-edit controls require the displayed family and exact shared identity", () => {
  const items = [{ id: "a", label: "Alpha", lora_identity: "one" }];
  const galleryControl = { ...control, items, default: [{ id: "a", strength: 0 }] };
  const adminController = { currentOperation: () => null, state: { library: { key: "family", eligible: true, items, members: [] }, libraries: [], pendingUploads: new Map(), uploadPercent: null } };
  const options = { control: galleryControl, admin: true, adminController, menuId: "a", libraryKey: "family" };
  assert.match(loraManagerMarkup(options), /data-lora-delete="a" >Delete/);
  assert.match(loraManagerMarkup({ ...options, libraryKey: "other" }), /data-lora-delete="a" disabled/);
  const different = { ...galleryControl, items: [{ ...items[0], lora_identity: "different" }] };
  assert.match(loraManagerMarkup({ ...options, control: different }), /data-lora-delete="a" disabled/);
});

test("active strip reorders enabled entries once while preserving disabled entries and strengths", () => {
  const values = [{ id: "a", strength: .65 }, { id: "b", strength: 0 }, { id: "c", strength: 1.2 }];
  assert.deepEqual(moveEnabledLora(values, "c", 0), [{ id: "c", strength: 1.2 }, { id: "b", strength: 0 }, { id: "a", strength: .65 }]);
  assert.deepEqual(values.map((entry) => entry.id), ["a", "b", "c"]);
});

test("library refresh preserves draft picks by identity, removes deleted items, and adds disabled items", () => {
  const oldControl = { ...control, items: [{ id: "a", label: "A", lora_identity: "same" }, { id: "b", label: "B", lora_identity: "removed" }] };
  const next = { ...control, items: [{ id: "renamed", label: "Renamed", lora_identity: "same" }, { id: "new", label: "New", lora_identity: "added" }], default: [{ id: "renamed", strength: 0 }, { id: "new", strength: 0 }] };
  const reconciled = reconcileLoraDraft({ control: oldControl, values: [{ id: "b", strength: 1 }, { id: "a", strength: .65 }], memory: { a: .65, b: 1 } }, { control: next });
  assert.deepEqual(reconciled.values, [{ id: "renamed", strength: .65 }, { id: "new", strength: 0 }]);
  assert.deepEqual(reconciled.memory, { renamed: .65 });
});

test("the topmost enabled LoRA publishing a trigger word supplies the subject", () => {
  const catalog = structuredClone(control);
  catalog.items = [
    { id: "a", label: "Alpha", trigger_word: '<Alpha & "Character">' },
    { id: "b", label: "Beta" },
    { id: "c", label: "Cinematic grain" },
  ];
  catalog.default = [...catalog.default, { id: "c", strength: 0 }];
  // Beta is the strongest enabled entry and sits at the top of the application order,
  // but it publishes no trigger word, so the subject comes from Alpha.
  const rows = [{ id: "b", strength: 1.5 }, { id: "a", strength: 1 }, { id: "c", strength: 0 }];
  const subject = subjectLoraTrigger(catalog, rows);
  assert.equal(subject.triggerWord, '<Alpha & "Character">');
  assert.equal(subject.entry.id, "a");
  assert.equal(subject.item.label, "Alpha");
  const markup = loraManagerMarkup({ control: catalog, values: rows });
  assert.match(markup, /&lt;Alpha &amp; &quot;Character&quot;&gt;/);
  assert.match(markup, /Subject on Apply: &lt;Alpha &amp; &quot;Character&quot;&gt;/);
  assert.doesNotMatch(markup, /<Alpha/);
  assert.match(loraManagerMarkup({ control: catalog, values: rows, subjectAvailable: false }), /Subject unchanged · Prompt Generation subject is unavailable/);

  const stylisticOnly = [{ id: "a", strength: 0 }, { id: "b", strength: 1.2 }, { id: "c", strength: 0.5 }];
  assert.equal(subjectLoraTrigger(catalog, stylisticOnly).triggerWord, null);
  assert.equal(subjectLoraTrigger(catalog, stylisticOnly).entry, null);
  assert.match(loraManagerMarkup({ control: catalog, values: stylisticOnly }), /Subject unchanged · no enabled LoRA publishes a trigger word/);

  assert.equal(subjectLoraTrigger(catalog, catalog.default).triggerWord, null);
  assert.equal(subjectLoraTrigger(catalog).triggerWord, null);
  assert.match(loraManagerMarkup({ control: catalog, values: catalog.default }), /Subject unchanged · no LoRA enabled/);

  // Application order, not strength, breaks a tie between two entries that do publish one.
  const both = [{ id: "c", strength: 2 }, { id: "b", strength: 2 }, { id: "a", strength: 2 }];
  catalog.items[2].trigger_word = "grainy 35mm";
  assert.equal(subjectLoraTrigger(catalog, both).triggerWord, "grainy 35mm");
});

test("a catalog entry offers its verified trigger word, falling back to its published title", () => {
  assert.deepEqual(loraTriggerWord({ id: "a", label: "Alpha", trigger_word: "  AlphaCharacter  " }), { text: "AlphaCharacter", source: "verified" });
  assert.deepEqual(loraTriggerWord({ id: "b", label: " Beta " }), { text: "Beta", source: "title" });
  assert.deepEqual(loraTriggerWord({ id: "c", label: "C", trigger_word: "   " }), { text: "C", source: "title" });
  for (const item of [{ id: "d", label: "  " }, { id: "e" }, undefined]) {
    assert.deepEqual(loraTriggerWord(item), { text: null, source: null });
  }
});

test("the sidebar tile is the copy control and the read-only summary has no dead button", () => {
  const withTrigger = structuredClone(control);
  withTrigger.items[0].trigger_word = 'Alpha & "Character"';
  withTrigger.items[1].label = 'Beta & "Co"';
  const rows = [{ id: "a", strength: 1 }, { id: "b", strength: 1.2 }];
  const editable = loraStackMarkup(withTrigger, rows, {}, { editable: true });
  assert.match(editable, /<button type="button" class="lm-summary-thumb" data-lora-copy-trigger="a" aria-label="Copy trigger word for Alpha" title="Copy trigger word for Alpha"><span aria-hidden="true">✧<\/span><span class="lm-summary-copy-hint" aria-hidden="true"><svg/);
  assert.match(editable, /aria-label="Copy trigger word for Beta &amp; &quot;Co&quot;"/);
  assert.doesNotMatch(editable, /Copy trigger word for Beta & "Co"/);
  const readOnly = loraStackMarkup(withTrigger, rows);
  assert.match(readOnly, /<span class="lm-summary-thumb"><span aria-hidden="true">✧<\/span><\/span>/);
  assert.doesNotMatch(readOnly, /data-lora-copy-trigger|lm-summary-copy-hint/);
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
    // A minimum above zero would put the published all-zero defaults below the floor.
    (api) => { api[2].inputs.minimum = 0.5; },
    (api) => { api[2].inputs.maximum = 0; },
    (api) => { api[2].inputs.step = 0; },
    (api) => { api[2].inputs.minimum = "0"; },
    (api, defs) => { defs.LoraLoaderModelOnly.input.required.lora_name = [[]]; },
    ...[null, "", " ", 1, {}, "x".repeat(1001)].map((description) => (api) => {
      const catalog = JSON.parse(api[2].inputs.catalog_json);
      catalog[0].description = description;
      api[2].inputs.catalog_json = JSON.stringify(catalog);
    }),
    ...[null, "", " ", 1, {}, "x".repeat(1001)].map((trigger_word) => (api) => {
      const catalog = JSON.parse(api[2].inputs.catalog_json);
      catalog[0].trigger_word = trigger_word;
      api[2].inputs.catalog_json = JSON.stringify(catalog);
    }),
    ...[null, "1", true, {}, Infinity].map((preferred_strength) => (api) => {
      const catalog = JSON.parse(api[2].inputs.catalog_json);
      catalog[0].preferred_strength = preferred_strength;
      api[2].inputs.catalog_json = JSON.stringify(catalog);
    }),
  ]) {
    const { api, workflow, defs } = fixture();
    mutate(api, defs);
    assert.ok(validatePublication(workflow, api, defs).errors.length);
  }
});

test("publisher publishes a stack whose strengths may go below zero", () => {
  const { api, workflow, defs } = fixture();
  api[2].inputs.minimum = -1;
  const validation = validatePublication(workflow, api, defs);
  assert.deepEqual(validation.errors, []);
  const stack = validation.inputs.find((input) => input.type === "lora_stack");
  assert.equal(stack.minimum, -1);
  // Zero is still what gets published as the default, whichever way the range goes.
  assert.deepEqual(stack.default, control.default);
});

test("publisher carries a preferred strength into the contract and the inventory", () => {
  const { api, workflow, defs } = fixture();
  api[2].inputs.minimum = -1;
  const catalog = JSON.parse(api[2].inputs.catalog_json);
  catalog[0].preferred_strength = -1;
  api[2].inputs.catalog_json = JSON.stringify(catalog);
  const validation = validatePublication(workflow, api, defs);
  assert.deepEqual(validation.errors, []);
  const stack = validation.inputs.find((input) => input.type === "lora_stack");
  assert.equal(stack.items[0].preferred_strength, -1);
  assert.equal(Object.hasOwn(stack.items[1], "preferred_strength"), false);
  assert.doesNotMatch(JSON.stringify(stack), /filename|catalog_json|private\//);
  const metadata = derivePublishedMetadata(workflow, api, validation);
  assert.equal(metadata.technical_inventory.loras[0].items[0].preferred_strength, -1);
  assert.doesNotMatch(JSON.stringify(metadata.technical_inventory.loras), /private\//);
});

test("recalling records predating LoRA controls restores an all-zero stack", () => {
  const contract = { inputs: [control] };
  const state = { parameters: { loras: [{ id: "b", strength: 2 }, { id: "a", strength: 1 }] } };
  const recalled = overwriteWithRecall(state, { source_available: false, parameters: { prompt: "old" }, input_definitions: [] }, contract);
  assert.deepEqual(recalled.parameters.loras, control.default);
});

// A published stack may reach below zero: zero is the only value that leaves the model
// alone, so a negative strength is a LoRA being applied, and everything that asks
// "is this LoRA in use?" has to ask it the same way.
const signed = { ...control, minimum: -1 };

test("a negative strength validates on the same grid as a positive one", () => {
  const at = (strength) => loraStackError(signed, [{ id: "a", strength }, { id: "b", strength: 0 }]);
  for (const strength of [-1, -0.95, -0.75, -0.05, -0, 0, 0.05, 1, 2]) assert.equal(at(strength), null);
  assert.equal(at(-0.73), "Use LoRA strength increments of 0.05.");
  assert.equal(at(-0.775), "Use LoRA strength increments of 0.05.");
  assert.equal(at(-1.05), "LoRA strength must be from -1 to 2.");
  assert.equal(at(2.05), "LoRA strength must be from -1 to 2.");
  assert.equal(at(3), "LoRA strength must be from -1 to 2.");
  // The server measures alignment from the published minimum and the browser from zero.
  // Those grids are only ever different for a contract that cannot be published: the
  // all-zero defaults have to clear the server's own check against the minimum first.
  assert.ok(loraStackError({ ...control, minimum: -1.02 }, [{ id: "a", strength: -1.02 }, { id: "b", strength: 0 }]));
});

test("a negatively weighted LoRA renders, reports and steps like a positive one", () => {
  const rows = [{ id: "a", strength: -1 }, { id: "b", strength: 0 }];
  assert.equal(formatLoraStrength(-1), "-1.00");
  assert.equal(formatLoraStrength(-0.75), "-0.75");
  const readOnly = loraStackMarkup(signed, rows);
  assert.match(readOnly, /data-lora-summary-id="a"/);
  assert.match(readOnly, /<span class="lm-summary-strength">-1\.00<\/span>/);
  assert.doesNotMatch(readOnly, /No LoRAs enabled|data-lora-summary-id="b"/);
  const editable = loraStackMarkup(signed, rows, {}, { editable: true });
  assert.match(editable, /min="-1" max="2" step="0\.05"/);
  assert.match(editable, /value="-1\.00"/);
  assert.match(editable, /Disable Alpha/);
  assert.doesNotMatch(editable, /Disable Beta/);
  // At the published floor the fine and coarse steps down are spent, the steps up are not.
  assert.match(editable, /Decrease Alpha strength" disabled/);
  assert.doesNotMatch(editable, /Increase Alpha strength" disabled/);
  assert.equal(steppedLoraStrength(signed, -1, -1), -1);
  assert.equal(steppedLoraStrength(signed, -1, 1), -0.5);
  assert.equal(steppedLoraStrength(signed, 0, -1), -0.5);
  assert.equal(steppedLoraStrength(signed, -0.05, -1), -0.55);
  assert.equal(steppedLoraStrength(signed, 1.85, 1), 2);
});

test("the gallery counts, orders and enables a negatively weighted LoRA", () => {
  const rows = [{ id: "a", strength: -1 }, { id: "b", strength: 0 }];
  const gallery = loraManagerMarkup({ control: signed, values: rows, memory: {} });
  assert.match(gallery, /1 of 2 enabled/);
  assert.match(gallery, /data-lora-order-id="a"/);
  assert.match(gallery, /data-lora-toggle="a"[^>]*aria-pressed="true"/);
  assert.match(gallery, /class="lm-card lm-card-minimal is-enabled" data-lora-id="a"/);
  assert.match(gallery, /class="lm-card lm-card-minimal" data-lora-id="b"/);
  assert.doesNotMatch(gallery, /no LoRA enabled/);
  assert.deepEqual(
    moveEnabledLora([{ id: "a", strength: 1 }, { id: "b", strength: -1 }], "b", 0),
    [{ id: "b", strength: -1 }, { id: "a", strength: 1 }],
  );
  // An all-zero stack is still "no LoRA enabled" whichever bounds it publishes under.
  assert.match(loraStackMarkup(signed, signed.default), /No LoRAs enabled/);
});

test("a negatively applied LoRA never names the Subject", () => {
  const triggered = {
    ...signed,
    items: [{ id: "a", label: "Alpha", trigger_word: "AlphaCharacter" }, { id: "b", label: "Beta" }],
  };
  assert.equal(subjectLoraTrigger(triggered, [{ id: "a", strength: -1 }, { id: "b", strength: 0 }]).triggerWord, null);
  assert.equal(subjectLoraTrigger(triggered, [{ id: "a", strength: 1 }, { id: "b", strength: 0 }]).triggerWord, "AlphaCharacter");
  // Copying a trigger word reads the catalog, so it works whichever way it is applied.
  assert.equal(loraTriggerWord(triggered.items[0]).text, "AlphaCharacter");
});

test("enabling a LoRA starts where the workflow says to pull it", () => {
  const wet = { id: "wet", label: "Wetness", preferred_strength: -1 };
  assert.equal(loraEnableStrength(signed, wet), -1);
  // Absent, zero, or unusable for this stack, enabling keeps its long-standing answer.
  assert.equal(loraEnableStrength(signed, { id: "a", label: "Alpha" }), 1);
  assert.equal(loraEnableStrength(control, wet), 1);
  assert.equal(loraEnableStrength(signed, { ...wet, preferred_strength: 0 }), 1);
  assert.equal(loraEnableStrength(signed, { ...wet, preferred_strength: -3 }), 1);
  assert.equal(loraEnableStrength(signed, { ...wet, preferred_strength: -0.73 }), 1);
  assert.equal(loraEnableStrength(signed, { ...wet, preferred_strength: "1" }), 1);
  assert.equal(loraEnableStrength(signed, undefined), 1);
  // A stack with no positive strength left to offer still enables the LoRA it points below zero.
  const sunken = { ...control, minimum: -1, maximum: 0.4, step: 1 };
  assert.equal(loraDefaultPositiveStrength(sunken), null);
  assert.equal(loraEnableStrength(sunken, wet), -1);
});

test("a negative strength survives request snapshots and exact recall", () => {
  const rows = [{ id: "a", strength: -1 }, { id: "b", strength: 0.05 }];
  const contract = { inputs: [signed] };
  const requested = parametersForRequest(contract, { loras: rows });
  rows[0].strength = 0;
  assert.equal(requested.loras[0].strength, -1);
  const recalled = overwriteWithRecall({}, { source_key: "source", parameters: requested });
  assert.deepEqual(recalled.parameters.loras, [{ id: "a", strength: -1 }, { id: "b", strength: 0.05 }]);
  assert.deepEqual(clientValidate(contract, requested), {});
  // A stack that only publishes the positive side still rejects the negative one.
  assert.match(clientValidate({ inputs: [control] }, { loras: [{ id: "a", strength: -1 }, { id: "b", strength: 0 }] }).loras, /must be from 0 to 2/);
});
