import assert from "node:assert/strict";
import test from "node:test";

import {
  cardFooterMarkup,
  controlMarkup,
  detailMarkup,
  favoritesMarkup,
  galleryCardMarkup,
  galleryMarkup,
  generationPanelMarkup,
  passwordChangeMarkup,
  photoViewerMarkup,
  promptEditorMarkup,
  serviceBannerMarkup,
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
      semantic_role: "seed",
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
      semantic_role: "height",
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
      semantic_role: "width",
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

test("published image input renders a required computer-and-gallery drop target", () => {
  const image = {
    id: "reference_image",
    type: "image",
    label: "Reference Image",
    description: "Guides the edit.",
    required: true,
    media: {
      accepted_mime_types: ["image/png", "image/jpeg", "image/webp"],
      max_bytes: 20 * 1024 * 1024,
      max_width: 8192,
      max_height: 8192,
      animated: false,
    },
  };
  const empty = controlMarkup(image, {}, { inputs: [image] });
  assert.match(empty, /data-image-drop-control="reference_image"/);
  assert.match(empty, /Drop an image here/);
  assert.match(empty, /From your computer or the gallery/);
  assert.match(empty, />Browse<input/);
  assert.match(empty, /accept="image\/png,image\/jpeg,image\/webp"/);
  assert.doesNotMatch(empty, /Guides the edit\.|20 MB|8192 × 8192 px|help-text|role="tooltip"/);
  assert.doesNotMatch(empty, /fixture\.png/);

  const selected = controlMarkup(
    image,
    {
      reference_image: {
        asset_id: "owned-asset",
        preview_url: "/api/uploads/owned-asset/content",
        width: 384,
        height: 640,
        name: "portrait.png",
      },
    },
    { inputs: [image] },
  );
  assert.match(selected, /portrait\.png/);
  assert.match(selected, /384 × 640/);
  assert.match(selected, /data-clear-upload="reference_image"/);
});

test("gallery cards expose only the opaque artifact id as drag data metadata", () => {
  const html = galleryCardMarkup({
    id: "generation-1",
    status: "succeeded",
    display_artifact: {
      id: "artifact-opaque-id",
      kind: "image",
      content_url: "/api/artifacts/artifact-opaque-id/content",
    },
  });
  assert.match(html, /draggable="true"/);
  assert.match(html, /data-gallery-artifact-id="artifact-opaque-id"/);
  assert.doesNotMatch(html, /data-gallery-artifact-url/);
});

const loraChoice = {
  id: "lora",
  label: "LoRA",
  description: "Selects the LoRA applied by the primary loader.",
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
};

test("gallery defaults to newest request first regardless of input order", () => {
  const markup = galleryMarkup([
    {
      id: "oldest",
      accepted_at: "2026-07-14T12:00:00Z",
      status: "succeeded",
      workflow_display_name: "Oldest",
    },
    {
      id: "newest",
      accepted_at: "2026-07-14T12:02:00Z",
      status: "running",
      workflow_display_name: "Newest",
    },
    {
      id: "previous",
      accepted_at: "2026-07-14T12:01:00Z",
      status: "succeeded",
      workflow_display_name: "Previous",
    },
  ]);
  const cardIds = Array.from(
    markup.matchAll(/<article class="gallery-card[^"]*" data-generation-id="([^"]+)"/g),
    (match) => match[1],
  );

  assert.deepEqual(cardIds, ["newest", "previous", "oldest"]);
});

test("gallery and service regions render independent progressive startup states", () => {
  assert.match(galleryMarkup([], { status: "loading" }), /Loading gallery/);
  const failedGallery = galleryMarkup([], {
    status: "error",
    message: "Gallery history timed out after 15 seconds. <private>",
  });
  assert.match(failedGallery, /Gallery temporarily unavailable/);
  assert.match(failedGallery, /Gallery history timed out after 15 seconds/);
  assert.match(failedGallery, /&lt;private&gt;/);
  assert.match(failedGallery, /data-action="retry-gallery"/);
  const failedGalleryWithLiveCard = galleryMarkup(
    [
      {
        id: "live-generation",
        accepted_at: "2026-07-14T12:02:00Z",
        status: "running",
        workflow_display_name: "Live generation",
      },
    ],
    { status: "error", message: "Snapshot failed." },
  );
  assert.match(failedGalleryWithLiveCard, /data-action="retry-gallery"/);
  assert.match(failedGalleryWithLiveCard, /data-generation-id="live-generation"/);

  assert.match(serviceBannerMarkup([], "loading"), /Checking generation service/);
  assert.match(
    serviceBannerMarkup([], "error", "Service status timed out after 8 seconds."),
    /Service status timed out after 8 seconds/,
  );
});

test("password change fields allow eight-character passwords", () => {
  const html = passwordChangeMarkup("ImageGen V2", true);
  assert.match(html, /name="new_password"[^>]*minlength="8"/);
  assert.match(html, /name="confirm_password"[^>]*minlength="8"/);
});

test("prompt is contract-rendered with helper text removed and Creative Direction exposed", () => {
  const describedPrompt = { ...promptControl, description: "Describe the image." };
  const html = controlMarkup(describedPrompt, { "prompt.text": "hello" }, contract);
  assert.match(html, />Prompt</);
  assert.doesNotMatch(html, /Positive prompt/);
  assert.match(html, /data-action="open-prompt-editor"/);
  assert.match(html, /aria-label="Open focused prompt editor"/);
  assert.match(html, /data-speech-target="control-prompt-text"/);
  assert.match(html, /aria-label="Start voice input for Prompt"/);
  assert.ok(html.indexOf('data-action="open-prompt-editor"') < html.indexOf('data-control-id="prompt.text"'));
  assert.match(html, /data-control-id="prompt.text"[^>]*rows="10"/);
  assert.doesNotMatch(html, /Describe the image\.|help-text|role="tooltip"|has-contextual-help/);
  assert.match(html, /<section class="prompt-assistant" id="prompt-assistant"/);
  assert.match(html, /data-speech-target="creative-direction"/);
  assert.doesNotMatch(html, /<details|<summary|>Mode</);
  assert.match(html, /Refine Current Prompt/);
  assert.match(html, /New Prompt from Creative Direction/);
  assert.match(html, /Apply Creative Direction/);
});

test("focused prompt editor renders the prompt and mirrored Prompt Assistant draft", () => {
  const html = promptEditorMarkup("prompt.text", "Prompt", "One <two>\nthree", {
    available: true,
    mode: "create",
    creativeDirection: "Moody <light>",
    historicalModel: "model-one",
  });
  assert.match(html, /<h2 id="prompt-editor-title">Focused prompt editor<\/h2>/);
  assert.match(html, /aria-label="Prompt editor"/);
  assert.match(html, /data-speech-target="prompt-editor-textarea"/);
  assert.match(html, />One &lt;two&gt;\nthree<\/textarea>/);
  assert.match(html, />3 words<\/span>/);
  assert.match(html, />15 characters<\/span>/);
  assert.match(html, /data-action="select-prompt-editor-text"/);
  assert.match(html, /data-action="clear-prompt-editor-text"/);
  assert.doesNotMatch(html, /Prompt Assistant|>Mode</);
  assert.match(html, /id="prompt-editor-creative-direction"[^>]*>Moody &lt;light&gt;<\/textarea>/);
  assert.match(html, /data-speech-target="prompt-editor-creative-direction"/);
  assert.match(html, /name="prompt-editor-assistant-mode" value="create" checked/);
  assert.match(html, /class="prompt-editor-assistant-action-row"/);
  assert.match(html, /Refine Current Prompt/);
  assert.match(html, /New Prompt from Creative Direction/);
  assert.doesNotMatch(html, /Historical composition used model-one|prompt-editor-assistant-message/);
  assert.match(html, /data-action="compose-prompt-editor"/);
  assert.match(html, /Apply Creative Direction/);
  assert.ok(
    html.indexOf('id="prompt-editor-creative-direction"') <
      html.indexOf('class="prompt-editor-assistant-action-row"'),
  );
  assert.match(html, /data-action="cancel-prompt-editor">Cancel<\/button>/);
  assert.match(html, /data-action="apply-prompt-editor">Apply<\/button>/);
});

test("generation panel places the custom source picker before generated controls", () => {
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
  assert.match(html.match(/<button id="workflow-source"[^>]*>/)?.[0] || "", /aria-expanded="false"/);
  assert.doesNotMatch(html, /<select id="workflow-source"/);
  assert.doesNotMatch(html, /All Generation Sources/);
  assert.match(html, /data-primary-source-key="p1"/);
  assert.match(
    html.match(/<input[^>]*data-shared-source-key="p1"[^>]*>/)?.[0] || "",
    /checked[^>]*disabled/,
  );
  assert.match(
    html,
    /data-control-section="advanced"[\s\S]*?data-action="toggle-control-section"[^>]*aria-expanded="false"/,
  );
  assert.doesNotMatch(html, /<details class="advanced-group"/);
});

test("generation panel turns a Basic group into a divider section while preserving control metadata", () => {
  const state = {
    submitting: false,
    services: [{ service: "comfyui", available: true }],
    sources: [publishedSource],
    activeSourceKey: publishedSource.source_key,
    sourceCatalogStatus: "ready",
    sourceDetailLoading: false,
    parameters: { prompt: "hello" },
    fieldErrors: {},
    formError: null,
  };
  const basicInterface = {
    inputs: [
      {
        id: "prompt",
        label: "Prompt",
        type: "string",
        default: "",
        group: "Basic",
        advanced: false,
      },
    ],
  };

  const html = generationPanelMarkup(state, publishedSource, basicInterface);

  assert.match(html, /data-control-section="group-prompt"/);
  assert.match(html, /class="control-section-title">Prompt/);
  assert.match(html, /data-control-group="Basic"/);
  assert.doesNotMatch(html, /<h3 class="control-group-heading">Basic<\/h3>/);
});

test("source picker marks primary and shared sources and shows the selected queue count", () => {
  const state = {
    submitting: true,
    comparisonSourceKeys: new Set(["two"]),
    services: [{ service: "comfyui", available: true }],
    sources: [
      { source_key: "one", display_name: "One", available: true },
      { source_key: "two", display_name: "Two", available: true },
      { source_key: "offline", display_name: "Offline", available: false },
    ],
    activeSourceKey: "one",
    parameters: { "prompt.text": "hello" },
    fieldErrors: {},
  };
  const html = generationPanelMarkup(state, state.sources[0], publishedInterface);
  assert.match(html.match(/<button id="workflow-source"[^>]*>/)?.[0] || "", /disabled/);
  assert.match(
    html.match(/<input[^>]*data-shared-source-key="one"[^>]*>/)?.[0] || "",
    /checked[^>]*disabled/,
  );
  assert.match(
    html.match(/<input[^>]*data-shared-source-key="two"[^>]*>/)?.[0] || "",
    /checked[^>]*disabled/,
  );
  assert.match(html, /Queueing 2…/);
  assert.match(html, /2 sources · shared prompt, resolution &amp; seed/);
});

test("card footer groups generation actions and exposes permanent deletion", () => {
  const generation = {
    id: "g1",
    workflow_display_name: "Portrait Workflow",
    accepted_at: "2026-07-12T12:00:00Z",
    status: "failed_with_artifacts",
    recall_available: true,
    is_favorite: false,
    final_prompt: "private prompt",
    resolved_seeds: { seed: 99 },
    display_artifact: {
      kind: "image",
      content_url: "/api/artifacts/current/content",
    },
  };
  const html = cardFooterMarkup(generation);
  assert.match(html, />Portrait Workflow<\/button>/);
  assert.doesNotMatch(html, /Jul 12|2026/);
  assert.match(html, /data-action="open-detail"/);
  assert.match(html, /href="\/api\/artifacts\/current\/content" download aria-label="Download current image"/);
  assert.equal((html.match(/<button/g) || []).length, 4);
  assert.match(html, /aria-label="Add to Favorites" aria-pressed="false"/);
  assert.ok(html.indexOf('data-action="toggle-favorite"') < html.indexOf('data-action="recall"'));
  assert.match(html, /data-action="recall"[^>]+aria-label="Recall settings"/);
  assert.match(html, /aria-label="Recall settings"[^>]*>[\s\S]*?<svg[^>]+viewBox="0 0 24 24"/);
  assert.doesNotMatch(html, />Recall settings<\/button>/);
  assert.match(html, /data-action="delete-generation"[^>]+aria-label="Delete generation"/);
  assert.ok(html.indexOf('data-action="recall"') < html.indexOf('data-action="delete-generation"'));
  assert.doesNotMatch(html, /Failed|private prompt|99|Cancel/);

  const historical = cardFooterMarkup({
    ...generation,
    recall_source_available: false,
    recall_warning: "The original generation source is unavailable; the current source will stay selected.",
  });
  assert.match(historical, /data-action="recall"[^>]+aria-label="Recall settings"/);
  assert.doesNotMatch(historical, /data-action="recall"[^>]+disabled/);
  assert.match(historical, /title="The original generation source is unavailable; the current source will stay selected\."/);

  const active = cardFooterMarkup({ ...generation, is_favorite: true });
  assert.match(active, /aria-label="Remove from Favorites" aria-pressed="true"/);
  assert.match(active, /<svg[^>]+viewBox="0 0 24 24"/);

  const pending = cardFooterMarkup({ ...generation, delete_pending: true });
  assert.match(pending, /data-action="delete-generation"[^>]+disabled[^>]+aria-label="Deletion pending"/);
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
  assert.match(first, /data-action="open-photo"/);
  assert.match(first, /data-action="open-detail"/);
});

test("photo viewer fills by default, omits sizing and unavailable navigation controls, and shows active status", () => {
  const html = photoViewerMarkup(
    {
      id: "g-live",
      workflow_display_name: "Progressive source",
      status: "running",
      current_stage_label: "Refining details",
      display_artifact: {
        kind: "image",
        content_url: "/api/artifacts/latest/content",
      },
    },
    { hasOlder: true, hasNewer: false },
  );
  assert.match(html, /src="\/api\/artifacts\/latest\/content"/);
  assert.match(html, /data-photo-view-mode="fill"/);
  assert.doesNotMatch(html, /Image sizing|set-photo-view|>Fit<|>Fill</);
  assert.match(html, /draggable="false"/);
  assert.match(html, /data-action="toggle-photo-fullscreen" aria-pressed="false">Full screen/);
  assert.match(html, /aria-label="Close image viewer"/);
  assert.doesNotMatch(html, /data-direction="newer"/);
  assert.match(html, /photo-viewer-older[^>]*data-direction="older"[^>]*>›<\/button>/);
  assert.match(html, /<div class="photo-viewer-status" role="status">Refining details<\/div>/);

  const middle = photoViewerMarkup(
    {
      id: "g-middle",
      workflow_display_name: "Progressive source",
      status: "succeeded",
      display_artifact: { kind: "image", content_url: "/middle.png" },
    },
    { hasOlder: true, hasNewer: true },
  );
  assert.match(middle, /data-direction="newer"/);
  assert.match(middle, /data-direction="older"/);
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
  assert.match(
    html,
    /<div class="resolution-preview">[\s\S]*?<\/div>\s*<div class="resolution-control">[\s\S]*?<\/div>\s*<p class="resolution-summary"/,
  );
});

test("unavailable and invalid semantic controls retain validation without helper explanations", () => {
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
  assert.match(html, /aria-describedby="control-post-seedvr2-enabled-error"/);
  assert.doesNotMatch(html, /Required model is not installed\.|role="tooltip"|help-text/);
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
  assert.doesNotMatch(html, /aria-describedby|Final image size\.|role="tooltip"|help-text/);
  assert.match(html, /<legend>Resolution/);
  assert.match(html, /<label for="control-size-resolution-width"><span>Width<\/span>/);
  assert.match(html, /id="control-size-resolution-width"/);
  assert.match(html, /<label for="control-size-resolution-height"><span>Height<\/span>/);
  assert.match(html, /id="control-size-resolution-height"/);
});

test("published source pairs scalar dimensions in the resolution picker and renders remaining input types", () => {
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
  assert.match(html, /data-control-id="prompt"[^>]*rows="10"/);
  assert.match(html, /data-resolution-width-id="width" data-resolution-height-id="height"/);
  assert.match(html, /data-control-id="width"[^>]*data-resolution-axis="width"[^>]*type="number"[^>]*step="8"/);
  assert.match(html, /data-control-id="height"[^>]*data-resolution-axis="height"[^>]*type="number"[^>]*step="8"/);
  assert.match(html, /data-resolution-summary[^>]*>1080 × 1920 · 2.07 MP · 9:16/);
  assert.match(html, /data-control-section-status="resolution">1080 × 1920<\/span>/);
  assert.match(html, /data-control-section-status="seed">Random<\/span>/);
  assert.match(html, /<div class="resolution-preview">/);
  assert.doesNotMatch(html, /<span aria-hidden="true">×<\/span>/);
  assert.ok(html.indexOf('data-control-block="prompt"') < html.indexOf("data-resolution-pair-block"));
  assert.ok(html.indexOf('data-control-block="width"') < html.indexOf('data-control-block="height"'));
  assert.ok(html.indexOf('data-control-block="height"') < html.indexOf('data-control-block="seed"'));
  assert.match(html, /data-control-id="seed"[^>]*type="text"[^>]*inputmode="numeric"/);
  assert.doesNotMatch(html, /Use random or enter an exact seed\.|role="tooltip"|help-text/);
  assert.match(html, /data-control-id="enable_seedvr2_upscale"[^>]*type="checkbox"/);
  assert.match(
    html,
    /data-control-id="knpv4_1_strength"[^>]*data-number-slider[^>]*type="range"[^>]*min="0"[^>]*max="2"[^>]*step="0.05"/,
  );
  assert.match(
    html,
    /data-control-id="knpv4_1_strength"[^>]*data-number-entry[^>]*type="number"[^>]*step="0.05"/,
  );
  for (const section of ["prompt", "resolution", "seed", "upscaling"]) {
    assert.match(
      html,
      new RegExp(
        `data-control-section="${section}"[\\s\\S]*?data-action="toggle-control-section"[^>]*aria-expanded="true"`,
      ),
    );
  }
  assert.match(
    html,
    /data-control-section="advanced"[\s\S]*?data-action="toggle-control-section"[^>]*aria-expanded="false"/,
  );
  assert.doesNotMatch(html, /<details class="advanced-group"/);
  assert.doesNotMatch(html, /Source warning/);
  assert.doesNotMatch(html, /negative.prompt|Negative prompt/i);
  const button = html.match(/<button id="generate-button"[^>]*>/)?.[0] || "";
  assert.doesNotMatch(button, /disabled/);
});

test("choice inputs render one finite single-select with public values and labels", () => {
  const html = controlMarkup(loraChoice, { lora: "knp_v3_1" }, { inputs: [loraChoice] });
  assert.equal((html.match(/<select\b/g) || []).length, 1);
  assert.equal((html.match(/<option\b/g) || []).length, 4);
  assert.match(html, /<select[^>]*data-control-id="lora"[^>]*>/);
  assert.match(html, /<option value="knp_v3_1" selected>KNP v3\.1<\/option>/);
  assert.match(html, /<option value="mysticxxx_krea2_v1" >MysticXXX Krea2 v1<\/option>/);
  assert.doesNotMatch(html, /Selects the LoRA applied by the primary loader\.|help-text|role="tooltip"/);
  assert.doesNotMatch(html, /\bmultiple\b|type="text"|<textarea|safetensors|options_json/);

  const defaults = controlMarkup(loraChoice, {}, { inputs: [loraChoice] });
  assert.match(defaults, /<option value="knp_v4_1" selected>KNP v4\.1<\/option>/);
});

test("choice controls honor advanced grouping and order before their strength companion", () => {
  const strength = {
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
  };
  const choiceContract = { inputs: [strength, loraChoice] };
  const state = {
    submitting: false,
    services: [{ service: "comfyui", available: true }],
    sources: [publishedSource],
    activeSourceKey: publishedSource.source_key,
    sourceCatalogStatus: "ready",
    sourceDetailLoading: false,
    parameters: { lora: "knp_v4_1", lora_strength: 1 },
    fieldErrors: {},
    formError: null,
  };
  const html = generationPanelMarkup(state, publishedSource, choiceContract);
  assert.match(
    html,
    /data-control-section="advanced"[\s\S]*?data-action="toggle-control-section"[^>]*aria-expanded="false"/,
  );
  assert.match(html, /<section class="control-group" data-interface-group="Advanced">/);
  assert.ok(html.indexOf('data-control-block="lora"') < html.indexOf('data-control-block="lora_strength"'));
  assert.doesNotMatch(html, /Krea2\/KNPV4\.1_pre\.safetensors/);
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

test("paired width and height omit source descriptions and tooltips", () => {
  const describedInterface = {
    ...publishedInterface,
    inputs: publishedInterface.inputs.map((input) => {
      if (input.id === "width") return { ...input, description: "Image width in pixels." };
      if (input.id === "height") return { ...input, description: "Image height in pixels." };
      return input;
    }),
  };
  const html = generationPanelMarkup(
    {
      submitting: false,
      services: [{ service: "comfyui", available: true }],
      sources: [publishedSource],
      activeSourceKey: publishedSource.source_key,
      sourceCatalogStatus: "ready",
      parameters: { width: 1080, height: 1920 },
      fieldErrors: {},
    },
    publishedSource,
    describedInterface,
  );
  assert.match(html, /class="resolution-axis-field" data-control-block="width"/);
  assert.match(html, /class="resolution-axis-field" data-control-block="height"/);
  assert.doesNotMatch(html, /Image (?:width|height) in pixels\.|role="tooltip"|help-text/);
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
  assert.match(
    html,
    /data-control-section="advanced"[\s\S]*?data-action="toggle-control-section"[^>]*aria-expanded="true"/,
  );
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

  const servicePending = generationPanelMarkup(
    {
      submitting: false,
      services: [],
      servicesStatus: "loading",
      sources: [publishedSource],
      activeSourceKey: publishedSource.source_key,
      sourceCatalogStatus: "ready",
      parameters: {},
      fieldErrors: {},
    },
    publishedSource,
    publishedInterface,
  );
  assert.match(servicePending, /Checking generation service availability/);
  assert.match(
    servicePending.match(/<button id="generate-button"[^>]*>/)?.[0] || "",
    /disabled/,
  );

  const serviceFailed = generationPanelMarkup(
    {
      submitting: false,
      services: [],
      servicesStatus: "error",
      servicesMessage: "Service status timed out after 8 seconds.",
      sources: [publishedSource],
      activeSourceKey: publishedSource.source_key,
      sourceCatalogStatus: "ready",
      parameters: {},
      fieldErrors: {},
    },
    publishedSource,
    publishedInterface,
  );
  assert.match(serviceFailed, /Service status timed out after 8 seconds/);
  assert.match(
    serviceFailed.match(/<button id="generate-button"[^>]*>/)?.[0] || "",
    /disabled/,
  );

  const sourceFailed = generationPanelMarkup(
    {
      submitting: false,
      services: [{ service: "comfyui", available: true }],
      servicesStatus: "ready",
      sources: [publishedSource],
      activeSourceKey: publishedSource.source_key,
      sourceCatalogStatus: "error",
      sourceCatalogMessage: "Source catalog timed out after 15 seconds.",
      parameters: {},
      fieldErrors: {},
    },
    publishedSource,
    publishedInterface,
  );
  assert.match(sourceFailed, /Source catalog timed out after 15 seconds/);
  assert.match(sourceFailed, /data-action="retry-generation-sources"/);
  assert.match(
    sourceFailed.match(/<button id="generate-button"[^>]*>/)?.[0] || "",
    /disabled/,
  );

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
      width: 1024,
      height: 1600,
      seed: "9223372036854775807",
    },
    input_definitions: [
      { id: "prompt", label: "Prompt", type: "string", semantic_role: "positive_prompt" },
      { id: "width", label: "Width", type: "integer", semantic_role: "width" },
      { id: "height", label: "Height", type: "integer", semantic_role: "height" },
      { id: "seed", label: "Seed", type: "seed" },
    ],
    final_prompt: "a tree with chickens",
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
  assert.match(html, /Generation inputs/);
  assert.match(html, /a tree with chickens/);
  assert.match(html, /1024 × 1600/);
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
  assert.ok(html.indexOf("Generation inputs") < html.indexOf("Primary result"));
  assert.ok(html.indexOf("Primary result") < html.indexOf("Prototypes and earlier passes"));
  assert.ok(html.indexOf("Prototypes and earlier passes") < html.indexOf("Comparisons and alternates"));
  assert.match(html, /batch 1/);
  assert.match(html, /batch 2/);
});
