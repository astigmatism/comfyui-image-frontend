import test from "node:test";
import assert from "node:assert/strict";
import { automaticPromptUpdate, olderAutomaticProgress } from "../src/auto-generation-progress.mjs";
import { promptPipelineMarkup } from "../src/render.mjs";

const context = { ready: true, source: "image-source", revision: { publication_id: "v1" }, generator: "text-source" };
function automatic(overrides = {}, progress = {}) {
  return { enabled: true, status: "preparing", revision: 4,
    snapshot: { generation: { source_key: context.source, revision: context.revision },
      prompt_generation: { source_key: context.generator }, assistant: { mode: "refine" } },
    progress: { revision: 4, cycle_id: "cycle-a", cycle_created_at: "2026-09-24T01:00:00Z",
      active_stages: ["creative_direction"], raw_prompt: "Complete generated prompt", refined_prompt: null, ...progress }, ...overrides };
}

test("raw text is applied before refinement and the refined result replaces it", () => {
  const raw = automaticPromptUpdate(automatic(), context);
  assert.equal(raw.action, "apply");
  assert.equal(raw.prompt, "Complete generated prompt");
  const refined = automaticPromptUpdate(automatic({}, { refined_prompt: "Refined full prompt" }), context, raw.receipt);
  assert.equal(refined.action, "apply");
  assert.equal(refined.prompt, "Refined full prompt");
  assert.equal(automaticPromptUpdate(automatic(), context, refined.receipt).action, "ignore");
});

test("deduplication uses cycle and phase, including repeated text", () => {
  const raw = automaticPromptUpdate(automatic(), context);
  assert.equal(automaticPromptUpdate(automatic(), context, raw.receipt).action, "ignore");
  assert.equal(automaticPromptUpdate(automatic({}, { refined_prompt: raw.prompt }), context, raw.receipt).action, "apply");
  assert.equal(automaticPromptUpdate(automatic({}, { cycle_id: "cycle-b" }), context, raw.receipt).action, "apply");
});

for (const protection of [{ dirty: true }, { editorOpen: true }]) {
  test(`draft protection offers both phases: ${JSON.stringify(protection)}`, () => {
    const raw = automaticPromptUpdate(automatic(), { ...context, ...protection });
    assert.equal(raw.action, "offer");
    const refined = automaticPromptUpdate(automatic({}, { refined_prompt: "refined" }), { ...context, ...protection }, raw.receipt);
    assert.equal(refined.action, "offer");
    assert.equal(refined.prompt, "refined");
    assert.equal(refined.autoCycleId, "cycle-a");
  });
}

test("startup retains a preview until matching controls load; reload can replay it", () => {
  const deferred = automaticPromptUpdate(automatic(), { ...context, ready: false });
  assert.equal(deferred.action, "defer");
  assert.equal(deferred.receipt, undefined);
  assert.equal(automaticPromptUpdate(automatic(), context, deferred.receipt).action, "apply");
  assert.equal(automaticPromptUpdate(automatic(), { ...context, dirty: true }, null).action, "offer");
});

test("source, publication, and generator changes reject unrelated results", () => {
  for (const change of [{ source: "other" }, { revision: { publication_id: "v2" } }, { generator: "other" }]) {
    assert.equal(automaticPromptUpdate(automatic(), { ...context, ...change }).action, "ignore");
  }
});

test("stopping and obsolete revisions reject previews; final limited batches remain visible", () => {
  assert.equal(automaticPromptUpdate(automatic({ enabled: false, status: "off" }), context).action, "ignore");
  assert.equal(automaticPromptUpdate(automatic({}, { revision: 3 }), context).action, "ignore");
  assert.equal(automaticPromptUpdate(automatic(), context, { revision: 5 }).action, "ignore");
  assert.equal(automaticPromptUpdate(automatic({ enabled: false, status: "completed" }, { refined_prompt: "final" }), context).prompt, "final");
});

test("out-of-order server snapshots cannot roll back revision, cycle, or phase", () => {
  const current = automatic({}, { cycle_id: "cycle-b", cycle_created_at: "2026-09-24T02:00:00Z" });
  assert.equal(olderAutomaticProgress(current, automatic()), true);
  assert.equal(olderAutomaticProgress(current, automatic({ revision: 3 })), true);
  assert.equal(olderAutomaticProgress(automatic({}, { refined_prompt: "final" }), automatic()), true);
  assert.equal(olderAutomaticProgress(current, automatic({ revision: 5 })), false);
  assert.equal(olderAutomaticProgress(null, automatic()), false);
});

function render(auto = automatic(), overrides = {}) {
  return promptPipelineMarkup({ autoGenerate: auto.enabled, autoGenerateStatus: auto.status,
    automation: auto, promptGeneration: { enabled: true }, autoGenerateCreativeDirection: true,
    promptAssistant: { mode: "refine" }, ...overrides });
}

for (const stage of ["prompt_generation", "creative_direction", "image"]) {
  test(`caption marks ${stage} as active with accessible text`, () => {
    const html = render(automatic({}, { active_stages: [stage] }));
    assert.match(html, new RegExp(`data-pipeline-stage="${stage}" class="pipeline-stage is-active"`));
    assert.equal((html.match(/is-active/g) || []).length, 1);
    assert.match(html, /aria-label="[^"]+ \(active\)"/);
    assert.equal(html.replace(/<[^>]*>/g, ""), "Repeat · Prompt generation → Refine → Image");
  });
}

test("caption can show overlapping refinement and images", () => {
  const html = render(automatic({}, { active_stages: ["creative_direction", "image"] }));
  assert.equal((html.match(/is-active/g) || []).length, 2);
});

test("stopped, failed, reconnecting, and missing progress captions are neutral", () => {
  for (const status of ["blocked", "paused", "retrying", "off", "completed"]) assert.doesNotMatch(render(automatic({ status })), /is-active/);
  assert.doesNotMatch(render(automatic({ enabled: false })), /is-active|Repeat/);
  assert.doesNotMatch(render(automatic(), { automationUnavailable: true }), /is-active/);
  assert.doesNotMatch(render(automatic({ progress: null })), /is-active/);
});

test("caption follows captured stages, including assistant-only Create and images alone", () => {
  const auto = automatic();
  auto.snapshot.prompt_generation = null;
  auto.snapshot.assistant.mode = "create";
  assert.equal(render(auto).replace(/<[^>]*>/g, ""), "Repeat · Create → Image");
  auto.snapshot.assistant = null;
  assert.equal(render(auto).replace(/<[^>]*>/g, ""), "Repeat · Current prompt → Image");
  auto.snapshot.prompt_generation = {};
  assert.equal(render(auto).replace(/<[^>]*>/g, ""), "Repeat · Prompt generation → Image");
});
