# Ordered LoRA controls

Published `lora_stack` inputs render a row for every catalog item. Top-to-bottom order is application order. Each row has a drag handle, a slider, an exact numeric field, and touch-friendly up/down buttons. Arrow Up/Down on the handle moves the row with a screen-reader announcement. Strength belongs to the public ID, so reordering never changes it. Zero rows stay visible and are skipped at execution. No trigger words are inserted.

The Moody Krea2 Simple v31 conversion uses Spread, Claire, NexBlend08, and Tifa in that order, each initially zero, with range 0–2 and step 0.05. These are workflow-author constraints, not a claim about visual quality at high strengths or particular combinations.

## Contract and execution

The public input contains `id`, `type: "lora_stack"`, normal display metadata, `items: [{id, label}]`, `minimum`, `maximum`, `step`, and `default: [{id, strength}]`. A request supplies one ordered array in `parameters.loras`. Every published ID must appear exactly once. Extra fields, missing/unknown/duplicate IDs, strings, booleans, nonfinite numbers, values outside the bounds, and values outside the decimal step are rejected. Omission uses the all-zero published default; explicit null is invalid.

The trusted declaration binds only `CIFLoraStack.value`. The frozen graph retains `catalog_json`, a private array of `{id, label, filename}`. Publication checks its public projection and defaults against that graph, and the browser receives neither filenames nor graph bindings. The Save & Publish extension verifies the installed native loader inventory before publishing. Technical inventory describes `usage: "public_stack"`; it does not report every catalog member as active.

`CIFLoraStack` uses ComfyUI's native `LoraLoaderModelOnly` sequentially. An all-zero configuration returns the exact incoming model without constructing a loader or loading weights. A nonzero request creates its own loader and passes the returned model to the next nonzero entry. There is no request-shared model or configuration cache in this node. Text-encoder connections remain unchanged.

The backend validates the selected execution runtime's companion node and complete catalog before acceptance and again before dispatch. A secondary runtime without those prerequisites is rejected, including for a zero stack. Runtime inventory uses the adapter's latest health-probe snapshot; ComfyUI still performs its normal native validation at execution time.

Requested and effective arrays remain in existing generation JSON fields. Recall and details retain labels, order, and strengths. Historical records with no stack restore zero defaults. Checkpoint fan-out takes a deep snapshot before submission; later form edits affect future submissions. No database migration is required.

## Package ownership

`comfyui_extension/comfyui-image-frontend-interface/` now owns the complete existing interface/publisher package alongside `cif_artifact_cleanup`. Install this directory as one ComfyUI custom-node package; no new Python dependencies are needed beyond ComfyUI's existing environment. Its browser extension registers **Save & Publish for Image Frontend**.

The standalone public validator in the companion package deliberately has no web-application imports. Its identical backend counterpart is checked by a parity test. Change both together. Existing publication/v1 inputs and legacy interface nodes continue to work; `lora_stack` is an additive supported type. Deploy the new frontend before publishing an interface containing it.

## Rollout

1. Preserve the current adjacent `.json`, `.api.json`, and `.interface.json` files with their exact bytes, plus the installed custom-node package and current frontend image tag. Keep workflow snapshots private; they can contain prompts and private model bindings.
2. Inspect the ComfyUI container's mounts and image build source. Install the entire companion package through the persistent custom-node mount or its image build source; do not rely on a write to an ephemeral container layer. Keep a backup of the previous package. Deploy the frontend image from the same feature commit, preserving its existing environment and data mounts.
3. Register the new node with a ComfyUI service restart when the runtime is idle. Verify `GET /object_info/CIFLoraStack` returns its definition. Check `GET /object_info/LoraLoaderModelOnly` contains all four original files. These checks do not generate images.
4. Run `python scripts/lora/prepare_workflow.py ROLLBACK_DIRECTORY OUTPUT_DIRECTORY`. It verifies publication hashes, checks node 822 and its model destinations, prepares the editable workflow and candidate API graph, and removes only the old loader's unused CLIP input. It refuses an unexpected graph. All other compiled nodes stay unchanged.
5. Inspect offline with `node scripts/lora/validate_candidate.mjs WORKFLOW_JSON API_JSON OBJECT_INFO_JSON`. The object-info snapshot must include the installed companion node. During pre-installation review it can instead contain the prospective node definition; that is a static check, not proof of live installation.
6. Open the prepared editable workflow in ComfyUI, save it at the existing `workflows/comfyui-image-frontend/Moody Krea2 Simple v31.json` path, and choose **Save & Publish for Image Frontend**. This invokes ComfyUI's official graph compiler, writes the frozen API graph, then publishes the manifest last. Do not queue the workflow during rollout.
7. Refresh Administration → Workflow diagnostics in the frontend. Verify source readiness, the four LoRA labels, zero defaults, range 0–2/step 0.05, and order. Verify primary, second-pass, upscale, prompt/CLIP, and output connections using the saved graph. Leave other runtimes unenabled until their companion and catalog are verified.

For rollback, restore the three-file bundle with the manifest last, refresh discovery, and then restore the previous node package/frontend image if needed. Keep user data and database files intact. Live image generation and visual combination testing remain a subsequent user-controlled step.

## Tests

Run the repository's Python tests excluding `live`, frontend tests/build, Ruff, mypy, and the Playwright LoRA spec. The checkpoint fan-out journey additionally exercises the real application UI against its local fake runtime. `backend/tests/unit/test_lora_stack.py` tests mocked loader order, zeros, bounds, isolation, publication drift/privacy, and runtime prerequisites; its subprocess runs the preserved legacy companion-node tests. The integration test verifies batch persistence and exact recall. None of these tests require a household ComfyUI runtime or load LoRA weights.
