import assert from "node:assert/strict";
import test from "node:test";
import {
  clientValidate,
  migrateInterfaceState,
  overwriteWithRecall,
  parametersForRequest,
  reconcileInterfaceValues,
} from "../src/lib.mjs";

function contractFor(ids, id = "loras") {
  return {
    inputs: [{
      id,
      type: "lora_stack",
      semantic_role: "lora",
      items: ids.map((id) => ({ id, label: id })),
      minimum: 0,
      maximum: 2,
      step: 0.05,
      default: ids.map((id) => ({ id, strength: 0 })),
    }],
  };
}

const oldIds = ["spread", "claire", "nexblend", "tifa", "alysa_liu"];
const newIds = Array.from({ length: 11 }, (_, index) => `new_${index}`);
const savedStack = [...oldIds].reverse().map((id, index) => ({ id, strength: index * 0.25 }));
const paths = [
  ["saved settings", (target, source, values) => reconcileInterfaceValues(target, values, source)],
  ["settings already saved with the new snapshot", (target, source, values) =>
    reconcileInterfaceValues(target, values, target)],
  ["migration by ID", (target, source, values) => migrateInterfaceState(target, source, values).values],
  ["migration by role", (target, source, values) => migrateInterfaceState(
    target,
    { inputs: [{ ...source.inputs[0], id: "old_loras" }] },
    Object.hasOwn(values, "loras") ? { old_loras: values.loras } : {},
  ).values],
  ...[true, false].map((sourceAvailable) => [
    `recall with source ${sourceAvailable ? "available" : "unavailable"}`,
    (target, source, values) => overwriteWithRecall(
      { parameters: { loras: target.inputs[0].default.map(({ id }) => ({ id, strength: 2 })) } },
      { source_available: sourceAvailable, parameters: values, input_definitions: source.inputs },
      target,
    ).parameters,
  ]),
  ["recall by role", (target, source, values) => overwriteWithRecall(
    { parameters: { loras: target.inputs[0].default.map(({ id }) => ({ id, strength: 2 })) } },
    {
      source_available: false,
      parameters: Object.hasOwn(values, "loras") ? { old_loras: values.loras } : {},
      input_definitions: [{ ...source.inputs[0], id: "old_loras" }],
    },
    target,
  ).parameters],
];

for (const [name, apply] of paths) {
  test(`${name}: a five-item stack grows to sixteen with saved order and strengths`, () => {
    const source = contractFor(oldIds);
    // Interleave old and new IDs to distinguish saved order from contract order.
    const target = contractFor([...newIds.slice(0, 3), ...oldIds, ...newIds.slice(3)]);
    for (const stack of [savedStack, savedStack.map(({ id }) => ({ id, strength: 0 }))]) {
      const values = { loras: structuredClone(stack) };
      const original = structuredClone({ values, source, target });
      const reconciled = apply(target, source, values);
      const expected = [...stack, ...newIds.map((id) => ({ id, strength: 0 }))];
      assert.deepEqual(reconciled.loras, expected);
      assert.deepEqual(clientValidate(target, reconciled), {});
      assert.deepEqual(parametersForRequest(target, reconciled).loras, expected);
      assert.deepEqual(reconcileInterfaceValues(target, reconciled, target), reconciled);
      reconciled.loras[0].strength = 1.75;
      reconciled.loras.at(-1).strength = 1.75;
      assert.deepEqual({ values, source, target }, original);
    }
  });

  test(`${name}: removed items are dropped and a reordered contract preserves saved order`, () => {
    const source = contractFor(oldIds);
    for (const ids of [[...oldIds].reverse(), ["claire", "spread", "tifa"]]) {
      const target = contractFor(ids);
      const values = apply(target, source, { loras: savedStack });
      assert.deepEqual(values.loras, savedStack.filter(({ id }) => ids.includes(id)));
      assert.deepEqual(clientValidate(target, values), {});
    }
  });

  test(`${name}: appended strengths come from defaults by ID in item order`, () => {
    const source = contractFor(["spread"]);
    const target = contractFor(["spread", "claire", "tifa"]);
    target.inputs[0].default = [
      { id: "tifa", strength: 0.75 },
      { id: "claire", strength: 0.25 },
      { id: "spread", strength: 0 },
    ];
    const values = apply(target, source, { loras: [{ id: "spread", strength: 1 }] });
    assert.deepEqual(values.loras, [
      { id: "spread", strength: 1 },
      { id: "claire", strength: 0.25 },
      { id: "tifa", strength: 0.75 },
    ]);
    assert.deepEqual(clientValidate(target, values), {});
  });

  test(`${name}: missing or malformed saved values restore the complete default`, () => {
    const source = contractFor(oldIds);
    const target = contractFor([...oldIds, ...newIds]);
    target.inputs[0].default.reverse();
    const malformed = [
      undefined, null, "[]", {}, 1,
      [null], [[]], ["spread"],
      [{ id: "spread" }],
      [{ strength: 0 }],
      [{ id: "spread", strength: 1, extra: true }],
      [{ id: 1, strength: 0 }],
      ...[null, "1", true, NaN, Infinity].map((strength) => [{ id: "spread", strength }]),
      [{ id: "spread", strength: 1 }, { id: "spread", strength: 0 }],
      [{ id: "retired", strength: 1 }, { id: "retired", strength: 0 }],
      [{ id: "spread", strength: 1 }, { id: "retired" }],
    ];
    for (const saved of [{}, ...malformed.map((loras) => ({ loras }))]) {
      const values = apply(target, source, saved);
      assert.deepEqual(values.loras, target.inputs[0].default);
      assert.notEqual(values.loras, target.inputs[0].default);
      assert.notEqual(values.loras[0], target.inputs[0].default[0]);
      assert.deepEqual(clientValidate(target, values), {});
    }
  });

  test(`${name}: empty or fully retired stacks use current items`, () => {
    const target = contractFor(newIds);
    for (const stack of [[], savedStack]) {
      const values = apply(target, contractFor(oldIds), { loras: stack });
      assert.deepEqual(values.loras, target.inputs[0].default);
      assert.deepEqual(clientValidate(target, values), {});
    }
  });

  test(`${name}: reconciliation leaves strength bounds and steps to strict validation`, () => {
    const target = contractFor(["spread", "claire"]);
    for (const strength of [-0.05, 2.05, 0.051]) {
      const values = apply(target, contractFor(["spread"]), { loras: [{ id: "spread", strength }] });
      assert.equal(values.loras[0].strength, strength);
      assert.ok(clientValidate(target, values).loras);
    }
  });
}
