import { interfaceInputs, migrateInterfaceState } from "./lib.mjs";

// The subject a LoRA supplies lives in the prompt source's own values, and every
// text publication names it the same way.
export const PROMPT_SUBJECT_INPUT_ID = "subject_name";

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

// Changing the prompt source keeps the draft on screen, exactly as changing the image
// workflow does: every value the destination accepts carries over, and inputs only it
// declares keep their own last-used value or published default. A topmost enabled
// LoRA's trigger word outranks the subject either source was holding, and a source
// left with nothing to carry never blanks the destination's own subject.
export function promptSourceDraft({ previous = null, next, subjectTrigger = null } = {}) {
  const baseValues = structuredClone(next?.values || {});
  const baseExplicitInputIds = [...(next?.explicitInputIds || [])];
  let values = structuredClone(baseValues);
  const explicit = new Set(baseExplicitInputIds);
  if (previous?.interface && next?.interface) {
    const migrated = migrateInterfaceState(
      next.interface,
      previous.interface,
      previous.values || {},
      previous.explicitInputIds || [],
      baseValues,
      baseExplicitInputIds,
      { rejectInvalid: true },
    );
    values = migrated.values;
    explicit.clear();
    for (const id of migrated.explicitInputIds) explicit.add(id);
  }
  const subject = interfaceInputs(next?.interface).find(
    (input) => input.id === PROMPT_SUBJECT_INPUT_ID && input.type === "string");
  let subjectApplied = false;
  if (subject) {
    const left = String(previous?.values?.[subject.id] ?? "").trim();
    // With both interfaces known the migration already kept whatever the destination
    // accepted; otherwise the value as it stood is the only thing there is to carry.
    const carried = previous?.interface ? values[subject.id] : left;
    const wanted = subjectTrigger?.trim() || (left ? carried : null);
    if (wanted) {
      values[subject.id] = wanted;
      explicit.add(subject.id);
    } else if (previous?.interface && Object.hasOwn(baseValues, subject.id)) {
      values[subject.id] = baseValues[subject.id];
      if (baseExplicitInputIds.includes(subject.id)) explicit.add(subject.id);
      else explicit.delete(subject.id);
    }
    subjectApplied = values[subject.id] !== baseValues[subject.id];
  }
  return { values, explicitInputIds: [...explicit], subjectApplied };
}
