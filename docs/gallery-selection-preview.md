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

The initial gallery contains image cards, a three-image batch, favorites, and nested collections.
Optional JPEGs from `backend/data/gallery-preview-input/` are used when the empty database is
seeded; without them, the fixture generates labeled sample images. The current preview uses
these Unsplash samples: [mountain](https://images.unsplash.com/photo-1464822759023-fed622ff2c3b),
[lake](https://images.unsplash.com/photo-1470770841072-f978cf4d019e),
[forest](https://images.unsplash.com/photo-1441974231531-c6227db76b6e), and
[sunrise](https://images.unsplash.com/photo-1470252649378-9c29740c9fa8).

## Try the interaction

1. Hover an image or folder and click the checkbox in its lower-right toolkit. It appears
   with the existing tools after the hover delay (or keyboard focus). Touch screens keep the
   existing toolkit available. Selection reveals checkboxes on every card. A small count and four
   outlined icons replace the collection breadcrumb in the normal-height title bar; Favorites,
   gallery scale, activity and account controls stay available. The icons select loaded items,
   open Move / Copy, open Delete, and clear the selection. Hover for their labels. The toolbar
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

Blue checks and outlines identify selected cards; existing gold favorite indicators remain
distinct. Selection is limited to the currently loaded cards, rather than implicitly including
unloaded history. The API accepts up to 500 explicit IDs per request. Ordinary card controls for
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
ownership, overlapping selections, independent copied files and recall, rollback, nesting limits
and recursive deletion. Browser coverage includes keyboard selection, incoming cards, failed
operations, shared destination controls and toolbar/modal bounds at 320–1024px.
