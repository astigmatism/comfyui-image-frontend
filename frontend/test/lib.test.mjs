import assert from "node:assert/strict";
import test from "node:test";

import {
  applyChoiceStrengthDefaults,
  clientValidate,
  choiceStrengthCompanion,
  controlPresentation,
  createLatestRequestGate,
  defaultsForContract,
  defaultsForInterface,
  footerText,
  normalizeInputValue,
  overwriteWithRecall,
  parametersForRequest,
  reconcileInterfaceValues,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionSummary,
  scaleToLayout,
  seedAllowsRandom,
  seedFormValue,
  snapResolutionValue,
  sortInterfaceInputs,
} from "../src/lib.mjs";

const publishedInterface = {
  inputs: [
    {
      id: "knpv4_1_strength",
      label: "LoRA strength",
      type: "number",
      default: 1,
      minimum: 0,
      maximum: 2,
      step: 0.05,
      group: "Finishing",
      order: 60,
      advanced: true,
    },
    {
      id: "height",
      label: "Height",
      type: "integer",
      default: 1920,
      minimum: 16,
      maximum: 2048,
      step: 8,
      group: "Size",
      order: 30,
      advanced: false,
    },
    {
      id: "prompt",
      label: "Prompt",
      type: "string",
      default: "a tree with chickens",
      required: true,
      semantic_role: "positive_prompt",
      group: "Prompt",
      order: 10,
      advanced: false,
    },
    {
      id: "width",
      label: "Width",
      type: "integer",
      default: 1080,
      minimum: 16,
      maximum: 2048,
      step: 8,
      group: "Size",
      order: 20,
      advanced: false,
    },
    {
      id: "seed",
      label: "Seed",
      type: "seed",
      default: null,
      default_mode: "random",
      minimum: "0",
      maximum: "9223372036854775807",
      group: "Sampling",
      order: 40,
      advanced: false,
    },
    {
      id: "enable_seedvr2_upscale",
      label: "Enable SeedVR2 upscale",
      type: "boolean",
      default: false,
      group: "Finishing",
      order: 50,
      advanced: false,
    },
  ],
};

const choiceInterface = {
  inputs: [
    {
      id: "lora",
      label: "LoRA",
      type: "choice",
      default: "knp_v4_1",
      semantic_role: "lora",
      advanced: true,
      group: "Advanced",
      order: 55,
      choices: [
        { value: "knp_v4_1", label: "KNP v4.1", default_strength: 1 },
        { value: "knp_v3_1", label: "KNP v3.1", default_strength: 0.5 },
        { value: "knp_v2", label: "KNP v2", default_strength: 1 },
        { value: "mysticxxx_krea2_v1", label: "MysticXXX Krea2 v1", default_strength: 1 },
      ],
    },
    {
      id: "lora_strength",
      label: "LoRA Strength",
      type: "number",
      default: 1,
      semantic_role: "lora",
      minimum: 0,
      maximum: 2,
      step: 0.05,
      advanced: true,
      group: "Advanced",
      order: 60,
    },
  ],
};

test("gallery scale spans compact thumbnails through a full-width card", () => {
  assert.deepEqual(scaleToLayout(100), { full: true, cardWidth: 1200 });
  assert.equal(scaleToLayout(0).cardWidth, 170);
  assert.ok(scaleToLayout(75).cardWidth > scaleToLayout(25).cardWidth);
});

test("recall immediately replaces source, controls, seed, and submitted prompt state", () => {
  const state = {
    activeProfileId: "newer",
    controls: { "prompt.text": "unsaved", "generation.seed": "random" },
    compositionId: "composition-old",
    promptAssistant: { available: true, message: null },
    fieldErrors: { "prompt.text": "Required" },
    formError: "Invalid",
  };
  const recalled = overwriteWithRecall(state, {
    profile_id: "historical",
    controls: { "prompt.text": "exact final prompt", "generation.seed": 424242 },
    identity: { workflow_version: "1.0.0" },
    prompt_assistant: { mode: "create", creative_direction: "historical direction", model: "m1" },
  });
  assert.equal(recalled.activeProfileId, "historical");
  assert.deepEqual(recalled.controls, {
    "prompt.text": "exact final prompt",
    "generation.seed": 424242,
  });
  assert.equal(recalled.compositionId, null);
  assert.deepEqual(recalled.fieldErrors, {});
  assert.equal(recalled.promptAssistant.available, true);
  assert.equal(recalled.promptAssistant.historicalModel, "m1");
});

test("contract defaults are cloned and capabilities disable rather than hide controls", () => {
  const contract = {
    controls: [{ id: "size", default: { width: 512, height: 512 } }],
  };
  const defaults = defaultsForContract(contract);
  defaults.size.width = 1024;
  assert.equal(contract.controls[0].default.width, 512);

  const presentation = controlPresentation(
    { capability: "upscale", conditions: [] },
    {},
    { upscale: { available: false, reason: "Model not installed." } },
  );
  assert.equal(presentation.visible, true);
  assert.equal(presentation.enabled, false);
  assert.equal(presentation.reason, "Model not installed.");
});

test("resolution constraints accept contract axis aliases and reject invalid requests", () => {
  const control = {
    id: "size.resolution",
    type: "resolution",
    required: true,
    constraints: {
      minimum_width: 64,
      maximum_width: 2048,
      minimum_height: 64,
      maximum_height: 2048,
      multiple: 8,
      maximum_pixels: 2_000_000,
    },
  };
  assert.deepEqual(resolutionConstraints(control), {
    minimumWidth: 64,
    maximumWidth: 2048,
    minimumHeight: 64,
    maximumHeight: 2048,
    widthStep: 8,
    heightStep: 8,
    multiple: 8,
    maximumPixels: 2_000_000,
  });
  assert.deepEqual(clientValidate({ controls: [control] }, { "size.resolution": { width: 512, height: 512 } }), {});
  assert.match(clientValidate({ controls: [control] }, { "size.resolution": { width: 510, height: 512 } })[control.id], /multiples of 8/);
  assert.match(clientValidate({ controls: [control] }, { "size.resolution": { width: 2048, height: 2048 } })[control.id], /exceeds/);
});

test("resolution grid mirrors Resolution Master snapping and live details", () => {
  const control = {
    constraints: {
      minimum_width: 64,
      maximum_width: 2048,
      minimum_height: 64,
      maximum_height: 2048,
      multiple: 8,
    },
  };
  assert.deepEqual(resolutionGridConstraints(control), {
    minimumWidth: 0,
    maximumWidth: 2048,
    minimumHeight: 0,
    maximumHeight: 2048,
    widthStep: 64,
    heightStep: 64,
  });
  assert.equal(snapResolutionValue(1051, 0, 2048, 64), 1024);
  assert.equal(snapResolutionValue(2029, 0, 2048, 64), 2048);
  assert.deepEqual(resolutionSummary(1024, 1600), {
    width: 1024,
    height: 1600,
    megapixels: "1.64",
    aspectRatio: "16:25",
    text: "1024 × 1600 · 1.64 MP · 16:25",
  });
});

test("footer uses only source, centered dot, and localized submission date text", () => {
  const value = footerText("Portrait Workflow", "2026-07-12T12:00:00Z", "en-US");
  assert.equal(value, "Portrait Workflow · Jul 12, 2026");
});

test("published inputs sort basic before advanced with deterministic order, group, and id fallbacks", () => {
  const sorted = sortInterfaceInputs([
    ...publishedInterface.inputs,
    { id: "zeta", type: "string", group: "B", advanced: false },
    { id: "alpha", type: "string", group: "A", advanced: false },
  ]);
  assert.deepEqual(sorted.map((input) => input.id), [
    "prompt",
    "width",
    "height",
    "seed",
    "enable_seedvr2_upscale",
    "alpha",
    "zeta",
    "knpv4_1_strength",
  ]);
});

test("random seeds are omitted and fixed decimal seeds round-trip without Number coercion", () => {
  const values = defaultsForInterface(publishedInterface);
  assert.deepEqual(values.seed, { mode: "random", value: "0" });
  assert.equal(Object.hasOwn(parametersForRequest(publishedInterface, values), "seed"), false);

  values.seed = { mode: "fixed", value: "9223372036854775807" };
  values.private_binding = "must not leave the browser";
  const parameters = parametersForRequest(publishedInterface, values);
  assert.equal(parameters.seed, "9223372036854775807");
  assert.equal(Object.hasOwn(parameters, "private_binding"), false);
  assert.deepEqual(clientValidate(publishedInterface, values), {});

  values.seed.value = "9223372036854775808";
  assert.match(clientValidate(publishedInterface, values).seed, /Maximum/);
});

test("required random seeds submit an explicit sentinel while fixed-mode seeds submit their default", () => {
  const requiredRandom = {
    inputs: [
      {
        id: "seed",
        type: "seed",
        required: true,
        default: null,
        default_mode: "random",
        minimum: "0",
        maximum: "1125899906842624",
      },
    ],
  };
  const randomValues = defaultsForInterface(requiredRandom);
  assert.equal(seedAllowsRandom(requiredRandom.inputs[0]), true);
  assert.deepEqual(parametersForRequest(requiredRandom, randomValues), { seed: "random" });

  const fixed = {
    inputs: [
      {
        id: "seed",
        type: "seed",
        required: true,
        default: "424242",
        default_mode: "fixed",
        minimum: "0",
        maximum: "1125899906842624",
      },
    ],
  };
  assert.equal(seedAllowsRandom(fixed.inputs[0]), false);
  assert.deepEqual(seedFormValue(fixed.inputs[0]), { mode: "fixed", value: "424242" });
  assert.deepEqual(seedFormValue(fixed.inputs[0], { mode: "random", value: "7" }), {
    mode: "fixed",
    value: "424242",
  });
  assert.deepEqual(parametersForRequest(fixed, defaultsForInterface(fixed)), { seed: "424242" });
});

test("latest request gate rejects stale completions and invalidated generations", () => {
  const gate = createLatestRequestGate();
  const first = gate.issue("generation-1");
  const second = gate.issue("generation-1");
  assert.equal(gate.isCurrent("generation-1", first), false);
  assert.equal(gate.isCurrent("generation-1", second), true);

  gate.invalidate("generation-1");
  assert.equal(gate.isCurrent("generation-1", second), false);
  const third = gate.issue("generation-1");
  gate.clear();
  assert.equal(gate.isCurrent("generation-1", third), false);
});

test("published numeric validation enforces integer and step contracts", () => {
  const values = defaultsForInterface(publishedInterface);
  values.width = 1080.5;
  assert.match(clientValidate(publishedInterface, values).width, /whole number/);
  values.width = 1080;
  values.knpv4_1_strength = 1.03;
  assert.match(clientValidate(publishedInterface, values).knpv4_1_strength, /increments of 0.05/);
});

test("integer controls reject values that cannot round-trip as safe JSON numbers", () => {
  const width = publishedInterface.inputs.find((input) => input.id === "width");
  const unsafe = normalizeInputValue(width, "9007199254740993");
  assert.equal(unsafe, "9007199254740993");
  assert.match(clientValidate({ inputs: [width] }, { width: unsafe }).width, /safe whole number/);

  const safe = normalizeInputValue(width, "2048");
  assert.equal(safe, 2048);
  assert.deepEqual(clientValidate({ inputs: [width] }, { width: safe }), {});
});

test("republished sources retain values only when public id and type still match", () => {
  const previous = { inputs: [{ id: "prompt", type: "string" }, { id: "seed", type: "string" }] };
  const values = { prompt: "retained", seed: "old string seed" };
  const reconciled = reconcileInterfaceValues(publishedInterface, values, previous);
  assert.equal(reconciled.prompt, "retained");
  assert.deepEqual(reconciled.seed, { mode: "random", value: "0" });
});

test("choice defaults and requests use stable public values", () => {
  const values = defaultsForInterface(choiceInterface);
  assert.deepEqual(values, { lora: "knp_v4_1", lora_strength: 1 });
  assert.deepEqual(clientValidate(choiceInterface, values), {});
  assert.deepEqual(parametersForRequest(choiceInterface, values), values);

  values.lora = "knp_v3_1";
  const initialized = applyChoiceStrengthDefaults(choiceInterface, values, ["lora"], "lora");
  assert.deepEqual(initialized, { lora: "knp_v3_1", lora_strength: 0.5 });
  assert.deepEqual(parametersForRequest(choiceInterface, initialized), initialized);
});

test("choice validation rejects empty, labeled, unknown, and private-looking values", () => {
  const required = {
    inputs: [{ ...choiceInterface.inputs[0], required: true }],
  };
  assert.match(clientValidate(required, { lora: null }).lora, /Required/);
  assert.match(clientValidate(required, { lora: "" }).lora, /knp_v4_1/);
  for (const value of ["", "KNP v3.1", "unknown", "Krea2/KNPV4.1_pre.safetensors"]) {
    const error = clientValidate(choiceInterface, { lora: value }).lora;
    assert.match(error, /knp_v4_1/);
    assert.doesNotMatch(error, /safetensors|Krea2\//);
  }
});

test("choice strength hints initialize companions without replacing explicit overrides", () => {
  assert.equal(choiceStrengthCompanion(choiceInterface, choiceInterface.inputs[0]).id, "lora_strength");
  const selected = { lora: "knp_v3_1", lora_strength: 1 };
  assert.equal(
    applyChoiceStrengthDefaults(choiceInterface, selected, ["lora"], "lora").lora_strength,
    0.5,
  );
  assert.equal(
    applyChoiceStrengthDefaults(
      choiceInterface,
      { ...selected, lora_strength: 0.7 },
      ["lora", "lora_strength"],
      "lora",
    ).lora_strength,
    0.7,
  );

  const withoutHint = structuredClone(choiceInterface);
  delete withoutHint.inputs[0].choices[1].default_strength;
  assert.equal(
    applyChoiceStrengthDefaults(withoutHint, selected, ["lora"], "lora").lora_strength,
    1,
  );

  const semanticFallback = structuredClone(choiceInterface);
  semanticFallback.inputs[1].id = "model_weight";
  assert.equal(
    choiceStrengthCompanion(semanticFallback, semanticFallback.inputs[0]).id,
    "model_weight",
  );
  const ambiguousChoices = structuredClone(semanticFallback);
  ambiguousChoices.inputs.push({
    ...structuredClone(ambiguousChoices.inputs[0]),
    id: "secondary_lora",
  });
  assert.equal(choiceStrengthCompanion(ambiguousChoices, ambiguousChoices.inputs[0]), null);
  assert.equal(choiceStrengthCompanion(ambiguousChoices, ambiguousChoices.inputs[2]), null);
  const mismatchedExact = structuredClone(choiceInterface);
  mismatchedExact.inputs[1].semantic_role = "unrelated_strength";
  assert.equal(choiceStrengthCompanion(mismatchedExact, mismatchedExact.inputs[0]), null);
  semanticFallback.inputs.push({ id: "clip_weight", type: "number", semantic_role: "lora" });
  assert.equal(choiceStrengthCompanion(semanticFallback, semanticFallback.inputs[0]), null);
});

test("choice reconciliation retains only values still declared by the current publication", () => {
  const previous = structuredClone(choiceInterface);
  const retained = reconcileInterfaceValues(
    choiceInterface,
    { lora: "knp_v3_1", lora_strength: 0.7 },
    previous,
    ["lora", "lora_strength"],
  );
  assert.deepEqual(retained, { lora: "knp_v3_1", lora_strength: 0.7 });

  const republished = structuredClone(choiceInterface);
  republished.inputs[0].default = "knp_v2";
  republished.inputs[0].choices = republished.inputs[0].choices.filter(
    (option) => option.value !== "knp_v3_1",
  );
  const reset = reconcileInterfaceValues(
    republished,
    { lora: "knp_v3_1", lora_strength: 1 },
    previous,
    ["lora"],
  );
  assert.deepEqual(reset, { lora: "knp_v2", lora_strength: 1 });
  assert.equal(
    reconcileInterfaceValues(choiceInterface, { lora: null }, null).lora,
    "knp_v4_1",
  );

  const changedType = structuredClone(previous);
  changedType.inputs[0].type = "string";
  assert.equal(
    reconcileInterfaceValues(choiceInterface, { lora: "knp_v2" }, changedType).lora,
    "knp_v4_1",
  );
});
