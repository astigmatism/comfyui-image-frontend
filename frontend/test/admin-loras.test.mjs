import assert from "node:assert/strict";
import { test } from "node:test";
import { adminLoraMarkup, createAdminLoraController, libraryStatusState, loraLibraryChipMarkup, loraLibraryMenuId, loraLibraryNoticeMarkup, loraLibraryPopoverMarkup, MAX_TRIGGER_WORD, newLoraOperationKey, reconcileLoraStrengthMemory, uploadLoraFile, validateLoraEdit, validateLoraInstall } from "../src/admin-loras.mjs";

const revision = { publication_id: "publication-1", workflow_sha256: "a", api_sha256: "b", manifest_sha256: "c" };
const triggerPhrase = "1oandve, short, straight black hair with bangs, pale skin, green eyes, accentuated with black winged eyeliner, light pink lipstick";
const expectedLibrary = [
  { source_key: "a".repeat(64), revision },
  { source_key: "b".repeat(64), revision: { ...revision, publication_id: "publication-2" } },
];
const members = [
  { source_key: "a".repeat(64), display_name: "Moody Krea2 Advanced v1", revision, in_sync: true, missing_count: 0, item_count: 1 },
  { source_key: "b".repeat(64), display_name: "Moody Krea2 Minimal v1", revision: expectedLibrary[1].revision, in_sync: true, missing_count: 0, item_count: 1 },
];
const library = (items, extra = {}) => ({ key: "krea2", label: "Krea 2", members, items, in_sync: true, conflicts: [], eligible: true, reason: null, can_sync: false, expected_library: expectedLibrary, ...extra });
const view = (items, extra = {}) => ({ libraries: [library(items, extra)], active_operation: null });
const isLibrary = (path) => path === "/api/admin/lora-library";
const toolbar = (state = {}, operation) => ({ state: { loading: false, busy: false, uploadPercent: null, status: "", error: "", selectedLibraryKey: "krea2", pendingUploads: new Map(), ...state }, currentOperation: () => operation });
const outOfSync = [members[0], { ...members[1], in_sync: false, missing_count: 1 }];

test("operation keys have the backend UUID shape when randomUUID is unavailable", () => {
  assert.match(newLoraOperationKey(), /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});

test("LoRA administration presents public catalog data without exposing server filenames", () => {
  const markup = adminLoraMarkup({
    library: library([{ id: "portrait", label: '<Portrait & "style">', trigger_word: "portrait subject", filename: "private/portrait.safetensors" }], { members: [{ ...members[0], display_name: "Image & workflow" }, members[1]] }),
  });
  assert.match(markup, /Image &amp; workflow/);
  assert.match(markup, /Moody Krea2 Minimal v1/);
  assert.match(markup, /shared by 2 workflows/);
  assert.match(markup, /&lt;Portrait &amp; &quot;style&quot;&gt;/);
  assert.match(markup, /portrait subject/);
  assert.match(markup, /data-admin-lora-remove="portrait"/);
  assert.match(markup, /data-admin-lora-edit="portrait"/);
  assert.match(markup, /name="display_name"/);
  assert.match(markup, /name="trigger_word"/);
  // A trigger phrase needs a multi-line box that accepts the whole cap.
  assert.match(markup, new RegExp(`<textarea name="trigger_word" rows="3" maxlength="${MAX_TRIGGER_WORD}"[^>]*></textarea>`));
  assert.match(markup, /class="field admin-lora-trigger"/);
  assert.doesNotMatch(markup, /private\/portrait\.safetensors/);
  const blocked = adminLoraMarkup({ library: library([], { eligible: false, reason: "Replica is offline" }) });
  assert.match(blocked, /Replica is offline/);
  assert.doesNotMatch(blocked, /admin-lora-install-form/);
  const drifted = adminLoraMarkup({ library: library([{ id: "portrait", label: "Portrait" }], { in_sync: false, eligible: false, can_sync: true, reason: "Sync the library before other changes.", members: [members[0], { ...members[1], in_sync: false, missing_count: 1 }] }) });
  assert.match(drifted, /Needs sync · 1 missing/);
  assert.match(drifted, /data-admin-lora-sync(?! disabled)/);
  const conflicted = adminLoraMarkup({ library: library([], { in_sync: false, eligible: false, conflicts: ["LoRA 'Beta' uses different files in different workflows."] }) });
  assert.match(conflicted, /uses different files/);
  assert.doesNotMatch(conflicted, /data-admin-lora-sync/);
});

test("the toolbar chip states the library condition in one glance", () => {
  assert.deepEqual(libraryStatusState({ library: library([]) }), { tone: "is-synced", text: "In sync", meta: "2 workflows" });
  assert.deepEqual(libraryStatusState({ library: library([], { in_sync: false, can_sync: true, members: outOfSync }) }), { tone: "is-drifted", text: "Needs sync", meta: "1 missing" });
  assert.deepEqual(libraryStatusState({ library: library([], { conflicts: ["different files"] }) }), { tone: "is-alert", text: "Publishing conflict", meta: "1 to fix" });
  assert.deepEqual(libraryStatusState({ library: library([]), error: "Upload failed." }), { tone: "is-alert", text: "Needs attention" });
  assert.deepEqual(libraryStatusState({ library: library([]), busy: true, uploadPercent: 42 }), { tone: "is-busy", text: "Uploading 42%" });
  assert.deepEqual(libraryStatusState({ library: library([]), operation: { status: "running" } }), { tone: "is-busy", text: "Working…" });
  // A live operation outranks the stale failure beside it, repair aside.
  assert.deepEqual(libraryStatusState({ library: library([]), operation: { status: "repair_required" }, busy: true }), { tone: "is-alert", text: "Needs repair" });
  assert.deepEqual(libraryStatusState({ library: library([]), busy: true, error: "Upload failed." }), { tone: "is-busy", text: "Working…" });
  assert.deepEqual(libraryStatusState({ loading: true }), { tone: "is-muted", text: "Checking library…" });
  assert.deepEqual(libraryStatusState({}), { tone: "is-muted", text: "No library" });
});

test("the toolbar chip is the popover trigger and keeps its message announced", () => {
  const drifted = toolbar({ library: library([], { in_sync: false, can_sync: true, members: outOfSync }), status: "Syncing library workflows…" });
  const chip = loraLibraryChipMarkup(drifted, { open: true });
  assert.match(chip, new RegExp(`data-lora-menu="${loraLibraryMenuId}"`));
  assert.match(chip, /aria-haspopup="dialog" aria-expanded="true"/);
  assert.match(chip, /aria-label="Library status: Needs sync, 1 missing"/);
  assert.match(chip, /class="lm-status-chip is-drifted"/);
  assert.match(chip, /role="status" aria-live="polite">Syncing library workflows…</);
  assert.match(loraLibraryChipMarkup(drifted), /aria-expanded="false"/);
  assert.equal(loraLibraryChipMarkup(null), "");
});

test("the toolbar popover keeps every diagnostic and action of the retired panel", () => {
  const drifted = loraLibraryPopoverMarkup(toolbar({ library: library([], { in_sync: false, can_sync: true, members: outOfSync }) }));
  assert.match(drifted, /Changes affect every workflow in this model family\./);
  assert.match(drifted, /Moody Krea2 Minimal v1/);
  assert.match(drifted, /Needs sync · 1 missing/);
  assert.match(drifted, /data-lora-library-sync(?! disabled)/);
  assert.match(drifted, /data-lora-library-reload>/);
  assert.doesNotMatch(drifted, /data-lora-library-retry/);
  // Only the header announces; the popover repeats the message as plain text.
  assert.doesNotMatch(drifted, /role="status"|role="alert"/);

  const uploading = toolbar({ library: library([]), busy: true, uploadPercent: 42, status: "Uploading weight file to ComfyUI…" }, { status: "awaiting_upload" });
  const progress = loraLibraryPopoverMarkup(uploading);
  assert.match(progress, /<progress max="100" value="42" aria-label="LoRA upload progress">/);
  assert.match(progress, /Uploading weight file to ComfyUI…/);
  assert.match(progress, /data-lora-library-reload disabled>/);
  // Recovery actions return only once the upload itself has stopped.
  assert.doesNotMatch(progress, /data-lora-library-retry|data-lora-library-cancel-upload/);

  // A half-finished upload offers to resend the same operation while the file
  // is still held, and conflicts are listed instead of offering a refused sync.
  const resumable = toolbar({ library: library([]), pendingUploads: new Map([["krea2", { name: "portrait.safetensors" }]]) }, { status: "awaiting_upload" });
  assert.match(loraLibraryPopoverMarkup(resumable), /data-lora-library-retry>/);
  assert.match(loraLibraryPopoverMarkup(resumable), /data-lora-library-cancel-upload>/);
  assert.deepEqual(libraryStatusState({ library: library([]), operation: { status: "awaiting_upload" } }), { tone: "is-busy", text: "Awaiting file" });
  const stalled = toolbar({ library: library([]), error: "The upload was interrupted.", pendingUploads: new Map() }, { status: "awaiting_upload" });
  const stalledPopover = loraLibraryPopoverMarkup(stalled);
  assert.match(stalledPopover, /data-lora-library-retry disabled>/);
  assert.match(stalledPopover, /The upload was interrupted\./);
  const conflicted = loraLibraryPopoverMarkup(toolbar({ library: library([], { in_sync: false, eligible: false, conflicts: ["LoRA “Beta” uses <different> files & names."] }) }));
  assert.match(conflicted, /LoRA “Beta” uses &lt;different&gt; files &amp; names\./);
  assert.doesNotMatch(conflicted, /data-lora-library-sync/);
  assert.match(loraLibraryPopoverMarkup(toolbar({})), /No published workflow has a LoRA library available here\./);
  assert.match(loraLibraryPopoverMarkup(toolbar({ loading: true, library: null })), /Checking the LoRA library…/);
  assert.equal(loraLibraryPopoverMarkup(null), "");
});

test("a library failure stays visible beside the gallery without reopening anything", () => {
  assert.equal(loraLibraryNoticeMarkup(toolbar({ library: library([]), error: "Boom." })), '<p class="lm-header-notice form-error" role="alert">Boom.</p>');
  assert.match(loraLibraryNoticeMarkup(toolbar({ library: library([], { conflicts: ["one", "two"] }) })), /2 publishing conflicts — republish the affected workflows/);
  assert.match(loraLibraryNoticeMarkup(toolbar({ library: library([], { conflicts: ["one"] }) })), /1 publishing conflict —/);
  assert.equal(loraLibraryNoticeMarkup(toolbar({ library: library([]) })), "");
  assert.equal(loraLibraryNoticeMarkup(null), "");
});

test("editing preloads public title and trigger without exposing private filename", () => {
  const markup = adminLoraMarkup({
    library: library([{ id: "portrait", label: '<Portrait & "style">', trigger_word: "portrait subject", filename: "private/portrait.safetensors" }]),
    editingLoraId: "portrait",
  });
  assert.match(markup, /data-admin-lora-edit-form="portrait"/);
  assert.match(markup, /value="&lt;Portrait &amp; &quot;style&quot;&gt;"/);
  assert.match(markup, new RegExp(`<textarea name="trigger_word" rows="3" maxlength="${MAX_TRIGGER_WORD}"[^>]*>portrait subject</textarea>`));
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
  assert.match(validateLoraEdit({ item, displayName: "Portrait", triggerWord: "x".repeat(MAX_TRIGGER_WORD + 1) }), new RegExp(`${MAX_TRIGGER_WORD} characters`));
});

test("a whole character description works as the trigger phrase", () => {
  assert.equal(triggerPhrase.length, 130);
  const item = { id: "portrait", label: "Portrait", trigger_word: "person" };
  const file = { name: "test.safetensors", size: 24 };
  assert.equal(validateLoraInstall({ file, displayName: "Style", triggerWord: triggerPhrase }), null);
  assert.equal(validateLoraEdit({ item, displayName: "Portrait", triggerWord: triggerPhrase }), null);
  assert.equal(validateLoraEdit({ item, displayName: "Portrait", triggerWord: "x".repeat(MAX_TRIGGER_WORD) }), null);
  assert.match(validateLoraInstall({ file, displayName: "Style", triggerWord: "x".repeat(MAX_TRIGGER_WORD + 1) }), new RegExp(`${MAX_TRIGGER_WORD} characters`));
  const markup = adminLoraMarkup({ library: library([{ id: "portrait", label: "Portrait", trigger_word: triggerPhrase }]), editingLoraId: "portrait" });
  assert.ok(markup.includes(`>${triggerPhrase}</textarea>`));
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
    if (isLibrary(path)) return view(items);
    if (path === "/api/admin/lora-operations") return { id: "operation-1", status: "running" };
    if (path.endsWith("/operation-1")) return { id: "operation-1", status: "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let refreshes = 0;
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-1", confirm: () => true, pause: async () => {}, refreshSources: async () => { refreshes += 1; items = []; } });
  controller.mount(host, [{ source_key: "source-1", display_name: "Image workflow" }], "source-1");
  await waitFor(() => controller.state.library);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-remove]" ? { dataset: { adminLoraRemove: "portrait" } } : null } });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "remove", library: "krea2", expected_library: expectedLibrary, idempotency_key: "request-1", lora_id: "portrait" });
  assert.equal(request.options.method, "POST");
  assert.equal(controller.state.library.items.length, 0);
  assert.doesNotMatch(host.innerHTML, /Remove Portrait/);
});

test("install sends title, trigger, filename, then uploads one raw file before refresh", async () => {
  const calls = [];
  const file = { name: "portrait.safetensors", size: 96 };
  let items = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (isLibrary(path)) return view(items);
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
  await waitFor(() => controller.state.library);
  listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "install", library: "krea2", expected_library: expectedLibrary, idempotency_key: "request-2", filename: "portrait.safetensors", display_name: "Portrait", trigger_word: "person" });
  assert.equal(upload.path, "/api/admin/lora-operations/operation-2/file");
  assert.equal(upload.body, file);
  assert.equal(upload.options.csrfToken, "token");
  assert.equal(controller.state.library.items.length, 1);
});

test("edit sends a trimmed title and an empty trigger without upload or confirmation", async () => {
  const calls = [];
  let items = [{ id: "portrait", label: "Portrait" }];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (isLibrary(path)) return view(items);
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
  await waitFor(() => controller.state.library);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-edit]" ? { dataset: { adminLoraEdit: "portrait" } } : null } });
  assert.match(host.innerHTML, /data-admin-lora-edit-form="portrait"/);
  listeners.submit({ target: { dataset: { adminLoraEditForm: "portrait" } }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "edit", library: "krea2", expected_library: expectedLibrary, idempotency_key: "request-edit", lora_id: "portrait", display_name: "New Portrait", trigger_word: "" });
  assert.equal(controller.state.status, "LoRA details updated in every library workflow.");
  assert.equal(controller.state.editingLoraId, null);
  assert.equal(controller.state.library.items[0].label, "New Portrait");
});

test("edit no-op stays local, and Cancel discards the draft", async () => {
  const calls = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (isLibrary(path)) return view([{ id: "portrait", label: "Portrait" }]);
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", readForm: () => ({ get: (name) => ({ display_name: " Portrait ", trigger_word: " " })[name] }), refreshSources: async () => {} });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);
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
    if (isLibrary(path)) {
      catalogCalls += 1;
      return view([{ id: "portrait", label: catalogCalls === 1 ? "Portrait" : "Other title" }]);
    }
    if (path === "/api/admin/lora-operations") {
      const error = new Error("The publication changed.");
      error.code = "library_changed";
      error.status = 409;
      throw error;
    }
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", readForm: () => ({ get: (name) => ({ display_name: "Changed", trigger_word: "" })[name] }), refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-edit]" ? { dataset: { adminLoraEdit: "portrait" } } : null } });
  listeners.submit({ target: { dataset: { adminLoraEditForm: "portrait" } }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && catalogCalls === 2 && !controller.state.busy);
  assert.equal(controller.state.editingLoraId, null);
  assert.equal(controller.state.library.items[0].label, "Other title");
  assert.match(controller.state.error, /publication changed/);
});

test("interrupted upload checks operation state and retries the same operation", async () => {
  const file = { name: "portrait.safetensors", size: 96 };
  const observed = ["running", "awaiting_upload", "succeeded"];
  let uploads = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (isLibrary(path)) return view([]);
    if (path === "/api/admin/lora-operations") return { id: "operation-3", status: "awaiting_upload" };
    if (path.endsWith("/operation-3")) return { id: "operation-3", status: observed.shift() };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-3", pause: async () => {}, readForm: () => ({ get: (name) => ({ file, display_name: "Portrait", trigger_word: "person" })[name] }), uploadFile: async (path, body) => { assert.equal(path, "/api/admin/lora-operations/operation-3/file"); assert.equal(body, file); uploads += 1; if (uploads === 1) throw new Error("Connection lost"); }, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);
  listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  assert.equal(uploads, 2);
  assert.equal(controller.state.error, "");
});

test("removal blocker leaves the published item and reports the reason", async () => {
  const api = async (path) => {
    if (isLibrary(path)) return view([{ id: "portrait", label: "Portrait" }]);
    if (path === "/api/admin/lora-operations") return { id: "operation-4", status: "running" };
    if (path.endsWith("/operation-4")) return { id: "operation-4", status: "failed", message: "Removal was blocked.", blockers: ["An active generation needs this LoRA."] };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let refreshes = 0;
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-4", confirm: () => true, pause: async () => {}, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-remove]" ? { dataset: { adminLoraRemove: "portrait" } } : null } });
  await waitFor(() => !controller.state.busy && controller.state.error);
  assert.equal(refreshes, 0);
  assert.equal(controller.state.library.items.length, 1);
  assert.match(controller.state.error, /active generation/);
});

test("an incomplete upload can be resumed with the selected file", async () => {
  const file = { name: "portrait.safetensors", size: 96 };
  let uploads = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (isLibrary(path)) return view([]);
    if (path === "/api/admin/lora-operations") return { id: "operation-5", status: "awaiting_upload" };
    if (path.endsWith("/operation-5")) return { id: "operation-5", status: uploads < 3 ? "awaiting_upload" : "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-5", pause: async () => {}, readForm: () => ({ get: (name) => ({ file, display_name: "Portrait", trigger_word: "person" })[name] }), uploadFile: async () => { uploads += 1; if (uploads <= 2) throw new Error("Connection lost"); }, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);
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
    if (isLibrary(path)) return view([]);
    if (path.endsWith("/operation-6/cancel")) return { id: "operation-6", status: "failed", message: "Upload cancelled." };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", refreshSources: async () => {} });
  controller.state.operations.set("krea2", { id: "operation-6", status: "awaiting_upload" });
  controller.mount(host, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);
  assert.match(host.innerHTML, /Cancel pending upload/);
  assert.doesNotMatch(host.innerHTML, /Retry upload/);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-cancel-upload]" ? {} : null } });
  await waitFor(() => controller.state.operations.get("krea2")?.status === "failed" && !controller.state.busy);
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
    if (isLibrary(path)) return view([]);
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
  await waitFor(() => controller.state.library);
  first.listeners.submit({ target: { id: "admin-lora-install-form" }, preventDefault() {} });
  await waitFor(() => uploads === 2 && !controller.state.busy);
  assert.match(values.get("cif-admin-lora-operations:admin-1"), new RegExp(operationId));

  const second = makeHost();
  const restored = createAdminLoraController({ api, getCsrfToken: () => "token", storage: () => storage, actorId: () => "admin-1", refreshSources: async () => {} });
  restored.mount(second.host, [{ source_key: source }], source);
  await waitFor(() => restored.state.library && restored.state.operations.get("krea2")?.status === "awaiting_upload");
  assert.match(second.host.innerHTML, /Cancel pending upload/);
  assert.doesNotMatch(second.host.innerHTML, /Retry upload/);
  second.listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-cancel-upload]" ? {} : null } });
  await waitFor(() => restored.state.operations.get("krea2")?.status === "failed" && !restored.state.busy);
  assert.equal(values.has("cif-admin-lora-operations:admin-1"), false);
});

test("a running edit resumes after reload and reports edit success", async () => {
  const source = "b".repeat(64);
  const operationId = "22222222-2222-4222-8222-222222222222";
  const values = new Map([["cif-admin-lora-operations:admin-1", JSON.stringify({ krea2: { id: operationId, kind: "edit" } })]]);
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  let statusCalls = 0;
  let refreshes = 0;
  const api = async (path) => {
    if (isLibrary(path)) return view([{ id: "portrait", label: "New title" }]);
    if (path.endsWith(`/${operationId}`)) return { id: operationId, status: ++statusCalls === 1 ? "running" : "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const host = { isConnected: true, innerHTML: "", addEventListener() {} };
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", storage: () => storage, actorId: () => "admin-1", pause: async () => {}, refreshSources: async () => { refreshes += 1; } });
  controller.mount(host, [{ source_key: source }], source);
  await waitFor(() => refreshes === 1 && controller.state.operations.get("krea2")?.status === "succeeded");
  assert.equal(controller.state.status, "LoRA details updated in every library workflow.");
  assert.equal(values.has("cif-admin-lora-operations:admin-1"), false);
});

test("sync adds missing library LoRAs to drifted workflows in one operation", async () => {
  const calls = [];
  let synced = false;
  const api = async (path, options) => {
    calls.push({ path, options });
    if (isLibrary(path)) return synced ? view([{ id: "portrait", label: "Portrait" }]) : view([{ id: "portrait", label: "Portrait" }], { in_sync: false, eligible: false, can_sync: true, reason: "Sync the library before other changes." });
    if (path === "/api/admin/lora-operations") return { id: "operation-sync", status: "running" };
    if (path.endsWith("/operation-sync")) return { id: "operation-sync", status: "succeeded" };
    throw new Error(`Unexpected path ${path}`);
  };
  const listeners = {};
  const host = { isConnected: true, innerHTML: "", addEventListener(type, listener) { listeners[type] = listener; } };
  let refreshes = 0;
  const controller = createAdminLoraController({ api, getCsrfToken: () => "token", createId: () => "request-sync", pause: async () => {}, refreshSources: async (key) => { assert.equal(key, null); refreshes += 1; synced = true; } });
  controller.mount(host);
  await waitFor(() => controller.state.library);
  assert.doesNotMatch(host.innerHTML, /admin-lora-install-form/);
  listeners.click({ target: { closest: (selector) => selector === "[data-admin-lora-sync]" ? {} : null } });
  await waitFor(() => refreshes === 1 && !controller.state.busy);
  const request = calls.find((call) => call.path === "/api/admin/lora-operations");
  assert.deepEqual(JSON.parse(request.options.body), { kind: "sync", library: "krea2", expected_library: expectedLibrary, idempotency_key: "request-sync" });
  assert.equal(controller.state.status, "Library workflows now share the same LoRAs.");
  assert.equal(controller.state.library.in_sync, true);
  assert.match(host.innerHTML, /admin-lora-install-form/);
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

test("a disabled LoRA is deleted from the Demo Reel only after the administrator confirms it", async () => {
  const calls = [];
  let items = [{ id: "style", label: "Style", lora_identity: "identity-1", demo_reel_enabled: true }];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (isLibrary(path)) return view(items);
    if (path === "/api/admin/lora-library/krea2/demo-reel/identity-1") return { demo_reel_enabled: false, images_removed: 4 };
    throw new Error(`Unexpected path ${path}`);
  };
  const notices = [];
  let asked = null;
  const controller = createAdminLoraController({
    api,
    getCsrfToken: () => "token",
    notify: (message) => notices.push(message),
    confirm: (message) => { asked = message; return true; },
    refreshSources: async () => {},
  });
  controller.mount({ isConnected: true, innerHTML: "", addEventListener() {} }, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);

  items = [{ ...items[0], demo_reel_enabled: false }];
  assert.equal(await controller.setDemoReel("style", false), true);
  const request = calls.find((call) => call.path.endsWith("/demo-reel/identity-1"));
  assert.equal(request.options.method, "PUT");
  assert.deepEqual(JSON.parse(request.options.body), { enabled: false });
  assert.match(asked, /Disable the Demo Reel for “Style”/);
  assert.match(notices.at(-1), /images and its library picture are deleted/);
  assert.equal(controller.state.library.items[0].demo_reel_enabled, false);
  assert.equal(controller.state.busy, false);
  assert.equal(controller.state.error, "");
});

test("backing out of the confirmation asks the server nothing, and re-enabling does not ask at all", async () => {
  const calls = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    if (isLibrary(path)) return view([{ id: "style", label: "Style", lora_identity: "identity-1", demo_reel_enabled: false }]);
    if (path.endsWith("/demo-reel/identity-1")) return { demo_reel_enabled: true, images_removed: 0 };
    throw new Error(`Unexpected path ${path}`);
  };
  const controller = createAdminLoraController({
    api, getCsrfToken: () => "token", confirm: () => false, refreshSources: async () => {},
  });
  controller.mount({ isConnected: true, innerHTML: "", addEventListener() {} }, [{ source_key: "source-1" }], "source-1");
  await waitFor(() => controller.state.library);

  assert.equal(await controller.setDemoReel("style", false), false);
  assert.equal(calls.filter((call) => call.path.endsWith("/demo-reel/identity-1")).length, 0);

  assert.equal(await controller.setDemoReel("style", true), true);
  const request = calls.find((call) => call.path.endsWith("/demo-reel/identity-1"));
  assert.deepEqual(JSON.parse(request.options.body), { enabled: true });
  assert.match(controller.state.status, /will be generated again/);
});
