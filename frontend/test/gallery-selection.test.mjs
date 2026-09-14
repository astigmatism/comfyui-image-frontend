import assert from "node:assert/strict";
import test from "node:test";
import { deleteSelectionMarkup, selectionPlan, transferDialogMarkup } from "../src/gallery-selection.mjs";
import { galleryCardMarkup, collectionTileMarkup } from "../src/render.mjs";

const collections = [
  { id: "parent", name: "Studies", parent_id: null, generation_count: 2 },
  { id: "child", name: "Winter", parent_id: "parent", generation_count: 3 },
  { id: "other", name: "Archive", parent_id: null, generation_count: 0 },
];
const generations = [
  { id: "inside", collection_id: "child", status: "succeeded" },
  { id: "outside", collection_id: null, status: "succeeded" },
];

test("mixed favorite selection processes a folder and its descendants once", () => {
  const plan = selectionPlan(new Set(["collection:parent", "collection:child", "generation:inside", "generation:outside"]), {
    collections, favoritesView: true, favorites: { items: generations.map((generation) => ({ generation })) },
  });
  assert.deepEqual(plan.collection_ids, ["parent"]);
  assert.deepEqual(plan.generation_ids, ["outside"]);
  assert.equal(plan.generationCount, 6);
  assert.equal(plan.count, 2);
  assert.deepEqual(plan.favorites.collection_ids, ["parent", "child"]);
  assert.deepEqual(plan.favorites.generation_ids, ["inside", "outside"]);
  assert.equal(plan.favorites.count, 4);
  assert.equal(plan.downloadable, true);
});

test("favorites remain set and only selections with available content enable downloads", () => {
  const state = { collections, generations: [{ ...generations[0], is_favorite: true, image_count: 0 }] };
  const plan = selectionPlan(new Set(["generation:inside"]), state);
  assert.equal(plan.favorites.allFavorited, true);
  assert.equal(plan.downloadable, false);
  assert.equal(selectionPlan(new Set(["collection:other"]), state).downloadable, false);
  assert.equal(selectionPlan(new Set(["collection:parent", "generation:inside"]), state).favorites.allFavorited, false);
});

test("destination dialog supports both operations and excludes selected subtree", () => {
  const plan = selectionPlan(new Set(["collection:parent"]), { collections, generations });
  const html = transferDialogMarkup(plan, collections, null);
  assert.match(html, /data-bulk-action="copy">Copy here/);
  assert.match(html, /data-bulk-action="move">Move here/);
  assert.match(html, /value="parent" disabled/);
  assert.match(html, /value="child" disabled/);
  assert.doesNotMatch(html, /value="other" disabled/);
  assert.match(deleteSelectionMarkup(plan), /5 generations/);
  assert.match(deleteSelectionMarkup(plan), /including nested folders/);
});

test("active descendants disable copy and deletion explains cancellation", () => {
  const plan = selectionPlan(new Set(["collection:parent"]), { collections: [{ ...collections[0], remaining_count: 1 }], generations });
  assert.equal(plan.active, true);
  assert.match(deleteSelectionMarkup(plan), /cancelled and deleted/);
});

test("image and folder cards keep a selection control and individual delete, without individual move", () => {
  const imageCard = galleryCardMarkup({ id: "g", status: "succeeded" });
  const folderCard = collectionTileMarkup(collections[0]);
  for (const html of [imageCard, folderCard]) {
    assert.match(html, /role="checkbox" aria-checked="false"/);
    assert.match(html, /data-action="select-gallery-card"/);
    assert.doesNotMatch(html, /data-action="move-generation"/);
  }
  assert.match(imageCard, /data-action="delete-generation"/);
  assert.match(folderCard, /data-action="delete-collection"/);
});
