# Prompt generation

Prompt Generation is an optional preparation stage. It starts off for accounts without a saved preference. Its source selector lists text publications from the existing workflow registry, even when only one exists. Subject entry is manual and independent of LoRAs.

The control panel uses independent switches and expansion buttons for Auto-generation, Prompt Generation, and Creative Direction. Enabling Prompt Generation or Creative Direction expands its section; Auto-generation preserves its expansion state. The caption beneath Generate reflects the enabled stages. During auto-generation, active stages use blue, underlined text and accessible status labels; overlapping image and Creative Direction work can highlight both stages. Stopped, blocked, retrying, completed, or disconnected automation has no active highlight. The existing Generate button, quantity selector and model controls remain in place. Both runtime selectors are removed; server configuration assigns the GPU and CPU services.

- **Generate prompt** executes only the selected text publication and places the result in the prompt editor.
- The button animates while submitting, waiting for ComfyUI capacity, generating, or reconnecting. Prompt-only requests can queue while images run, including during auto-generation; routine progress and completion do not add helper rows.
- **Generate** executes enabled stages in order: prompt generation, Creative Direction, image generation. Quantity and model selections expand into independent image requests, but the entire batch shares one generated prompt and at most one Creative Direction refinement; images resolve separate random seeds. A failed prompt or refinement fails the whole batch.
- While Prompt Generation is enabled, Creative Direction uses Refine. Its previous Create/Refine choice is restored when Prompt Generation is disabled.
- Generated text follows the editor until the user edits it. Later results offer **Use latest prompt** instead of replacing that draft. Results belonging to another source or source revision cannot replace the editor.
- Auto-generation prepares one text prompt and at most one Creative Direction refinement for an entire batch. Every quantity/checkpoint item shares that final prompt; random image seeds resolve separately for quantity repetitions, while fixed seeds remain fixed. A new batch starts a new text run. Completed prompt/refinement work survives restart, and the image batch is accepted atomically.
- The generated text is published before Creative Direction starts and fills the Prompt field during refinement. The saved refinement replaces it before image acceptance, including while the image runtime is unavailable. Refinement continues immediately without waiting for a browser acknowledgment. Fast stages may pass between browser updates. Edits and open-editor drafts are preserved with **Use latest prompt**; stale cycles cannot replace newer text. Reloading or loading workflow controls late still restores the current preview. `/api/auto-generation` exposes optional `progress` with the current cycle identity/revision, active stages, raw prompt, and refined prompt; `latest_prompt` remains the accepted-prompt baseline.
- Valid panel edits synchronize automatically to the next batch. Unsaved automatic edits are journaled per account and origin; cross-device conflicts and failed saves offer explicit retry. Reconnects never overwrite running settings with an unchanged browser's stale panel. Settings changes discard obsolete, unaccepted preparation; accepted images finish.
- Enabling auto-generation pins the current folder until it is turned off. Navigation cannot retarget it, and the API rejects destination changes while enabled. A deleted destination blocks generation until the user turns automation off and enables it in another folder.
- The dropdown contains only **Stop after [number] images queued**, with blank meaning unlimited. The default remains 200. Counts cover accepted image jobs since enabling, excluding text jobs. Limit edits preserve the count and can truncate the final batch; lowering a limit below the accepted count stops new work immediately. Stopping automation preserves all accepted images.
- Both Generate buttons and new manual image/preparation API submissions are blocked while automation is enabled. Replaying an already accepted idempotency receipt remains supported. Prompt-only requests remain available.
- Disabled image Generate buttons show **Auto Generating** with the same spinner while automation is enabled. Normal automatic-settings saves are silent; errors and conflicts retain their recovery controls.

## Browser and shared preferences

`cif.control-panel.v1.<user-id>` stores a versioned local snapshot, last synchronized base, server revision, and unresolved conflict. Writes happen on input; only server synchronization is debounced. The origin and authenticated user isolate the journal. Existing local controls are imported into the shared settings format.

Settings include source-specific parameters and interfaces, prompt text, model selection, quantity, seed, resolution, LoRAs, Creative Direction, enabled stages, expansion states, and the draft limit. `cif.panel-draft.<user-id>` retains editor draft protection. `cif.auto-settings.v1.<user-id>` retains unacknowledged automatic settings changes. The server remains authoritative for running automation.

Restore the journal first. Load current interfaces before comparing local/base/remote values and reconcile all three with the existing interface migration helpers. This avoids treating newly published defaults as edits. LoRA order and strengths survive for retained items, retired items disappear, and new items use published defaults. Historical generation snapshots remain unchanged. The existing conflict controls resolve genuine cross-device conflicts explicitly. Unavailable sources and storage errors are visible; saved selections are not replaced silently. Uploaded asset references retain the existing ownership validation.

## Publication contract

The single final output determines a publication's kind. `GET /api/workflows` remains image-only. `GET /api/workflows?output_kind=text` lists text sources; summaries and details include `output_kind`.

A text publication uses a connected `CIFPublishText` publisher, a valid `text` binding, and cardinality `one`. The initial contract supports exactly one required input plus optional string, numeric, boolean, choice, and seed parameters; it does not require `positive_prompt`. An explicitly supplied empty text string is valid and differs from a missing value, allowing workflow-defined subject fallback. Media and LoRA-stack inputs are not supported for text sources in this release and are rejected explicitly.

Only the declared final publisher's matching ID, instance UUID, kind, role, and cardinality are accepted from ComfyUI history. Its text result must be exactly one nonblank string, at most 100,000 characters. Text jobs create no gallery entries, image artifacts, or thumbnails.

The supplied bundle is unchanged. The temporary compatibility adapter is restricted to publication `b11b9ce9-53f0-44f1-8e8d-296fc54c5949` and these hashes:

| File | SHA-256 |
| --- | --- |
| Editable workflow | `0fffb74f0331a8918b97129c1d3c91625bff5d16bec39979dffe8b4f9c4f53e3` |
| Frozen API graph | `84843b65f2c0847ae4ff5ef644d56559800bce7278ed47a27968ceffb01f3a6c` |
| Interface manifest | `e5ae15f00a6ae252226364f71afcbfe6657ccc888644ad91461c069acb9341f5` |

After cloning the graph, the adapter verifies node `909` is `HFDatasetShuffle`, resolves a fresh seed in the live node’s inclusive range `0–2,147,483,647`, and records the seed and compiled graph hash. A changed bundle requires review; it is not silently patched. Future publishers should expose an ordinary seed parameter. Random sampling does not guarantee a unique caption.

## Durable APIs and recovery

All mutation endpoints require authentication, CSRF, generation protocol `3`, and an `Idempotency-Key`. Existing account-scoped submission receipts also cover these endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/prompt-generations` | Accept a source key, revision, and parameters; return a durable run with HTTP 202. |
| `GET /api/prompt-generations/{id}` | Read status, text, revision, resolved seeds, compiled hash, and any error. |
| `POST /api/generation-preparations` | Accept an expanded `items` list; each item has an image `generation`, `prompt_generation`, and optional Refine `assistant`. All items must request the same prompt and refinement — the group produces one text run shared by every item. Return a durable group with HTTP 202. |
| `GET /api/generation-preparations/{id}` | Read each item's stage, raw/final text, linked text run and image, and any error. |

`PromptGenerationRun` freezes the text graph, parameters, publication revision, registered ComfyUI instance, and resolved seeds. `GenerationPreparation` freezes each image request and optional assistant inputs, then links the completed stages and accepted image. Text and image execution use their independently assigned server runtimes and authoritative catalogs. Retired runtime preferences are discarded without losing workflow controls.

Text and image work use the same scheduler, per-instance capacity, account fairness, and priority for manual work already accepted before automation was enabled. A finished text job releases its slot before downstream image work. Pending preparations contribute to activity and automatic limits. The image acceptance transaction is the existing generation service transaction; failures never fall back to an older prompt.

A known ComfyUI prompt ID reconnects to history after restart. Completed text and saved refinement are reused. A crash or lost response after an ambiguous ComfyUI submission fails visibly and never blindly resubmits. Browser-to-application submission recovery automatically checks the receipt, replaying a missing request with its original body and idempotency key. Recovery runs one request at a time with backoff capped at 30 seconds and stops on logout; unresolved account-scoped receipts remain available on the next sign-in in that tab. Accepted jobs are persisted locally before their submission receipt is cleared. Restored results wait for the source controls to load before being applied to the editor.

## Migration and release

Migration `b73a94f1c205`, following `a12c39e781b4`, adds the text-run and preparation tables without modifying historical image records. Migration `c92f6e81ab30` adds bounded internal prompt-rejection diagnostics. It retains only validation types and numeric bounds, not upstream prompt contents. Startup retires incompatible unaccepted legacy automatic preparations while preserving accepted images and history. Test upgrades on a populated database copy. Failed-cutover recovery uses the pinned release runner's existing backup transaction: preserve the failed candidate database, restore the pre-cutover database and operational files, and verify the previous image. Keep assets, uploads, workflows, and recovery artifacts.

Deploy through Samus's supported `update_production --sha <full-reviewed-main-sha> --wait` entrypoint. Application releases do not replace the pinned runner. Verify the completed release job, application SHA/image, health, HTTPS, and fingerprinted frontend assets. Live feature acceptance is a separate verification.


## Fixed text and image services

`CIF_COMFYUI_TEXT_INSTANCE_ID` assigns the CPU prompt catalog and execution service.
`CIF_COMFYUI_DEFAULT_INSTANCE_ID` assigns the GPU image catalog and execution service.
The assignments must be distinct. There are no runtime selectors or request overrides.
An unset text assignment disables prompt generation; it never falls back to the GPU.

New requests omit runtime IDs. Existing clients may supply the assigned ID for
compatibility, but a conflicting ID is rejected. Responses and accepted preparations
record both assignments. Future automatic cycles normalize old selections to server
configuration; accepted work retains its recorded runtime. Revision checks still apply.
See [CPU deployment and copy checks](comfyui-promptgen.md).
