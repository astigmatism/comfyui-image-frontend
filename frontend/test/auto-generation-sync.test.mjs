import test from "node:test";
import assert from "node:assert/strict";
import { createAutoGenerationSync } from "../src/auto-generation-sync.mjs";

function fixture(overrides = {}) {
  const snapshot = { generation: { source_key: "source", revision: {}, parameters: { seed: "random" }, collection_id: "pinned" }, variants: [{}], quantity: 1, max_generations: 200 };
  let auto = { enabled: true, revision: 1, snapshot };
  let local = structuredClone(snapshot);
  let report;
  const requests = [];
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  const controller = new AbortController();
  const options = {
    signal: controller.signal, storage, storageKey: "owner", delay: 10_000,
    current: () => auto, read: () => local, apply: (value) => { auto = value; },
    status: (status, message) => { report = { status, message }; }, saving: () => {},
    api: async (path, options) => {
      if (!options?.body) return auto;
      const payload = JSON.parse(options.body);
      requests.push(payload);
      return { ...auto, revision: auto.revision + 1, snapshot: payload.snapshot };
    }, ...overrides,
  };
  const sync = createAutoGenerationSync(options);
  return { sync, options, requests, values, controller,
    local: () => local, edit: (update) => { local = { ...local, ...update }; },
    auto: () => auto, remote: (update) => { auto = { ...auto, ...update }; }, report: () => report };
}

test("quantity edits coalesce and preserve pinned destination and limit", async () => {
  const f = fixture();
  try {
    f.edit({ quantity: 2 }); f.sync.stage();
    f.edit({ quantity: 3 }); f.sync.stage();
    await f.sync.flush();
    assert.equal(f.requests.length, 1);
    assert.equal(f.auto().snapshot.quantity, 3);
    assert.equal(f.requests[0].snapshot.generation.collection_id, "pinned");
    assert.equal(f.requests[0].snapshot.max_generations, 200);
    assert.equal(f.values.size, 0);
  } finally { f.controller.abort(); }
});

test("edits made during a save are sent with the acknowledged revision", async () => {
  let release;
  let entered;
  const started = new Promise((done) => { entered = done; });
  const f = fixture({ api: async (path, options) => {
    const payload = JSON.parse(options.body);
    f.requests.push(payload);
    if (f.requests.length === 1) { entered(); await new Promise((done) => { release = done; }); }
    return { ...f.auto(), revision: payload.expected_revision + 1, snapshot: payload.snapshot };
  } });
  try {
    f.edit({ quantity: 2 }); f.sync.stage();
    const first = f.sync.flush(); await started;
    f.edit({ quantity: 4 }); f.sync.stage(); release(); await first;
    await f.sync.flush();
    assert.deepEqual(f.requests.map((r) => [r.expected_revision, r.snapshot.quantity]), [[1, 2], [2, 4]]);
  } finally { f.controller.abort(); }
});

test("lost response reconciles accepted settings without resending", async () => {
  let committed;
  const f = fixture({ api: async (path, options) => {
    if (!options.body) return committed;
    const payload = JSON.parse(options.body);
    f.requests.push(payload);
    committed = { ...f.auto(), revision: 2, snapshot: payload.snapshot };
    throw new Error("connection closed");
  } });
  try {
    f.edit({ quantity: 2 }); f.sync.stage(); await f.sync.flush();
    assert.equal(f.requests.length, 1);
    assert.equal(f.values.size, 0);
    assert.equal(f.auto().snapshot.quantity, 2);
  } finally { f.controller.abort(); }
});

test("cross-device conflicts retain the edit and require explicit retry", async () => {
  const f = fixture();
  try {
    f.edit({ quantity: 2 }); f.sync.stage();
    f.remote({ revision: 2, snapshot: { ...f.auto().snapshot, quantity: 3 } });
    f.sync.observe(f.auto()); await f.sync.flush();
    assert.equal(f.requests.length, 0);
    assert.equal(f.report().status, "conflict");
    assert.equal(f.values.size, 1);
    await f.sync.retry();
    assert.equal(f.requests[0].expected_revision, 2);
    assert.equal(f.auto().snapshot.quantity, 2);
  } finally { f.controller.abort(); }
});

test("persisted edits survive reload and stop cannot be undone by an edit", async () => {
  const f = fixture();
  try {
    f.edit({ quantity: 2 }); f.sync.stage();
    const restored = createAutoGenerationSync(f.options);
    restored.observe(f.auto()); await restored.flush();
    assert.equal(f.auto().snapshot.quantity, 2);
    f.edit({ quantity: 3 }); f.sync.stage();
    f.remote({ enabled: false }); f.sync.observe(f.auto()); await f.sync.flush();
    assert.equal(f.requests.length, 1);
    assert.equal(f.values.size, 0);
  } finally { f.controller.abort(); }
});

test("server broadcasts alone never submit the panel's stale settings", async () => {
  const f = fixture();
  try {
    f.remote({ revision: 9, snapshot: { ...f.auto().snapshot, quantity: 5 } });
    f.sync.observe(f.auto()); await f.sync.flush();
    assert.equal(f.requests.length, 0);
  } finally { f.controller.abort(); }
});

test("an edit made while the workflow loads resumes when its controls are ready", async () => {
  let loading = true;
  const f = fixture({ read: () => {
    if (loading) throw new Error("Wait for the workflow settings to load.");
    return f.local();
  } });
  try {
    f.edit({ max_generations: 7 }); f.sync.stage();
    await f.sync.flush();
    assert.equal(f.requests.length, 0);
    loading = false;
    f.sync.resume(); await f.sync.flush();
    assert.equal(f.auto().snapshot.max_generations, 7);
  } finally { f.controller.abort(); }
});


test("restored pending automation edits discard old runtime selections", async () => {
  const f = fixture();
  try {
    const old = structuredClone(f.local());
    old.quantity = 3;
    old.generation.comfyui_instance_id = "cpu";
    old.prompt_generation = { source_key: "text", parameters: {}, comfyui_instance_id: "gpu" };
    f.values.set("owner", JSON.stringify({ revision: 1, snapshot: old }));
    const restored = createAutoGenerationSync(f.options);
    await restored.flush();
    assert.equal(f.requests.length, 1);
    const sent = f.requests[0].snapshot;
    assert.equal(sent.quantity, 3);
    assert.equal(Object.hasOwn(sent.generation, "comfyui_instance_id"), false);
    assert.equal(Object.hasOwn(sent.prompt_generation, "comfyui_instance_id"), false);
    restored.observe({ ...f.auto(), snapshot: { ...sent,
      generation: { ...sent.generation, comfyui_instance_id: "gpu" },
      prompt_generation: { ...sent.prompt_generation, comfyui_instance_id: "cpu" },
    } });
    await restored.flush();
    assert.equal(f.requests.length, 1);
  } finally { f.controller.abort(); }
});
