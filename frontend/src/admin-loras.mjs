const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const terminalStatuses = new Set(["succeeded", "failed", "blocked", "repair_required"]);
const sourceKey = (source) => source?.source_key || source?.profile_id || null;

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

export function reconcileLoraStrengthMemory(contract, memory) {
  const controls = new Map((contract?.inputs || contract?.controls || []).filter((input) => input.type === "lora_stack").map((input) => [input.id, new Set((input.items || []).map((item) => item.id))]));
  return Object.fromEntries(Object.entries(memory || {}).filter(([id]) => controls.has(id)).map(([id, strengths]) => [id, Object.fromEntries(Object.entries(strengths || {}).filter(([itemId, strength]) => controls.get(id).has(itemId) && typeof strength === "number" && Number.isFinite(strength) && strength > 0))]));
}

export function adminLoraMarkup({ sources = [], selectedSourceKey = null, catalog = null, loading = false, busy = false, uploadPercent = null, operation = null, operationSourceKey = null, canRetryUpload = false, status = "", error = "" } = {}) {
  const selected = sources.find((source) => sourceKey(source) === selectedSourceKey);
  const catalogReady = Boolean(catalog?.eligible && catalog?.revision && !loading);
  const currentOperation = operationSourceKey === selectedSourceKey ? operation : null;
  const available = catalogReady && !["awaiting_upload", "running", "repair_required"].includes(currentOperation?.status);
  const items = Array.isArray(catalog?.items) ? catalog.items : [];
  const options = sources.map((source) => {
    const key = sourceKey(source);
    return `<option value="${escape(key)}" ${key === selectedSourceKey ? "selected" : ""}>${escape(source.display_name || source.name || key)}</option>`;
  }).join("");
  const message = loading ? "Checking this workflow…" : !selected ? "No published image source is available."
    : !catalog ? "Select a published workflow to inspect its LoRAs."
      : !catalog.eligible ? catalog.reason || "LoRA administration is unavailable for this workflow."
        : currentOperation?.status === "repair_required" ? "This workflow needs repair before another LoRA change."
          : currentOperation?.status === "awaiting_upload" ? "An installation is waiting for its file upload."
            : currentOperation?.status === "running" ? "A LoRA change is still running for this workflow."
        : `${items.length} LoRA${items.length === 1 ? "" : "s"} published for this workflow.`;
  const rows = items.map((item) => `<li class="admin-lora-row"><div><strong>${escape(item.label || item.id)}</strong><span>${escape(item.trigger_word || "No published trigger word")}</span></div><button type="button" class="button destructive low" data-admin-lora-remove="${escape(item.id)}" ${busy || !available ? "disabled" : ""} aria-label="Remove ${escape(item.label || item.id)}">Remove</button></li>`).join("");
  const progress = busy && uploadPercent !== null ? `<progress max="100" value="${Math.max(0, Math.min(100, Number(uploadPercent) || 0))}" aria-label="LoRA upload progress"></progress>` : "";
  return `<div class="section-heading"><h3>LoRA library</h3></div>
    <p class="muted">Install or remove LoRAs in the selected ComfyUI published workflow.</p>
    <label class="field admin-lora-source"><span>Published workflow</span><select data-admin-lora-source ${busy || !sources.length ? "disabled" : ""}>${options}</select></label>
    <p class="admin-lora-message" role="status">${escape(message)}</p>
    ${catalogReady ? `<ul class="admin-lora-list" aria-label="Published LoRAs">${rows || '<li class="muted">No LoRAs published yet.</li>'}</ul>
    <form id="admin-lora-install-form" class="admin-lora-install"><h4>Install a LoRA</h4><div class="admin-lora-fields"><label class="field"><span>Weight file</span><input type="file" name="file" accept=".safetensors" required ${busy || !available ? "disabled" : ""} /></label><label class="field"><span>Display title</span><input name="display_name" maxlength="120" required ${busy || !available ? "disabled" : ""} /></label><label class="field"><span>Trigger word</span><input name="trigger_word" maxlength="120" required ${busy || !available ? "disabled" : ""} /></label></div><button class="button primary" type="submit" ${busy || !available ? "disabled" : ""}>Install LoRA</button></form>` : ""}
    ${canRetryUpload && currentOperation?.status === "awaiting_upload" && !busy ? '<button type="button" class="button secondary" data-admin-lora-retry-upload>Retry upload</button>' : ""}
    ${currentOperation?.status === "awaiting_upload" && !busy ? '<button type="button" class="button low" data-admin-lora-cancel-upload>Cancel pending upload</button>' : ""}
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

export function createAdminLoraController({ api, getCsrfToken, refreshSources, notify, confirm = (message) => window.confirm(message), uploadFile = uploadLoraFile, createId = newLoraOperationKey, readForm = (form) => new FormData(form), storage = () => null, actorId = () => null, pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)) }) {
  const state = { host: null, sources: [], selectedSourceKey: null, catalog: null, loading: false, busy: false, uploadPercent: null, status: "", error: "", operation: null, operationSourceKey: null, operationsBySource: new Map(), operationKinds: new Map(), pendingUploads: new Map(), recoveringSources: new Set(), restoredKey: null, requestToken: 0 };
  const currentOperation = () => state.operationsBySource.get(state.selectedSourceKey);
  const storageKey = () => actorId() ? `cif-admin-lora-operations:${actorId()}` : null;
  const persistOperations = () => {
    const key = storageKey();
    if (!key) return;
    try {
      const target = storage();
      if (!target) return;
      const entries = [...state.operationsBySource.entries()]
        .filter(([, operation]) => ["awaiting_upload", "running", "repair_required"].includes(operation?.status))
        .map(([source, operation]) => [source, { id: operation.id, kind: state.operationKinds.get(source) || "install" }]);
      if (entries.length) target.setItem(key, JSON.stringify(Object.fromEntries(entries)));
      else target.removeItem(key);
    } catch { /* Storage failure does not change the server operation. */ }
  };
  const setOperation = (operation, source = state.operationSourceKey, kind = null) => {
    state.operation = operation;
    if (operation && source) {
      state.operationsBySource.set(source, operation);
      if (kind) state.operationKinds.set(source, kind);
      persistOperations();
    }
  };
  const restoreOperations = () => {
    const key = storageKey();
    if (state.restoredKey === key) return;
    state.restoredKey = key;
    state.operationsBySource.clear();
    state.operationKinds.clear();
    state.pendingUploads.clear();
    if (!key) return;
    try {
      const saved = JSON.parse(storage()?.getItem(key) || "{}");
      for (const [source, entry] of Object.entries(saved || {})) {
        if (!/^[a-f0-9]{64}$/.test(source) || !/^[a-f0-9-]{36}$/.test(entry?.id) || !["install", "remove"].includes(entry?.kind)) continue;
        state.operationsBySource.set(source, { id: entry.id, status: "running" });
        state.operationKinds.set(source, entry.kind);
      }
    } catch { /* A missing or damaged browser journal does not affect ComfyUI state. */ }
  };
  const render = () => {
    if (state.host?.isConnected) state.host.innerHTML = adminLoraMarkup({ ...state, operation: currentOperation(), operationSourceKey: state.selectedSourceKey, canRetryUpload: state.pendingUploads.has(state.selectedSourceKey) });
  };
  const setSources = (sources, preferredKey = null) => {
    state.sources = sources.filter((source) => sourceKey(source));
    if (!state.sources.some((source) => sourceKey(source) === state.selectedSourceKey)) {
      state.selectedSourceKey = state.sources.some((source) => sourceKey(source) === preferredKey) ? preferredKey : sourceKey(state.sources[0]);
    }
  };
  async function loadCatalog() {
    const token = ++state.requestToken;
    state.catalog = null;
    state.loading = Boolean(state.selectedSourceKey);
    state.error = "";
    render();
    if (!state.selectedSourceKey) return;
    try {
      const catalog = await api(`/api/admin/workflows/${encodeURIComponent(state.selectedSourceKey)}/loras`);
      if (token !== state.requestToken) return;
      state.catalog = catalog;
    } catch (error) {
      if (token !== state.requestToken) return;
      state.error = error.message || "Could not load the LoRA catalog.";
    } finally {
      if (token === state.requestToken) { state.loading = false; render(); }
    }
  }
  async function pollOperation(id, file = null, source = state.operationSourceKey) {
    let resumedUploads = 0;
    for (;;) {
      const operation = await api(`/api/admin/lora-operations/${encodeURIComponent(id)}`);
      setOperation(operation, source);
      state.status = operation.message || (operation.status === "running" ? "Updating ComfyUI publication and replicas…" : "");
      render();
      if (terminalStatuses.has(operation.status)) return operation;
      if (operation.status === "awaiting_upload") {
        if (!file) return operation;
        if (++resumedUploads > 2) throw new Error("The upload did not complete. Check this operation before starting another.");
        state.status = "Resuming the interrupted upload…";
        state.uploadPercent = 0;
        render();
        const observed = await sendFileWithRecovery(id, file, source);
        if (terminalStatuses.has(observed?.status)) return observed;
      }
      await pause(1000);
    }
  }
  async function sendFileWithRecovery(operationId, file, source = state.operationSourceKey) {
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        await uploadFile(`/api/admin/lora-operations/${encodeURIComponent(operationId)}/file`, file, { csrfToken: getCsrfToken(), onProgress: (percent) => { state.uploadPercent = percent; render(); } });
        state.uploadPercent = null;
        return null;
      } catch (uploadError) {
        const current = await api(`/api/admin/lora-operations/${encodeURIComponent(operationId)}`);
        setOperation(current, source);
        if (current.status !== "awaiting_upload") return current;
        if (attempt === 2) throw uploadError;
        state.status = "The upload was interrupted. Retrying the same operation…";
        state.uploadPercent = 0;
        render();
      }
    }
  }
  async function finish(operation, kind, source) {
    state.pendingUploads.delete(source);
    if (operation.status !== "succeeded") {
      const blocker = Array.isArray(operation.blockers) && operation.blockers.length ? ` ${operation.blockers.map((item) => typeof item === "string" ? item : item.message || item.code || "Dependency blocked removal").join(" ")}` : "";
      state.error = `${operation.message || (operation.status === "repair_required" ? "LoRA operation requires repair." : "LoRA operation failed.")}${blocker}`;
      state.status = "";
      render();
      return;
    }
    state.status = kind === "install" ? "LoRA installed and published." : "LoRA removed from ComfyUI and the published workflow.";
    state.error = "";
    try {
      const sources = await refreshSources(source);
      if (Array.isArray(sources)) setSources(sources);
      await loadCatalog();
      notify?.(state.status, "success");
    } catch (error) {
      state.error = `The LoRA operation succeeded, but source refresh failed: ${error.message}`;
    }
    render();
  }
  async function runOperation(kind, data, file = null) {
    if (state.busy || ["awaiting_upload", "running", "repair_required"].includes(currentOperation()?.status) || !state.catalog?.eligible || !state.catalog.revision || !state.selectedSourceKey) return;
    const source = state.selectedSourceKey;
    const idempotencyKey = createId();
    state.operation = null;
    state.operationSourceKey = source;
    state.busy = true;
    state.uploadPercent = null;
    state.error = "";
    state.status = kind === "install" ? "Creating installation operation…" : "Checking dependencies before removal…";
    render();
    try {
      const request = { method: "POST", body: JSON.stringify({ kind, source_key: source, expected_revision: state.catalog.revision, idempotency_key: idempotencyKey, ...data }) };
      let operation;
      try { operation = await api("/api/admin/lora-operations", request); }
      catch (error) {
        if (error.status && ![429, 500, 502, 503, 504].includes(error.status)) throw error;
        operation = await api("/api/admin/lora-operations", request);
      }
      setOperation(operation, source, kind);
      if (file && operation.status === "awaiting_upload") state.pendingUploads.set(source, file);
      let observed = null;
      if (file && operation.status === "awaiting_upload") {
        state.status = "Uploading weight file to ComfyUI…";
        state.uploadPercent = 0;
        render();
        observed = await sendFileWithRecovery(operation.id, file);
      }
      const result = terminalStatuses.has(observed?.status || operation.status) ? observed || operation : await pollOperation(operation.id, file);
      await finish(result, kind, source);
    } catch (error) {
      if (["source_republished", "lora_not_found"].includes(error.code)) {
        try {
          const sources = await refreshSources(source);
          if (Array.isArray(sources)) setSources(sources);
          await loadCatalog();
        } catch { /* The original conflict remains the actionable error. */ }
      }
      state.error = error.message || "LoRA operation failed.";
      state.status = state.operation?.status === "awaiting_upload"
        ? "The upload is incomplete. Review this operation before starting another."
        : state.operation?.id ? "The operation may still be running. Reopen Administration to check its status." : "";
      render();
    } finally {
      state.busy = false;
      state.uploadPercent = null;
      render();
    }
  }
  async function install(form) {
    if (!state.catalog?.eligible || ["awaiting_upload", "running", "repair_required"].includes(currentOperation()?.status)) return;
    const fields = readForm(form);
    const file = fields.get("file");
    const displayName = String(fields.get("display_name") || "").trim();
    const triggerWord = String(fields.get("trigger_word") || "").trim();
    const error = validateLoraInstall({ file, displayName, triggerWord });
    if (error) { state.error = error; render(); return; }
    await runOperation("install", { filename: file.name, display_name: displayName, trigger_word: triggerWord }, file);
  }
  async function remove(id) {
    const item = state.catalog?.items?.find((candidate) => candidate.id === id);
    if (!item || state.busy) return;
    if (!confirm(`Remove “${item.label || item.id}” from this workflow and delete its weight file? Historical generations stay visible, but exact regeneration with that weight will no longer be possible.`)) return;
    await runOperation("remove", { lora_id: id });
  }
  async function retryUpload() {
    const source = state.selectedSourceKey;
    const operation = currentOperation();
    const file = state.pendingUploads.get(source);
    if (state.busy || operation?.status !== "awaiting_upload" || !file) return;
    state.busy = true;
    state.operationSourceKey = source;
    state.uploadPercent = 0;
    state.error = "";
    state.status = "Retrying the selected file upload…";
    render();
    try {
      const observed = await sendFileWithRecovery(operation.id, file);
      const result = terminalStatuses.has(observed?.status) ? observed : await pollOperation(operation.id, file);
      await finish(result, "install", source);
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
    const source = state.selectedSourceKey;
    const operation = currentOperation();
    if (state.busy || operation?.status !== "awaiting_upload") return;
    state.busy = true;
    state.operationSourceKey = source;
    state.status = "Cancelling the pending upload…";
    state.error = "";
    render();
    try {
      const result = await api(`/api/admin/lora-operations/${encodeURIComponent(operation.id)}/cancel`, { method: "POST" });
      setOperation(result);
      state.pendingUploads.delete(source);
      if (result.status === "repair_required") {
        state.error = result.message || "The upload could not be cleaned up. This source requires repair.";
        state.status = "";
      } else {
        state.status = "Pending upload cancelled.";
        await loadCatalog();
      }
    } catch (error) {
      state.error = error.message || "Could not cancel the pending upload.";
      state.status = "";
    } finally {
      state.busy = false;
      render();
    }
  }
  async function recoverSource(source) {
    const pending = state.operationsBySource.get(source);
    if (!pending?.id || state.busy || state.recoveringSources.has(source)) return;
    state.recoveringSources.add(source);
    try {
      let operation = await api(`/api/admin/lora-operations/${encodeURIComponent(pending.id)}`);
      setOperation(operation, source);
      if (operation.status === "running") operation = await pollOperation(operation.id, null, source);
      if (terminalStatuses.has(operation.status) && operation.status !== "repair_required") {
        await finish(operation, state.operationKinds.get(source) || "install", source);
      } else if (operation.status === "repair_required") {
        state.error = operation.message || "This LoRA operation requires repair.";
      } else if (operation.status === "awaiting_upload") {
        state.status = "The previous upload still awaits a file. Cancel it to start again.";
      }
    } catch (error) {
      state.error = `Could not check the previous LoRA operation: ${error.message}`;
    } finally {
      state.recoveringSources.delete(source);
      render();
    }
  }
  function mount(host, sources, preferredKey = null) {
    state.host = host;
    restoreOperations();
    setSources(sources, preferredKey);
    host.addEventListener("change", (event) => {
      if (!event.target.matches("[data-admin-lora-source]") || state.busy) return;
      state.selectedSourceKey = event.target.value;
      state.status = "";
      void loadCatalog();
      void recoverSource(state.selectedSourceKey);
    });
    host.addEventListener("submit", (event) => {
      if (event.target.id !== "admin-lora-install-form") return;
      event.preventDefault();
      void install(event.target);
    });
    host.addEventListener("click", (event) => {
      if (event.target.closest("[data-admin-lora-retry-upload]")) { void retryUpload(); return; }
      if (event.target.closest("[data-admin-lora-cancel-upload]")) { void cancelPendingUpload(); return; }
      const button = event.target.closest("[data-admin-lora-remove]");
      if (button) void remove(button.dataset.adminLoraRemove);
    });
    if (state.busy) render();
    else { void loadCatalog(); void recoverSource(state.selectedSourceKey); }
  }
  return { mount, loadCatalog, state };
}
