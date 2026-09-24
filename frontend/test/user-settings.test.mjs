import test from "node:test";
import assert from "node:assert/strict";
import { mergeSettings, createSettingsSync } from "../src/user-settings.mjs";

test("independent nested edits merge, conflicting prompts remain local", () => {
  const base = { sources: { a: { prompt: "old", width: 512 } }, quantity: 1 };
  const local = structuredClone(base); local.sources.a.prompt = "my draft";
  const remote = structuredClone(base); remote.sources.a.width = 1024;
  assert.deepEqual(mergeSettings(base, local, remote), {
    value: { sources: { a: { prompt: "my draft", width: 1024 } }, quantity: 1 }, conflicts: [],
  });
  remote.sources.a.prompt = "other draft";
  assert.deepEqual(mergeSettings(base, local, remote).conflicts, ["sources.a.prompt"]);
  assert.equal(mergeSettings(base, local, remote).value.sources.a.prompt, "my draft");
});

test("arrays conflict rather than silently merging checkpoint ordering", () => {
  const merged = mergeSettings({ choices: ["a"] }, { choices: ["a", "b"] }, { choices: ["c"] });
  assert.deepEqual(merged.conflicts, ["choices"]);
});

test("remote settings conflict preserves draft until explicit resolution", async () => {
  const controller = new AbortController();
  let server = { revision: 1, settings_initialized: true, settings: { prompt: "initial", quantity: 1 }, gallery_scale: 45, checkpoint_tiers: {} };
  let local;
  let status;
  const sync = createSettingsSync({ signal: controller.signal,
    read: () => structuredClone(local), apply: (value) => { local = structuredClone(value); },
    status: (value) => { status = value; },
    api: async (_path, options) => {
      if (options.method === "PUT") {
        const body = JSON.parse(options.body);
        assert.equal(body.expected_revision, server.revision);
        server = { ...server, ...body, revision: server.revision + 1 };
      }
      return structuredClone(server);
    },
  });
  await sync.load();
  local.settings.prompt = "local";
  server.settings.prompt = "remote"; server.settings.quantity = 4; server.revision += 1;
  await sync.refresh();
  assert.equal(status, "conflict");
  assert.equal(local.settings.prompt, "local");
  await sync.resolve(true);
  assert.equal(server.settings.prompt, "local");
  assert.equal(server.settings.quantity, 4);
  assert.equal(status, "saved");
  controller.abort();
});

test("simultaneous startup refreshes perform one settings load", async () => {
  const controller = new AbortController();
  let calls = 0;
  const sync = createSettingsSync({ signal: controller.signal,
    read: () => ({}), apply: () => {}, status: () => {},
    api: async () => { calls += 1; await new Promise((r) => setTimeout(r, 5)); return { settings_initialized: true, revision: 0, settings: {}, gallery_scale: 45, checkpoint_tiers: {} }; },
  });
  await Promise.all([sync.load(), sync.refresh(), sync.refresh()]);
  assert.equal(calls, 1);
  controller.abort();
});

test("frequent background refreshes cannot postpone a pending settings save", async () => {
  const controller = new AbortController();
  let server = { settings_initialized: true, revision: 1, settings: { prompt: "old", quantity: 1 }, gallery_scale: 45, checkpoint_tiers: {} };
  let local;
  let writes = 0;
  const sync = createSettingsSync({ signal: controller.signal,
    read: () => structuredClone(local), apply: (value) => { local = structuredClone(value); }, status: () => {},
    api: async (_path, options) => {
      if (options.method === "PUT") { writes += 1; server = { ...server, ...JSON.parse(options.body), revision: server.revision + 1 }; }
      return structuredClone(server);
    },
  });
  try {
    await sync.load();
    local.settings.prompt = "my draft";
    server.settings.quantity = 2; server.revision += 1;
    await sync.refresh();
    for (let i = 0; i < 14; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      await sync.refresh();
    }
    assert.equal(writes, 1);
    assert.equal(server.settings.prompt, "my draft");
    assert.equal(server.settings.quantity, 2);
  } finally { controller.abort(); }
});

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}

test("reload preserves unsynced local edits while accepting unrelated remote changes", async () => {
  const storage = memoryStorage();
  const base = { settings: { prompt: "old", quantity: 1 }, gallery_scale: 45, checkpoint_tiers: {} };
  storage.setItem("user-one", JSON.stringify({ version: 1, revision: 2, base, local: { ...base, settings: { prompt: "unsynced", quantity: 1 } } }));
  let local = structuredClone(base);
  let server = { ...base, settings: { prompt: "old", quantity: 3 }, revision: 3, settings_initialized: true };
  const controller = new AbortController();
  const sync = createSettingsSync({ storage, storageKey: "user-one", signal: controller.signal,
    read: () => local, apply: (value) => { local = value; }, status: () => {},
    api: async (_path, options) => {
      if (options.method === "PUT") server = { ...server, ...JSON.parse(options.body), revision: 4 };
      return structuredClone(server);
    },
  });
  await sync.load();
  assert.deepEqual(local.settings, { prompt: "unsynced", quantity: 3 });
  local.settings.prompt = "last keystroke";
  sync.schedule();
  assert.equal(JSON.parse(storage.getItem("user-one")).local.settings.prompt, "last keystroke");
  controller.abort();
});

test("an unresolved settings conflict survives another refresh", async () => {
  const storage = memoryStorage();
  const base = { settings: { prompt: "old" }, gallery_scale: 45, checkpoint_tiers: {} };
  storage.setItem("user-one", JSON.stringify({ version: 1, revision: 1, base, local: { ...base, settings: { prompt: "mine" } } }));
  const server = { ...base, settings: { prompt: "theirs" }, revision: 2, settings_initialized: true };
  for (let refresh = 0; refresh < 2; refresh += 1) {
    const controller = new AbortController();
    let local = base;
    let status;
    const sync = createSettingsSync({ storage, storageKey: "user-one", signal: controller.signal,
      read: () => local, apply: (value) => { local = value; }, status: (value) => { status = value; },
      api: async (_path, options) => { assert.notEqual(options.method, "PUT"); return structuredClone(server); },
    });
    await sync.load();
    assert.equal(local.settings.prompt, "mine");
    assert.equal(status, "conflict");
    controller.abort();
  }
});

test("republishing reconciles all three versions before comparing an unsynced LoRA edit", async () => {
  const { reconcileInterfaceValues, overwriteWithRecall } = await import("../src/lib.mjs");
  const previous = { inputs: [{ id: "loras", type: "lora_stack", semantic_role: "lora", items: [{ id: "a" }, { id: "retired" }], minimum: 0, maximum: 2, step: 0.05, default: [{ id: "a", strength: 0 }, { id: "retired", strength: 0 }] }] };
  const current = { inputs: [{ ...previous.inputs[0], items: [{ id: "a" }, { id: "new" }], default: [{ id: "a", strength: 0 }, { id: "new", strength: 0.5 }] }] };
  const entry = (contract, loras) => ({ settings: { source: { interface: contract, values: { loras } } }, gallery_scale: 45, checkpoint_tiers: {} });
  const base = entry(previous, [{ id: "retired", strength: 0.3 }, { id: "a", strength: 0.1 }]);
  let local = entry(previous, [{ id: "retired", strength: 0.3 }, { id: "a", strength: 0.8 }]);
  const server = { ...entry(current, [{ id: "a", strength: 0.1 }, { id: "new", strength: 0.5 }]), revision: 2, settings_initialized: true };
  const storage = memoryStorage();
  storage.setItem("owner", JSON.stringify({ version: 1, base, local, revision: 1 }));
  let loaded = false;
  let status;
  const controller = new AbortController();
  const sync = createSettingsSync({ storage, storageKey: "owner", signal: controller.signal, read: () => local, apply: (value) => { local = value; }, status: (value) => { status = value; }, api: async () => server,
    prepareMerge: async () => { loaded = true; },
    normalize: (value) => {
      if (!loaded) return structuredClone(value);
      const result = structuredClone(value);
      result.settings.source.values = reconcileInterfaceValues(current, result.settings.source.values, result.settings.source.interface, ["loras"]);
      result.settings.source.interface = current;
      return result;
    },
  });
  await sync.load();
  assert.notEqual(status, "conflict");
  assert.deepEqual(local.settings.source.values.loras, [{ id: "a", strength: 0.8 }, { id: "new", strength: 0.5 }]);
  const history = { source_available: true, parameters: base.settings.source.values, input_definitions: previous.inputs };
  const untouched = structuredClone(history);
  assert.deepEqual(overwriteWithRecall({ parameters: local.settings.source.values }, history, current).parameters.loras, [{ id: "a", strength: 0.1 }, { id: "new", strength: 0.5 }]);
  assert.deepEqual(history, untouched);
  controller.abort();
});

test("browser journal is account scoped and storage failures stay visible", async () => {
  const storage = memoryStorage();
  const value = { settings: { prompt: "account two" }, gallery_scale: 45, checkpoint_tiers: {} };
  storage.setItem("one", JSON.stringify({ version: 1, revision: 1, base: value, local: { ...value, settings: { prompt: "private draft" } } }));
  const controller = new AbortController();
  let local = value;
  let status;
  const sync = createSettingsSync({ storage, storageKey: "two", signal: controller.signal, read: () => local, apply: (v) => { local = v; }, status: (v) => { status = v; }, api: async () => ({ ...value, revision: 1, settings_initialized: true }) });
  await sync.load();
  assert.equal(local.settings.prompt, "account two");
  storage.setItem = () => { throw new Error("quota"); };
  sync.schedule();
  await sync.save();
  assert.equal(status, "error");
  controller.abort();
});

test("conflict resolution reconciles either choice against the latest interface", async () => {
  for (const keepLocal of [false, true]) {
    const controller = new AbortController();
    let local = { settings: { prompt: "base", retired: 1 }, gallery_scale: 45, checkpoint_tiers: {} };
    let server = { ...structuredClone(local), revision: 1, settings_initialized: true };
    let republished = false;
    const sync = createSettingsSync({ signal: controller.signal, read: () => local, apply: (value) => { local = value; }, status: () => {},
      normalize: (value) => { const result = structuredClone(value); if (republished) delete result.settings.retired; return result; },
      api: async (_path, options) => { if (options.method === "PUT") server = { ...server, ...JSON.parse(options.body), revision: 3 }; return structuredClone(server); },
    });
    await sync.load();
    local.settings.prompt = "mine";
    server.settings.prompt = "theirs";
    server.revision = 2;
    await sync.refresh();
    republished = true;
    await sync.resolve(keepLocal);
    assert.equal(local.settings.prompt, keepLocal ? "mine" : "theirs");
    assert.equal(Object.hasOwn(local.settings, "retired"), false);
    if (keepLocal) assert.equal(Object.hasOwn(server.settings, "retired"), false);
    controller.abort();
  }
});
