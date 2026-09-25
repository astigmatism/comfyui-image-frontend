import assert from "node:assert/strict";
import test from "node:test";
import { catalogSource, reconcileSourceKey, promptRuntimeId, promptRuntimeError } from "../src/prompt-routing.mjs";
import { sameAutomationConfiguration } from "../src/auto-generation-sync.mjs";
import { generationPanelMarkup, generationActivityMarkup } from "../src/render.mjs";

const revision = { publication_id: "publication", workflow_sha256: "workflow", api_sha256: "api", manifest_sha256: "manifest" };
const source = { source_key: "cpu-text", instance_id: "promptgen", revision,
  display_name: "Dataset prompt", output_kind: "text", available: true,
  interface: { inputs: [], outputs: [] },
  replicas: [
    { instance_id: "primary", source_key: "gpu-text", revision, available: true },
    { instance_id: "promptgen", source_key: "cpu-text", revision, available: true },
  ],
};
const state = () => ({
  promptGeneration: { enabled: true, active_source: source.source_key, sources: {} },
  promptGeneratorSource: source, promptGeneratorSources: [source],
  comfyuiInstancesStatus: "ready", defaultComfyuiInstanceId: "primary", textComfyuiInstanceId: "promptgen",
  selectedComfyuiInstanceId: "primary",
  comfyuiInstances: [{ id: "primary", label: "Primary", available: true }, { id: "promptgen", label: "Prompt Generator", available: true }],
});

test("text default, explicit override, and source fallback are independent of image selection", () => {
  const value = state();
  assert.equal(promptRuntimeId(value), "promptgen");
  value.promptGeneration.runtime_id = "primary";
  assert.equal(promptRuntimeId(value), "primary");
  value.selectedComfyuiInstanceId = "promptgen";
  assert.equal(promptRuntimeId(value), "primary");
  value.promptGeneration.runtime_id = null;
  value.textComfyuiInstanceId = null;
  assert.equal(promptRuntimeId(value), source.instance_id);
});

test("saved alias values survive deduplication without merging unrelated names", () => {
  const saved = { "gpu-text": { values: { subject: "Mira", seed: "17" }, explicitInputIds: ["subject"] } };
  assert.equal(catalogSource([source], "gpu-text"), source);
  assert.equal(reconcileSourceKey([source], "gpu-text", saved), "cpu-text");
  assert.deepEqual(saved["cpu-text"], saved["gpu-text"]);
  assert.notEqual(saved["cpu-text"], saved["gpu-text"]);
  assert.equal(reconcileSourceKey([source], "unrelated", saved), "unrelated");
});

test("missing, drifted, offline, and removed text targets do not fall back", () => {
  const value = state();
  assert.equal(promptRuntimeError(value), null);
  value.promptGeneration.runtime_id = "gone";
  assert.match(promptRuntimeError(value), /configured/);
  value.promptGeneration.runtime_id = "promptgen";
  value.comfyuiInstances[1].available = false;
  assert.match(promptRuntimeError(value), /unavailable/);
  value.comfyuiInstances[1].available = true;
  value.promptGeneratorSource = structuredClone(source);
  value.promptGeneratorSource.replicas[1].revision = { ...revision, api_sha256: "drift" };
  assert.match(promptRuntimeError(value), /different publication revision/);
  value.promptGeneratorSource.replicas.pop();
  assert.match(promptRuntimeError(value), /does not have/);
  assert.equal(promptRuntimeId(value), "promptgen");
});

test("server-populated text pins reconcile but explicit runtime edits remain changes", () => {
  const snapshot = { generation: { source_key: "image", comfyui_instance_id: "primary" }, prompt_generation: { source_key: "cpu-text", revision, parameters: {} } };
  const pinned = structuredClone(snapshot);
  pinned.prompt_generation.comfyui_instance_id = "promptgen";
  assert.equal(sameAutomationConfiguration(snapshot, pinned), true);
  snapshot.prompt_generation.comfyui_instance_id = "primary";
  assert.equal(sameAutomationConfiguration(snapshot, pinned), false);
  snapshot.prompt_generation.comfyui_instance_id = "promptgen";
  snapshot.generation.comfyui_instance_id = "promptgen";
  assert.equal(sameAutomationConfiguration(snapshot, pinned), false);
});

test("prompt runtime selector and automatic status show separate targets", () => {
  const value = { ...state(), parameters: {}, sources: [source], promptAssistant: {},
    activeSource: { ...source, output_kind: "image" }, activeSourceKey: "image",
    controlSectionOpen: { "prompt-generation": true }, generationQuantity: 1 };
  const markup = generationPanelMarkup(value, value.activeSource, { inputs: [
    { id: "prompt", label: "Prompt", type: "multiline_string", semantic_role: "positive_prompt", default: "" },
  ], outputs: [] });
  assert.match(markup, /Prompt runtime/);
  assert.match(markup, /id="prompt-generation-runtime"/);
  assert.match(markup, /value="promptgen" selected/);
  value.autoGenerate = true;
  value.maxAutoGenerations = 20;
  value.automation = { snapshot: { generation: { comfyui_instance_id: "primary" }, prompt_generation: { comfyui_instance_id: "promptgen" } } };
  const activity = generationActivityMarkup(value);
  assert.match(activity, /Image runtime: Primary/);
  assert.match(activity, /Prompt runtime: Prompt Generator/);
});
