import { settingsEqual } from "./user-settings.mjs";

export function olderAutomaticProgress(current, incoming) {
  if (!current || !incoming) return false;
  if (incoming.revision !== current.revision) return incoming.revision < current.revision;
  const before = current.progress;
  const after = incoming.progress;
  if (!before?.cycle_id || !after?.cycle_id) return false;
  if (before.cycle_id === after.cycle_id) return Boolean(before.refined_prompt && !after.refined_prompt);
  return String(after.cycle_created_at) < String(before.cycle_created_at) ||
    (after.cycle_created_at === before.cycle_created_at && after.cycle_id < before.cycle_id);
}

// A deferred result is intentionally not acknowledged: retry when the panel is ready.
export function automaticPromptUpdate(auto, context, previous = null) {
  const progress = auto?.progress;
  if (!progress?.cycle_id || progress.revision !== auto.revision ||
      (!auto.enabled && auto.status !== "completed")) return { action: "ignore" };
  const phase = progress.refined_prompt ? "refined" : "raw";
  const prompt = progress.refined_prompt || progress.raw_prompt;
  if (!prompt) return { action: "ignore" };
  const receipt = { revision: auto.revision, cycle: progress.cycle_id, phase };
  if (previous && (previous.revision > receipt.revision ||
      (previous.revision === receipt.revision && previous.cycle === receipt.cycle &&
        (previous.phase === "refined" || previous.phase === phase)))) return { action: "ignore" };
  if (!context.ready) return { action: "defer" };
  const generation = auto.snapshot?.generation;
  const generator = auto.snapshot?.prompt_generation?.source_key;
  if (generation?.source_key !== context.source || !settingsEqual(generation?.revision, context.revision) ||
      (generator && generator !== context.generator)) return { action: "ignore" };
  return {
    action: context.dirty || context.editorOpen ? "offer" : "apply", prompt, receipt,
    source: generation.source_key, revision: generation.revision, generator,
    autoRevision: auto.revision, autoCycleId: progress.cycle_id,
  };
}
