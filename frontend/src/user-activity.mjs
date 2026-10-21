// Input activity only: polling, SSE and programmatic rendering never keep the server awake.
export function createActivityReporter({ send, ready, now = () => Date.now(), schedule = setTimeout, cancel = clearTimeout, interval = 5000 }) {
  let lastSent = -Infinity, timer = null, pending = false, sending = false;
  async function flush() {
    timer = null;
    if (!pending || !ready() || sending) return;
    pending = false; sending = true; lastSent = now();
    try { await send(); }
    catch { pending = true; }
    finally {
      sending = false;
      if (pending && ready() && timer === null) timer = schedule(flush, interval);
    }
  }
  function activity() {
    if (!ready()) return;
    pending = true;
    if (!sending && now() - lastSent >= interval) { if (timer !== null) cancel(timer); timer = null; void flush(); }
    else if (timer === null) timer = schedule(flush, Math.max(0, interval - (now() - lastSent)));
  }
  return { activity, dispose: () => { if (timer !== null) cancel(timer); timer = null; pending = false; } };
}

export function installActivityReporting(options) {
  const reporter = createActivityReporter(options);
  for (const kind of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "touchmove"]) {
    document.addEventListener(kind, (event) => { if (event.isTrusted && !document.hidden) reporter.activity(); }, { capture: true, passive: true });
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) reporter.activity(); });
  window.addEventListener("focus", () => reporter.activity());
  window.addEventListener("popstate", () => reporter.activity());
  return reporter;
}
