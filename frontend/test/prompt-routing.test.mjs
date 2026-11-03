import assert from "node:assert/strict";
import test from "node:test";
import { catalogSource, defaultSourceKey, promptSourceDraft, reconcileSourceKey, promptRuntimeId, promptRuntimeError } from "../src/prompt-routing.mjs";
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

test("text assignment ignores stale preferences and has no fallback", () => {
  const value = state();
  assert.equal(promptRuntimeId(value), "promptgen");
  value.promptGeneration.runtime_id = "primary";
  assert.equal(promptRuntimeId(value), "promptgen");
  value.selectedComfyuiInstanceId = "promptgen";
  assert.equal(promptRuntimeId(value), "promptgen");
  value.promptGeneration.runtime_id = null;
  value.textComfyuiInstanceId = null;
  assert.equal(promptRuntimeId(value), null);
  assert.match(promptRuntimeError(value), /not configured/);
});

test("saved alias values survive deduplication without merging unrelated names", () => {
  const saved = { "gpu-text": { values: { subject: "Mira", seed: "17" }, explicitInputIds: ["subject"] } };
  assert.equal(catalogSource([source], "gpu-text"), source);
  assert.equal(reconcileSourceKey([source], "gpu-text", saved), "cpu-text");
  assert.deepEqual(saved["cpu-text"], saved["gpu-text"]);
  assert.notEqual(saved["cpu-text"], saved["gpu-text"]);
  assert.equal(reconcileSourceKey([source], "unrelated", saved), "unrelated");
});

test("CPU publication health is authoritative and cached jobs may wait", () => {
  const value = state();
  assert.equal(promptRuntimeError(value), null);
  value.promptGeneration.runtime_id = "gone";
  assert.equal(promptRuntimeError(value), null);
  value.comfyuiInstances[1].available = false;
  assert.equal(promptRuntimeError(value), null);
  value.promptGeneratorSource = { ...source, available: false };
  assert.match(promptRuntimeError(value), /unavailable/);
  value.promptGeneratorSource = { ...source, instance_id: "primary" };
  assert.match(promptRuntimeError(value), /assigned CPU/);
  value.textComfyuiInstanceId = "removed";
  assert.match(promptRuntimeError(value), /not configured/);
});

test("server-owned stage pins never cause automation settings updates", () => {
  const snapshot = { generation: { source_key: "image", comfyui_instance_id: "primary" }, prompt_generation: { source_key: "cpu-text", revision, parameters: {} } };
  const pinned = structuredClone(snapshot);
  pinned.prompt_generation.comfyui_instance_id = "promptgen";
  assert.equal(sameAutomationConfiguration(snapshot, pinned), true);
  snapshot.prompt_generation.comfyui_instance_id = "primary";
  assert.equal(sameAutomationConfiguration(snapshot, pinned), true);
  snapshot.prompt_generation.comfyui_instance_id = "promptgen";
  snapshot.generation.comfyui_instance_id = "promptgen";
  assert.equal(sameAutomationConfiguration(snapshot, pinned), true);
});

test("runtime selectors are absent and automatic status reports both assignments", () => {
  const value = { ...state(), parameters: {}, sources: [source], promptAssistant: {},
    activeSource: { ...source, output_kind: "image" }, activeSourceKey: "image",
    controlSectionOpen: { "prompt-generation": true }, generationQuantity: 1 };
  const markup = generationPanelMarkup(value, value.activeSource, { inputs: [
    { id: "prompt", label: "Prompt", type: "multiline_string", semantic_role: "positive_prompt", default: "" },
  ], outputs: [] });
  assert.doesNotMatch(markup, /id="prompt-generation-runtime"|id="comfyui-instance"/);
  assert.match(markup, /id="prompt-generation-source"/);
  value.autoGenerate = true;
  value.maxAutoGenerations = 20;
  value.automation = { snapshot: { generation: { comfyui_instance_id: "primary" }, prompt_generation: { comfyui_instance_id: "promptgen" } } };
  const activity = generationActivityMarkup(value);
  assert.equal(activity, ""); // Enabled automation alone is not accepted image work.
});

test("the source select has no empty choice and defaults to an available source", () => {
  const offline = { ...source, source_key: "offline", available: false };
  assert.equal(defaultSourceKey([offline, source]), "cpu-text");
  assert.equal(defaultSourceKey([offline]), null);
  assert.equal(defaultSourceKey([]), null);
  const value = { ...state(), parameters: {}, sources: [source], promptAssistant: {},
    activeSource: { ...source, output_kind: "image" }, activeSourceKey: "image",
    controlSectionOpen: { "prompt-generation": true }, generationQuantity: 1 };
  const markup = generationPanelMarkup(value, value.activeSource, { inputs: [], outputs: [] });
  assert.doesNotMatch(markup, /Choose a prompt source|<option value=""/);
  value.promptGeneratorSource = null;
  value.promptGeneratorsLoaded = false;
  assert.match(promptRuntimeError(value), /Loading prompt sources/);
  value.promptGeneratorsLoaded = true;
  value.promptGeneration.active_source = null;
  assert.match(promptRuntimeError(value), /No prompt sources are available/);
  assert.doesNotMatch(promptRuntimeError(value), /Choose/);
});

const textContract = (...inputs) => ({ inputs, outputs: [] });
const subjectInput = { id: "subject_name", label: "Subject name", type: "string", semantic_role: "custom", default: "Mira" };

test("changing the prompt source carries the draft the destination accepts", () => {
  const previous = {
    interface: textContract(subjectInput,
      { id: "style", label: "Style", type: "string", default: "flat" },
      { id: "seed", label: "Seed", type: "seed", minimum: 0, maximum: 9, default_mode: "random", default: 1 }),
    values: { subject_name: "Mira", style: "baroque", seed: { mode: "fixed", value: "7" } },
    explicitInputIds: ["subject_name", "style", "seed"],
  };
  const next = {
    interface: textContract(subjectInput,
      { id: "style", label: "Style", type: "string", default: "flat" },
      { id: "voice", label: "Voice", type: "string", default: "close" }),
    values: { subject_name: "Elsa", style: "noir", voice: "distant" },
    explicitInputIds: ["style"],
  };
  const carried = promptSourceDraft({ previous, next });
  // The subject follows the person to the new source, and an input only the destination
  // declares keeps its own value; a seed the destination never published is dropped.
  assert.deepEqual(carried.values, { subject_name: "Mira", style: "baroque", voice: "distant" });
  assert.deepEqual([...carried.explicitInputIds].sort(), ["style", "subject_name"]);
  assert.equal(carried.subjectApplied, true);

  // A published trigger word outranks the subject either source was holding.
  assert.equal(promptSourceDraft({ previous, next, subjectTrigger: "TifaLockhart" }).values.subject_name, "TifaLockhart");

  // A choice the destination does not publish falls back to its own value rather than an empty field.
  const previousChoice = {
    interface: textContract(subjectInput, { id: "mood", label: "Mood", type: "choice", default: "calm", choices: [{ value: "calm" }, { value: "storm" }] }),
    values: { subject_name: "Mira", mood: "storm" },
    explicitInputIds: ["mood"],
  };
  const nextChoice = {
    interface: textContract(subjectInput, { id: "mood", label: "Mood", type: "choice", default: "calm", choices: [{ value: "calm" }, { value: "bright" }] }),
    values: { subject_name: "Elsa", mood: "bright" },
    explicitInputIds: [],
  };
  assert.deepEqual(promptSourceDraft({ previous: previousChoice, next: nextChoice }).values, { subject_name: "Mira", mood: "bright" });
});

test("a prompt source left with nothing to say about the subject keeps the destination's own", () => {
  const next = { interface: textContract(subjectInput), values: { subject_name: "Elsa" }, explicitInputIds: ["subject_name"] };
  const blank = { interface: textContract(subjectInput), values: { subject_name: "   " }, explicitInputIds: ["subject_name"] };
  const cleared = promptSourceDraft({ previous: blank, next });
  assert.deepEqual(cleared.values, { subject_name: "Elsa" });
  assert.equal(cleared.subjectApplied, false);

  // An unsaved destination subject is not promoted to an edit just because the previous source was blank.
  const untouched = promptSourceDraft({ previous: blank, next: { ...next, explicitInputIds: [] } });
  assert.deepEqual(untouched.values, { subject_name: "Elsa" });
  assert.deepEqual(untouched.explicitInputIds, []);

  // A trigger word still speaks when the source being left has no draft of its own.
  const firstUse = promptSourceDraft({ next, subjectTrigger: "TifaLockhart" });
  assert.deepEqual(firstUse.values, { subject_name: "TifaLockhart" });
  assert.deepEqual(firstUse.explicitInputIds, ["subject_name"]);

  // And a source with no subject input at all is left exactly as it arrived.
  const plain = { interface: textContract({ id: "voice", label: "Voice", type: "string", default: "close" }), values: { voice: "distant" }, explicitInputIds: [] };
  const unrelated = promptSourceDraft({ previous: blank, next: plain, subjectTrigger: "TifaLockhart" });
  assert.deepEqual(unrelated.values, { voice: "distant" });
  assert.equal(unrelated.subjectApplied, false);
});
