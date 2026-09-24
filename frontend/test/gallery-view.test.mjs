import assert from "node:assert/strict";
import test from "node:test";
import { galleryMarkup } from "../src/render.mjs";
import { galleryLayoutMarkup, galleryViewChecked, galleryViewKeys, galleryViewScope } from "../src/gallery-view.mjs";

test("classic removes prompt boundaries while retaining card order, folders and image proportions", () => {
  const generations = [
    { id: "older", accepted_at: "2026-09-01", status: "succeeded", prompt_fingerprint: "A", expected_width: 512, expected_height: 1024 },
    { id: "newer", accepted_at: "2026-09-02", status: "succeeded", prompt_fingerprint: "B", expected_width: 1024, expected_height: 512 },
  ];
  const options = { promptGroups: { metadata: new Map(), collapsed: new Set(), changes: new Map() }, collections: [{ id: "folder", name: "Studies", parent_id: null }] };
  const grouped = galleryMarkup(generations, options);
  const classic = galleryMarkup(generations, { ...options, galleryLayout: "classic" });
  assert.equal((grouped.match(/data-prompt-group=/g) || []).length, 2);
  assert.doesNotMatch(classic, /data-prompt-group=|Select group|Prompt changes/);
  assert.equal((classic.match(/data-select-view /g) || []).length, 1);
  assert.ok(classic.indexOf('data-collection-id="folder"') < classic.indexOf('class="classic-gallery-grid"'));
  assert.ok(classic.indexOf('data-generation-id="newer"') < classic.indexOf('data-generation-id="older"'));
  for (const match of grouped.matchAll(/style="--gallery-media-aspect: ([^"]+)"/g)) assert.ok(classic.includes(match[0]));
});

test("whole-view checked state includes unloaded items and never selects a new arrival", () => {
  const inventory = { generations: Array.from({ length: 525 }, (_, i) => ({ id: String(i) })), collection_ids: ["folder"] };
  const selected = new Set(galleryViewKeys(inventory));
  assert.equal(selected.size, 526);
  assert.equal(galleryViewChecked(inventory, selected), "true");
  inventory.generations.push({ id: "new-arrival" });
  assert.equal(galleryViewChecked(inventory, selected), "mixed");
  assert.equal(selected.has("generation:new-arrival"), false);
  selected.clear();
  assert.equal(galleryViewChecked(inventory, selected), "false");
  selected.add("collection:folder");
  assert.equal(galleryViewChecked(inventory, selected), "mixed");
});

test("layout defaults to grouped; scope restricts Home and Favorites explicitly", () => {
  assert.match(galleryLayoutMarkup(), /data-gallery-layout="grouped" aria-pressed="true"/);
  assert.match(galleryLayoutMarkup("classic"), /data-gallery-layout="classic" aria-pressed="true"/);
  assert.deepEqual(galleryViewScope({}), { collection_id: null, favorites_only: false });
  assert.deepEqual(galleryViewScope({ currentCollectionId: "studies", favoritesFilter: true }), { collection_id: "studies", favorites_only: true });
  assert.match(galleryMarkup([], { galleryLayout: "classic" }), /data-select-view/);
});
