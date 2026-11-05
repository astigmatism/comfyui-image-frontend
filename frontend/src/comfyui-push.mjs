// One gallery image, copied into the input folder of the ComfyUI service that can
// use it. The server decides which service that is and reports the name ComfyUI
// actually wrote, so this module only locks the control, carries the intent, and
// says plainly where the file ended up.
import { api } from "./api.mjs";

const inFlight = new Set();

// Two ComfyUI services can both be reachable, so the copy names the one it chose.
export function pushNotice(result) {
  const place = result.subfolder ? `${result.subfolder}/` : "";
  const saved = `Sent ${result.name} to ${result.instance_label} → ${place}input`;
  return result.warning ? `${saved}. ${result.warning}` : saved;
}

export function installComfyUIPush(root, { notify }) {
  async function push(button) {
    const artifactId = button.dataset.artifactId;
    if (!artifactId || inFlight.has(artifactId)) return;
    inFlight.add(artifactId);
    const lock = (target) => {
      target.disabled = true;
      target.setAttribute("aria-busy", "true");
    };
    const unlock = (target) => {
      if (!target) return;
      target.disabled = false;
      target.removeAttribute("aria-busy");
    };
    const live = () => root.querySelector(`[data-action="push-comfyui"][data-artifact-id="${artifactId}"]`);
    lock(live() || button);
    try {
      const result = await api(`/api/artifacts/${encodeURIComponent(artifactId)}/push-to-comfyui`, {
        method: "POST",
        body: "{}",
      });
      notify(pushNotice(result), result.warning ? "warning" : "success");
    } catch (error) {
      notify(error.message || "The image could not be sent to ComfyUI.", "error");
    } finally {
      inFlight.delete(artifactId);
      unlock(live() || button);
    }
  }

  function onClick(event) {
    const button = event.target.closest('[data-action="push-comfyui"]');
    if (!button || button.disabled) return undefined;
    event.preventDefault();
    // Returned so a caller (and a test) can wait for one copy to finish.
    return push(button);
  }

  root.addEventListener("click", onClick);
  return { dispose: () => root.removeEventListener("click", onClick) };
}
