export function catalogSource(sources, key) {
  return (sources || []).find((source) => source.source_key === key ||
    source.replicas?.some((replica) => replica.source_key === key)) || null;
}

// Retain the old key as well: accepted jobs and another browser may still use it.
export function reconcileSourceKey(sources, key, saved) {
  const representative = catalogSource(sources, key);
  if (!representative || representative.source_key === key) return key;
  if (saved?.[key]) {
    saved[representative.source_key] = structuredClone(saved[key]);
  }
  return representative.source_key;
}

// The source select has no empty choice, so an unset selection resolves to the
// first available catalog entry.
export function defaultSourceKey(sources) {
  return (sources || []).find((source) => source.available !== false)?.source_key || null;
}

export function samePublication(a, b) {
  return Boolean(a && b && ["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"]
    .every((field) => a[field] === b[field]));
}

export function promptRuntimeId(state) {
  return state.textComfyuiInstanceId || null;
}

export function promptRuntimeError(state) {
  if (state.comfyuiInstancesStatus !== "ready") return "Prompt service configuration is still being checked.";
  const id = promptRuntimeId(state);
  if (!id) return "Prompt generation is not configured. The server needs a CPU prompt service assignment.";
  const runtime = (state.comfyuiInstances || []).find((item) => item.id === id);
  if (!runtime) return "The assigned CPU prompt service is not configured.";
  const source = state.promptGeneratorSource;
  if (!source) {
    if (!runtime.available) return "The CPU prompt service is unavailable; waiting for its workflow catalog.";
    if (state.promptGeneratorLoadError) return "Prompt sources could not be loaded.";
    if (!state.promptGeneratorsLoaded) return "Loading prompt sources.";
    if (!state.promptGeneration?.active_source) return "No prompt sources are available on the CPU prompt service.";
    return "Loading the prompt source.";
  }
  if (source.instance_id !== id) return "Reload prompt sources from the assigned CPU service.";
  if (source.available === false) return "This publication is unavailable on the CPU prompt service.";
  // Validated cached publications may queue while the assigned service is offline.
  return null;
}
