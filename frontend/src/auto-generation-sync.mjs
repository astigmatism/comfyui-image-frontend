import { settingsEqual } from "./user-settings.mjs";

// Compare the editable contract, excluding server-populated optional fields.
export function automationConfiguration(snapshot) {
  if (!snapshot) return null;
  const generation = {};
  for (const key of ["source_key", "revision", "parameters", "prompt_assistant", "comfyui_instance_id", "collection_id"]) {
    generation[key] = snapshot.generation[key] ?? null;
  }
  return { generation, variants: snapshot.variants, quantity: snapshot.quantity,
    assistant: snapshot.assistant ?? null, prompt_generation: snapshot.prompt_generation ?? null,
    max_generations: snapshot.max_generations ?? null };
}

export function createAutoGenerationSync({ api, read, current, apply, status, saving, signal, storage, storageKey, canSave = () => true, delay = 400 }) {
  let pending = null;
  let timer = null;
  let busy = false;
  let conflict = false;
  let invalid = false;
  let failed = false;
  let needsRead = false;
  const persist = () => {
    try {
      if (pending) storage.setItem(storageKey, JSON.stringify(pending));
      else storage.removeItem(storageKey);
    } catch { status("error", "Automatic settings could not be saved in this browser."); }
  };
  try { pending = JSON.parse(storage.getItem(storageKey) || "null"); }
  catch { status("error", "Saved automatic settings could not be read."); }
  if (pending && (!Number.isInteger(pending.revision) || !pending.snapshot?.generation)) pending = null;

  const clear = () => {
    clearTimeout(timer);
    timer = null;
    pending = null;
    conflict = invalid = failed = needsRead = false;
    persist();
    status("saved", null);
  };
  const schedule = () => {
    if (timer === null && !signal.aborted && pending && !busy && !conflict && !invalid && !failed) timer = setTimeout(() => { timer = null; void flush(); }, delay);
  };
  const changedElsewhere = () => {
    conflict = true;
    status("conflict", "Automatic settings changed on another device. Your edits are saved here; retry to apply them.");
  };
  const observe = (auto) => {
    if (signal.aborted || busy || !pending) return;
    if (!auto.enabled || settingsEqual(automationConfiguration(pending.snapshot), automationConfiguration(auto.snapshot))) {
      clear();
    } else if (auto.revision !== pending.revision) changedElsewhere();
    else schedule();
  };
  const stage = () => {
    const auto = current();
    if (signal.aborted || !auto?.enabled) return;
    clearTimeout(timer);
    timer = null;
    failed = false;
    needsRead = true;
    let snapshot;
    try { snapshot = read(); invalid = needsRead = false; }
    catch (error) {
      invalid = true;
      clearTimeout(timer);
      status("error", error.message);
      return;
    }
    if (!busy && settingsEqual(automationConfiguration(snapshot), automationConfiguration(auto.snapshot))) { clear(); return; }
    pending = { snapshot: structuredClone(snapshot), revision: pending?.revision ?? auto.revision };
    persist();
    if (!conflict) status("pending", "Updating the next automatic batch…");
    schedule();
  };
  const flush = async () => {
    if (signal.aborted || busy || conflict || invalid || !pending) return;
    if (!canSave()) { schedule(); return; }
    const auto = current();
    if (!auto?.enabled) { clear(); return; }
    if (auto.revision !== pending.revision) { changedElsewhere(); return; }
    const sent = structuredClone(pending);
    busy = true;
    saving(true);
    status("saving", "Updating the next automatic batch…");
    let succeeded = false;
    try {
      const result = await api("/api/auto-generation/apply", { method: "POST", signal,
        body: JSON.stringify({ expected_revision: sent.revision, snapshot: sent.snapshot }) });
      if (signal.aborted) return;
      succeeded = true;
      apply(result);
      if (!result.enabled || settingsEqual(pending?.snapshot, sent.snapshot)) clear();
      else if (pending) { pending.revision = result.revision; persist(); }
    } catch (error) {
      if (signal.aborted) return;
      // A lost response may have committed. A read reconciles without a second POST.
      try {
        const result = await api("/api/auto-generation", { signal, deadlineMs: 5000 });
        if (signal.aborted) return;
        apply(result);
        if (!result.enabled) clear();
        else if (settingsEqual(automationConfiguration(result.snapshot), automationConfiguration(sent.snapshot))) {
          succeeded = true;
          if (settingsEqual(pending?.snapshot, sent.snapshot)) clear();
          else if (pending) { pending.revision = result.revision; persist(); }
        } else if (result.revision !== sent.revision) changedElsewhere();
      } catch { /* Preserve the durable edit for explicit retry or reconnect. */ }
      if (pending && !conflict && !succeeded) { failed = true; status("error", error.message || "Automatic settings could not be saved. Retry to apply them."); }
    } finally {
      busy = false;
      if (!signal.aborted) { saving(false); if (succeeded) schedule(); }
    }
  };
  const retry = async () => {
    if (busy || signal.aborted) return;
    try {
      const auto = await api("/api/auto-generation", { signal, deadlineMs: 5000 });
      if (signal.aborted) return;
      apply(auto);
      if (!auto.enabled) { clear(); return; }
      if (pending) pending.revision = auto.revision;
      conflict = false;
      stage();
      await flush();
    } catch (error) { if (!signal.aborted) status("error", error.message); }
  };
  signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  return { stage, observe, retry, clear, flush, resume: () => { if (needsRead) stage(); } };
}
