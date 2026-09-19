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
