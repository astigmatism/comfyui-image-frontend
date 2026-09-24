// Merge independently edited fields; arrays (checkpoint order, selections) are atomic.
export function settingsEqual(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => Object.hasOwn(b, key) && settingsEqual(a[key], b[key]));
}
const equal = settingsEqual;
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
export function mergeSettings(base, local, remote, path = "") {
  if (equal(local, base)) return { value: structuredClone(remote), conflicts: [] };
  if (equal(remote, base) || equal(local, remote)) return { value: structuredClone(local), conflicts: [] };
  if (object(base) && object(local) && object(remote)) {
    const value = {};
    const conflicts = [];
    for (const key of new Set([...Object.keys(base), ...Object.keys(local), ...Object.keys(remote)])) {
      const merged = mergeSettings(base[key], local[key], remote[key], path ? `${path}.${key}` : key);
      if (merged.value !== undefined) value[key] = merged.value;
      conflicts.push(...merged.conflicts);
    }
    return { value, conflicts };
  }
  return { value: structuredClone(local), conflicts: [path] };
}

const editable = (response) => ({
  settings: response.settings,
  gallery_scale: response.gallery_scale,
  checkpoint_tiers: response.checkpoint_tiers,
});

export function createSettingsSync({ api, read, apply, status: reportStatus, signal, storage = null, storageKey = null, normalize = (value) => value, prepareMerge = async () => {} }) {
  let storageError = null;
  const status = (value, message) => reportStatus(storageError ? "error" : value, storageError || message);
  let base = null;
  let revision = 0;
  let timer = null;
  let remoteConflict = null;
  let busy = false;
  let applying = false;
  let pending = false;
  let loading = null;
  let waiters = [];
  const release = () => { const waiting = waiters; waiters = []; for (const done of waiting) done(); };
  const active = () => !signal.aborted;
  const persistLocal = () => {
    if (!storage || !storageKey || !active()) return;
    try { storage.setItem(storageKey, JSON.stringify({ version: 1, base, revision, local: read(), conflict: remoteConflict })); }
    catch { storageError = "Browser settings could not be saved locally. Check available storage."; status("error"); }
  };
  const readLocal = () => {
    if (!storage || !storageKey) return null;
    try {
      const raw = storage.getItem(storageKey);
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (saved?.version !== 1 || !saved.local?.settings || !Number.isInteger(saved.revision)) throw new Error("Invalid settings");
      return saved;
    } catch { storageError = "Saved browser settings could not be read."; status("error"); return null; }
  };
  const merge = (ancestor, local, remote) => mergeSettings(normalize(ancestor), normalize(local), normalize(remote));
  const applyValue = async (value) => {
    if (!active()) return;
    applying = true;
    try { await apply(value); } finally { applying = false; }
  };
  const performLoad = async () => {
    status("loading");
    const saved = readLocal();
    if (saved) {
      base = saved.base;
      revision = saved.revision;
      await applyValue(normalize(saved.local));
    }
    const initial = structuredClone(read());
    let result = await api("/api/preferences", { signal, deadlineMs: 5000, operation: "Shared settings" });
    if (!active()) return;
    if (!result.settings_initialized) {
      try {
        result = await api("/api/preferences", {
          method: "PUT", signal,
          body: JSON.stringify({ settings: read().settings, expected_revision: result.revision, import_if_empty: true }),
        });
      } catch (error) {
        if (error.status !== 409) throw error;
        result = await api("/api/preferences", { signal });
      }
    }
    const remote = editable(result);
    await prepareMerge(saved?.base || initial, read(), remote);
    const merged = merge(saved?.base || initial, read(), remote);
    base = remote;
    revision = result.revision;
    await applyValue(merged.value);
    if (merged.conflicts.length || (saved?.conflict && !equal(normalize(read()), normalize(remote)))) {
      remoteConflict = result;
      status("conflict", merged.conflicts.join(", "));
    } else {
      pending = !equal(normalize(base), normalize(read()));
      status(pending ? "saving" : "saved");
      if (pending) schedule();
    }
    persistLocal();
  };
  const load = () => {
    if (!loading) loading = performLoad().finally(() => { loading = null; });
    return loading;
  };
  const refresh = async () => {
    if (!active() || busy || remoteConflict) return;
    if (!base) return load();
    busy = true;
    try {
      const result = await api("/api/preferences", { signal, deadlineMs: 5000 });
      if (!active() || result.revision === revision) return;
      const remote = editable(result);
      await prepareMerge(base, read(), remote);
      const merged = merge(base, read(), remote);
      if (merged.conflicts.length) {
        remoteConflict = result;
        await applyValue(merged.value);
        status("conflict", merged.conflicts.join(", "));
        persistLocal();
        return;
      }
      base = remote;
      revision = result.revision;
      await applyValue(merged.value);
      if (!equal(base, read())) pending = true;
      persistLocal();
    } finally {
      busy = false;
      release();
      if (pending) schedule(false);
    }
  };
  const save = async () => {
    if (!active() || applying || remoteConflict) return;
    if (!base) { status("error", "Settings have not loaded. Retry before saving."); return; }
    if (busy) { pending = true; await new Promise((done) => waiters.push(done)); return save(); }
    const sent = read();
    if (equal(sent, base)) { pending = false; status("saved"); return; }
    busy = true;
    pending = false;
    status("saving");
    try {
      const result = await api("/api/preferences", {
        method: "PUT", signal,
        body: JSON.stringify({ ...sent, expected_revision: revision }),
      });
      if (!active()) return;
      base = editable(result);
      revision = result.revision;
      status("saved");
      pending = !equal(read(), sent);
      persistLocal();
    } catch (error) {
      if (!active()) return;
      if (error.status === 409) {
        busy = false;
        await refresh();
        pending = !remoteConflict;
      } else status("error", error.message);
    } finally {
      busy = false;
      release();
      if (pending && !remoteConflict) schedule(false);
    }
  };
  const schedule = (debounce = true) => {
    if (!active() || applying) return;
    persistLocal();
    // User edits debounce; background refreshes must not keep moving a pending
    // save into the future while generation events arrive continuously.
    if (!debounce && timer !== null) return;
    clearTimeout(timer);
    if (base && !remoteConflict) status("saving");
    timer = setTimeout(() => { timer = null; void save(); }, 400);
  };
  const resolve = async (keepLocal) => {
    if (!remoteConflict) return;
    await prepareMerge(base, read(), editable(remoteConflict));
    base = editable(remoteConflict);
    revision = remoteConflict.revision;
    remoteConflict = null;
    await applyValue(normalize(keepLocal ? read() : base));
    if (keepLocal) await save();
    else status("saved");
    persistLocal();
  };
  signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  return { load, refresh, save, schedule, resolve, persistLocal, get applying() { return applying; } };
}
