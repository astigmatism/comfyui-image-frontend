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

export function samePublication(a, b) {
  return Boolean(a && b && ["publication_id", "workflow_sha256", "api_sha256", "manifest_sha256"]
    .every((field) => a[field] === b[field]));
}

export function promptRuntimeId(state) {
  return state.promptGeneration?.runtime_id || state.textComfyuiInstanceId ||
    state.promptGeneratorSource?.instance_id || state.defaultComfyuiInstanceId || null;
}

export function promptRuntimeError(state, id = promptRuntimeId(state)) {
  if (state.comfyuiInstancesStatus !== "ready") return "Prompt runtime availability is still being checked.";
  const runtime = (state.comfyuiInstances || []).find((item) => item.id === id);
  if (!runtime) return "Choose a configured prompt runtime.";
  if (!runtime.available) return `${runtime.label} is unavailable.`;
  const source = state.promptGeneratorSource;
  if (!source) return "Choose a prompt source.";
  // Older descriptors have no replica list; the server still validates the request.
  if (!source.replicas?.length) return null;
  const replica = source.replicas.find((item) => item.instance_id === id);
  if (!replica) return "This prompt runtime does not have the publication. Copy the bundle and refresh its catalog.";
  if (!samePublication(replica.revision, source.revision)) return "This prompt runtime has a different publication revision. Re-copy the bundle and refresh its catalog.";
  if (!replica.available) return "This publication is unavailable on the selected prompt runtime.";
  return null;
}
