const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const terminalStatuses = new Set(["succeeded", "failed", "blocked", "repair_required"]);

export function newLoraOperationKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function validateLoraInstall({ file, displayName, triggerWord }) {
  if (!file) return "Choose one .safetensors file.";
  if (!/^[^/\\]+\.safetensors$/i.test(file.name || "") || file.name.length > 255 || !Number.isFinite(file.size) || file.size <= 8) {
    return "Choose a nonempty .safetensors file.";
  }
  if (!String(displayName || "").trim()) return "Enter the display title.";
  if (String(displayName).trim().length > 120) return "Keep the display title to 120 characters or fewer.";
  if (!String(triggerWord || "").trim()) return "Enter the trigger word.";
  if (String(triggerWord).trim().length > 120) return "Keep the trigger word to 120 characters or fewer.";
  return null;
}

export function validateLoraEdit({ item, displayName, triggerWord }) {
  const title = String(displayName ?? "").trim();
  const trigger = String(triggerWord ?? "").trim();
  if (!title) return "Enter the display title.";
  if (title.length > 120) return "Keep the display title to 120 characters or fewer.";
  if (trigger.length > 120) return "Keep the trigger word to 120 characters or fewer.";
  if (title === String(item?.label ?? "").trim() && trigger === String(item?.trigger_word ?? "").trim()) return "Change the title or trigger word before saving.";
  return null;
}

export function reconcileLoraStrengthMemory(contract, memory) {
  const controls = new Map((contract?.inputs || contract?.controls || []).filter((input) => input.type === "lora_stack").map((input) => [input.id, new Set((input.items || []).map((item) => item.id))]));
  return Object.fromEntries(Object.entries(memory || {}).filter(([id]) => controls.has(id)).map(([id, strengths]) => [id, Object.fromEntries(Object.entries(strengths || {}).filter(([itemId, strength]) => controls.get(id).has(itemId) && typeof strength === "number" && Number.isFinite(strength) && strength > 0))]));
}

const libraryKeyPattern = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
const pendingStatuses = ["awaiting_upload", "running", "repair_required"];

function memberMarkup(member) {
  const state = member.in_sync ? "In sync" : `Needs sync${member.missing_count ? ` · ${member.missing_count} missing` : ""}`;
  return `<li class="admin-lora-member${member.in_sync ? "" : " is-out-of-sync"}"><span>${escape(member.display_name || member.source_key)}</span><span class="admin-lora-member-state">${escape(state)}</span></li>`;
}

export function adminLoraMarkup({ libraries = [], selectedLibraryKey = null, library = null, loading = false, busy = false, uploadPercent = null, operation = null, canRetryUpload = false, editingLoraId = null, editDraft = null, status = "", error = "" } = {}) {
  const selected = library || libraries.find((item) => item.key === selectedLibraryKey) || null;
  const pending = pendingStatuses.includes(operation?.status);
  const available = Boolean(selected?.eligible && !loading && !pending);
  const syncable = Boolean(selected?.can_sync && !loading && !pending);
  const items = Array.isArray(selected?.items) ? selected.items : [];
  const members = Array.isArray(selected?.members) ? selected.members : [];
  const options = libraries.length > 1 ? `<label class="field admin-lora-source"><span>Model family</span><select data-admin-lora-library ${busy ? "disabled" : ""}>${libraries.map((item) => `<option value="${escape(item.key)}" ${item.key === selected?.key ? "selected" : ""}>${escape(item.label || item.key)}</option>`).join("")}</select></label>` : "";
  const message = loading ? "Checking the LoRA library…" : !selected ? "No published workflow has a LoRA list yet."
    : operation?.status === "repair_required" ? "A LoRA change needs repair before another change."
      : operation?.status === "awaiting_upload" ? "An installation is waiting for its file upload."
        : operation?.status === "running" ? "A LoRA change is still running."
          : !selected.eligible ? selected.reason || "LoRA administration is unavailable."
            : `${items.length} LoRA${items.length === 1 ? "" : "s"} shared by ${members.length} workflow${members.length === 1 ? "" : "s"}.`;
  const rows = items.map((item) => {
    if (editingLoraId === item.id) {
      const title = editDraft?.displayName ?? item.label ?? "";
      const trigger = editDraft?.triggerWord ?? item.trigger_word ?? "";
      return `<li class="admin-lora-row is-editing"><form class="admin-lora-edit" data-admin-lora-edit-form="${escape(item.id)}" aria-label="Edit ${escape(item.label || item.id)} LoRA"><div class="admin-lora-edit-fields"><label class="field"><span>Display title</span><input name="display_name" maxlength="120" required value="${escape(title)}" ${busy || !available ? "disabled" : ""} /></label><label class="field"><span>Trigger word (optional)</span><input name="trigger_word" maxlength="120" value="${escape(trigger)}" ${busy || !available ? "disabled" : ""} /></label></div><div class="admin-lora-actions"><button type="submit" class="button primary" ${busy || !available ? "disabled" : ""}>Save</button><button type="button" class="button low" data-admin-lora-edit-cancel="${escape(item.id)}" ${busy ? "disabled" : ""}>Cancel</button></div></form></li>`;
    }
    return `<li class="admin-lora-row"><div class="admin-lora-summary"><strong>${escape(item.label || item.id)}</strong><span>${escape(item.trigger_word || "No published trigger word")}</span></div><div class="admin-lora-actions"><button type="button" class="button low" data-admin-lora-edit="${escape(item.id)}" ${busy || !available ? "disabled" : ""} aria-label="Edit ${escape(item.label || item.id)}">Edit</button><button type="button" class="button destructive low" data-admin-lora-remove="${escape(item.id)}" ${busy || !available ? "disabled" : ""} aria-label="Remove ${escape(item.label || item.id)}">Remove</button></div></li>`;
  }).join("");
  const conflicts = Array.isArray(selected?.conflicts) && selected.conflicts.length
    ? `<div class="form-error admin-lora-conflicts" role="alert"><p>These workflows disagree about some LoRAs. Republish them so each LoRA uses one file and one ID:</p><ul>${selected.conflicts.map((item) => `<li>${escape(item)}</li>`).join("")}</ul></div>` : "";
  const sync = selected && !selected.in_sync && !selected.conflicts?.length
    ? `<div class="admin-lora-sync"><p>Some workflows are missing library LoRAs. Sync adds them; it never removes a LoRA.</p><button type="button" class="button secondary" data-admin-lora-sync ${busy || !syncable ? "disabled" : ""}>Sync library</button></div>` : "";
  const progress = busy && uploadPercent !== null ? `<progress max="100" value="${Math.max(0, Math.min(100, Number(uploadPercent) || 0))}" aria-label="LoRA upload progress"></progress>` : "";
  return `<div class="section-heading"><h3>LoRA library</h3></div>
    <p class="muted">LoRAs are shared by every workflow below. Installing, editing, or removing one changes all of them.</p>
    ${options}
    ${members.length ? `<ul class="admin-lora-members" aria-label="Workflows using this library">${members.map(memberMarkup).join("")}</ul>` : ""}
    ${conflicts}${sync}
    <p class="admin-lora-message" role="status">${escape(message)}</p>
    ${selected?.eligible && !loading ? `<ul class="admin-lora-list" aria-label="Library LoRAs">${rows || '<li class="muted">No LoRAs in the library yet.</li>'}</ul>
    <form id="admin-lora-install-form" class="admin-lora-install"><h4>Install a LoRA</h4><div class="admin-lora-fields"><label class="field"><span>Weight file</span><input type="file" name="file" accept=".safetensors" required ${busy || !available ? "disabled" : ""} /></label><label class="field"><span>Display title</span><input name="display_name" maxlength="120" required ${busy || !available ? "disabled" : ""} /></label><label class="field"><span>Trigger word</span><input name="trigger_word" maxlength="120" required ${busy || !available ? "disabled" : ""} /></label></div><button class="button primary" type="submit" ${busy || !available ? "disabled" : ""}>Install LoRA</button></form>` : ""}
    ${canRetryUpload && operation?.status === "awaiting_upload" && !busy ? '<button type="button" class="button secondary" data-admin-lora-retry-upload>Retry upload</button>' : ""}
    ${operation?.status === "awaiting_upload" && !busy ? '<button type="button" class="button low" data-admin-lora-cancel-upload>Cancel pending upload</button>' : ""}
    ${progress}<p class="admin-lora-status" role="status" aria-live="polite">${escape(status)}</p>${error ? `<p class="form-error" role="alert">${escape(error)}</p>` : ""}`;
}

export function uploadLoraFile(path, file, { csrfToken, onProgress, XMLHttpRequestClass = XMLHttpRequest } = {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequestClass();
    request.open("PUT", path);
    request.withCredentials = true;
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-CIF-Generation-Protocol", "3");
    if (csrfToken) request.setRequestHeader("X-CSRF-Token", csrfToken);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(Math.round(event.loaded * 100 / event.total));
    };
    request.onerror = () => reject(new Error("The upload connection failed. Check the operation status before retrying."));
    request.onload = () => {
      let payload = null;
      try { payload = JSON.parse(request.responseText || "null"); } catch { /* The server may return an empty error body. */ }
      if (request.status >= 200 && request.status < 300) resolve(payload);
      else {
        const failure = new Error(payload?.error?.message || `Upload failed with HTTP ${request.status}.`);
        failure.code = payload?.error?.code;
        failure.details = payload?.error?.details || {};
        reject(failure);
      }
    };
    request.send(file);
  });
}

const successMessages = {
  install: "LoRA installed in every library workflow.",
  edit: "LoRA details updated in every library workflow.",
  remove: "LoRA removed from every library workflow and ComfyUI.",
  sync: "Library workflows now share the same LoRAs.",
};

export function createAdminLoraController({ api, getCsrfToken, refreshSources, notify, confirm = (message) => window.confirm(message), uploadFile = uploadLoraFile, createId = newLoraOperationKey, readForm = (form) => new FormData(form), storage = () => null, actorId = () => null, pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) {
  const state = { host: null, libraries: [], selectedLibraryKey: null, library: null, loading: false, busy: false, uploadPercent: null, status: "", error: "", editingLoraId: null, editDraft: null, operations: new Map(), operationKinds: new Map(), pendingUploads: new Map(), recovering: new Set(), restoredKey: null, requestToken: 0 };
  const currentOperation = () => state.operations.get(state.selectedLibraryKey);
  const storageKey = () => actorId() ? `cif-admin-lora-operations:${actorId()}` : null;
  const persistOperations = () => {
    const key = storageKey();
    if (!key) return;
    try {
      const target = storage();
      if (!target) return;
      const entries = [...state.operations.entries()]
        .filter(([, operation]) => pendingStatuses.includes(operation?.status))
        .map(([library, operation]) => [library, { id: operation.id, kind: state.operationKinds.get(library) || "install" }]);
      if (entries.length) target.setItem(key, JSON.stringify(Object.fromEntries(entries)));
      else target.removeItem(key);
    } catch { /* Storage failure does not change the server operation. */ }
  };
  const setOperation = (operation, library, kind = null) => {
    if (!operation || !library) return;
    state.operations.set(library, operation);
    if (kind) state.operationKinds.set(library, kind);
    persistOperations();
  };
  const restoreOperations = () => {
    const key = storageKey();
    if (state.restoredKey === key) return;
    state.restoredKey = key;
    state.operations.clear();
    state.operationKinds.clear();
    state.pendingUploads.clear();
    if (!key) return;
    try {
      const saved = JSON.parse(storage()?.getItem(key) || "{}");
      for (const [library, entry] of Object.entries(saved || {})) {
        if (!libraryKeyPattern.test(library) || !/^[a-f0-9-]{36}$/.test(entry?.id) || !["install", "remove", "edit", "sync"].includes(entry?.kind)) continue;
        state.operations.set(library, { id: entry.id, status: "running" });
        state.operationKinds.set(library, entry.kind);
      }
    } catch { /* A missing or damaged browser journal does not affect ComfyUI state. */ }
  };
  const render = () => {
    if (state.host?.isConnected) state.host.innerHTML = adminLoraMarkup({ ...state, operation: currentOperation(), canRetryUpload: state.pendingUploads.has(state.selectedLibraryKey) });
  };
  const selectLibrary = (preferred = state.selectedLibraryKey) => {
    state.library = state.libraries.find((item) => item.key === preferred) || state.libraries[0] || null;
    state.selectedLibraryKey = state.library?.key || null;
  };
  async function loadLibrary() {
    const token = ++state.requestToken;
    state.editingLoraId = null;
    state.editDraft = null;
    state.loading = true;
    state.error = "";
    render();
    try {
      const view = await api("/api/admin/lora-library");
      if (token !== state.requestToken) return;
      state.libraries = Array.isArray(view?.libraries) ? view.libraries : [];
      selectLibrary();
    } catch (error) {
      if (token !== state.requestToken) return;
      state.libraries = [];
      state.library = null;
      state.error = error.message || "Could not load the LoRA library.";
    } finally {
      if (token === state.requestToken) { state.loading = false; render(); }
    }
  }
  async function pollOperation(id, file, library) {
    let resumedUploads = 0;
    for (;;) {
      const operation = await api(`/api/admin/lora-operations/${encodeURIComponent(id)}`);
      setOperation(operation, library);
      state.status = operation.message || (operation.status === "running" ? "Updating every library workflow in ComfyUI…" : "");
      render();
      if (terminalStatuses.has(operation.status)) return operation;
      if (operation.status === "awaiting_upload") {
        if (!file) return operation;
        if (++resumedUploads > 2) throw new Error("The upload did not complete. Check this operation before starting another.");
        state.status = "Resuming the interrupted upload…";
        state.uploadPercent = 0;
        render();
        const observed = await sendFileWithRecovery(id, file, library);
        if (terminalStatuses.has(observed?.status)) return observed;
      }
      await pause(1000);
    }
  }
  async function sendFileWithRecovery(operationId, file, library) {
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        await uploadFile(`/api/admin/lora-operations/${encodeURIComponent(operationId)}/file`, file, { csrfToken: getCsrfToken(), onProgress: (percent) => { state.uploadPercent = percent; render(); } });
        state.uploadPercent = null;
        return null;
      } catch (uploadError) {
        const current = await api(`/api/admin/lora-operations/${encodeURIComponent(operationId)}`);
        setOperation(current, library);
        if (current.status !== "awaiting_upload") return current;
        if (attempt === 2) throw uploadError;
        state.status = "The upload was interrupted. Retrying the same operation…";
        state.uploadPercent = 0;
        render();
      }
    }
  }
  async function finish(operation, kind, library) {
    state.pendingUploads.delete(library);
    if (operation.status !== "succeeded") {
      const blocker = Array.isArray(operation.blockers) && operation.blockers.length ? ` ${operation.blockers.map((item) => typeof item === "string" ? item : item.message || item.code || "Dependency blocked removal").join(" ")}` : "";
      state.error = `${operation.message || (operation.status === "repair_required" ? "LoRA operation requires repair." : "LoRA operation failed.")}${blocker}`;
      state.status = "";
      render();
      return;
    }
    state.status = successMessages[kind] || successMessages.install;
    state.error = "";
    state.editingLoraId = null;
    state.editDraft = null;
    try {
      await refreshSources(null);
      await loadLibrary();
      notify?.(state.status, "success");
    } catch (error) {
      state.error = `The LoRA operation succeeded, but refreshing workflows failed: ${error.message}`;
    }
    render();
  }
  async function runOperation(kind, data, file = null) {
    const library = state.library;
    const allowed = kind === "sync" ? library?.can_sync : library?.eligible;
    if (state.busy || pendingStatuses.includes(currentOperation()?.status) || !allowed || !library?.expected_library?.length) return;
    const key = library.key;
    const idempotencyKey = createId();
    state.busy = true;
    state.uploadPercent = null;
    state.error = "";
    state.status = { install: "Creating installation operation…", edit: "Updating LoRA details…", remove: "Checking dependencies before removal…", sync: "Syncing library workflows…" }[kind];
    render();
    try {
      const request = { method: "POST", body: JSON.stringify({ kind, library: key, expected_library: library.expected_library, idempotency_key: idempotencyKey, ...data }) };
      let operation;
      try { operation = await api("/api/admin/lora-operations", request); }
      catch (error) {
        if (error.status && ![429, 500, 502, 503, 504].includes(error.status)) throw error;
        operation = await api("/api/admin/lora-operations", request);
      }
      setOperation(operation, key, kind);
      if (file && operation.status === "awaiting_upload") state.pendingUploads.set(key, file);
      let observed = null;
      if (file && operation.status === "awaiting_upload") {
        state.status = "Uploading weight file to ComfyUI…";
        state.uploadPercent = 0;
        render();
        observed = await sendFileWithRecovery(operation.id, file, key);
      }
      const result = terminalStatuses.has(observed?.status || operation.status) ? observed || operation : await pollOperation(operation.id, file, key);
      await finish(result, kind, key);
    } catch (error) {
      if (["library_changed", "lora_not_found", "lora_library_out_of_sync", "lora_library_in_sync"].includes(error.code)) {
        try {
          await refreshSources(null);
          await loadLibrary();
        } catch { /* The original conflict remains the actionable error. */ }
      }
      const operation = state.operations.get(key);
      state.error = error.message || "LoRA operation failed.";
      state.status = operation?.status === "awaiting_upload"
        ? "The upload is incomplete. Review this operation before starting another."
        : operation?.id && pendingStatuses.includes(operation.status) ? "The operation may still be running. Reopen Administration to check its status." : "";
      render();
    } finally {
      state.busy = false;
      state.uploadPercent = null;
      render();
    }
  }
  const findItem = (id) => state.library?.items?.find((candidate) => candidate.id === id);
  async function install(form) {
    if (!state.library?.eligible || pendingStatuses.includes(currentOperation()?.status)) return;
    const fields = readForm(form);
    const file = fields.get("file");
    const displayName = String(fields.get("display_name") || "").trim();
    const triggerWord = String(fields.get("trigger_word") || "").trim();
    const error = validateLoraInstall({ file, displayName, triggerWord });
    if (error) { state.error = error; render(); return; }
    await runOperation("install", { filename: file.name, display_name: displayName, trigger_word: triggerWord }, file);
  }
  async function remove(id) {
    const item = findItem(id);
    if (!item || state.busy) return;
    const count = state.library?.members?.length || 0;
    if (!confirm(`Remove “${item.label || item.id}” from all ${count} library workflow${count === 1 ? "" : "s"} and delete its weight file? Historical generations stay visible, but exact regeneration with that weight will no longer be possible.`)) return;
    await runOperation("remove", { lora_id: id });
  }
  async function sync() {
    if (state.busy || !state.library?.can_sync) return;
    await runOperation("sync", {});
  }
  function beginEdit(id) {
    const item = findItem(id);
    if (!item || state.busy || !state.library?.eligible || pendingStatuses.includes(currentOperation()?.status)) return;
    state.editingLoraId = id;
    state.editDraft = { displayName: item.label || "", triggerWord: item.trigger_word || "" };
    state.error = "";
    render();
    state.host?.querySelector?.(".admin-lora-edit input[name=display_name]")?.focus();
  }
  function cancelEdit(id) {
    if (state.busy || state.editingLoraId !== id) return;
    state.editingLoraId = null;
    state.editDraft = null;
    state.error = "";
    render();
    state.host?.querySelector?.(`[data-admin-lora-edit="${id}"]`)?.focus();
  }
  async function edit(id, form) {
    const item = findItem(id);
    if (!item || state.editingLoraId !== id || state.busy) return;
    const fields = readForm(form);
    const displayName = String(fields.get("display_name") ?? "").trim();
    const triggerWord = String(fields.get("trigger_word") ?? "").trim();
    state.editDraft = { displayName, triggerWord };
    const error = validateLoraEdit({ item, displayName, triggerWord });
    if (error) { state.error = error; render(); return; }
    await runOperation("edit", { lora_id: id, display_name: displayName, trigger_word: triggerWord });
  }
  async function retryUpload() {
    const key = state.selectedLibraryKey;
    const operation = currentOperation();
    const file = state.pendingUploads.get(key);
    if (state.busy || operation?.status !== "awaiting_upload" || !file) return;
    state.busy = true;
    state.uploadPercent = 0;
    state.error = "";
    state.status = "Retrying the selected file upload…";
    render();
    try {
      const observed = await sendFileWithRecovery(operation.id, file, key);
      const result = terminalStatuses.has(observed?.status) ? observed : await pollOperation(operation.id, file, key);
      await finish(result, "install", key);
    } catch (error) {
      state.error = error.message || "The upload did not complete.";
      state.status = "The same operation still awaits its file.";
    } finally {
      state.busy = false;
      state.uploadPercent = null;
      render();
    }
  }
  async function cancelPendingUpload() {
    const key = state.selectedLibraryKey;
    const operation = currentOperation();
    if (state.busy || operation?.status !== "awaiting_upload") return;
    state.busy = true;
    state.status = "Cancelling the pending upload…";
    state.error = "";
    render();
    try {
      const result = await api(`/api/admin/lora-operations/${encodeURIComponent(operation.id)}/cancel`, { method: "POST" });
      setOperation(result, key);
      state.pendingUploads.delete(key);
      if (result.status === "repair_required") {
        state.error = result.message || "The upload could not be cleaned up. The library requires repair.";
        state.status = "";
      } else {
        state.status = "Pending upload cancelled.";
        await loadLibrary();
      }
    } catch (error) {
      state.error = error.message || "Could not cancel the pending upload.";
      state.status = "";
    } finally {
      state.busy = false;
      render();
    }
  }
  async function recoverOperations() {
    for (const [key, pending] of [...state.operations.entries()]) {
      if (!pending?.id || state.busy || state.recovering.has(key)) continue;
      state.recovering.add(key);
      try {
        let operation = await api(`/api/admin/lora-operations/${encodeURIComponent(pending.id)}`);
        setOperation(operation, key);
        if (operation.status === "running") operation = await pollOperation(operation.id, null, key);
        if (terminalStatuses.has(operation.status) && operation.status !== "repair_required") {
          await finish(operation, state.operationKinds.get(key) || "install", key);
        } else if (operation.status === "repair_required") {
          state.error = operation.message || "This LoRA operation requires repair.";
        } else if (operation.status === "awaiting_upload") {
          state.status = "The previous upload still awaits a file. Cancel it to start again.";
        }
      } catch (error) {
        state.error = `Could not check the previous LoRA operation: ${error.message}`;
      } finally {
        state.recovering.delete(key);
        render();
      }
    }
  }
  function mount(host) {
    state.host = host;
    restoreOperations();
    host.addEventListener("change", (event) => {
      if (!event.target.matches?.("[data-admin-lora-library]") || state.busy) return;
      selectLibrary(event.target.value);
      state.status = "";
      render();
    });
    host.addEventListener("input", (event) => {
      if (!state.editingLoraId || !state.editDraft || !event.target.closest?.("[data-admin-lora-edit-form]")) return;
      if (event.target.name === "display_name") state.editDraft.displayName = event.target.value;
      if (event.target.name === "trigger_word") state.editDraft.triggerWord = event.target.value;
    });
    host.addEventListener("submit", (event) => {
      const editId = event.target.dataset?.adminLoraEditForm;
      if (editId) { event.preventDefault(); void edit(editId, event.target); return; }
      if (event.target.id !== "admin-lora-install-form") return;
      event.preventDefault();
      void install(event.target);
    });
    host.addEventListener("click", (event) => {
      if (event.target.closest("[data-admin-lora-retry-upload]")) { void retryUpload(); return; }
      if (event.target.closest("[data-admin-lora-cancel-upload]")) { void cancelPendingUpload(); return; }
      if (event.target.closest("[data-admin-lora-sync]")) { void sync(); return; }
      const editButton = event.target.closest("[data-admin-lora-edit]");
      if (editButton) { beginEdit(editButton.dataset.adminLoraEdit); return; }
      const cancelButton = event.target.closest("[data-admin-lora-edit-cancel]");
      if (cancelButton) { cancelEdit(cancelButton.dataset.adminLoraEditCancel); return; }
      const button = event.target.closest("[data-admin-lora-remove]");
      if (button) void remove(button.dataset.adminLoraRemove);
    });
    if (state.busy) render();
    else void loadLibrary().then(recoverOperations);
  }
  return { mount, loadLibrary, state };
}
