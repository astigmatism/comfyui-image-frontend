import assert from "node:assert/strict";
import test from "node:test";

import {
  cardFooterMarkup,
  controlMarkup,
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

test("card footer contains no status, prompt, seed, or extra action", () => {
  const generation = {
    id: "g1",
    workflow_display_name: "Portrait Workflow",
    accepted_at: "2026-07-12T12:00:00Z",
    status: "failed_with_artifacts",
    recall_available: true,
    final_prompt: "private prompt",
    resolved_seeds: { seed: 99 },
  };
  const html = cardFooterMarkup(generation);
  assert.match(html, /Portrait Workflow · Jul 12, 2026/);
  assert.equal((html.match(/<button/g) || []).length, 1);
  assert.match(html, />Recall settings<\/button>/);
  assert.doesNotMatch(html, /Failed|private prompt|99|Cancel|Delete/);
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
