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

export function createSettingsSync({ api, read, apply, status, signal }) {
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
  const applyValue = async (value) => {
    if (!active()) return;
    applying = true;
    try { await apply(value); } finally { applying = false; }
  };
  const performLoad = async () => {
    status("loading");
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
    base = editable(result);
    revision = result.revision;
    await applyValue(base);
    status("saved");
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
      const merged = mergeSettings(base, read(), remote);
      if (merged.conflicts.length) {
        remoteConflict = result;
        await applyValue(merged.value);
        status("conflict", merged.conflicts.join(", "));
        return;
      }
      base = remote;
      revision = result.revision;
      await applyValue(merged.value);
      if (!equal(base, read())) pending = true;
    } finally {
      busy = false;
      release();
      if (pending) schedule();
    }
  };
  const save = async () => {
    if (!active() || applying || remoteConflict) return;
    if (!base) { status("error", "Settings have not loaded. Retry before saving."); return; }
    if (busy) { pending = true; await new Promise((done) => waiters.push(done)); return save(); }
    const sent = read();
    if (equal(sent, base)) { status("saved"); return; }
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
      if (pending && !remoteConflict) schedule();
    }
  };
  const schedule = () => {
    if (!active() || applying) return;
    clearTimeout(timer);
    if (base && !remoteConflict) status("saving");
    timer = setTimeout(() => void save(), 400);
  };
  const resolve = async (keepLocal) => {
    if (!remoteConflict) return;
    base = editable(remoteConflict);
    revision = remoteConflict.revision;
    remoteConflict = null;
    if (keepLocal) await save();
    else { await applyValue(base); status("saved"); }
  };
  signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
  return { load, refresh, save, schedule, resolve, get applying() { return applying; } };
}
