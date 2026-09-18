import assert from "node:assert/strict";
import test from "node:test";

import {
  createCoalescedTaskQueue,
  AUTO_GENERATE_COMPOSITION_MAX_ATTEMPTS,
  AUTO_GENERATE_COMPOSITION_RETRY_MAX_MS,
  DEFAULT_OPEN_CONTROL_SECTION_KINDS,
  MAX_GENERATION_QUANTITY,
  MIN_GENERATION_QUANTITY,
  activeSourceStorageKey,
  applyChoiceStrengthDefaults,
  autoGenerateCompositionRetryDelayMs,
  autoGenerationPromptAssistantFingerprint,
  clampGenerationQuantity,
  clientValidate,
  choiceStrengthCompanion,
  collectionAncestors,
  collectionDepth,
  collectionSubtree,
  collectionTreeRows,
  controlPresentation,
  controlSectionStorageKey,
  createLatestRequestGate,
  creativeDirectionStorageKey,
  defaultsForContract,
  defaultsForInterface,
  directionSignalNextStatus,
  formatTimelineMonth,
  generationSourceModelVariant,
  hasActiveGeneration,
  insertTranscription,
  isRetryablePromptAssistantError,
  latestCompletedImageGeneration,
  migrateInterfaceState,
  normalizeCheckpointTierLayout,
  normalizeSourceModelSelections,
  normalizeInputValue,
  normalizeStoredActiveSource,
  normalizeStoredControlSections,
  normalizeStoredCreativeDirectionDraft,
  normalizeStoredParameterState,
  overwriteWithRecall,
  parametersForRequest,
  parameterStateStorageKey,
  photoViewerImageLayout,
  loadRecentResolutions,
  recentResolutionKey,
  recordRecentResolution,
  RECENT_RESOLUTIONS_LIMIT,
  RESOLUTION_PRESET_GROUPS,
  recalledComfyuiInstanceState,
  removeRecentResolution,
  reconcileInterfaceValues,
  resolutionConstraints,
  resolutionGridConstraints,
  resolutionPresetForValue,
  resolutionPresets,
  resolutionSummary,
  scaleToLayout,
  seedAllowsRandom,
  seedFormValue,
  snapResolutionValue,
  sortGenerationsNewestFirst,
  sortInterfaceInputs,
  sourceModelParameterVariants,
  sourceModelSelectors,
  validTimelineMonth,
} from "../src/lib.mjs";

test("collection tree helpers resolve ancestry, subtree membership, depth, and display rows", () => {
  const collections = [
    { id: "alpha", parent_id: null, name: "Alpha" },
    { id: "beta", parent_id: "alpha", name: "Beta" },
    { id: "gamma", parent_id: "beta", name: "Gamma" },
    { id: "other", parent_id: null, name: "Other" },
  ];
  assert.deepEqual(
    collectionAncestors(collections, "gamma").map((item) => item.id),
    ["alpha", "beta", "gamma"],
  );
  assert.equal(collectionDepth(collections, "gamma"), 3);
  assert.deepEqual(
    collectionSubtree(collections, "alpha").map((item) => item.id),
    ["alpha", "beta", "gamma"],
  );
  assert.deepEqual(
    collectionTreeRows(collections).map(({ collection, depth }) => [
      collection.id,
      depth,
    ]),
    [
      ["alpha", 1],
      ["beta", 2],
      ["gamma", 3],
      ["other", 1],
    ],
  );
});

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

test("source model selectors normalize one published fanout parameter into scalar variants", () => {
  const source = {
    model_selectors: [
      {
        parameter_id: "checkpoint",
        label: "Checkpoint",
        default: "v2",
        choices: [
          { value: "v1", label: "Version 1", released_month: "2026-01" },
          { value: "v2", label: "Version 2" },
          { value: "v2", label: "Duplicate ignored" },
        ],
      },
      {
        parameter_id: "refiner",
        label: "Refiner",
        default: "r1",
        choices: [{ value: "r1", label: "Refiner 1" }],
      },
    ],
  };

  assert.deepEqual(
    sourceModelSelectors(source).map((selector) => selector.parameter_id),
    ["checkpoint"],
  );
  assert.deepEqual(normalizeSourceModelSelections(source), { checkpoint: ["v2"] });
  assert.deepEqual(
    normalizeSourceModelSelections(source, { checkpoint: ["missing"] }, { checkpoint: "v1" }),
    { checkpoint: ["v1"] },
  );
  assert.deepEqual(
    sourceModelParameterVariants(source, { checkpoint: ["v2", "v1"] }),
    [{ checkpoint: "v1" }, { checkpoint: "v2" }],
  );
  assert.equal(
    Array.isArray(sourceModelParameterVariants(source, { checkpoint: ["v1", "v2"] })[0].checkpoint),
    false,
  );
});

test("source model selectors derive directly from a canonical checkpoint detail", () => {
  const source = {
    model_selectors: [
      {
        parameter_id: "stale_model",
        label: "Stale model selector",
        default: "stale",
        choices: [{ value: "stale", label: "Stale" }],
      },
      {
        parameter_id: "checkpoint",
        label: "Projected checkpoint label",
        default: "v4_int8",
        choices: [
          {
            value: "v4_int8",
            label: "Projected V4 label",
            released_month: "2026-07",
          },
        ],
      },
    ],
    interface: {
      inputs: [
        {
          id: "checkpoint",
          type: "choice",
          label: "Checkpoint",
          description: "Selects the Moody Krea 2 diffusion checkpoint.",
          semantic_role: "model",
          advanced: true,
          default: "v4_int8",
          choices: [
            { value: "v4_int8", label: "Moody Krea 2 V4 INT8 ConvRot" },
            { value: "v5_bf16", label: "Moody Krea 2 V5 BF16" },
          ],
        },
      ],
    },
  };

  assert.deepEqual(sourceModelSelectors(source), [
    {
      parameter_id: "checkpoint",
      label: "Checkpoint",
      description: "Selects the Moody Krea 2 diffusion checkpoint.",
      default: "v4_int8",
      choices: [
        {
          value: "v4_int8",
          label: "Moody Krea 2 V4 INT8 ConvRot",
          released_month: "2026-07",
        },
        { value: "v5_bf16", label: "Moody Krea 2 V5 BF16" },
      ],
    },
  ]);
});

test("source model selectors ignore projected metadata when detail has no model input", () => {
  assert.deepEqual(
    sourceModelSelectors({
      model_selectors: [
        {
          parameter_id: "stale_model",
          label: "Stale model selector",
          default: "stale",
          choices: [{ value: "stale", label: "Stale" }],
        },
      ],
      interface: {
        inputs: [
          { id: "sampler", type: "choice", label: "Sampler", default: "euler", choices: [] },
        ],
      },
    }),
    [],
  );
});

test("checkpoint tier layouts preserve known order and append new choices to Unsorted", () => {
  const selector = {
    choices: [
      { value: "alpha", label: "Alpha" },
      { value: "beta", label: "Beta" },
      { value: "new", label: "New" },
    ],
  };
  assert.deepEqual(
    normalizeCheckpointTierLayout(selector, {
      top_picks: ["beta", "removed"],
      preferred: ["alpha", "beta"],
      occasional: [],
      unsorted: [],
      unknown: ["new"],
    }),
    {
      top_picks: ["beta"],
      preferred: ["alpha"],
      occasional: [],
      unsorted: ["new"],
    },
  );
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

test("active generation detection covers every non-terminal generation phase", () => {
  for (const status of ["queued", "dispatching", "running", "cancel_requested"]) {
    assert.equal(hasActiveGeneration([{ id: status, status }]), true);
  }
  assert.equal(
    hasActiveGeneration([
      { id: "complete", status: "succeeded" },
      { id: "failed", status: "failed_without_artifacts" },
      { id: "cancelled", status: "cancelled_without_artifacts" },
    ]),
    false,
  );
  assert.equal(hasActiveGeneration([]), false);
});

test("auto-generation fingerprints non-empty Prompt Assistant input for one prepared cycle", () => {
  const refine = autoGenerationPromptAssistantFingerprint({
    sourceKey: "landscape",
    mode: "refine",
    creativeDirection: "cinematic light",
    prompt: "a lighthouse",
    think: true,
  });
  assert.ok(refine);
  assert.notEqual(refine, autoGenerationPromptAssistantFingerprint({
    sourceKey: "landscape", sourceRevision: { workflow_sha256: "republished" },
    mode: "refine", creativeDirection: "cinematic light", prompt: "a lighthouse", think: true,
  }));
  assert.notEqual(refine, autoGenerationPromptAssistantFingerprint({
    sourceKey: "landscape", mode: "refine", creativeDirection: "cinematic light",
    prompt: "a lighthouse", think: true, instructions: "Write in French.",
  }));
  assert.equal(
    autoGenerationPromptAssistantFingerprint({
      sourceKey: "landscape",
      mode: "refine",
      creativeDirection: "   ",
      prompt: "a lighthouse",
    }),
    null,
  );
  assert.notEqual(
    refine,
    autoGenerationPromptAssistantFingerprint({
      sourceKey: "landscape",
      mode: "create",
      creativeDirection: "cinematic light",
      prompt: "a lighthouse",
    }),
  );
  assert.notEqual(
    refine,
    autoGenerationPromptAssistantFingerprint({
      sourceKey: "landscape",
      mode: "refine",
      creativeDirection: "cinematic light",
      prompt: "a mountain",
    }),
  );
  assert.notEqual(
    refine,
    autoGenerationPromptAssistantFingerprint({
      sourceKey: "landscape",
      mode: "refine",
      creativeDirection: "cinematic light",
      prompt: "a lighthouse",
      think: false,
    }),
  );
});

test("the creative-direction border signal invalidates applied text but keeps in-flight composition", () => {
  assert.equal(
    directionSignalNextStatus({ status: "composing", appliedValue: null, currentValue: "a lighthouse" }),
    "composing",
  );
  assert.equal(
    directionSignalNextStatus({ status: "applied", appliedValue: "composed text", currentValue: "composed text" }),
    "applied",
  );
  assert.equal(
    directionSignalNextStatus({ status: "applied", appliedValue: "composed text", currentValue: "composed text, edited" }),
    "idle",
  );
  assert.equal(
    directionSignalNextStatus({ status: "applied", appliedValue: "composed text", currentValue: "recalled prompt" }),
    "idle",
  );
  assert.equal(directionSignalNextStatus({ status: "idle", appliedValue: null, currentValue: "" }), "idle");
  assert.equal(directionSignalNextStatus({ status: "unexpected", appliedValue: "x", currentValue: "x" }), "idle");
});

test("auto-generation retries only recoverable Prompt Assistant failures with bounded backoff", () => {
  for (const code of [
    "ollama_output_budget_exhausted",
    "ollama_generate_unavailable",
    "ollama_generate_transport_error",
    "ollama_generate_timeout",
    "ollama_generate_invalid_json",
    "ollama_unavailable",
  ]) {
    assert.equal(isRetryablePromptAssistantError({ code }), true);
  }
  for (const code of [
    "ollama_invalid_response",
    "ollama_generate_rejected",
    "prompt_refinement_unchanged",
  ]) {
    assert.equal(isRetryablePromptAssistantError({ code }), false);
  }
  assert.equal(AUTO_GENERATE_COMPOSITION_MAX_ATTEMPTS, 3);
  assert.deepEqual(
    [1, 2, 3, 20].map(autoGenerateCompositionRetryDelayMs),
    [1_000, 2_000, 4_000, AUTO_GENERATE_COMPOSITION_RETRY_MAX_MS],
  );
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

test("recall restores creative direction, mode, instructions, and thinking mode", () => {
  const state = {
    activeProfileId: "current",
    controls: { "prompt.text": "draft", "generation.seed": "random" },
    promptAssistant: {
      available: true,
      mode: "create",
      creativeDirection: "current direction",
      think: true,
      instructionOverrides: { create: "current custom instructions" },
      defaultInstructions: { refine: "default refine", create: "default create" },
    },
  };
  const recalled = overwriteWithRecall(state, {
    source_key: "krea",
    parameters: { "prompt.text": "historical prompt" },
    revision: { publication_id: "p1" },
    prompt_assistant: {
      mode: "refine",
      creative_direction: "historical direction",
      instructions: "historical custom instructions",
      thinking_enabled: false,
      model: "ollama-model",
    },
  });
  assert.equal(recalled.promptAssistant.mode, "refine");
  assert.equal(recalled.promptAssistant.creativeDirection, "historical direction");
  assert.equal(recalled.promptAssistant.think, false);
  assert.equal(
    recalled.promptAssistant.instructionOverrides.refine,
    "historical custom instructions",
  );
  // The other mode's override is left untouched.
  assert.equal(
    recalled.promptAssistant.instructionOverrides.create,
    "current custom instructions",
  );
  assert.equal(recalled.promptAssistant.historicalModel, "ollama-model");
});

test("recall preserves the current thinking mode when the recall carries none", () => {
  const state = {
    activeProfileId: "current",
    controls: { "prompt.text": "draft" },
    promptAssistant: { available: true, think: true },
  };
  const recalled = overwriteWithRecall(state, {
    source_key: "krea",
    parameters: { "prompt.text": "historical prompt" },
    prompt_assistant: { mode: "create", creative_direction: "historical direction" },
  });
  assert.equal(recalled.promptAssistant.think, true);
  assert.equal(recalled.promptAssistant.creativeDirection, "historical direction");
});

test("recall restores a configured historical ComfyUI runtime even while it is offline", () => {
  const result = recalledComfyuiInstanceState(
    {
      selectedComfyuiInstanceId: "primary",
      comfyuiInstances: [
        { id: "primary", label: "Primary", available: true },
        { id: "worker-2", label: "Worker 2", available: true },
      ],
    },
    {
      comfyui_instance_id: "worker-2",
      comfyui_instance_label: "Worker 2",
      comfyui_instance_configured: true,
      comfyui_instance_available: false,
      comfyui_instance_warning: "Worker 2 is currently unavailable.",
    },
  );

  assert.equal(result.state.selectedComfyuiInstanceId, "worker-2");
  assert.equal(result.state.comfyuiInstanceSelectionInitialized, true);
  assert.equal(result.state.comfyuiInstanceError, "Worker 2 is currently unavailable.");
  assert.equal(
    result.state.comfyuiInstances.find((item) => item.id === "worker-2").available,
    false,
  );
  assert.equal(result.notice, "Worker 2 is currently unavailable.");
});

test("recall preserves the current runtime when the historical runtime was removed", () => {
  const current = {
    selectedComfyuiInstanceId: "primary",
    comfyuiInstances: [{ id: "primary", label: "Primary", available: true }],
  };
  const result = recalledComfyuiInstanceState(
    current,
    {
      comfyui_instance_id: "retired-worker",
      comfyui_instance_label: "Retired worker",
      comfyui_instance_configured: false,
      comfyui_instance_available: false,
      comfyui_instance_warning:
        "Retired worker is no longer configured. The current runtime selection was preserved.",
    },
  );

  const applied = { ...current, ...result.state };
  assert.equal(applied.selectedComfyuiInstanceId, "primary");
  assert.match(result.state.comfyuiInstanceWarning, /current runtime selection was preserved/);
  assert.match(result.notice, /Retired worker is no longer configured/);
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

test("resolution presets cover symmetric landscape and portrait sets", () => {
  const presets = resolutionPresets();
  // Ultra-wide is intentionally landscape-only, so symmetry applies to the
  // other groups only.
  const symmetric = RESOLUTION_PRESET_GROUPS.filter((group) => group.label !== "Ultra-wide").flatMap((group) =>
    group.options,
  );
  const landscape = symmetric.filter((preset) => preset.width > preset.height);
  const portrait = symmetric.filter((preset) => preset.width < preset.height);
  assert.equal(landscape.length, portrait.length);
  const swapped = new Set(portrait.map((preset) => `${preset.height}x${preset.width}`));
  for (const preset of landscape) {
    assert.ok(
      swapped.has(`${preset.width}x${preset.height}`),
      `${preset.width} × ${preset.height} should have a portrait counterpart`,
    );
  }
  assert.ok(resolutionPresets().length >= 15);
  for (const preset of presets) {
    assert.ok(preset.width % 8 === 0 && preset.height % 8 === 0, `${preset.label} should be divisible by 8`);
    assert.ok(Math.max(preset.width, preset.height) <= 2048, `${preset.label} should stay within the 2048 long-edge limit`);
  }
  const ultraWideGroup = RESOLUTION_PRESET_GROUPS.find((group) => group.label === "Ultra-wide");
  assert.ok(ultraWideGroup, "an Ultra-wide group should exist");
  assert.deepEqual(
    ultraWideGroup.options.map((option) => `${option.width}x${option.height}`),
    ["1792x768", "1920x800", "2048x880"],
  );
  assert.match(resolutionPresetForValue({ width: 1920, height: 800 }).label, /12:5/);
  assert.deepEqual(resolutionPresetForValue({ width: 1024, height: 1536 }), presets.find((preset) => preset.width === 1024 && preset.height === 1536));
  assert.equal(resolutionPresetForValue({ width: 1025, height: 1536 }), null);
  assert.equal(resolutionPresetForValue({ width: 1024 }), null);
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
  const largeGrid = resolutionGridConstraints({ constraints: { maximum: 16384 } });
  assert.equal(largeGrid.maximumWidth, 2048);
  assert.equal(largeGrid.maximumHeight, 2048);
  assert.deepEqual(resolutionGridConstraints({}), largeGrid);
  const mixedGrid = resolutionGridConstraints({
    constraints: { maximum_width: 16384, maximum_height: 2048 },
  });
  assert.equal(mixedGrid.maximumWidth, 2048);
  assert.equal(mixedGrid.maximumHeight, 2048);
  assert.equal(snapResolutionValue(16384, 0, largeGrid.maximumWidth, largeGrid.widthStep), 2048);
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

test("recent resolutions storage key is scoped per user and per source", () => {
  assert.equal(recentResolutionKey("u1", "wf_a"), "cif.recent-resolutions.u1.wf_a");
  assert.equal(recentResolutionKey("u1", "wf_b"), "cif.recent-resolutions.u1.wf_b");
  assert.equal(recentResolutionKey(null, "wf_a"), "cif.recent-resolutions.anonymous.wf_a");
  assert.equal(RECENT_RESOLUTIONS_LIMIT, 5);
});

test("loadRecentResolutions validates, dedupes, and caps stored entries", () => {
  assert.deepEqual(loadRecentResolutions(null), []);
  assert.deepEqual(loadRecentResolutions(""), []);
  assert.deepEqual(loadRecentResolutions("not json"), []);
  assert.deepEqual(loadRecentResolutions('{"width":1080,"height":1920}'), []);
  assert.deepEqual(
    loadRecentResolutions(
      JSON.stringify([
        { width: 1080, height: 1920 },
        { width: "1080", height: 1920 },
        { width: 0, height: 512 },
        { width: 1024, height: 1024 },
        { width: 1344, height: 768 },
        { width: 1920, height: 1080 },
        { width: 1536, height: 1024 },
        { width: 2048, height: 2048 },
      ]),
    ),
    [
      { width: 1080, height: 1920 },
      { width: 1024, height: 1024 },
      { width: 1344, height: 768 },
      { width: 1920, height: 1080 },
      { width: 1536, height: 1024 },
    ],
  );
});

test("recordRecentResolution moves the committed value to the front and dedupes by exact WxH", () => {
  let entries = [];
  entries = recordRecentResolution(entries, { width: 1024, height: 1024 });
  entries = recordRecentResolution(entries, { width: 1344, height: 768 });
  entries = recordRecentResolution(entries, { width: 1024, height: 1024 });
  assert.deepEqual(entries, [
    { width: 1024, height: 1024 },
    { width: 1344, height: 768 },
  ]);
  entries = recordRecentResolution(entries, { width: 1080, height: 1920 });
  entries = recordRecentResolution(entries, { width: 1920, height: 1080 });
  entries = recordRecentResolution(entries, { width: 1536, height: 1024 });
  entries = recordRecentResolution(entries, { width: 2048, height: 2048 });
  assert.equal(entries.length, RECENT_RESOLUTIONS_LIMIT);
  assert.deepEqual(entries, [
    { width: 2048, height: 2048 },
    { width: 1536, height: 1024 },
    { width: 1920, height: 1080 },
    { width: 1080, height: 1920 },
    { width: 1024, height: 1024 },
  ]);
  const unchanged = recordRecentResolution(entries, { width: null, height: 768 });
  assert.equal(unchanged, entries);
  const fromNumericStrings = recordRecentResolution(entries, { width: "1080", height: "1920" });
  assert.deepEqual(fromNumericStrings, [
    { width: 1080, height: 1920 },
    { width: 2048, height: 2048 },
    { width: 1536, height: 1024 },
    { width: 1920, height: 1080 },
    { width: 1024, height: 1024 },
  ]);
});

test("removeRecentResolution drops only the exact WxH pair", () => {
  const entries = [
    { width: 1080, height: 1920 },
    { width: 1920, height: 1080 },
    { width: 1024, height: 1024 },
  ];
  assert.deepEqual(removeRecentResolution(entries, 1920, 1080), [
    { width: 1080, height: 1920 },
    { width: 1024, height: 1024 },
  ]);
  assert.deepEqual(removeRecentResolution([], 1024, 1024), []);
  assert.deepEqual(removeRecentResolution(null, 1024, 1024), []);
});

test("session storage keys are scoped per user with an anonymous fallback", () => {
  assert.equal(parameterStateStorageKey("u1"), "cif.parameter-state.u1");
  assert.equal(activeSourceStorageKey("u2"), "cif.active-source.u2");
  assert.equal(controlSectionStorageKey("u3"), "cif.control-sections.u3");
  assert.equal(creativeDirectionStorageKey("u4"), "cif.creative-direction.u4");
  assert.equal(parameterStateStorageKey(null), "cif.parameter-state.anonymous");
  assert.equal(parameterStateStorageKey(undefined), "cif.parameter-state.anonymous");
  assert.equal(activeSourceStorageKey(""), "cif.active-source.anonymous");
});

test("normalizeStoredParameterState keeps well-formed per-source entries and drops the rest", () => {
  assert.deepEqual(normalizeStoredParameterState(null), {});
  assert.deepEqual(normalizeStoredParameterState("not json"), {});
  assert.deepEqual(normalizeStoredParameterState("[1, 2]"), {});
  assert.deepEqual(normalizeStoredParameterState('"wf_a"'), {});
  const stored = {
    wf_a: {
      interface: { inputs: [] },
      revision: { publication_id: "p1" },
      values: { prompt: "kept" },
      explicitInputIds: ["prompt", 7, "seed", ""],
      selectedPreset: "preset-1",
    },
    wf_b: { values: null },
    wf_c: { interface: null, revision: null, values: { seed: { mode: "fixed", value: "42" } } },
  };
  assert.deepEqual(normalizeStoredParameterState(JSON.stringify(stored)), {
    wf_a: {
      interface: { inputs: [] },
      revision: { publication_id: "p1" },
      values: { prompt: "kept" },
      explicitInputIds: ["prompt", "seed"],
      selectedPreset: "preset-1",
    },
    wf_c: {
      interface: null,
      revision: null,
      values: { seed: { mode: "fixed", value: "42" } },
      explicitInputIds: [],
      selectedPreset: null,
    },
  });
});

test("normalizeStoredActiveSource only accepts a JSON-encoded source key", () => {
  assert.equal(normalizeStoredActiveSource(null), null);
  assert.equal(normalizeStoredActiveSource(""), null);
  assert.equal(normalizeStoredActiveSource("not json"), null);
  assert.equal(normalizeStoredActiveSource('{"weird":true}'), null);
  assert.equal(normalizeStoredActiveSource('"   "'), null);
  assert.equal(normalizeStoredActiveSource('"wf_a"'), "wf_a");
  assert.equal(normalizeStoredActiveSource(JSON.stringify("local::workflows/x.json")), "local::workflows/x.json");
});

test("normalizeStoredControlSections keeps only boolean section entries", () => {
  assert.deepEqual(normalizeStoredControlSections(null), {});
  assert.deepEqual(normalizeStoredControlSections("nope"), {});
  assert.deepEqual(normalizeStoredControlSections('"wf"'), {});
  assert.deepEqual(
    normalizeStoredControlSections(
      JSON.stringify({ prompt: false, seed: true, "group-loras": "yes", advanced: 1 }),
    ),
    { prompt: false, seed: true },
  );
});

test("normalizeStoredCreativeDirectionDraft falls back to safe defaults", () => {
  assert.deepEqual(normalizeStoredCreativeDirectionDraft(null), {
    creativeDirection: "",
    mode: "refine",
    think: true,
  });
  assert.deepEqual(normalizeStoredCreativeDirectionDraft("corrupt"), {
    creativeDirection: "",
    mode: "refine",
    think: true,
  });
  assert.deepEqual(
    normalizeStoredCreativeDirectionDraft(
      JSON.stringify({ creativeDirection: "a portrait", mode: "create", think: false }),
    ),
    { creativeDirection: "a portrait", mode: "create", think: false },
  );
  assert.deepEqual(
    normalizeStoredCreativeDirectionDraft(
      JSON.stringify({ creativeDirection: 9, mode: "weird", think: "yes" }),
    ),
    { creativeDirection: "", mode: "refine", think: true },
  );
});

test("default open control section kinds are the minimal generation set", () => {
  assert.deepEqual([...DEFAULT_OPEN_CONTROL_SECTION_KINDS].sort(), ["prompt", "resolution", "seed"]);
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


test("event refresh queue bounds concurrency, coalesces running keys and clears pending work", async () => {
  const calls = [];
  const releases = new Map();
  const queue = createCoalescedTaskQueue((key, value) => {
    calls.push([key, value]);
    return new Promise((resolve) => releases.set(key, resolve));
  }, 2);
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  queue.enqueue("a", 1);
  queue.enqueue("b", 1);
  queue.enqueue("c", 1);
  await settle();
  assert.deepEqual(calls, [["a", 1], ["b", 1]]);
  queue.enqueue("a", 2);
  queue.enqueue("a", 3);
  releases.get("a")();
  await settle();
  assert.deepEqual(calls, [["a", 1], ["b", 1], ["c", 1]]);
  releases.get("c")();
  await settle();
  assert.deepEqual(calls.at(-1), ["a", 3]);
  queue.enqueue("d", 1);
  queue.clear();
  releases.get("a")();
  releases.get("b")();
  await settle();
  assert.equal(calls.some(([key]) => key === "d"), false);
  queue.enqueue("e", 1);
  queue.clear();
  await settle();
  assert.equal(calls.some(([key]) => key === "e"), false);
});

test("generation quantity clamps into the supported range", () => {
  assert.equal(MIN_GENERATION_QUANTITY, 1);
  assert.equal(MAX_GENERATION_QUANTITY, 16);
  assert.equal(clampGenerationQuantity("3"), 3);
  assert.equal(clampGenerationQuantity(0), 1);
  assert.equal(clampGenerationQuantity(-4), 1);
  assert.equal(clampGenerationQuantity(17), 16);
  assert.equal(clampGenerationQuantity("999"), 16);
  assert.equal(clampGenerationQuantity(""), 1);
  assert.equal(clampGenerationQuantity(null), 1);
  assert.equal(clampGenerationQuantity(undefined), 1);
  assert.equal(clampGenerationQuantity("1abc"), 1);
  assert.equal(clampGenerationQuantity("007"), 7);
  assert.equal(clampGenerationQuantity("16"), 16);
});
