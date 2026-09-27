import assert from "node:assert/strict";
import { test } from "node:test";
import { adminLoraMarkup, createAdminLoraController, newLoraOperationKey, reconcileLoraStrengthMemory, uploadLoraFile, validateLoraEdit, validateLoraInstall } from "../src/admin-loras.mjs";

const revision = { publication_id: "publication-1", workflow_sha256: "a", api_sha256: "b", manifest_sha256: "c" };

test("operation keys have the backend UUID shape when randomUUID is unavailable", () => {
  assert.match(newLoraOperationKey(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});

test("LoRA administration presents public catalog data without exposing server filenames", () => {
  const markup = adminLoraMarkup({
    sources: [{ source_key: "source-1", display_name: "Image & workflow" }],
    selectedSourceKey: "source-1",
    catalog: { eligible: true, revision, items: [{ id: "portrait", label: '<Portrait & "style">', trigger_word: "portrait subject", filename: "private/portrait.safetensors" }] },
  });
  assert.match(markup, /Image &amp; workflow/);
  assert.match(markup, /&lt;Portrait &amp; &quot;style&quot;&gt;/);
  assert.match(markup, /portrait subject/);
  assert.match(markup, /data-admin-lora-remove="portrait"/);
  assert.match(markup, /data-admin-lora-edit="portrait"/);
  assert.match(markup, /name="display_name"/);
  assert.match(markup, /name="trigger_word"/);
  assert.doesNotMatch(markup, /private\/portrait\.safetensors/);
  const blocked = adminLoraMarkup({ sources: [{ source_key: "source-1" }], selectedSourceKey: "source-1", catalog: { eligible: false, reason: "Replica is offline", items: [] } });
  assert.match(blocked, /Replica is offline/);
  assert.doesNotMatch(blocked, /admin-lora-install-form/);
});

test("editing preloads public title and trigger without exposing private filename", () => {
  const markup = adminLoraMarkup({
    sources: [{ source_key: "source-1" }], selectedSourceKey: "source-1",
    catalog: { eligible: true, revision, items: [{ id: "portrait", label: '<Portrait & "style">', trigger_word: "portrait subject", filename: "private/portrait.safetensors" }] },
    editingLoraId: "portrait",
  });
  assert.match(markup, /data-admin-lora-edit-form="portrait"/);
  assert.match(markup, /value="&lt;Portrait &amp; &quot;style&quot;&gt;"/);
  assert.match(markup, /value="portrait subject"/);
  assert.match(markup, /Trigger word \(optional\)/);
  assert.match(markup, /data-admin-lora-edit-cancel="portrait"/);
  assert.doesNotMatch(markup, /private\/portrait\.safetensors/);
  assert.doesNotMatch(markup, /data-admin-lora-remove="portrait"/);
});

test("edit validation accepts clearing a trigger and rejects unchanged or oversized values", () => {
  const item = { id: "portrait", label: "Portrait", trigger_word: "person" };
  assert.equal(validateLoraEdit({ item, displayName: " Portrait ", triggerWord: "  " }), null);
  assert.equal(validateLoraEdit({ item: { id: "portrait", label: "Portrait" }, displayName: " New title ", triggerWord: "" }), null);
  assert.match(validateLoraEdit({ item, displayName: "Portrait", triggerWord: "person" }), /Change the title or trigger word/);
  assert.match(validateLoraEdit({ item, displayName: " ", triggerWord: "person" }), /display title/);
  assert.match(validateLoraEdit({ item, displayName: "x".repeat(121), triggerWord: "" }), /120 characters/);
  assert.match(validateLoraEdit({ item, displayName: "Portrait", triggerWord: "x".repeat(121) }), /120 characters/);
});

test("install validation requires one weight file, title, and trigger", () => {
  const good = { file: { name: "test.safetensors", size: 24 }, displayName: "Style", triggerWord: "style trigger" };
  assert.equal(validateLoraInstall(good), null);
  assert.match(validateLoraInstall({ ...good, file: { name: "../test.safetensors", size: 24 } }), /safetensors/);
  assert.match(validateLoraInstall({ ...good, file: { name: "test.ckpt", size: 24 } }), /safetensors/);
  assert.match(validateLoraInstall({ ...good, displayName: " " }), /display title/);
  assert.match(validateLoraInstall({ ...good, triggerWord: " " }), /trigger word/);
});

test("raw upload reports progress and carries CSRF protection", async () => {
  let request;
  class FakeXHR {
    upload = {};
    headers = {};
    constructor() { request = this; }
    open(method, path) { this.method = method; this.path = path; }
    setRequestHeader(key, value) { this.headers[key] = value; }
    send(file) {
      this.file = file;
      this.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 });
      this.status = 202;
      this.responseText = JSON.stringify({ id: "operation-1", status: "running" });
      this.onload();
    }
  }
  const file = { name: "test.safetensors", size: 10 };
  let progress;
  const response = await uploadLoraFile("/api/admin/lora-operations/operation-1/file", file, { csrfToken: "csrf-token", onProgress: (value) => { progress = value; }, XMLHttpRequestClass: FakeXHR });
  assert.equal(request.method, "PUT");
  assert.equal(request.headers["Content-Type"], "application/octet-stream");
  assert.equal(request.headers["X-CSRF-Token"], "csrf-token");
  assert.equal(request.file, file);
  assert.equal(progress, 50);
  assert.equal(response.status, "running");
});

test("remove sends an exact revision and refreshes after confirmed success", async () => {
  const calls = [];
  let items = [{ id: "portrait", label: "Portrait", trigger_word: "person" }];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items };
    if (path === "/api/admin/lora-operations") return { id: "operation-1", status: "running" };
    if (path.endsWith("/operation-1")) return { id: "operation-1", status: "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let refreshes = 0;
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-1", confirm: () => true, pause: async () => {}, refreshSources: async () => { refreshes += 1; items = []; } });
  controller.mount(host, [{ source_key: "source-1", display_name: "Image workflow" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-remove]" ? { dataset: { adminLoraRemove: "portrait" } } : null } });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "remove", source_key: "source-1", expected_revision: revision, idempotency_key: "request-1", lora_id: "portrait" });
  assert.equal(request.options.method, "POST");
  assert.equal(controller.state.catalog.items.length, 0);
  assert.doesNotMatch(host.innerHTML, /Remove Portrait/);
});

test("install sends title, trigger, filename, then uploads one raw file before refresh", async () => {
  const calls = [];
  const file = { name: "portrait.safetensors", size: 96 };
  let items = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items };
    if (path === "/api/admin/lora-operations") return { id: "operation-2", status: "awaiting_upload" };
    if (path.endsWith("/operation-2")) return { id: "operation-2", status: "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let upload;
  let refreshes = 0;
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-2", pause: async () => {}, readForm: () => ({ get: (name) => ({ file, display_name: "Portrait", trigger_word: "person" })[name] }), uploadFile: async (path, body, options) => { upload = { path, body, options }; options.onProgress(100); }, refreshSources: async () => { refreshes += 1; items = [{ id: "portrait", label: "Portrait", trigger_word: "person" }]; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "install", source_key: "source-1", expected_revision: revision, idempotency_key: "request-2", filename: "portrait.safetensors", display_name: "Portrait", trigger_word: "person" });
  assert.equal(upload.path, "/api/admin/lora-operations/operation-2/file");
  assert.equal(upload.body, file);
  assert.equal(upload.options.csrfToken, "token");
  assert.equal(controller.state.catalog.items.length, 1);
});

test("edit sends a trimmed title and an empty trigger without upload or confirmation", async () => {
  const calls = [];
  let items = [{ id: "portrait", label: "Portrait" }];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items };
    if (path === "/api/admin/lora-operations") return { id: "operation-edit", status: "running" };
    if (path.endsWith("/operation-edit")) return { id: "operation-edit", status: "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let refreshes = 0;
  const controller = createAdminLoraController({
    api, getCsrfToken: () => "token", createId: () => "request-edit", pause: async () => {},
    readForm: () => ({ get: (name) => ({ display_name: " New Portrait ", trigger_word: " " })[name] }),
    uploadFile: () => { throw new Error("Edit must not upload a file"); },
    confirm: () => { throw new Error("Edit must not require removal confirmation"); },
    refreshSources: async () => { refreshes += 1; items = [{ id: "portrait", label: "New Portrait" }]; },
  });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-edit]" ? { dataset: { adminLoraEdit: "portrait" } } : null } });
  assert.match(host.innerHTML, /data-admin-lora-edit-form="portrait"/);
  listeners.submit({ target: { dataset: { adminLoraEditForm: "portrait" } }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "edit", source_key: "source-1", expected_revision: revision, idempotency_key: "request-edit", lora_id: "portrait", display_name: "New Portrait", trigger_word: "" });
  assert.equal(controller.state.status, "LoRA details updated and published.");
  assert.equal(controller.state.editingLoraId, null);
  assert.equal(controller.state.catalog.items[0].label, "New Portrait");
});

test("edit no-op stays local, and Cancel discards the draft", async () => {
  const calls = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items: [{ id: "portrait", label: "Portrait" }] };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", readForm: () => ({ get: (name) => ({ display_name: " Portrait ", trigger_word: " " })[name] }), refreshSources: async () => {} });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-edit]" ? { dataset: { adminLoraEdit: "portrait" } } : null } });
  listeners.submit({ target: { dataset: { adminLoraEditForm: "portrait" } }, preventDefault() {} });
  await waitFor(() => /Change the title or trigger word/.test(controller.state.error));
  assert.equal(calls.filter((call) => call.path === "/api/admin/lora-operations").length, 0);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-edit-cancel]" ? { dataset: { adminLoraEditCancel: "portrait" } } : null } });
  assert.equal(controller.state.editingLoraId, null);
  assert.doesNotMatch(host.innerHTML, /data-admin-lora-edit-form/);
  assert.equal(controller.state.error, "");
});

test("stale edit revision refreshes the catalog and closes the old draft", async () => {
  let catalogCalls = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (path.endsWith("/loras")) {
      catalogCalls += 1;
      return { source_key: "source-1", revision: catalogCalls === 1 ? revision : { ...revision, manifest_sha256: "new" }, eligible: true, items: [{ id: "portrait", label: catalogCalls === 1 ? "Portrait" : "Other title" }] };
    }
    if (path === "/api/admin/lora-operations") {
      const error = new Error("The publication changed.");
      error.code = "source_republished";
      error.status = 409;
      throw error;
    }
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", readForm: () => ({ get: (name) => ({ display_name: "Changed", trigger_word: "" })[name] }), refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-edit]" ? { dataset: { adminLoraEdit: "portrait" } } : null } });
  listeners.submit({ target: { dataset: { adminLoraEditForm: "portrait" } }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && catalogCalls === 2 && !controller.state.busy);
  assert.equal(controller.state.editingLoraId, null);
  assert.equal(controller.state.catalog.items[0].label, "Other title");
  assert.match(controller.state.error, /publication changed/);
});

test("interrupted upload checks operation state and retries the same operation", async () => {
  const file = { name: "portrait.safetensors", size: 96 };
  const observed = ["running", "awaiting_upload", "succeeded"];
  let uploads = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items: [] };
    if (path === "/api/admin/lora-operations") return { id: "operation-3", status: "awaiting_upload" };
    if (path.endsWith("/operation-3")) return { id: "operation-3", status: observed.shift() };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-3", pause: async () => {}, readForm: () => ({ get: (name) => ({ file, display_name: "Portrait", trigger_word: "person" })[name] }), uploadFile: async (path, body) => { assert.equal(path, "/api/admin/lora-operations/operation-3/file"); assert.equal(body, file); uploads += 1; if (uploads === 1) throw new Error("Connection lost"); }, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  assert.equal(uploads, 2);
  assert.equal(controller.state.error, "");
});

test("removal blocker leaves the published item and reports the reason", async () => {
  const api = async (path) => {
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items: [{ id: "portrait", label: "Portrait" }] };
    if (path === "/api/admin/lora-operations") return { id: "operation-4", status: "running" };
    if (path.endsWith("/operation-4")) return { id: "operation-4", status: "failed", message: "Removal was blocked.", blockers: ["An active generation needs this LoRA."] };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let refreshes = 0;
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-4", confirm: () => true, pause: async () => {}, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-remove]" ? { dataset: { adminLoraRemove: "portrait" } } : null } });
  await waitFor(() => !controller.state.busy && controller.state.error);
  assert.equal(refreshes, 0);
  assert.equal(controller.state.catalog.items.length, 1);
  assert.match(controller.state.error, /active generation/);
});

test("an incomplete upload can be resumed with the selected file", async () => {
  const file = { name: "portrait.safetensors", size: 96 };
  let uploads = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items: [] };
    if (path === "/api/admin/lora-operations") return { id: "operation-5", status: "awaiting_upload" };
    if (path.endsWith("/operation-5")) return { id: "operation-5", status: uploads < 3 ? "awaiting_upload" : "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-5", pause: async () => {}, readForm: () => ({ get: (name) => ({ file, display_name: "Portrait", trigger_word: "person" })[name] }), uploadFile: async () => { uploads += 1; if (uploads <= 2) throw new Error("Connection lost"); }, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => !controller.state.busy && uploads === 2);
  assert.match(host.innerHTML, /Retry upload/);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-retry-upload]" ? {} : null } });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  assert.equal(uploads, 3);
  assert.equal(controller.state.pendingUploads.size, 0);
});

test("pending upload can be cancelled without a file", async () => {
  const calls = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/loras")) return { source_key: "source-1", revision, eligible: true, items: [] };
    if (path.endsWith("/operation-6/cancel")) return { id: "operation-6", status: "failed", message: "Upload cancelled." };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", refreshSources: async () => {} });
  controller.state.operationsBySource.set("source-1", { id: "operation-6", status: "awaiting_upload" });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.catalog);
  assert.match(host.innerHTML, /Cancel pending upload/);
  assert.doesNotMatch(host.innerHTML, /Retry upload/);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-cancel-upload]" ? {} : null } });
  await waitFor(() => controller.state.operationsBySource.get("source-1")?.status === "failed" && !controller.state.busy);
  assert.equal(calls.find((call) => call.path.endsWith("/cancel")).options.method, "POST");
  assert.match(host.innerHTML, /Pending upload cancelled/);
});

test("pending operation ID survives a page reload so upload can be cancelled", async () => {
  const source = "a".repeat(64);
  const operationId = "11111111-1111-4111-8111-111111111111";
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  let uploads = 0;
  const api = async (path) => {
    if (path.endsWith("/loras")) return { source_key: source, revision, eligible: true, items: [] };
    if (path === "/api/admin/lora-operations") return { id: operationId, status: "awaiting_upload" };
    if (path.endsWith(`/${operationId}`)) return { id: operationId, status: "awaiting_upload" };
    if (path.endsWith(`/${operationId}/cancel`)) return { id: operationId, status: "failed", message: "Upload cancelled." };
    throw new Error(`Unexpected path ${path}`);
  };
  const makeHost = () => {
    const listeners = {};
    return { host: { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } }, listeners };
  };
  const first = makeHost();
  const file = { name: "portrait.safetensors", size: 96 };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => operationId, storage: () => storage, actorId: () => "admin-1", readForm: () => ({ get: (name) => ({ file, display_name: "Portrait", trigger_word: "person" })[name] }), uploadFile: async () => { uploads += 1; throw new Error("Connection lost"); }, refreshSources: async () => {} });
  controller.mount(first.host, [{ source_key: source }], source);
  await waitFor(() => controller.state.catalog);
  first.listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => uploads === 2 && !controller.state.busy);
  assert.match(values.get("cif-admin-lora-operations:admin-1"), new RegExp(operationId));

  const second = makeHost();
  const restored = createAdminLoraController({ api, getCsrfToken: () => "token", storage: () => storage, actorId: () => "admin-1", refreshSources: async () => {} });
  restored.mount(second.host, [{ source_key: source }], source);
  await waitFor(() => restored.state.catalog && restored.state.operationsBySource.get(source)?.status === "awaiting_upload");
  assert.match(second.host.innerHTML, /Cancel pending upload/);
  assert.doesNotMatch(second.host.innerHTML, /Retry upload/);
  second.listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-cancel-upload]" ? {} : null } });
  await waitFor(() => restored.state.operationsBySource.get(source)?.status === "failed" && !restored.state.busy);
  assert.equal(values.has("cif-admin-lora-operations:admin-1"), false);
});

test("a running edit resumes after reload and reports edit success", async () => {
  const source = "b".repeat(64);
  const operationId = "22222222-2222-4222-8222-222222222222";
  const values = new Map([["cif-admin-lora-operations:admin-1", JSON.stringify({ [source]: { id: operationId, kind: "edit" } })]]);
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  let statusCalls = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (path.endsWith("/loras")) return { source_key: source, revision, eligible: true, items: [{ id: "portrait", label: "New title" }] };
    if (path.endsWith(`/${operationId}`)) return { id: operationId, status: ++statusCalls === 1 ? "running" : "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const host = { isConnected: true, innerHTML: "", addEventListener() {} };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", storage: () => storage, actorId: () => "admin-1", pause: async () => {}, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: source }], source);
  await waitFor(() => refreshes === 1 && controller.state.operationsBySource.get(source)?.status === "succeeded");
  assert.equal(controller.state.status, "LoRA details updated and published.");
  assert.equal(values.has("cif-admin-lora-operations:admin-1"), false);
});

test("reconciliation removes strengths for unpublished LoRAs", () => {
  const contract = { inputs: [{ id: "loras", type: "lora_stack", items: [{ id: "remaining" }] }] };
  assert.deepEqual(reconcileLoraStrengthMemory(contract, { loras: { remaining: 0.7, removed: 0.4 }, other: { old: 1 } }), { loras: { remaining: 0.7 } });
});

async function waitFor(check) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.fail("Expected asynchronous state was not reached.");
}
