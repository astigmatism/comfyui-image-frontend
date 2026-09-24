export function galleryLayoutMarkup(layout = "grouped") {
  return `<div class="gallery-layout-control" role="group" aria-label="Gallery layout">
    <button type="button" data-action="set-gallery-layout" data-gallery-layout="grouped" aria-pressed="${layout !== "classic"}">Grouped</button>
    <button type="button" data-action="set-gallery-layout" data-gallery-layout="classic" aria-pressed="${layout === "classic"}">Classic</button>
  </div>`;
}

export function classicGalleryHeaderMarkup() {
  return `<header class="classic-gallery-header">
    <span>All items</span><span class="prompt-group-rule" aria-hidden="true"></span>
    <span class="classic-gallery-count" role="status">Loading count…</span>
    <button type="button" class="prompt-group-select" data-select-view role="checkbox" aria-checked="false" aria-label="Select all items in this view"><span class="prompt-group-check" aria-hidden="true"></span><span>Select all</span></button>
  </header>`;
}

export function galleryViewScope(state) {
  return { collection_id: state.currentCollectionId || null, favorites_only: Boolean(state.favoritesFilter) };
}

export function galleryViewKeys(inventory) {
  return [...inventory.generations.map((item) => `generation:${item.id}`), ...inventory.collection_ids.map((id) => `collection:${id}`)];
}

export function galleryViewChecked(inventory, selected) {
  if (!inventory) return selected.size ? "mixed" : "false";
  const keys = galleryViewKeys(inventory);
  const count = keys.filter((key) => selected.has(key)).length;
  return count === 0 ? "false" : count === keys.length ? "true" : "mixed";
}
