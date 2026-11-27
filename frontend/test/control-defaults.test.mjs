import assert from "node:assert/strict";
import test from "node:test";

import { applyCheckpointControlDefaults } from "../src/lib.mjs";

// The published bounds of the first real use of this feature: a re-draw amount whose
// published default renders skin as faceted patches on a high-micro-contrast checkpoint.
const contract = {
  inputs: [
    { id: "refine_denoise", label: "Refinement denoise", type: "number", default: 0.39, minimum: 0, maximum: 1, step: 0.01 },
    { id: "cfg", label: "Guidance", type: "number", default: 4.5, minimum: 1, maximum: 12, step: 0.5 },
    { id: "steps", label: "Steps", type: "integer", default: 20, minimum: 1, maximum: 60, step: 1 },
    { id: "half_step", label: "Odd grid", type: "integer", default: 4, minimum: 1, maximum: 9, step: 0.5 },
    { id: "unbounded", label: "No grid", type: "number", default: 2 },
    { id: "face_fix", label: "Face fix", type: "boolean", default: false },
    { id: "sampler", label: "Sampler", type: "choice", default: "euler_a", choices: [{ value: "euler_a", label: "Euler a" }, { value: "dpmpp_2m", label: "DPM++ 2M" }] },
    { id: "seed", label: "Seed", type: "seed", default: null, minimum: "0", maximum: "9223372036854775807" },
    { id: "style", label: "Style", type: "string", default: "" },
    { id: "init_image", label: "Init image", type: "image", default: null },
    { id: "loras", label: "LoRAs", type: "lora_stack", default: [], items: [] },
  ],
};

const INT8 = "intorealism_krea2_flash_v3_int8";

function apply({ values = {}, explicitInputIds = [], optionValue = INT8, defaults = {} } = {}) {
  return applyCheckpointControlDefaults({ contract, values, explicitInputIds, optionValue, defaults });
}

test("a checkpoint with no recorded defaults leaves every value alone", () => {
  const values = { refine_denoise: 0.39, prompt: "a portrait" };
  const { values: next, applied } = apply({
    values,
    defaults: { other_checkpoint: { refine_denoise: 0 } },
  });
  assert.deepEqual(next, { refine_denoise: 0.39, prompt: "a portrait" });
  assert.deepEqual(applied, []);
});

test("an explicit value for this generation is never clobbered by a default", () => {
  const defaults = { [INT8]: { refine_denoise: 0, cfg: 2 } };
  const { values, applied } = apply({
    values: { refine_denoise: 0.25 },
    explicitInputIds: ["refine_denoise"],
    defaults,
  });
  assert.equal(values.refine_denoise, 0.25);
  assert.deepEqual(applied, [{ id: "cfg", value: 2, previous: undefined }]);
  // A Set is what the panel keeps, and it has to be honoured the same way.
  assert.deepEqual(
    apply({ values: {}, explicitInputIds: new Set(["refine_denoise"]), defaults }).values.refine_denoise,
    undefined,
  );
});

test("a control the interface does not declare is skipped", () => {
  const { values, applied } = apply({
    values: { sampler: "euler_a" },
    defaults: { [INT8]: { sampler: "dpmpp_2m", retired_control: 0.5 } },
  });
  assert.deepEqual(Object.keys(values), ["sampler"]);
  assert.deepEqual(applied, [{ id: "sampler", value: "dpmpp_2m", previous: "euler_a" }]);
});

test("numbers are clamped to the published bounds and snapped to the published step", () => {
  // value, minimum, maximum, step, expected — every row is also asserted against
  // `_clamp_number` in backend/app/domain/control_defaults.py, which produces the same value.
  const cases = [
    [0, 0, 1, 0.01, 0],
    [0.39, 0, 1, 0.01, 0.39],
    [1.4, 0, 1, 0.01, 1],
    [-3, 0, 1, 0.01, 0],
    [4.27, 0, 1, 0.01, 1],
    [0.3949, 0, 1, 0.01, 0.39],
    [0.395, 0, 1, 0.01, 0.4],
    [0.005, 0, 1, 0.01, 0.01],
    [4.249999999999999, 1, 12, 0.5, 4],
    [4.26, 1, 12, 0.5, 4.5],
    [0.30000000000000004, 0, 1, 0.1, 0.3],
    [12.5, -20, 20, 2.5, 12.5],
  ];
  for (const [stored, minimum, maximum, step, expected] of cases) {
    const grid = { inputs: [{ id: "grid", type: "number", minimum, maximum, step }] };
    const { applied } = applyCheckpointControlDefaults({
      contract: grid,
      values: {},
      optionValue: "cp",
      defaults: { cp: { grid: stored } },
    });
    assert.deepEqual(applied, [{ id: "grid", value: expected, previous: undefined }], `${stored} on ${minimum}..${maximum}/${step}`);
  }
  // A control that publishes no usable grid cannot carry a numeric default at all.
  const unbounded = apply({ defaults: { [INT8]: { unbounded: 7 } } });
  assert.deepEqual(unbounded.applied, []);
  assert.equal(unbounded.values.unbounded, undefined);
});

test("an integer value the control cannot show is skipped instead of rounded", () => {
  const whole = apply({ defaults: { [INT8]: { steps: 28.6 } } });
  assert.deepEqual(whole.applied, [{ id: "steps", value: 29, previous: undefined }]);
  assert.deepEqual(apply({ defaults: { [INT8]: { half_step: 3.5 } } }).applied, []);
  assert.deepEqual(apply({ defaults: { [INT8]: { steps: "28" } } }).applied, []);
  assert.deepEqual(apply({ defaults: { [INT8]: { steps: Number.NaN } } }).applied, []);
});

test("a switch accepts only a real boolean and a choice only a published option", () => {
  assert.deepEqual(
    apply({ defaults: { [INT8]: { face_fix: true } } }).applied,
    [{ id: "face_fix", value: true, previous: undefined }],
  );
  assert.deepEqual(
    apply({ values: { face_fix: true }, defaults: { [INT8]: { face_fix: false } } }).values.face_fix,
    false,
  );
  assert.deepEqual(apply({ defaults: { [INT8]: { face_fix: "true" } } }).applied, []);
  assert.deepEqual(apply({ defaults: { [INT8]: { face_fix: 1 } } }).applied, []);
  const choice = apply({
    values: { sampler: "euler_a" },
    defaults: { [INT8]: { sampler: "dpmpp_2m" } },
  });
  assert.deepEqual(choice.applied, [{ id: "sampler", value: "dpmpp_2m", previous: "euler_a" }]);
  assert.deepEqual(apply({ defaults: { [INT8]: { sampler: "heun" } } }).applied, []);
  assert.deepEqual(apply({ defaults: { [INT8]: { sampler: true } } }).applied, []);
});

test("a seed, string, image or LoRA stack has no known-good value per checkpoint", () => {
  const { values, applied } = apply({
    values: { seed: { mode: "fixed", value: "12" }, style: "film", init_image: "asset-1", loras: [{ id: "l", strength: 1 }] },
    defaults: {
      [INT8]: {
        seed: "12",
        style: "portrait",
        init_image: "asset-2",
        loras: [{ id: "l", strength: 0.5 }],
      },
    },
  });
  assert.deepEqual(applied, []);
  assert.deepEqual(values, { seed: { mode: "fixed", value: "12" }, style: "film", init_image: "asset-1", loras: [{ id: "l", strength: 1 }] });
});

test("the inputs are never mutated and the applied list reports what was replaced", () => {
  const values = { refine_denoise: 0.39, loras: [{ id: "l", strength: 1 }] };
  const published = { [INT8]: { refine_denoise: 0, cfg: 2 } };
  const { values: next, applied } = apply({ values, defaults: published });
  assert.deepEqual(values, { refine_denoise: 0.39, loras: [{ id: "l", strength: 1 }] });
  assert.deepEqual(published, { [INT8]: { refine_denoise: 0, cfg: 2 } });
  assert.notEqual(next, values);
  assert.equal(next.refine_denoise, 0);
  assert.deepEqual(applied, [
    { id: "refine_denoise", value: 0, previous: 0.39 },
    { id: "cfg", value: 2, previous: undefined },
  ]);
});
