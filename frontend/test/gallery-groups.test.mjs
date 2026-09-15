import assert from "node:assert/strict";
import test from "node:test";
import { promptRuns, promptGroupsMarkup, promptChangesMarkup } from "../src/gallery-groups.mjs";
import { selectionPlan } from "../src/gallery-selection.mjs";

const items = ["A", "A", "B", "A", "A"].map((prompt, i) => ({ id: `g${i}`, accepted_at: `2026-09-15T00:00:0${i}Z`, prompt_fingerprint: prompt, status: "succeeded" }));
test("consecutive prompt runs preserve chronology and ignore generation settings", () => {
  items[1].expected_width = 2048;
  assert.deepEqual(promptRuns(items).map((group) => group.items.map((item) => item.id)), [["g4", "g3"], ["g2"], ["g1", "g0"]]);
  const metadata = new Map([["g0", { id: "older" }], ["g4", { id: "newer" }]]);
  assert.equal(promptRuns([items[0], items[4]], metadata).length, 2);
  const cached = new Map([["g3", { id: "stable-group", generation_count: 2 }]]);
  assert.equal(promptRuns(items.slice(3), cached)[0].id, "stable-group");
});
test("collapsed groups keep accessible controls, complete counts, and hidden cards", () => {
  const metadata = new Map(items.slice(3).map((item) => [item.id, { id: "g3", generation_count: 70, previous_generation_id: "g2" }]));
  const markup = promptGroupsMarkup(items.slice(3), (item) => `<article>${item.id}</article>`, { metadata, collapsed: new Set(["g3"]), changes: new Map() });
  assert.match(markup, /aria-label="Expand group of 70 generations"/);
  assert.match(markup, /id="prompt-cards-g3" hidden/);
  assert.match(markup, /data-prompt-group-select="g3"/);
  assert.match(markup, /2 of 70 loaded/);
});
test("diff text is escaped and long previews describe omitted edits", () => {
  const markup = promptChangesMarkup({ edit_count: 5, omitted_edits: 2, snippets: [[{ kind: "removed", text: "<script>bad</script>" }, { kind: "added", text: "sunset" }]] });
  assert.doesNotMatch(markup, /<script>/);
  assert.match(markup, /<ins>sunset<\/ins>/);
  assert.match(markup, /2 more edits/);
});
test("selection plans include unloaded members and prefer fresh visible details", () => {
  const plan = selectionPlan(new Set(["generation:g0", "generation:g1"]), { generations: [{ ...items[0], image_count: 2 }], selectionGenerations: [{ ...items[0], image_count: 0 }, items[1]] });
  assert.deepEqual(plan.generation_ids, ["g0", "g1"]);
  assert.equal(plan.downloadable, true);
  assert.equal(plan.count, 2);
});
