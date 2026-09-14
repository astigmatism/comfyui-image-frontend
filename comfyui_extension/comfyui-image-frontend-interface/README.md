# ComfyUI Image Frontend Interface

Reusable, prompt-local boundary nodes for workflows consumed by
`comfyui-image-frontend`.

The pack is deliberately workflow-agnostic. It contains no Krea-specific node IDs,
bindings, model names, paths, API routes, or mutable request store.

## Nodes

- **Image Frontend Interface** provides the common controls `positive_prompt`,
  `negative_prompt`, `seed`, `width`, `height`, `batch_size`, and
  `enable_upscale`.
- **Image Frontend Text/Integer/Decimal/Boolean/Seed Parameter** nodes declare
  additional typed controls with stable public IDs and authoring metadata.
- **Image Frontend Choice Parameter** maps a finite list of stable public option
  IDs to private destination-compatible `COMBO` values. Its authored
  `options_json` is never exposed as a caller-controlled binding map.
- **Image Frontend Image Parameter** selects or uploads one static PNG, JPEG, or
  WebP from ComfyUI's input store and emits both `IMAGE` and `MASK`. Publication
  exposes byte/dimension limits and the native upload route, but never the local
  authoring filename.
- **Publish Image to Image Frontend** durably saves every image in a batch using
  ComfyUI's built-in `SaveImage` implementation and emits a namespaced result in
  prompt history.
- **Publish Text to Image Frontend** emits a declared text result in prompt
  history.

Every node receives a stable `instance_uuid` from the bundled frontend extension.
An exporter must reject duplicate public IDs or instance UUIDs.

## Contract rules

The contract schema is `comfyui-image-frontend.interface/v1`.

- Wiring determines where a parameter goes.
- Public parameter IDs never contain private ComfyUI node IDs.
- Only connected outputs of the common interface node are eligible for export.
- One parameter node may fan out to multiple type-compatible destinations.
- API requests patch the node's prompt-local `value`; there is no global request
  dictionary.
- Choice requests accept only a declared public option ID. The node validates the
  selection and emits its private `COMBO` binding. Publication exposes labels and
  optional per-choice `default_strength` hints but not private bindings.
- Image requests contain bytes or a frontend-owned asset reference, never a
  filesystem path. The adapter uploads the validated bytes through
  `/upload/image`, clones the frozen API graph, and patches only the trusted
  image-parameter binding with the returned input-store locator. Image inputs are
  required in publication/v1; optional images need a real execution-safe branch.
- A seed node always executes with a concrete integer. For local ComfyUI queueing,
  its native control defaults to `randomize`, which resolves a new concrete value
  for each queued prompt. A saved random-mode seed is also primed with a fresh
  concrete value when its workflow loads, so the first local queue is not a fixed
  placeholder. For external calls, `default_mode=random` tells the adapter to
  resolve an omitted, blank, or null seed before calling `/prompt`.
- `enable_upscale` is valid only when wired to a real execution-safe switch.
- The publishers are output nodes, so their results are retained in native
  `/history/{prompt_id}` output records.
- Direct API prompt strings are literal unless an adapter explicitly implements a
  compatible dynamic-prompt expansion phase.

## Publishing a workflow

After saving a workflow that contains Image Frontend declarations, use
**File → Save & Publish for Image Frontend**. The command deliberately uses
ComfyUI's normal Save operation first and then calls the installed frontend's
official `app.graphToPrompt()` compiler. Ordinary Save is not patched.

Publication writes two artifacts beside the editable workflow:

```text
example.json
example.api.json
example.interface.json
```

For a file already named `example.workflow.json`, the artifacts are
`example.api.json` and `example.interface.json`.

The API file is the raw executable graph accepted by ComfyUI's `/prompt` route.
The interface manifest contains the input declarations, declared publishers,
all native output-node identities, runtime requirements, dependency class names,
and SHA-256 identifiers for the saved workflow and API graph. It also contains
two automatically compiled metadata sections:

- `generation_source` describes the graph's media flow, generation type,
  dimension policy, base-model architecture, technologies, and structural
  outcome summary. Its optional `base_model.timeline` records a sortable,
  provenance-backed architecture introduction month and, when the exact fixed
  or default public model is known, that model's release month. Months use
  `YYYY-MM`; workflow publication time remains a separate timestamp.
- `technical_inventory` records unambiguous output-reachable model, LoRA, text
  encoder, VAE, upscaler, detector, sampler, technology, class-type, and node
  count evidence. Compiled nodes that do not lead to a native output are counted
  and classified separately rather than silently treated as active.

This metadata is derived deterministically from the saved public interface and
the frozen output-reachable API graph. It is not entered by a human and does not
guess prompt subject matter, visual style, quality, licensing, or safety.
Unknown loader/provider classes remain visible in `unclassified_loaders` with a
warning instead of being assigned an invented meaning.

Timeline records come from the publisher's curated release catalog and include
their source URL. Publication never uses a model file's modification time,
download time, or the workflow's `published_at` value as a model release date.
Unknown dates are omitted rather than guessed. For a public checkpoint selector,
timeline variants are keyed only by the public parameter and option IDs; private
loader bindings remain private.

The API file is written first and the manifest last. ComfyUI's userdata writer
atomically replaces each file. Hashes identify the publication artifacts and can
diagnose stale or mixed bundles; local discovery must not reject an otherwise
valid adjacent bundle solely because a hash differs. Hashing and declaration
UUID generation also work when ComfyUI is opened over ordinary LAN HTTP, where
secure browser Web Crypto features may be unavailable.

Publication rejects malformed or duplicate public IDs, duplicate instance UUIDs,
unconnected parameters, missing positive prompts, saved/API declaration
mismatches, declarations nested inside subgraphs, and graphs with no native output
nodes. A workflow without a CIF publisher may still be published, but the manifest
warns that its native history outputs must be returned under `unmapped_outputs`.

This first version provides an explicit publication command because the installed
frontend does not expose a supported after-save extension hook. It does not
monkey-patch normal workflow saving.

## First workflow milestone

For the smallest proof, connect only:

1. `positive_prompt` to the confirmed prompt path;
2. `seed` to each sampler that should share the public seed; and
3. the confirmed final `IMAGE` edge to **Publish Image to Image Frontend** with
   `output_id=final`, `role=final`, and `cardinality=many`.

Leave unused common outputs disconnected. Do not connect `enable_upscale` merely
to frontend bypass/mute state.

## Known first-version limits

- No generic `ANY` parameter or publisher is provided.
- Image upload parameters support one required static PNG, JPEG, or WebP. Video,
  animated-image, arbitrary-file, and optional-media contracts remain deferred.
- The pack publishes declarations, runtime results, artifact identifiers,
  automatically inferred generation-source metadata, and a technical inventory.
  The appliance remains responsible for discovering adjacent bundles and
  recognizing these additive manifest sections.

## Ordered model-only LoRA stack

`CIFLoraStack` accepts a base MODEL, private `catalog_json` (`[{"id":"style","label":"Style","filename":"installed/file.safetensors"}]`), and an ordered JSON `value` (`[{"id":"style","strength":0}]`). Publish all entries with zero default strengths and semantic role `lora`; set the parameter group to `LoRAs`, required to false, and the desired nonnegative bounds/step (Moody Krea2 uses 0–2 in 0.05 increments). The frontend receives public IDs/labels only. Apply strength > 0 sequentially with the native model-only loader. All-zero input passes the exact model through. Full integration, rollout and rollback instructions are in `docs/lora-controls.md` in the frontend repository.
