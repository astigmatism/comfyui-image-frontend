import assert from "node:assert/strict";
import test from "node:test";

import {
  applyChoiceStrengthDefaults,
  clientValidate,
  choiceStrengthCompanion,
  comparisonInputs,
  comparisonInterface,
  comparisonParametersForRequest,
  controlPresentation,
  createLatestRequestGate,
  defaultsForContract,
  defaultsForInterface,
  formatTimelineMonth,
  generationSourceModelVariant,
  insertTranscription,
  latestCompletedImageGeneration,
  migrateInterfaceState,
  missingComparisonRoles,
  normalizeInputValue,
  overwriteWithRecall,
  parametersForRequest,
  photoViewerImageLayout,
  reconcileInterfaceValues,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionSummary,
  scaleToLayout,
  seedAllowsRandom,
  seedFormValue,
  snapResolutionValue,
  sortGenerationsNewestFirst,
  sortInterfaceInputs,
  validTimelineMonth,
} from "../src/lib.mjs";

test("timeline months validate exactly and format without a local-midnight shift", () => {
  assert.equal(validTimelineMonth("2026-01"), "2026-01");
  assert.equal(formatTimelineMonth("2026-01", "en-US"), "January 2026");
  for (const malformed of ["2026", "2026-00", "2026-13", "2200-01", " 2026-01 ", 202601]) {
    assert.equal(validTimelineMonth(malformed), "");
    assert.equal(formatTimelineMonth(malformed, "en-US"), "");
  }
});

test("model variants match only the public parameter id and value", () => {
  const first = {
    parameter_id: "checkpoint",
    value: "v4_int8",
    label: "Fixture V4 INT8",
    released_month: "2026-07",
  };
  const generationSource = {
    base_model: {
      timeline: {
        model_variants: [
          first,
          { parameter_id: "secondary_checkpoint", value: "v4_int8", label: first.label },
        ],
      },
    },
  };

  assert.equal(generationSourceModelVariant(generationSource, "checkpoint", "v4_int8"), first);
  assert.equal(generationSourceModelVariant(generationSource, "checkpoint", first.label), null);
  assert.equal(generationSourceModelVariant(generationSource, "unknown", "v4_int8"), null);
  assert.equal(generationSourceModelVariant(generationSource, "secondary_checkpoint", "v4_int8")?.parameter_id, "secondary_checkpoint");
  assert.doesNotMatch(JSON.stringify(first), /binding|filename|path|safetensors/);
});

test("voice transcripts insert at or replace the saved text selection", () => {
  assert.deepEqual(insertTranscription("hello world", "brave", 5, 5), {
    value: "hello brave world",
    cursor: 11,
  });
  assert.deepEqual(insertTranscription("paint the daytime sky", "nighttime", 10, 17), {
    value: "paint the nighttime sky",
    cursor: 19,
  });
  assert.deepEqual(insertTranscription("", "  a moonlit lake  ", 0, 0), {
    value: "a moonlit lake",
    cursor: 14,
  });
});

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
      semantic_role: "height",
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
      semantic_role: "width",
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
      semantic_role: "seed",
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

test("image controls require an opaque asset and serialize no preview metadata", () => {
  const imageInterface = {
    inputs: [{ id: "reference_image", type: "image", required: true }],
  };
  assert.match(clientValidate(imageInterface, {}).reference_image, /Required/);
  assert.match(
    clientValidate(imageInterface, { reference_image: { preview_url: "/private" } })
      .reference_image,
    /valid image/,
  );
  assert.deepEqual(
    parametersForRequest(imageInterface, {
      reference_image: {
        asset_id: "opaque-asset",
        preview_url: "/api/uploads/opaque-asset/content",
        width: 384,
        height: 640,
        sha256: "diagnostic-only",
      },
    }),
    { reference_image: { asset_id: "opaque-asset" } },
  );
});

test("generations sort by request acceptance time newest first with the API tie-breaker", () => {
  const generations = [
    { id: "older", accepted_at: "2026-07-14T12:00:00.100000Z" },
    { id: "same-a", accepted_at: "2026-07-14T12:00:01.123456Z" },
    { id: "newest", accepted_at: "2026-07-14T12:00:02.000000Z" },
    { id: "same-z", accepted_at: "2026-07-14T12:00:01.123456Z" },
    { id: "microsecond-newer", accepted_at: "2026-07-14T12:00:01.123457Z" },
  ];

  assert.deepEqual(
    sortGenerationsNewestFirst(generations).map((generation) => generation.id),
    ["newest", "microsecond-newer", "same-z", "same-a", "older"],
  );
  assert.deepEqual(generations.map((generation) => generation.id), [
    "older",
    "same-a",
    "newest",
    "same-z",
    "microsecond-newer",
  ]);
});

test("comparison requests map only prompt, resolution, and one concrete seed by semantic role", () => {
  const target = {
    inputs: [
      { id: "text", type: "string", semantic_role: "positive_prompt" },
      { id: "image_width", type: "integer", semantic_role: "width" },
      { id: "image_height", type: "integer", semantic_role: "height" },
      { id: "noise_seed", type: "seed", semantic_role: "seed" },
      { id: "steps", type: "integer", semantic_role: "iteration_count", default: 20 },
    ],
  };
  const values = {
    prompt: "same scene",
    width: 1024,
    height: 1600,
    seed: { mode: "random", value: "0" },
    knpv4_1_strength: 0.7,
  };

  assert.deepEqual(
    comparisonParametersForRequest(publishedInterface, values, target, "424242"),
    {
      text: "same scene",
      image_width: 1024,
      image_height: 1600,
      noise_seed: "424242",
    },
  );
  assert.deepEqual(
    comparisonInputs(publishedInterface).map((input) => input.semantic_role),
    ["height", "positive_prompt", "width", "seed"],
  );
  assert.deepEqual(
    comparisonInterface(publishedInterface).inputs.map((input) => input.semantic_role),
    ["height", "positive_prompt", "width", "seed"],
  );
  assert.deepEqual(missingComparisonRoles(publishedInterface), []);
});

test("comparison requests omit roles a target does not publish and ignore ambiguous mappings", () => {
  const source = {
    inputs: [
      { id: "prompt_a", type: "string", semantic_role: "positive_prompt" },
      { id: "prompt_b", type: "string", semantic_role: "positive_prompt" },
      { id: "width", type: "integer", semantic_role: "width" },
      { id: "seed", type: "seed", semantic_role: "seed" },
    ],
  };
  const target = {
    inputs: [
      { id: "prompt", type: "string", semantic_role: "positive_prompt" },
      { id: "width", type: "integer", semantic_role: "width" },
    ],
  };
  assert.deepEqual(
    comparisonParametersForRequest(
      source,
      { prompt_a: "one", prompt_b: "two", width: 768, seed: { mode: "fixed", value: "9" } },
      target,
    ),
    { width: 768 },
  );
  assert.deepEqual(missingComparisonRoles(target), ["height", "seed"]);
});

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

test("slideshow selects only the newest fully completed image generation", () => {
  const completed = {
    id: "completed",
    accepted_at: "2026-07-15T12:00:00Z",
    status: "succeeded",
    display_artifact: { kind: "image", state: "final" },
  };
  assert.equal(
    latestCompletedImageGeneration([
      {
        id: "older-completed",
        accepted_at: "2026-07-15T11:00:00Z",
        status: "succeeded",
        display_artifact: { kind: "image", state: "final" },
      },
      completed,
      {
        id: "prototype",
        accepted_at: "2026-07-15T12:02:00Z",
        status: "running",
        display_artifact: { kind: "image", state: "provisional" },
      },
      {
        id: "failed",
        accepted_at: "2026-07-15T12:03:00Z",
        status: "failed_with_artifacts",
        display_artifact: { kind: "image", state: "best_available" },
      },
    ]),
    completed,
  );
  assert.equal(
    latestCompletedImageGeneration([
      {
        id: "no-image",
        accepted_at: "2026-07-15T12:04:00Z",
        status: "succeeded",
        display_artifact: null,
      },
    ]),
    null,
  );
});

test("photo viewer fill zoom anchors overflow at the top of the viewport", () => {
  assert.deepEqual(photoViewerImageLayout(1600, 900, 800, 800), {
    width: 800,
    height: 450,
    fillZoom: 800 / 450,
    fillPanY: 0,
    oneToOneZoom: 2,
  });
  assert.deepEqual(photoViewerImageLayout(900, 1600, 800, 800), {
    width: 450,
    height: 800,
    fillZoom: 800 / 450,
    fillPanY: (800 * (800 / 450) - 800) / 2,
    oneToOneZoom: 2,
  });
  assert.equal(photoViewerImageLayout(0, 1600, 800, 800), null);
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

test("recall preserves the current source and migrates compatible historical metadata when its source is gone", () => {
  const currentContract = {
    inputs: [
      { id: "current_prompt", type: "text", semantic_role: "positive_prompt", default: "" },
      { id: "current_width", type: "integer", semantic_role: "width", default: 512 },
      { id: "current_seed", type: "seed", semantic_role: "seed" },
      { id: "current_style", type: "text", default: "cinematic" },
    ],
  };
  const state = {
    activeSourceKey: "current-source",
    activeProfileId: "current-source",
    parameters: {
      current_prompt: "unsaved",
      current_width: 512,
      current_seed: { mode: "random", value: "" },
      current_style: "watercolor",
    },
    explicitParameterIds: new Set(["current_style"]),
    selectedRevision: { publication_id: "current-revision" },
    promptAssistant: { available: true, message: null },
  };
  const recalled = overwriteWithRecall(
    state,
    {
      source_available: false,
      source_key: "missing-source",
      revision: { publication_id: "historical-revision" },
      parameters: {
        old_prompt: "historical prompt",
        old_width: 1024,
        old_seed: "424242",
      },
      input_definitions: [
        { id: "old_prompt", type: "text", semantic_role: "positive_prompt" },
        { id: "old_width", type: "integer", semantic_role: "width" },
        { id: "old_seed", type: "seed", semantic_role: "seed" },
      ],
    },
    currentContract,
  );
  assert.equal(recalled.activeSourceKey, "current-source");
  assert.equal(recalled.activeProfileId, "current-source");
  assert.deepEqual(recalled.selectedRevision, { publication_id: "current-revision" });
  assert.deepEqual(recalled.parameters, {
    current_prompt: "historical prompt",
    current_width: 1024,
    current_seed: { mode: "fixed", value: "424242" },
    current_style: "watercolor",
  });
  assert.deepEqual(
    new Set(recalled.explicitParameterIds),
    new Set(["current_prompt", "current_width", "current_seed", "current_style"]),
  );
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

test("published inputs put seed first, then sort by tier, order, group, and id", () => {
  const sorted = sortInterfaceInputs([
    ...publishedInterface.inputs,
    { id: "zeta", type: "string", group: "B", advanced: false },
    { id: "alpha", type: "string", group: "A", advanced: false },
  ]);
  assert.deepEqual(sorted.map((input) => input.id), [
    "seed",
    "prompt",
    "width",
    "height",
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

test("source changes migrate compatible prompt, resolution, seed, and shared controls", () => {
  const source = structuredClone(publishedInterface);
  source.inputs = source.inputs
    .filter((input) => input.id !== "enable_seedvr2_upscale")
    .map((input) => {
      const renamed = {
        prompt: "recalled_prompt",
        width: "recalled_width",
        height: "recalled_height",
        seed: "recalled_seed",
      }[input.id];
      return renamed ? { ...input, id: renamed } : input;
    });
  const target = structuredClone(publishedInterface);
  target.inputs.push({
    id: "target_only",
    label: "Target only",
    type: "string",
    default: "target default",
  });
  const base = {
    ...defaultsForInterface(target),
    prompt: "stale target prompt",
    target_only: "remembered target value",
  };
  const migrated = migrateInterfaceState(
    target,
    source,
    {
      recalled_prompt: "recalled final prompt",
      recalled_width: 1024,
      recalled_height: 1600,
      recalled_seed: { mode: "fixed", value: "424242" },
    },
    ["recalled_prompt", "recalled_width", "recalled_height", "recalled_seed"],
    base,
    ["target_only"],
  );

  assert.equal(migrated.values.prompt, "recalled final prompt");
  assert.equal(migrated.values.width, 1024);
  assert.equal(migrated.values.height, 1600);
  assert.deepEqual(migrated.values.seed, { mode: "fixed", value: "424242" });
  assert.equal(migrated.values.target_only, "remembered target value");
  assert.deepEqual(new Set(migrated.explicitInputIds), new Set([
    "prompt",
    "width",
    "height",
    "seed",
    "target_only",
  ]));
});

test("source changes keep destination defaults for incompatible and ambiguous controls", () => {
  const source = {
    inputs: [
      { id: "old_choice", type: "choice", semantic_role: "style", choices: [{ value: "old" }] },
      { id: "first_toggle", type: "boolean", semantic_role: "feature_toggle" },
      { id: "second_toggle", type: "boolean", semantic_role: "feature_toggle" },
    ],
  };
  const target = {
    inputs: [
      {
        id: "new_choice",
        type: "choice",
        semantic_role: "style",
        default: "new",
        choices: [{ value: "new" }],
      },
      { id: "toggle", type: "boolean", semantic_role: "feature_toggle", default: false },
    ],
  };
  const migrated = migrateInterfaceState(
    target,
    source,
    { old_choice: "old", first_toggle: true, second_toggle: true },
    ["old_choice", "first_toggle", "second_toggle"],
  );
  assert.deepEqual(migrated, {
    values: { new_choice: "new", toggle: false },
    explicitInputIds: [],
  });
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
