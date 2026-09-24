# Prompt generation

Prompt Generation is an optional preparation stage. It starts off for accounts without a saved preference. Its source selector lists text publications from the existing workflow registry, even when only one exists. Subject entry is manual and independent of LoRAs.

The control panel uses independent switches and expansion buttons for Auto-generation, Prompt Generation, and Creative Direction. Enabling a section expands it. The process indicator reflects enabled stages; execution status is separate. The existing Generate button, quantity selector, model controls, and image runtime remain in place.

- **Generate prompt** executes only the selected text publication and places the result in the prompt editor.
- **Generate** executes enabled stages in order: prompt generation, Creative Direction, image generation. Quantity and model selections expand into independent image requests; each receives a fresh generated prompt and optional refinement.
- While Prompt Generation is enabled, Creative Direction uses Refine. Its previous Create/Refine choice is restored when Prompt Generation is disabled.
- Generated text follows the editor until the user edits it. Later results offer **Use latest prompt** instead of replacing that draft. Results belonging to another source or source revision cannot replace the editor.
- Auto-generation captures a server-owned snapshot. Panel changes, including destination and limit, remain drafts until **Apply**. Applying a changed limit resets its count. Stop and Apply discard preparations not yet accepted; images already accepted from preparations continue. Refresh reconnects without issuing another start command. The existing default limit remains 200.

## Browser and shared preferences

`cif.control-panel.v1.<user-id>` stores a versioned local snapshot, last synchronized base, server revision, and unresolved conflict. Writes happen on input; only server synchronization is debounced. The origin and authenticated user isolate the journal. Existing local controls are imported into the shared settings format.

Settings include source-specific parameters and interfaces, prompt text, model selection, quantity, image runtime, seed, resolution, LoRAs, Creative Direction, enabled stages, expansion states, and the draft limit. `cif.panel-draft.<user-id>` retains the unapplied destination and editor draft protection. The server remains authoritative for running automation.

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

After cloning the graph, the adapter verifies node `909` is `HFDatasetShuffle`, resolves a fresh 32-bit seed, and records the seed and compiled graph hash. A changed bundle requires review; it is not silently patched. Future publishers should expose an ordinary seed parameter. Random sampling does not guarantee a unique caption.

## Durable APIs and recovery

All mutation endpoints require authentication, CSRF, generation protocol `3`, and an `Idempotency-Key`. Existing account-scoped submission receipts also cover these endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/prompt-generations` | Accept a source key, revision, and parameters; return a durable run with HTTP 202. |
| `GET /api/prompt-generations/{id}` | Read status, text, revision, resolved seeds, compiled hash, and any error. |
| `POST /api/generation-preparations` | Accept an expanded `items` list; each item has an image `generation`, `prompt_generation`, and optional Refine `assistant`. Return a durable group with HTTP 202. |
| `GET /api/generation-preparations/{id}` | Read each item's stage, raw/final text, linked text run and image, and any error. |

`PromptGenerationRun` freezes the text graph, parameters, publication revision, registered ComfyUI instance, and resolved seeds. `GenerationPreparation` freezes each image request and optional assistant inputs, then links the completed stages and accepted image. Text execution uses its publication's registered instance independently of the selected image runtime.

Text and image work use the same scheduler, per-instance capacity, account fairness, and manual priority. A finished text job releases its slot before downstream image work. Pending preparations contribute to activity and automatic limits. The image acceptance transaction is the existing generation service transaction; failures never fall back to an older prompt.

A known ComfyUI prompt ID reconnects to history after restart. Completed text and saved refinement are reused. A crash or lost response after an ambiguous ComfyUI submission fails visibly and never blindly resubmits. Browser submission recovery retains the same idempotency key; accepted jobs are persisted locally before their submission receipt is cleared.

## Migration and release

Migration `b73a94f1c205`, following `a12c39e781b4`, adds the text-run and preparation tables without modifying historical image records. Test upgrades on a populated database copy. Failed-cutover recovery uses the pinned release runner's existing backup transaction: preserve the failed candidate database, restore the pre-cutover database and operational files, and verify the previous image. Keep assets, uploads, workflows, and recovery artifacts.

Deploy through Samus's supported `update_production --sha <full-reviewed-main-sha> --wait` entrypoint. Application releases do not replace the pinned runner. Verify the completed release job, application SHA/image, health, HTTPS, and fingerprinted frontend assets. Live feature acceptance is a separate verification.
