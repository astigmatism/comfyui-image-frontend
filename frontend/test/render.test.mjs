import assert from "node:assert/strict";
import test from "node:test";

import {
  cardFooterMarkup,
  controlMarkup,
  detailMarkup,
  favoritesMarkup,
  galleryCardMarkup,
  generationPanelMarkup,
} from "../src/render.mjs";

const promptControl = {
  id: "prompt.text",
  label: "Positive prompt",
  type: "multiline_string",
  tier: "basic",
  required: true,
  default: "",
  bindings: [],
};

const contract = {
  controls: [
    promptControl,
    {
      id: "sampling.steps",
      label: "Steps",
      type: "integer",
      tier: "advanced",
      default: 8,
      constraints: { minimum: 1, maximum: 50 },
      bindings: [],
    },
  ],
  presets: [],
  capability_states: {},
};

const publishedInterface = {
  inputs: [
    {
      id: "knpv4_1_strength",
      label: "LoRA strength",
      description: "Core LoRA strength.",
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
      id: "seed",
      label: "Seed",
      description: "Use random or enter an exact seed.",
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
      id: "prompt",
      label: "Prompt",
      description: "Describe the image.",
      type: "string",
      default: "a tree with chickens",
      required: true,
      semantic_role: "positive_prompt",
      group: "Prompt",
      order: 10,
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
  ],
  outputs: [
    { id: "base", role: "preview", kind: "image", cardinality: "many" },
    { id: "second_pass", role: "comparison", kind: "image", cardinality: "many" },
    { id: "final", role: "final", kind: "image", cardinality: "many" },
  ],
  unmapped_outputs_policy: "collect",
};

const publishedSource = {
  source_key: "local::workflows/comfyui-image-frontend/Krea 2 NSFW V4.json",
  display_name: "Krea 2 NSFW V4",
  instance_id: "local",
  readiness: "ready",
  available: true,
  cached: false,
  warnings: [],
  revision: {
    publication_id: "publication-1",
    workflow_sha256: "workflow-hash",
    api_sha256: "api-hash",
    manifest_sha256: "manifest-hash",
  },
};

test("prompt is contract-rendered and Prompt Assistant remains collapsed by default", () => {
  const html = controlMarkup(promptControl, { "prompt.text": "hello" }, contract);
  assert.match(html, />Prompt</);
  assert.doesNotMatch(html, /Positive prompt/);
  assert.match(html, /<details class="prompt-assistant" id="prompt-assistant">/);
  assert.doesNotMatch(html, /<details[^>]+open/);
  assert.match(html, /Compose Prompt/);
});

test("generation panel fixes Generate first, source second, then basic and collapsed advanced controls", () => {
  const state = {
    submitting: false,
    services: [{ service: "comfyui", available: true }],
    workflows: [{ profile_id: "p1", display_name: "Portrait" }],
    activeProfileId: "p1",
    controls: { "prompt.text": "hello", "sampling.steps": 8 },
    fieldErrors: {},
    formError: null,
    selectedPreset: null,
  };
  const html = generationPanelMarkup(state, state.workflows[0], contract);
  const generateIndex = html.indexOf('id="generate-button"');
  const sourceIndex = html.indexOf('id="workflow-source"');
  const promptIndex = html.indexOf('data-control-block="prompt.text"');
  assert.ok(generateIndex >= 0 && generateIndex < sourceIndex && sourceIndex < promptIndex);
  assert.match(html, /<details class="advanced-group"><summary>Advanced<\/summary>/);
  assert.doesNotMatch(html, /<details class="advanced-group" open/);
});

test("card footer places an accessible heart immediately before recall", () => {
  const generation = {
    id: "g1",
    workflow_display_name: "Portrait Workflow",
    accepted_at: "2026-07-12T12:00:00Z",
    status: "failed_with_artifacts",
    recall_available: true,
    is_favorite: false,
    final_prompt: "private prompt",
    resolved_seeds: { seed: 99 },
  };
  const html = cardFooterMarkup(generation);
  assert.match(html, /Portrait Workflow · Jul 12, 2026/);
  assert.equal((html.match(/<button/g) || []).length, 2);
  assert.match(html, /aria-label="Add to Favorites" aria-pressed="false"/);
  assert.ok(html.indexOf('data-action="toggle-favorite"') < html.indexOf('data-action="recall"'));
  assert.match(html, />Recall settings<\/button>/);
  assert.doesNotMatch(html, /Failed|private prompt|99|Cancel|Delete/);

  const active = cardFooterMarkup({ ...generation, is_favorite: true });
  assert.match(active, /aria-label="Remove from Favorites" aria-pressed="true"/);
  assert.match(active, /<svg[^>]+viewBox="0 0 24 24"/);
});

test("Favorites modal renders a thumbnail, generation details, recall, and delete", () => {
  const html = favoritesMarkup([
    {
      id: "f1",
      created_at: "2026-07-13T12:00:00Z",
      final_prompt: "lighthouse <at dusk>",
      generation: {
        id: "g1",
        workflow_display_name: "Portrait Workflow",
        accepted_at: "2026-07-12T12:00:00Z",
        status: "succeeded",
        recall_available: true,
        display_artifact: {
          kind: "image",
          thumbnail_url: "/api/artifacts/a1/thumbnail",
          content_url: "/api/artifacts/a1/content",
        },
      },
    },
  ]);
  assert.match(html, /<h2>Favorites<\/h2>/);
  assert.match(html, /a1\/thumbnail/);
  assert.match(html, /Portrait Workflow/);
  assert.match(html, /lighthouse &lt;at dusk&gt;/);
  assert.match(html, /data-action="recall-favorite"/);
  assert.match(html, /data-action="delete-favorite"/);
  assert.ok(html.indexOf('data-action="recall-favorite"') < html.indexOf('data-action="delete-favorite"'));
});

test("one generation renders one card while progressive media changes in place", () => {
  const generation = {
    id: "g1",
    workflow_display_name: "Progressive",
    accepted_at: "2026-07-12T12:00:00Z",
    status: "running",
    recall_available: true,
    cancel_allowed: true,
    expected_width: 384,
    expected_height: 512,
    display_artifact: {
      kind: "image",
      role: "image.base",
      thumbnail_url: "/api/artifacts/a1/thumbnail",
      content_url: "/api/artifacts/a1/content",
    },
    final_artifact_count: 0,
  };
  const first = galleryCardMarkup(generation);
  const next = galleryCardMarkup({
    ...generation,
    display_artifact: { ...generation.display_artifact, role: "image.refined", thumbnail_url: "/api/artifacts/a2/thumbnail" },
  });
  assert.equal((first.match(/<article class="gallery-card/g) || []).length, 1);
  assert.equal((next.match(/<article class="gallery-card/g) || []).length, 1);
  assert.match(first, /a1\/thumbnail/);
  assert.match(next, /a2\/thumbnail/);
  assert.match(first, /--gallery-media-aspect: 384 \/ 512/);
  assert.match(first, /data-action="cancel-generation"/);
});

test("historical native-only image batches use complete artifact count on the gallery card", () => {
  const html = galleryCardMarkup({
    id: "g-unmapped-batch",
    workflow_display_name: "Published native output",
    accepted_at: "2026-07-12T12:00:00Z",
    status: "succeeded",
    recall_available: true,
    cancel_allowed: false,
    artifact_count: 3,
    final_artifact_count: 0,
    display_artifact: {
      kind: "image",
      thumbnail_url: "/api/artifacts/a1/thumbnail",
      content_url: "/api/artifacts/a1/content",
    },
  });
  assert.match(html, /aria-label="3 images">3<\/div>/);
});

test("cancelled image-less generation keeps its reserved card and a clear terminal message", () => {
  const html = galleryCardMarkup({
    id: "g-cancelled",
    workflow_display_name: "Portrait",
    accepted_at: "2026-07-12T12:00:00Z",
    status: "cancelled_without_artifacts",
    current_stage_label: "Finishing image",
    recall_available: true,
    cancel_allowed: false,
    expected_width: 640,
    expected_height: 960,
    display_artifact: null,
    final_artifact_count: 0,
  });
  assert.match(html, /--gallery-media-aspect: 640 \/ 960/);
  assert.match(html, /<strong>Cancelled generation<\/strong>/);
  assert.doesNotMatch(html, /Finishing image/);
  assert.doesNotMatch(html, /data-action="cancel-generation"/);
});

test("resolution markup exposes strict contract limits to native controls", () => {
  const control = {
    id: "size.resolution",
    label: "Resolution",
    type: "resolution",
    tier: "basic",
    constraints: {
      minimum_width: 64,
      maximum_width: 2048,
      minimum_height: 128,
      maximum_height: 1536,
      multiple: 8,
    },
  };
  const html = controlMarkup(control, { "size.resolution": { width: 512, height: 768 } }, { capability_states: {} });
  assert.match(html, /data-resolution-part="width"[^>]*min="64"[^>]*max="2048"[^>]*step="8"/);
  assert.match(html, /data-resolution-part="height"[^>]*min="128"[^>]*max="1536"[^>]*step="8"/);
});

test("resolution markup includes the responsive three-handle grid and live caption", () => {
  const control = {
    id: "size.resolution",
    label: "Resolution",
    type: "resolution",
    tier: "basic",
    constraints: { minimum: 64, maximum: 2048, multiple: 8 },
  };
  const html = controlMarkup(
    control,
    { "size.resolution": { width: 1024, height: 1600 } },
    { capability_states: {} },
  );
  assert.match(html, /data-resolution-grid/);
  assert.match(html, /data-resolution-min-width="0" data-resolution-max-width="2048"/);
  assert.match(html, /data-resolution-width-step="64" data-resolution-height-step="64"/);
  assert.match(html, /data-resolution-handle="both"/);
  assert.match(html, /data-resolution-handle="width"/);
  assert.match(html, /data-resolution-handle="height"/);
  assert.match(html, /data-resolution-summary[^>]*aria-live="polite">1024 × 1600 · 1.64 MP · 16:25/);
});

test("unavailable and invalid semantic controls expose accessible state and explanations", () => {
  const control = {
    id: "post.seedvr2.enabled",
    label: "SeedVR2",
    type: "boolean",
    tier: "advanced",
    default: false,
    available: false,
    unavailable_reason: "Required model is not installed.",
    bindings: [],
  };
  const html = controlMarkup(
    control,
    { "post.seedvr2.enabled": false },
    { capability_states: {} },
    { "post.seedvr2.enabled": "This option cannot be selected." },
  );
  assert.match(html, /<input[^>]*disabled/);
  assert.match(html, /aria-invalid="true"/);
  assert.match(html, /aria-describedby="control-post-seedvr2-enabled-description control-post-seedvr2-enabled-error"/);
  assert.match(html, /Required model is not installed\./);
  assert.match(html, /role="alert">This option cannot be selected\./);
});

test("composite resolution controls have distinct programmatic width and height labels", () => {
  const control = {
    id: "size.resolution",
    label: "Resolution",
    type: "resolution",
    tier: "basic",
    required: true,
    description: "Final image size.",
    constraints: { minimum: 64, maximum: 2048, multiple: 8 },
  };
  const html = controlMarkup(
    control,
    { "size.resolution": { width: 512, height: 768 } },
    { capability_states: {} },
  );
  assert.match(html, /<fieldset[^>]*aria-describedby="control-size-resolution-description"/);
  assert.match(html, /<legend>Resolution/);
  assert.match(html, /<label for="control-size-resolution-width"><span>Width<\/span>/);
  assert.match(html, /id="control-size-resolution-width"/);
  assert.match(html, /<label for="control-size-resolution-height"><span>Height<\/span>/);
  assert.match(html, /id="control-size-resolution-height"/);
});

test("published source renders all five v1 input types in deterministic Basic and Advanced sections", () => {
  const state = {
    submitting: false,
    services: [{ service: "comfyui", available: true }],
    sources: [publishedSource],
    activeSourceKey: publishedSource.source_key,
    sourceCatalogStatus: "ready",
    sourceDetailLoading: false,
    parameters: {
      prompt: "a tree with chickens",
      width: 1080,
      height: 1920,
      seed: { mode: "random", value: "0" },
      enable_seedvr2_upscale: false,
      knpv4_1_strength: 1,
    },
    fieldErrors: {},
    formError: null,
  };
  const html = generationPanelMarkup(state, publishedSource, publishedInterface);
  const ids = ["prompt", "width", "height", "seed", "enable_seedvr2_upscale", "knpv4_1_strength"];
  ids.forEach((id) => assert.match(html, new RegExp(`data-control-block="${id}"`)));
  ids.slice(1).forEach((id, index) => {
    assert.ok(html.indexOf(`data-control-block="${ids[index]}"`) < html.indexOf(`data-control-block="${id}"`));
  });
  assert.match(html, /data-control-id="prompt"[^>]*rows="6"/);
  assert.match(html, /data-control-id="width"[^>]*type="number"[^>]*step="8"/);
  assert.match(html, /data-control-id="seed"[^>]*type="text"[^>]*inputmode="numeric"/);
  assert.match(html, /data-control-id="enable_seedvr2_upscale"[^>]*type="checkbox"/);
  assert.match(
    html,
    /data-control-id="knpv4_1_strength"[^>]*data-number-slider[^>]*type="range"[^>]*min="0"[^>]*max="2"[^>]*step="0.05"/,
  );
  assert.match(
    html,
    /data-control-id="knpv4_1_strength"[^>]*data-number-entry[^>]*type="number"[^>]*step="0.05"/,
  );
  assert.match(html, /<details class="advanced-group"><summary>Advanced<\/summary>/);
  assert.doesNotMatch(html, /Source warning/);
  assert.doesNotMatch(html, /negative.prompt|Negative prompt/i);
  const button = html.match(/<button id="generate-button"[^>]*>/)?.[0] || "";
  assert.doesNotMatch(button, /disabled/);
});

test("fixed-mode seed renders no random choice and exposes its exact default", () => {
  const control = {
    id: "seed",
    label: "Seed",
    type: "seed",
    required: true,
    default: "1125899906842624",
    default_mode: "fixed",
    minimum: "0",
    maximum: "1125899906842624",
  };
  const html = controlMarkup(
    control,
    { seed: { mode: "fixed", value: "1125899906842624" } },
    { inputs: [control] },
  );
  assert.match(html, /aria-label="Seed mode"><option value="fixed" selected>Fixed<\/option>/);
  assert.doesNotMatch(html, /option value="random"/);
  assert.match(html, /<input[^>]*value="1125899906842624"[^>]*aria-label="Seed value"/);
});

test("seed mode availability is independent of public input id text", () => {
  const control = {
    id: "disabled_seed",
    label: "Seed",
    type: "seed",
    default: null,
    default_mode: "random",
  };
  const html = controlMarkup(
    control,
    { disabled_seed: { mode: "random", value: "0" } },
    { inputs: [control] },
  );
  const mode = html.match(/<select data-seed-mode="disabled_seed"[^>]*>/)?.[0] || "";
  assert.doesNotMatch(mode, /\sdisabled(?:\s|>)/);
});

test("advanced controls open when they contain a field error", () => {
  const state = {
    submitting: false,
    services: [{ service: "comfyui", available: true }],
    workflows: [{ profile_id: "p1", display_name: "Portrait" }],
    activeProfileId: "p1",
    controls: { "prompt.text": "hello", "sampling.steps": 100 },
    fieldErrors: { "sampling.steps": "Maximum 50." },
    formError: "Review the highlighted controls.",
  };
  const html = generationPanelMarkup(state, state.workflows[0], contract);
  assert.match(html, /<details class="advanced-group" open>/);
  assert.match(html, /aria-invalid="true"/);
});

test("source loading, empty catalog, and unavailable source states disable submission clearly", () => {
  const loading = generationPanelMarkup(
    {
      submitting: false,
      services: [],
      sources: [],
      sourceCatalogStatus: "loading",
      parameters: {},
      fieldErrors: {},
    },
    null,
    null,
  );
  assert.match(loading, /Discovering published generation sources/);
  assert.match(loading.match(/<button id="generate-button"[^>]*>/)?.[0] || "", /disabled/);

  const empty = generationPanelMarkup(
    {
      submitting: false,
      services: [],
      sources: [],
      sourceCatalogStatus: "ready",
      parameters: {},
      fieldErrors: {},
    },
    null,
    null,
  );
  assert.match(empty, /No published generation sources are available/);

  const unavailable = generationPanelMarkup(
    {
      submitting: false,
      services: [{ service: "comfyui", available: true }],
      sources: [{ ...publishedSource, available: false }],
      activeSourceKey: publishedSource.source_key,
      sourceCatalogStatus: "ready",
      parameters: {},
      fieldErrors: {},
    },
    { ...publishedSource, available: false },
    publishedInterface,
  );
  assert.match(unavailable, /unavailable and cannot generate images/i);
  assert.match(unavailable.match(/<button id="generate-button"[^>]*>/)?.[0] || "", /disabled/);

  const cachedOffline = generationPanelMarkup(
    {
      submitting: false,
      services: [{ service: "comfyui", available: false }],
      sources: [{ ...publishedSource, cached: true }],
      activeSourceKey: publishedSource.source_key,
      sourceCatalogStatus: "ready",
      parameters: {},
      fieldErrors: {},
    },
    { ...publishedSource, cached: true },
    publishedInterface,
  );
  assert.match(cachedOffline, /Krea 2 NSFW V4 · local — Cached/);
  assert.match(cachedOffline.match(/<button id="generate-button"[^>]*>/)?.[0] || "", /disabled/);
});

test("generation detail retains metadata and presents authored roles with every image batch", () => {
  const html = detailMarkup({
    id: "g-rich",
    status: "succeeded",
    accepted_at: "2026-07-13T12:00:00Z",
    generation_source: { ...publishedSource, revision: publishedSource.revision },
    prompt_id: "native-prompt-123",
    effective_parameters: {
      prompt: "a tree with chickens",
      seed: "9223372036854775807",
    },
    declared_outputs: [
      {
        id: "first_pass",
        role: "preview",
        description: "Early prototype",
        artifacts: [{ id: "a-preview", kind: "image", output_id: "first_pass", content_url: "/api/artifacts/preview/content", batch_index: 0 }],
      },
      {
        id: "alternate",
        role: "comparison",
        description: "Comparison pass",
        artifacts: [{ id: "a-comparison", kind: "image", output_id: "alternate", content_url: "/api/artifacts/comparison/content", batch_index: 0 }],
      },
      {
        id: "final",
        role: "final",
        cardinality: "many",
        description: "Authoritative result",
        artifacts: [
          { id: "a-final-0", kind: "image", output_id: "final", content_url: "/api/artifacts/one/content", batch_index: 0 },
          { id: "a-final-1", kind: "image", output_id: "final", content_url: "/api/artifacts/two/content", batch_index: 1 },
        ],
      },
    ],
    unmapped_outputs: {
      "156:155": {
        images: [{ content_url: "/api/artifacts/three/content", batch_index: 0 }],
        text: ["native UI text"],
      },
    },
    raw_history: { outputs: { "156:155": { cached: true } } },
    comfyui_status: { status_str: "success" },
    warnings: [{ code: "optional_artifact_unavailable", message: "One optional native image was unavailable." }],
    errors: [],
    artifacts: [],
    recall_available: true,
    cancel_allowed: false,
    delete_pending: false,
  });
  assert.match(html, /native-prompt-123/);
  assert.match(html, /publication-1/);
  assert.match(html, /9223372036854775807/);
  assert.match(html, /One optional native image was unavailable/);
  assert.match(html, /Primary result/);
  assert.match(html, /Prototypes and earlier passes/);
  assert.match(html, /Comparisons and alternates/);
  assert.match(html, /Declared output metadata/);
  assert.match(html, /Additional outputs/);
  assert.match(html, /156:155/);
  assert.match(html, /native UI text/);
  assert.match(html, /Raw ComfyUI history/);
  assert.match(html, /ComfyUI status/);
  assert.equal((html.match(/<img /g) || []).length, 5);
  assert.equal((html.match(/Download image/g) || []).length, 5);
  assert.ok(html.indexOf("Primary result") < html.indexOf("Prototypes and earlier passes"));
  assert.ok(html.indexOf("Prototypes and earlier passes") < html.indexOf("Comparisons and alternates"));
  assert.match(html, /batch 1/);
  assert.match(html, /batch 2/);
});
