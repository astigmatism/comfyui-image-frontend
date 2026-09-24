# Gallery selection preview

Run from the repository with Docker available:

```sh
bash scripts/gallery-preview.sh
```

Open [the local preview](http://127.0.0.1:8765) and sign in with username `preview` and password
`GalleryPreview123!`. The script builds the production application image and starts the
`cif-gallery-preview` container on host loopback. Its synthetic data lives in the separate
`cif-gallery-preview-data` Docker volume and survives preview restarts. Two in-process fake
ComfyUI services provide workflow discovery and generation; household runtimes are not used.

The initial gallery contains consecutive prompt groups, a three-image batch, favorites, and nested collections.
Optional JPEGs from `backend/data/gallery-preview-input/` are used when the empty database is
seeded; without them, the fixture generates labeled sample images. The current preview uses
these Unsplash samples: [mountain](https://images.unsplash.com/photo-1464822759023-fed622ff2c3b),
[lake](https://images.unsplash.com/photo-1470770841072-f978cf4d019e),
[forest](https://images.unsplash.com/photo-1441974231531-c6227db76b6e), and
[sunrise](https://images.unsplash.com/photo-1470252649378-9c29740c9fa8).

## Try the interaction

Use **Grouped / Classic** between Favorites and Gallery scale to change the gallery layout.
Grouped is the default; the choice is saved with your account settings. Classic keeps the existing
thumbnail proportions and scale in one continuous grid, with folder tiles above it. Its **All items**
header counts the entire current view. **Select all** includes unloaded generations and matching
folder tiles, including collections with more than 500 items. Favorites restricts this selection to
matching cards and folders. Individual deselection produces a mixed checkbox. Switching layouts
preserves selection, while new arrivals stay unselected. Classic also resumes any history skipped
by collapsed prompt groups. Changing location or the Favorites filter clears selection.

Prompt groups have neutral headers with a generation count, collapse arrow, **Prompt changes**,
and **Select group**. Hover or focus Prompt changes to compare with the immediately older group;
click to keep the preview open. The first group has no earlier comparison. Groups follow the exact
positive prompt and the gallery's chronological order, so A → B → A remains three groups. Changing
resolution, seed, or model alone does not start another group. Grouping applies within Home or the
current collection. The layout choice also applies with Favorites enabled.

Collapse keeps the header and selection available. **Select group** resolves the complete group,
including unloaded cards, before using the toolbar below. Individual deselection produces a mixed
group checkbox. Loading more, collapsing, and ordinary gallery refreshes preserve selection;
new arrivals remain unselected. A large collapsed group can be skipped during gallery pagination;
expand it and use **Load more in group** to retrieve its remaining cards.

1. Hover an image or folder and click the checkbox in its lower-right toolkit. It appears
   with the existing tools after the hover delay (or keyboard focus). Touch screens keep the
   existing toolkit available. Selection reveals checkboxes on every card. A small count and six
   outlined icons replace the collection breadcrumb in the normal-height title bar; Favorites,
   gallery scale, activity and account controls stay available. On narrow screens, selection actions
   use the second title-bar row and the other controls share the first row with the panel toggle.
   The icons select loaded items, add Favorites, download a ZIP, open Move / Copy, open Delete,
   and clear the selection. Hover for their labels. The toolbar
   fades and slides in over 190ms on the first selection, respecting reduced-motion preferences.
2. Click more cards, Shift-click a range, or use **Select loaded** / Cmd/Ctrl+A. Newly arriving
   cards remain unselected. **Clear selection** (the × icon), Escape, or deselecting the last item exits
   selection mode and restores the hover-only tools. Changing the gallery location clears the selection.
3. Choose **Move / Copy…**, pick a destination, then use **Copy here** or **Move here** in the
   same dialog. Copy preserves the originals, including all images in a batch; folders include
   their nested contents. Copy is available after selected generations finish. Moving to the
   current location is disabled; copying there is allowed.
4. Choose **Delete…** to inspect the confirmation. It shows the affected generation count and
   warns when folder contents or active generations are included. Confirming permanently deletes
   this sample content. A failed operation keeps the selection available for retry.
5. Choose **Add to Favorites** (the heart icon) to bookmark all selected cards. Selected folders
   are bookmarked themselves; their unselected contents are not favorited. Existing favorites
   remain set, and the action is disabled when every selected card is already a favorite.
6. Choose **Download selection** (the down arrow) to save one ZIP containing every available image
   from selected cards and folders, including batches and nested folders. Overlapping selections
   appear once. Both Favorites and Download keep the selection so you can use another action next.

Blue checks and outlines identify selected cards; existing gold favorite indicators remain
distinct. In Grouped view, **Select loaded** applies to loaded cards, while **Select group** includes
unloaded members. Classic's toolbar and Cmd/Ctrl+A use the full view. The API accepts up to 500
unscoped explicit IDs per request; oversized groups show a limit message without selecting a partial
group. Classic sends a fixed list of selected IDs with a collection scope, allowing larger selections
without including later arrivals. The server validates ownership and current scope before bulk actions.
Ordinary card controls for
details, download, favorite, recall, preview preference and rename remain available outside
selection mode; individual card Move/Delete controls have been replaced by selection.

Stop only this preview with `docker stop cif-gallery-preview`. Re-running the script rebuilds
and replaces the labeled preview container without removing its sample volume.

## Focused validation

With Python development dependencies and Playwright Chromium installed:

```sh
pytest backend/tests/integration/test_gallery_selection.py backend/tests/integration/test_collections.py
cd frontend
npm test
npm run lint
npm run format:check
CIF_E2E_PORT=8766 npx playwright test e2e/gallery-selection.spec.mjs e2e/gallery-hover.spec.mjs
```

The alternate browser-test port avoids the live preview on 8765. The new backend tests cover
ownership, overlapping selections, independent copied files and recall, rollback, nesting limits,
recursive deletion, explicit favorites, ZIP contents and temporary-file cleanup. Browser coverage
includes keyboard selection, incoming cards, failed operations, bulk favorites and downloads,
shared destination controls and toolbar/modal bounds at 320–1440px with generation activity visible.

Prompt grouping has additional coverage in `backend/tests/integration/test_prompt_groups.py` and
`frontend/e2e/gallery-groups.spec.mjs`, including page boundaries, reused prompts, complete membership,
owner isolation, group selection, compact diffs, collapse, and responsive sizing. The fingerprint
migration backfills existing generations without putting full prompts in gallery summaries.

Classic layout and whole-view selection are covered by `backend/tests/integration/test_gallery_view.py`,
`frontend/test/gallery-view.test.mjs`, and `frontend/e2e/gallery-view.spec.mjs`. These include unloaded
membership, exclusions, bulk operations over 500 cards, scoped downloads, Favorites, ownership,
saved layout and scale, pagination after collapse, new arrivals, keyboard controls, and toolbar bounds.

For a native preview, configure `CIF_DATA_DIR`, `CIF_DATABASE_PATH`, `CIF_FRONTEND_DIST`,
`CIF_PREVIEW_ASSETS`, `CIF_PREVIEW_HOST=127.0.0.1`, and `CIF_PREVIEW_PORT`, then run
`PYTHONPATH=backend python -m tests.gallery_preview` after building the frontend.
