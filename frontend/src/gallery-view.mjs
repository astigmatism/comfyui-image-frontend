import { CHECKPOINT_TIER_DEFINITIONS, checkpointRank } from "./lib.mjs";

export function excludedCheckpointRanks(state) {
  return CHECKPOINT_TIER_DEFINITIONS.map(({ id }) => id).filter((id) => state?.excludedCheckpointRanks?.includes(id));
}

export function galleryFilterActive(state) {
  return favoritesFilterActive(favoritesMode(state)) || excludedCheckpointRanks(state).length > 0;
}

export function galleryGenerationMatches(state, item) {
  const excluded = excludedCheckpointRanks(state);
  return favoritesModeMatches(favoritesMode(state), item)
    && (!excluded.length || !excluded.includes(checkpointRank(item?.checkpoint_id, state?.checkpointTiers)));
}

export function galleryFilterSignature(state) {
  return JSON.stringify([
    galleryViewScope(state),
    excludedCheckpointRanks(state).length ? state?.checkpointTiers : null,
  ]);
}

export function galleryViewParameters(state, extras = {}) {
  const scope = galleryViewScope(state);
  const parameters = new URLSearchParams({ collection_id: scope.collection_id || "", favorites_only: String(scope.favorites_only), unfavorited_only: String(scope.unfavorited_only), ...extras });
  for (const rank of scope.excluded_checkpoint_ranks || []) parameters.append("excluded_checkpoint_ranks", rank);
  return parameters;
}

export function checkpointRankFilterMarkup(state) {
  const excluded = excludedCheckpointRanks(state);
  return `<div class="checkpoint-rank-filter" role="group" aria-label="Visible model ranks"><div class="rank-filter-segments">${CHECKPOINT_TIER_DEFINITIONS.map(({ id }) => `<button type="button" class="rank-filter-button checkpoint-tier-${id}" data-action="toggle-checkpoint-rank-filter" data-checkpoint-rank="${id}" aria-label="Show rank ${id}" aria-pressed="${!excluded.includes(id)}" title="${excluded.includes(id) ? "Show" : "Hide"} images from rank ${id}${id === "C" ? " (includes unranked models)" : ""}">${id}</button>`).join("")}</div></div>`;
}

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

// The Favorites control is one button with three states: show everything, show
// favorites only, show unfavorited only. Both filtered states list generation
// cards alone, because a folder tile would offer a subtree that ignores the filter.
export const FAVORITES_MODES = ["all", "favorites", "unfavorited"];

function normalizeFavoritesMode(mode) {
  return FAVORITES_MODES.includes(mode) ? mode : "all";
}

export function favoritesMode(state) {
  return normalizeFavoritesMode(state?.favoritesMode);
}

export function nextFavoritesMode(mode) {
  const index = FAVORITES_MODES.indexOf(normalizeFavoritesMode(mode));
  return FAVORITES_MODES[(index + 1) % FAVORITES_MODES.length];
}

export function favoritesFilterActive(mode) {
  return mode === "favorites" || mode === "unfavorited";
}

export function favoritesModeMatches(mode, item) {
  if (mode === "favorites") return Boolean(item?.is_favorite);
  if (mode === "unfavorited") return !item?.is_favorite;
  return true;
}

// The accessible name stays "Favorites" so the control keeps one identity across
// the cycle; aria-pressed carries the tri-state, while the title and the visible
// label say which way the view is filtered.
const FAVORITES_PRESENTATION = {
  all: { pressed: "false", title: "Show only favorites", label: "Favorites" },
  favorites: { pressed: "true", title: "Showing only favorites", label: "Favorites" },
  unfavorited: { pressed: "mixed", title: "Showing only unfavorited items", label: "Unfavorited" },
};

export function favoritesFilterPresentation(mode) {
  return FAVORITES_PRESENTATION[normalizeFavoritesMode(mode)];
}

export function galleryViewScope(state) {
  const mode = favoritesMode(state);
  return {
    collection_id: state.currentCollectionId || null,
    favorites_only: mode === "favorites",
    unfavorited_only: mode === "unfavorited",
    ...(excludedCheckpointRanks(state).length ? { excluded_checkpoint_ranks: excludedCheckpointRanks(state) } : {}),
  };
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
