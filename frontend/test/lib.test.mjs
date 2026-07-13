import assert from "node:assert/strict";
import test from "node:test";

import {
  clientValidate,
  controlPresentation,
  defaultsForContract,
  footerText,
  overwriteWithRecall,
  resolutionConstraints,
  scaleToLayout,
} from "../src/lib.mjs";

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

test("footer uses only source, centered dot, and localized submission date text", () => {
  const value = footerText("Portrait Workflow", "2026-07-12T12:00:00Z", "en-US");
  assert.equal(value, "Portrait Workflow · Jul 12, 2026");
});
