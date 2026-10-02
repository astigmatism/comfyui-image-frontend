import assert from "node:assert/strict";
import test from "node:test";
import { checkpointRankFilterMarkup, galleryFilterActive, galleryFilterSignature, galleryGenerationMatches, galleryViewParameters, galleryViewScope } from "../src/gallery-view.mjs";
import { galleryMarkup } from "../src/render.mjs";
import { selectionPlan } from "../src/gallery-selection.mjs";

const grades = ["A", "B", "C", "D", "F"];
const checkpointTiers = Object.fromEntries(grades.map((grade) => [grade, [grade.toLowerCase()]]));
const items = [...grades.map((grade) => [grade, grade.toLowerCase()]), ["C", "unassigned"], ["C", null]]
  .flatMap(([rank, checkpoint_id], index) => [false, true].map((is_favorite) => ({ id: `${index}-${is_favorite}`, rank, checkpoint_id, is_favorite })));

test("every rank combination intersects all three Favorites modes, including C fallbacks", () => {
  for (let mask = 0; mask < 32; mask++) for (const favoritesMode of ["all", "favorites", "unfavorited"]) {
    const excludedCheckpointRanks = grades.filter((_, index) => mask & (1 << index));
    const state = { checkpointTiers, excludedCheckpointRanks, favoritesMode, generations: items };
    const expected = items.filter((item) => !excludedCheckpointRanks.includes(item.rank)
      && (favoritesMode === "all" || item.is_favorite === (favoritesMode === "favorites")));
    assert.deepEqual(items.filter((item) => galleryGenerationMatches(state, item)), expected);
    const keys = new Set(items.map((item) => `generation:${item.id}`));
    assert.deepEqual(selectionPlan(keys, state).generation_ids, expected.map((item) => item.id));
    assert.deepEqual(selectionPlan(keys, { ...state, generations: [], selectionGenerations: items }).generation_ids, expected.map((item) => item.id));
    assert.equal(galleryFilterActive(state), Boolean(mask) || favoritesMode !== "all");
  }
});

test("filter scopes use repeated rank parameters, canonical order and live rank revisions", () => {
  const state = { currentCollectionId: "folder", checkpointTiers, favoritesMode: "favorites", excludedCheckpointRanks: ["F", "C", "F", "invalid"] };
  const scope = galleryViewScope(state);
  assert.deepEqual(scope, { collection_id: "folder", favorites_only: true, unfavorited_only: false, excluded_checkpoint_ranks: ["C", "F"] });
  const params = galleryViewParameters(state, { cursor: "next", limit: "24" });
  assert.deepEqual(params.getAll("excluded_checkpoint_ranks"), ["C", "F"]);
  assert.equal(params.get("favorites_only"), "true");
  assert.equal(params.get("cursor"), "next");
  assert.notEqual(galleryFilterSignature(state), galleryFilterSignature({ ...state, checkpointTiers: {} }));
  assert.equal(galleryFilterSignature({ checkpointTiers }), galleryFilterSignature({}));
});

test("rank controls always expose five independently pressed buttons and recover all-off", () => {
  const markup = checkpointRankFilterMarkup({ excludedCheckpointRanks: ["C", "D", "F"] });
  assert.equal((markup.match(/data-action="toggle-checkpoint-rank-filter"/g) || []).length, 5);
  assert.equal((markup.match(/aria-pressed="true"/g) || []).length, 2);
  for (const grade of grades) assert.ok(markup.includes(`aria-label="Show rank ${grade}"`));
  const empty = galleryMarkup([], { excludedCheckpointRanks: grades, favoritesMode: "favorites" });
  assert.match(empty, /All model ranks are hidden/);
  assert.match(empty, /data-action="show-all-checkpoint-ranks"/);
  assert.match(galleryMarkup([], { excludedCheckpointRanks: ["C"] }), /No images match these filters/);
});

test("fresh membership overrides stale selected group members and filtered selections omit folders", () => {
  const keys = new Set(["generation:one", "collection:folder"]);
  const state = {
    favoritesMode: "favorites", checkpointTiers, excludedCheckpointRanks: ["C"],
    collections: [{ id: "folder", generation_count: 10 }],
    selectionGenerations: [{ id: "one", checkpoint_id: "a", is_favorite: true }],
    generations: [{ id: "one", checkpoint_id: "a", is_favorite: false }],
  };
  assert.equal(selectionPlan(keys, state).count, 0);
  assert.equal(selectionPlan(keys, { ...state, generations: [{ id: "one", checkpoint_id: "c", is_favorite: true }] }).count, 0);
  assert.deepEqual(selectionPlan(keys, { ...state, generations: [] }).generation_ids, ["one"]);
});
