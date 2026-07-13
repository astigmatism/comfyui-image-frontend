# ComfyUI Front-End Workflow Contract Design

**Status:** Revision 1.1 — progressive checkpoint artifacts and stage-aware cancellation added  
**Audience:** Application architects, ComfyUI integrators, workflow authors, and AI coding agents  
**Primary goal:** Allow a separate front-end application to discover, validate, configure, execute, and collect outputs from a ComfyUI workflow without hard-coding workflow-specific node IDs throughout the application.

---

## 1. Executive summary

A ComfyUI workflow should be treated as a versioned executable graph with a separately declared application contract.

The proposed design adds one custom ComfyUI node, `FrontendWorkflowContract`, to every application-supported workflow. The node contains a machine-readable manifest describing:

- the workflow identity and compatible graph hashes;
- every supported user-facing control;
- where each control is bound in the workflow;
- which controls are simple node inputs and which require graph transformation, uploads, or separate workflow variants;
- conditional visibility and validation rules;
- required models and custom-node classes;
- semantic execution stages for progress reporting;
- every supported output and its role;
- which outputs are progressive checkpoints that may be rendered while execution continues;
- which output becomes canonical only after the complete workflow succeeds;
- whether an available intermediate is retained and usable when the user cancels later processing;
- compatibility, security, and resource constraints.

The custom node is deliberately a **declarative manifest node**, not a giant parameter-routing node. Existing generation nodes remain the source of executable behavior. The application service reads the contract, validates it against the exact workflow and ComfyUI runtime, patches the real target nodes, compiles any requested branch state, submits the resulting API workflow, and normalizes the returned artifacts.

The contract also defines a **progressive artifact timeline**. A workflow may emit a base image, one or more refinement checkpoints, and a final upscaled image in sequence. The front-end can render each declared checkpoint as soon as it becomes available while the remaining graph continues to execute. The final artifact is not marked canonical until the terminal stage succeeds. If the user cancels after an earlier checkpoint appears, that artifact remains an explicitly provisional or best-available result rather than being mislabeled as the completed final.

For workflows that need reliable in-flight artifact delivery, the integration package may additionally provide zero or more `FrontendWorkflowArtifact` checkpoint nodes. These companion nodes are distinct from the single contract node: the contract declares semantics, while checkpoint nodes make selected image or text states observable during execution.

This design avoids rewiring every workflow through one custom node and supports control types that cannot be represented as ordinary ComfyUI sockets, including:

- rgthree bypass and group-bypass state;
- mutually exclusive graph branches;
- uploaded images and masks;
- derived dimensions;
- model and LoRA allowlists;
- interactive or multi-step operations;
- output selection and artifact retention;
- graph variants and separate endpoint workflows.

The two analyzed Krea 2 workflows demonstrate why this distinction matters. Both contain ordinary scalar controls, but they also contain bypass-controlled optional stages, multiple seeds, temporary comparer outputs, model-dependent capabilities, and outputs whose cardinality varies by branch and batch size. One workflow also contains manual-mask behavior that is not naturally compatible with a one-shot headless request.

---

## 2. Problem statement

A conventional ComfyUI workflow does not provide a stable application-facing contract.

A front-end application can inspect raw workflow JSON, but raw node graphs expose implementation details rather than durable product semantics:

- node IDs can change when a graph is edited;
- embedded subgraphs can be flattened or rewritten during API export;
- custom-node widgets may be serialized positionally;
- editor-only nodes may not exist in the backend API prompt;
- a Boolean-looking feature may actually be encoded as node mode or group bypass state;
- a visible preview may be temporary and not a durable output;
- a meaningful intermediate image may become available before the overall prompt completes;
- collecting outputs only after final history reconciliation is too late for progressive rendering and cancellation decisions;
- one semantic setting may patch several nodes;
- one node input may be derived from a higher-level application control;
- the available enum values can depend on the installed ComfyUI runtime;
- the graph may require specific model files and custom-node revisions;
- multiple save nodes and batches can produce a variable number of artifacts;
- cancellation can race with downstream execution, so already-emitted artifacts and the eventual terminal status must be reconciled explicitly.

The application therefore needs a stable, typed, versioned layer between product concepts and ComfyUI graph implementation.

---

## 3. Goals

The contract system shall:

1. Let the application discover whether a workflow is integration-ready.
2. Describe every supported product-facing control before generation.
3. Provide stable semantic control IDs independent of raw node IDs.
4. Bind each control to one or more graph locations with structural assertions.
5. Represent scalar inputs, branch state, uploads, derived controls, presets, and output policies.
6. Distinguish user controls from expert and operator-only settings.
7. Validate a workflow against its approved graph hash and runtime dependencies.
8. Describe every supported artifact and identify one canonical result when appropriate.
9. Describe the ordered progression from early preview or checkpoint artifacts to the definitive final artifact.
10. Deliver declared checkpoints to the front-end while later stages continue to execute.
11. Preserve already-emitted artifacts when a later stage is cancelled or fails, according to contract policy.
12. Support workflow-specific progress stages and meaningful errors.
13. Preserve requested and effective settings for reproducibility and audit.
14. Allow the front-end to render a control panel without knowing ComfyUI internals.
15. Permit future workflow revisions without changing stable public control IDs unnecessarily.

---

## 4. Non-goals

The initial contract system shall not:

- infer a trustworthy contract automatically from arbitrary workflows;
- expose every raw node widget merely because it exists;
- permit callers to select arbitrary filesystem paths;
- make the front-end directly manipulate ComfyUI node IDs;
- guarantee that every editor interaction can be converted into a one-shot API call;
- guarantee arbitrary pause-and-resume inside an already-running ComfyUI prompt;
- treat incidental sampler preview frames or comparer cache images as stable checkpoint artifacts unless the workflow contract explicitly promotes them;
- promise bit-for-bit reproducibility across different GPUs, model hashes, or package revisions;
- replace normal workflow version control;
- make an unvalidated workflow safe merely by adding a contract node.

Automatic discovery tools may assist an author, but an approved contract remains an intentional specification.

---

## 5. Core architectural decision

### 5.1 The custom node is declarative

The `FrontendWorkflowContract` node stores metadata and integration bindings. It does not become the executable source of every generation parameter.

The application patches the original target nodes identified by the contract.

This separation is required because many important workflow controls are not ordinary scalar inputs:

- enabling a group may require changing several node modes;
- disabling a second pass may require rewiring a selector;
- manual inpaint may require a separate image-and-mask workflow;
- a resolution preset may resolve to width, height, alignment, and derived tile values;
- output requests may require enabling or adding output sinks;
- a model selector must be constrained by an application allowlist and runtime inventory.

### 5.2 Why not route all values through one node

A giant routing node would create several problems:

- every existing workflow would need extensive rewiring;
- ComfyUI socket types would make a universal interface difficult;
- branches and node modes still would not be represented cleanly;
- duplicate values could drift between the contract node and target nodes;
- graph readability would decline;
- custom-node caching and execution dependencies could alter behavior;
- editor widgets and API widgets would remain difficult to synchronize;
- the node would become a single point of failure for every workflow.

The declarative approach leaves image generation behavior in the original graph while giving the application a stable semantic map.

---

## 6. System architecture

The recommended architecture has four layers.

### 6.1 Front-end client

The front-end:

- lists supported workflow profiles;
- requests a resolved workflow contract;
- renders controls from the contract;
- uploads files through the application service;
- submits semantic generation requests;
- displays normalized progress and artifacts;
- never patches ComfyUI JSON directly.

### 6.2 Application workflow service

The application service is the authoritative integration layer. It:

- stores approved workflow profiles;
- extracts and validates the embedded contract;
- resolves runtime capability information;
- validates user requests;
- transforms semantic controls into graph patches;
- selects or compiles branch variants;
- uploads files to ComfyUI;
- submits API-format workflows;
- monitors execution;
- enumerates and classifies all outputs;
- stores provenance and artifacts;
- exposes a stable product API.

### 6.3 Embedded `FrontendWorkflowContract` node

The contract node:

- identifies the workflow as application-integrated;
- stores the contract manifest in the workflow itself;
- is visible to workflow authors in ComfyUI;
- can be included in API export as an output node;
- can emit the contract or validation summary as non-media execution metadata;
- has no model-loading or image-processing side effects.

### 6.4 ComfyUI runtime

ComfyUI remains responsible for:

- node and model execution;
- queueing;
- WebSocket progress;
- history;
- temporary and durable output files;
- node schema discovery through `/object_info`;
- model and server capability discovery where supported.

---

## 7. Contract discovery lifecycle

Contract discovery has two stages.

### 7.1 Static workflow discovery

When a workflow is ingested, the service:

1. Reads the original UI-format workflow JSON.
2. Finds exactly one node whose class type is `FrontendWorkflowContract`.
3. Reads and parses its manifest.
4. Validates the manifest against the contract JSON Schema.
5. Computes the UI workflow hash.
6. Exports or obtains the approved API-format workflow.
7. Computes the API graph hash.
8. Validates every selector and binding against the graph.
9. Registers the workflow profile only if validation succeeds.

A workflow with no contract node is not automatically exposed as an application-supported workflow.

A workflow with more than one active contract node is invalid unless a future schema explicitly supports namespaced contracts.

### 7.2 Runtime capability resolution

At service startup or profile activation, the service also:

1. Queries `/object_info` for required node classes and input schemas.
2. Verifies required model assets.
3. Resolves dynamic enums such as samplers and schedulers.
4. Intersects runtime choices with the workflow's approved allowlists.
5. Checks optional branch dependencies.
6. Marks capabilities as available, unavailable, degraded, or operator-disabled.
7. Produces a resolved contract for the front-end.

The resolved contract may hide or disable controls that are declared by the workflow but unavailable in the current container.

---

## 8. Custom node specification

### 8.1 Stable identity

The node shall use a stable backend class type:

```text
FrontendWorkflowContract
```

The manifest shall also include:

```json
{
  "kind": "comfyui.frontend.workflow-contract"
}
```

The class type is used to locate the node. The `kind` field prevents accidental interpretation of unrelated JSON.

### 8.2 Suggested visible fields

The node should expose these visible widgets:

| Field | Purpose |
|---|---|
| `workflow_id` | Stable logical workflow profile ID |
| `workflow_version` | Workflow contract version visible to authors |
| `contract_schema_version` | Manifest schema version |
| `display_name` | Human-readable workflow name |
| `strict_validation` | Whether detected binding errors are fatal |
| `manifest_json` | Multiline JSON containing the full contract |

An optional custom JavaScript extension may render the manifest as read-only sections or an editable table, but the stored source of truth remains JSON.

### 8.3 Backend behavior

The node should:

- register as `OUTPUT_NODE = True` so it survives API-oriented workflow export;
- return no image, latent, model, or conditioning data;
- optionally emit a UI/history payload containing workflow ID, contract version, and contract hash;
- accept hidden `PROMPT`, `UNIQUE_ID`, and workflow metadata inputs when useful;
- perform lightweight manifest syntax validation;
- never load models;
- never write arbitrary files;
- never alter graph execution behavior.

Application-side validation remains authoritative because a standalone output node is not guaranteed to execute before expensive graph branches.

### 8.4 Contract node placement

Place the node in a clearly named group such as:

```text
APPLICATION CONTRACT - DO NOT DELETE
```

The node should be visually separate from the generation path. It need not be connected to image data.

### 8.5 Optional progressive artifact node

A workflow may use zero or more companion nodes with the stable backend class type:

```text
FrontendWorkflowArtifact
```

This node is optional. It exists only when an existing `PreviewImage`, save node, or custom output node cannot provide sufficiently reliable in-flight delivery.

Recommended inputs and behavior:

| Field/input | Purpose |
|---|---|
| `artifact_id` | Stable contract output ID such as `base_image` or `detail_checkpoint` |
| `stage_id` | Semantic stage that produced the artifact |
| `role` | Semantic role such as `image.base`, `image.refined`, or `image.final` |
| `sequence` | Ordered position in the workflow's visual progression |
| `label` | Human-readable front-end label |
| `image` or `text` | Artifact payload |
| `persistence` | `temporary`, `job_lifetime`, or `durable` |
| `usable_on_cancel` | Whether this artifact may be retained as a best-available result |
| `canonical_on_success` | Whether this output becomes canonical after successful terminal completion |

The node should:

- register as an output-capable node so ComfyUI executes it when its input becomes available;
- emit a machine-readable UI/history payload containing the declared artifact identity and retrieval information;
- optionally save a temporary or durable representation according to contract policy;
- pass the input through unchanged when downstream processing must continue;
- avoid modifying pixels or text;
- avoid inventing canonical status independently of the application service;
- permit the service to publish an `artifact.available` event immediately after execution.

Existing standard nodes may satisfy the same role when their runtime behavior is validated. The contract is authoritative about semantics; the helper node is only a reliable emission mechanism.

---

## 9. Manifest top-level schema

A contract manifest should have this conceptual structure:

```json
{
  "kind": "comfyui.frontend.workflow-contract",
  "contract_schema_version": "1.1.0",
  "workflow": {},
  "presentation": {},
  "requirements": {},
  "controls": [],
  "branches": [],
  "stages": [],
  "outputs": [],
  "progression": {},
  "presets": [],
  "policies": {},
  "extensions": {}
}
```

Unknown top-level fields should be rejected in strict mode unless they are placed under `extensions` with a namespaced key.

---

## 10. Workflow identity

The `workflow` object identifies the exact compatible graph.

```json
{
  "workflow": {
    "id": "krea2-uncensored-v1",
    "display_name": "Krea 2 Uncensored",
    "version": "1.0.0",
    "family": "krea2",
    "description": "Krea 2 generation with prompt processing and optional finishing stages",
    "ui_graph_sha256": "...",
    "api_graph_sha256": "...",
    "source_file": "Krea2v1.json",
    "adapter_version": "1.0.0"
  }
}
```

### 10.1 Version meanings

- `workflow.id` is stable across compatible revisions of the same product profile.
- `workflow.version` changes when behavior, controls, defaults, bindings, or outputs change.
- `contract_schema_version` changes when the contract language changes.
- `adapter_version` changes when application compilation logic changes without necessarily changing the original workflow.
- graph hashes bind the contract to exact artifacts.

---

## 11. Requirements and capabilities

The `requirements` section declares what must exist before the profile can run.

```json
{
  "requirements": {
    "node_classes": [
      {
        "class_type": "UNETLoader",
        "required": true
      },
      {
        "class_type": "SeedVR2VideoUpscaler",
        "required": false,
        "capability": "seedvr2"
      }
    ],
    "assets": [
      {
        "id": "krea2-base",
        "kind": "unet",
        "path": "Krea2/krea2_turbo_fp8_scaled.safetensors",
        "sha256": "...",
        "required": true
      }
    ],
    "runtime": {
      "minimum_comfyui_version": null,
      "features": []
    }
  }
}
```

Optional dependencies should be associated with a named capability. If unavailable, controls and branches requiring that capability are disabled rather than silently ignored.

---

## 12. Control model

Each control has a stable semantic ID and one or more bindings.

```json
{
  "id": "sampling.main.steps",
  "label": "Steps",
  "description": "Number of sampling steps used by the main generation pass.",
  "group": "sampling.main",
  "order": 30,
  "type": "integer",
  "default": 8,
  "required": false,
  "tier": "advanced",
  "constraints": {
    "minimum": 1,
    "maximum": 50,
    "step": 1
  },
  "bindings": [],
  "conditions": [],
  "provenance": true
}
```

### 12.1 Required control fields

| Field | Meaning |
|---|---|
| `id` | Stable semantic identifier |
| `label` | User-facing label |
| `type` | Contract data type |
| `default` | Workflow-profile default |
| `tier` | `basic`, `advanced`, or `operator` |
| `bindings` | How the value changes the graph or request |

### 12.2 Recommended control fields

- `description`
- `group`
- `order`
- `required`
- `constraints`
- `options`
- `conditions`
- `conflicts_with`
- `requires`
- `capability`
- `sensitive`
- `provenance`
- `ui`
- `deprecated`

### 12.3 Supported data types

The initial schema should support:

- `string`
- `multiline_string`
- `integer`
- `number`
- `boolean`
- `enum`
- `seed`
- `image_upload`
- `mask_upload`
- `asset_selector`
- `array`
- `object`
- `resolution`
- `output_role_set`

A control type describes the application contract, not necessarily the underlying ComfyUI socket type.

---

## 13. Binding model

A control can have one or more bindings. Each binding has a strategy.

### 13.1 `patch_input`

Patch a named input in API-format workflow JSON.

```json
{
  "strategy": "patch_input",
  "selector": {
    "node_id": "127",
    "class_type": "ClownsharKSampler_Beta",
    "title": "Main Sampler"
  },
  "input": "steps"
}
```

### 13.2 `patch_widget`

Patch a UI-format widget before API export. This should be used only when no stable named API input exists.

```json
{
  "strategy": "patch_widget",
  "selector": {
    "node_id": "13",
    "class_type": "ResolutionMaster"
  },
  "widget_name": "width"
}
```

Positional widget indexes are a last resort and must include expected class version and current default assertions.

### 13.3 `transform`

Apply a deterministic transformation before patching.

```json
{
  "strategy": "patch_input",
  "selector": {
    "node_id": "11",
    "class_type": "EmptyLatentImage"
  },
  "input": "width",
  "transform": {
    "name": "snap_to_multiple",
    "arguments": {
      "multiple": 64
    }
  }
}
```

### 13.4 `derive`

A semantic control resolves to several effective values.

Example: an aspect-ratio preset derives width and height, then patches two inputs.

### 13.5 `upload_then_patch`

Upload a file through the application and ComfyUI, then patch the returned server-side reference into a loader node.

```json
{
  "strategy": "upload_then_patch",
  "upload_kind": "image",
  "selector": {
    "node_id": "44",
    "class_type": "LoadImage"
  },
  "input": "image"
}
```

### 13.6 `select_branch`

Compile graph state according to a branch definition rather than patching a scalar.

```json
{
  "strategy": "select_branch",
  "branch_id": "second_pass"
}
```

### 13.7 `select_variant`

Select a precompiled API graph variant.

This is recommended for early implementations when node-mode transformations are complex or fragile.

### 13.8 `request_policy`

Affects application behavior rather than pixels, such as requesting preview artifacts or selecting retention policy.

### 13.9 `fixed`

Documents a value that is intentionally not configurable for this profile.

---

## 14. Selector model and binding safety

A binding must never rely on node ID alone.

A selector may include:

```json
{
  "node_id": "127",
  "class_type": "ClownsharKSampler_Beta",
  "title": "Main Sampler",
  "group": "Main sampler",
  "subgraph_path": ["definitions", "subgraphs", "4"],
  "route_name": "MODEL_LORA",
  "expected_inputs": ["model", "seed", "steps", "cfg"],
  "expected_default": 8
}
```

At registration time, the service must confirm that the selector resolves to exactly one target and that the target has the expected input.

The node ID is a precise anchor for a pinned graph. The structural fields protect against accidentally patching a different node after graph edits.

---

## 15. Conditions and cross-field validation

Controls can be conditionally visible or valid.

```json
{
  "conditions": [
    {
      "when": {
        "control": "prompt.mode",
        "operator": "in",
        "value": ["image_to_prompt", "image_plus_text"]
      },
      "effect": "required"
    }
  ]
}
```

Supported effects should include:

- `visible`
- `hidden`
- `enabled`
- `disabled`
- `required`
- `forbidden`

Examples:

- reference image is required for image prompt modes;
- second-pass denoise is hidden when the second pass is disabled;
- SeedVR2 settings are disabled when the capability is unavailable;
- manual mask is required only for a manual-inpaint endpoint;
- batch size must equal one for a detailer branch that cannot process a batch.

Validation occurs before any ComfyUI job is queued.

---

## 16. Branch model

A branch describes graph topology rather than a primitive value.

```json
{
  "id": "second_pass",
  "label": "Second Pass",
  "default_enabled": true,
  "strategy": "graph_transform",
  "capability": null,
  "transforms": {
    "enable": [],
    "disable": []
  },
  "invariants": [
    "selected_image_must_feed_postprocess"
  ]
}
```

### 16.1 Supported branch strategies

- `precompiled_variant`
- `graph_transform`
- `separate_workflow`
- `interaction_required`
- `unsupported`

### 16.2 Recommended initial policy

Use precompiled variants for a small set of high-value combinations. Use a graph compiler only after the transformation system has strong regression coverage.

### 16.3 Manual interaction

A branch such as manual masked inpaint should declare that it is not a normal one-shot toggle:

```json
{
  "id": "manual_inpaint",
  "strategy": "separate_workflow",
  "interaction": {
    "kind": "image_and_mask",
    "requires_prior_artifact_role": "image.selected"
  }
}
```

The contract should not pretend that an editor-only `PreviewBridge` can be controlled headlessly without adaptation.

---

## 17. Stage model

Stages map raw node execution to user-meaningful progress and define where meaningful artifacts can become available.

```json
{
  "id": "base_generation",
  "label": "Creating base image",
  "sequence": 30,
  "node_selectors": [
    {
      "node_id": "127",
      "title": "Main Sampler"
    }
  ],
  "emits_output_ids": [
    "base_image"
  ],
  "cancellable_after_emission": true
}
```

Example stage IDs:

- `loading_models`
- `prompt_processing`
- `base_generation`
- `second_pass`
- `latent_upscale`
- `tiled_upscale`
- `automatic_detail`
- `manual_inpaint`
- `color_correction`
- `grain`
- `sharpen`
- `seedvr2`
- `saving`
- `collecting_outputs`

Recommended stage attributes include:

- stable stage ID and label;
- ordered sequence number;
- node selectors;
- output IDs emitted by the stage;
- whether cancellation is useful after an emitted artifact;
- whether the stage is terminal;
- optional estimated-cost or quality-tier metadata;
- conditions describing when the stage is active.

The application maps ComfyUI WebSocket node IDs to stage IDs through the contract. Stage events and artifact events are related but distinct: a stage can complete without emitting a public artifact, and an artifact becomes renderable only after its declared output mechanism has produced retrievable data.

---

## 18. Output model

Outputs are declared by semantic role and by their place in the workflow's progression.

```json
{
  "id": "final_image",
  "role": "image.final",
  "kind": "image",
  "selector": {
    "node_id": "156",
    "title": "CivitAI Image Saver"
  },
  "history_field": "images",
  "durable": true,
  "temporary": false,
  "canonical_on_success": true,
  "availability": "on_node_execution",
  "progression": {
    "sequence": 90,
    "quality_tier": "final_upscaled",
    "supersedes": [
      "base_image",
      "detail_checkpoint"
    ]
  },
  "batch_semantics": "one_per_batch_item",
  "retention": "durable"
}
```

### 18.1 Required output attributes

- stable output ID;
- semantic role;
- artifact kind;
- output-node selector or emission strategy;
- availability timing;
- durability and retention;
- progression sequence;
- canonical-on-success status or priority;
- behavior when execution is cancelled or fails;
- expected batch behavior.

### 18.2 Typical roles

- `prompt.final`
- `image.preview`
- `image.base`
- `image.refined`
- `image.detail_checkpoint`
- `image.selected`
- `image.upscaled`
- `image.final`
- `image.mask_preview`
- `metadata.generation`
- `metadata.asset_hashes`
- `telemetry.execution_time`

### 18.3 Canonical output rules

The application must not select a final result by dictionary order or filename guesswork.

The contract shall explicitly designate either:

- one output as `canonical_on_success`; or
- a deterministic priority rule when mutually exclusive branches provide different terminal sinks.

Canonical status is a **terminal job property**, not merely a property asserted by the first image that appears. Until the workflow succeeds, progressive artifacts are provisional. If the run is cancelled or fails before the terminal output appears, `canonical_artifact_id` remains null unless a separate product action explicitly promotes an intermediate.

If a canonical output produces a batch, all batch items are canonical siblings unless the request explicitly selects one index.

### 18.4 Progressive checkpoint outputs

A semantically meaningful intermediate may be declared as a progressive checkpoint:

```json
{
  "id": "detail_checkpoint",
  "role": "image.detail_checkpoint",
  "kind": "image",
  "selector": {
    "class_type": "FrontendWorkflowArtifact",
    "title": "Detail Checkpoint"
  },
  "availability": "on_node_execution",
  "temporary": true,
  "durable": false,
  "canonical_on_success": false,
  "usable_on_cancel": true,
  "persist_on_cancel": true,
  "progression": {
    "sequence": 50,
    "quality_tier": "refined",
    "supersedes": [
      "base_image"
    ],
    "expected_successor": "final_image"
  },
  "presentation": {
    "auto_render": true,
    "label": "Detail refinement",
    "status_text": "Refining and upscaling…"
  }
}
```

A progressive output should correspond to a stable, meaningful workflow state—not every internal latent, tile, sampler callback, or low-resolution binary preview frame.

### 18.5 Temporary UI outputs

Incidental comparer caches, mask previews, bridge previews, and sampler preview frames should normally be omitted from the public contract.

A `PreviewImage` or other temporary output may be promoted to a first-class progressive checkpoint when all of the following are true:

- the graph position has stable semantic meaning;
- the output is explicitly declared in the contract;
- the service can retrieve it while the job is still running;
- its retention and cancellation behavior are defined;
- it is not confused with the terminal final artifact.

Comparer cache URLs alone are not an acceptable contract because they are editor-oriented, temporary, and may not be emitted consistently through the headless API.

---

## 19. Progressive outputs, checkpoints, and cancellation

### 19.1 User experience model

A supported workflow may produce an ordered series such as:

```text
base generation
  → detail refinement
  → automatic detail/inpaint
  → final upscale/enhancement
```

The front-end should render each declared artifact as soon as it is available. Execution continues automatically unless the user cancels. The UI should clearly communicate that earlier images are previews or checkpoints and that a higher-quality terminal result is still being produced.

The front-end should display at least:

- the most recent available checkpoint;
- previous checkpoints as an optional timeline or comparison strip;
- the current semantic stage;
- the next expected stage when known;
- a Cancel action while cancellation remains meaningful;
- a clear distinction between `provisional`, `best_available`, and `final`.

### 19.2 Progression declaration

The optional top-level `progression` object defines workflow-wide behavior:

```json
{
  "progression": {
    "enabled": true,
    "ordered_output_ids": [
      "base_image",
      "detail_checkpoint",
      "final_image"
    ],
    "continue_automatically": true,
    "terminal_output_id": "final_image",
    "on_cancel": {
      "retain_available_outputs": true,
      "best_available_strategy": "highest_sequence_usable_on_cancel",
      "promote_to_canonical": false
    },
    "on_failure": {
      "retain_available_outputs": true,
      "best_available_strategy": "highest_sequence_usable_on_failure"
    }
  }
}
```

`continue_automatically` means that the workflow does not pause for approval. It gives the user a cancellation opportunity while later stages run. Arbitrary in-prompt pause and resume is not assumed.

A workflow that needs a hard approval gate should instead use separate staged jobs: one job produces a checkpoint, and a second workflow consumes that artifact only after approval.

### 19.3 Artifact availability events

The application service should publish an event immediately when a declared progressive output becomes retrievable:

```json
{
  "type": "artifact.available",
  "job_id": "job_01...",
  "artifact": {
    "id": "artifact_detail_0",
    "output_id": "detail_checkpoint",
    "role": "image.detail_checkpoint",
    "stage_id": "automatic_detail",
    "sequence": 50,
    "batch_index": 0,
    "state": "provisional",
    "usable_on_cancel": true,
    "preview_url": "/v1/artifacts/artifact_detail_0/content"
  },
  "job": {
    "status": "running",
    "current_stage": "seedvr2",
    "best_available_artifact_id": "artifact_detail_0",
    "canonical_artifact_id": null
  }
}
```

The service may derive this event from:

- a validated ComfyUI `executed` WebSocket message containing output data;
- a custom `FrontendWorkflowArtifact` node event;
- a standard preview or save node whose in-flight behavior has been verified;
- a fast history/output reconciliation triggered when the producing node completes.

Waiting for the entire prompt to finish before enumerating outputs does not satisfy progressive-delivery requirements.

### 19.4 Cancellation semantics

Cancellation is asynchronous and may race with node completion. The service shall model at least these states:

- `cancel_requested` — the user asked to stop, but execution may still be winding down;
- `cancelled_without_artifacts` — no usable checkpoint was emitted;
- `cancelled_with_artifacts` — one or more usable checkpoints were retained;
- `succeeded` — terminal output completed before cancellation took effect;
- `failed_with_artifacts` — a later stage failed after a usable checkpoint was emitted.

When cancellation is requested:

1. The service records the request time and current stage.
2. It sends ComfyUI queue deletion or `/interrupt` as appropriate.
3. It continues processing WebSocket and history events until terminal state is reconciled.
4. It preserves every already-emitted output whose policy requires retention.
5. It selects `best_available_artifact_id` using the contract's strategy.
6. It does not mark that artifact canonical unless the product offers a separate explicit “Use this result” action.

Cancellation should remain available after an early checkpoint appears and while an expensive later stage such as tiled upscale or SeedVR2 is running. The UI should not promise that cancellation will be instantaneous or that no additional node can finish after the request.

### 19.5 Artifact state and lineage

Progressive artifacts use one of these states:

- `provisional` — valid checkpoint while the workflow continues;
- `superseded` — a later checkpoint is available;
- `best_available` — highest usable checkpoint after cancellation or failure;
- `final` — terminal canonical result after successful completion;
- `partial` — emitted from an incomplete or partially failed stage and not ordinarily selectable.

Every later image should retain lineage to its immediate predecessor where practical. This enables the front-end to present a coherent visual timeline and lets audit records show which intermediate was refined or upscaled into the final result.

### 19.6 Storage policy

Progressive rendering does not require indefinite storage. A checkpoint can be:

- temporary until the job ends;
- retained for the job's normal TTL;
- promoted to durable storage on cancellation;
- always durable;
- discarded after a later output supersedes it, when the contract and client request permit.

The default recommendation is:

- retain declared checkpoints for the job TTL;
- retain the best available checkpoint when the run is cancelled or fails;
- durably retain the terminal final according to normal output policy;
- avoid persisting incidental sampler frames.

---

## 20. Presets

A preset resolves to a complete set of semantic controls.

```json
{
  "id": "standard",
  "label": "Standard",
  "values": {
    "sampling.main.steps": 8,
    "sampling.main.cfg": 1.0,
    "sampling.second_pass.enabled": true,
    "sampling.second_pass.denoise": 0.2,
    "post.seedvr2.enabled": false
  }
}
```

Presets should be versioned with the workflow contract and expanded into effective controls before execution.

The application stores both the selected preset and the resolved values.

---

## 21. Dynamic options

Some options are determined by the runtime.

A control can declare an option provider:

```json
{
  "type": "enum",
  "options": {
    "source": "comfyui_object_info",
    "selector": {
      "class_type": "KSamplerAdvanced"
    },
    "input": "sampler_name",
    "allowlist": ["euler", "dpmpp_2m"]
  }
}
```

The service computes:

```text
resolved options = runtime options intersect contract allowlist intersect operator policy
```

For model assets, the public value should be a stable application asset ID, not an arbitrary local path.

---

## 22. Application API

A recommended public API is:

### 22.1 Workflow discovery

```text
GET /v1/workflow-profiles
GET /v1/workflow-profiles/{workflow_id}
GET /v1/workflow-profiles/{workflow_id}/contract
GET /v1/workflow-profiles/{workflow_id}/capabilities
```

The contract endpoint returns the resolved contract, including unavailable capabilities and operator limits.

### 22.2 Validation

```text
POST /v1/workflow-profiles/{workflow_id}/validate-request
```

This endpoint validates a semantic request without queueing a generation.

### 22.3 Generation

```text
POST /v1/generations
GET /v1/generations/{job_id}
POST /v1/generations/{job_id}/cancel
GET /v1/generations/{job_id}/artifacts
GET /v1/generations/{job_id}/events
```

`/events` may be implemented with Server-Sent Events or an application WebSocket. It publishes semantic stage changes, progressive artifact availability, cancellation reconciliation, errors, and terminal completion. The browser should not connect directly to the internal ComfyUI WebSocket.

### 22.4 Uploads

```text
POST /v1/uploads/images
POST /v1/uploads/masks
```

The client receives application upload IDs. The application maps them to ComfyUI server-side references during compilation.

---

## 23. Generation request contract

A semantic request should look like this:

```json
{
  "workflow_id": "krea2-uncensored-v1",
  "workflow_version": "1.0.0",
  "controls": {
    "prompt.mode": "enhance_text",
    "prompt.text": "cinematic portrait in evening light",
    "prompt.processor.seed": 451788923299051,
    "size.mode": "advanced",
    "size.width": 1080,
    "size.height": 1920,
    "generation.seed": 1003949590422403,
    "sampling.main.steps": 8,
    "sampling.second_pass.enabled": true,
    "sampling.second_pass.denoise": 0.2,
    "post.seedvr2.enabled": false
  },
  "requested_outputs": [
    "prompt.final",
    "image.base",
    "image.refined",
    "image.final",
    "metadata.generation"
  ],
  "output_delivery": {
    "progressive": true,
    "auto_render": true,
    "retain_on_cancel": true
  }
}
```

Unknown controls are rejected. Missing controls receive contract defaults only when allowed. A request may decline progressive delivery, but the workflow contract still determines which outputs exist and which one can become canonical.

---

## 24. Compilation and execution pipeline

For every generation request, the service shall:

1. Resolve the requested workflow profile and version.
2. Verify the stored workflow and contract hashes.
3. Resolve runtime capability state.
4. Validate all semantic controls and cross-field rules.
5. Resolve presets and derived values.
6. Resolve seed policies to effective integers.
7. Upload images or masks when required.
8. Clone the immutable approved graph.
9. Apply scalar input patches.
10. Apply graph-branch selection or choose a precompiled variant.
11. Activate only approved output sinks.
12. Validate the compiled graph against selectors and `/object_info`.
13. Compute a compiled graph hash.
14. Submit the API workflow to `/prompt`.
15. Store the returned `prompt_id`.
16. Monitor `/ws` and map nodes to semantic stages.
17. When a declared progressive output node executes, retrieve or persist its payload immediately.
18. Publish `artifact.available` and update `best_available_artifact_id` while the job remains running.
19. Accept cancellation requests throughout eligible later stages and reconcile cancellation races.
20. Reconcile terminal completion through `/history/{prompt_id}`.
21. Enumerate every output node and artifact entry, including those already emitted progressively.
22. De-duplicate and classify artifacts by contract role, batch index, and lineage.
23. Mark the declared terminal output canonical only after successful completion.
24. Retrieve and persist requested artifacts according to completion, cancellation, and failure policy.
25. Record requested controls, effective controls, seeds, hashes, warnings, errors, and artifact-event timing.

A queued prompt is an immutable execution snapshot. Later UI edits do not modify that run.

---

## 25. Normalized response

A successful response should include:

```json
{
  "job_id": "job_01...",
  "workflow": {
    "id": "krea2-uncensored-v1",
    "version": "1.0.0",
    "contract_hash": "...",
    "compiled_graph_hash": "..."
  },
  "status": "running",
  "current_stage": "seedvr2",
  "requested_controls": {},
  "effective_controls": {},
  "effective_seeds": {},
  "best_available_artifact_id": "artifact_detail_0",
  "canonical_artifact_id": null,
  "artifacts": [
    {
      "id": "artifact_detail_0",
      "output_id": "detail_checkpoint",
      "role": "image.detail_checkpoint",
      "state": "provisional",
      "sequence": 50,
      "usable_on_cancel": true
    }
  ],
  "warnings": [],
  "errors": []
}
```

While a job is running, `canonical_artifact_id` is normally null. After successful terminal completion it points to the declared final output. After cancellation or late-stage failure, `best_available_artifact_id` may identify the most advanced retained checkpoint without claiming that the workflow completed.

Every image artifact should include:

- semantic role;
- source node;
- filename, subfolder, and ComfyUI storage type;
- MIME type;
- dimensions;
- batch index;
- byte size;
- cryptographic hash;
- durability and canonical flags;
- progression sequence and artifact state;
- availability and emission timestamps;
- `usable_on_cancel` and retention behavior;
- parent artifact lineage where applicable.

---

## 26. Versioning and migration

### 26.1 Breaking changes

Increment the workflow major version when:

- a stable control ID is removed or changes meaning;
- an output role changes meaning;
- defaults change in a materially incompatible way;
- branch behavior changes significantly;
- the workflow changes from one-shot to interactive or vice versa.

### 26.2 Compatible changes

Increment the minor version when:

- optional controls are added;
- a new output role is added;
- a new optional capability is introduced;
- selectors are updated without changing public semantics.

### 26.3 Patch changes

Increment the patch version for:

- corrected selectors;
- documentation fixes;
- equivalent dependency pin updates;
- non-semantic graph cleanup.

### 26.4 Migration rule

A graph hash mismatch shall never be accepted silently. The workflow must be revalidated and its contract version updated or explicitly reapproved.

---

## 27. Security requirements

The application shall:

- keep ComfyUI behind the application service;
- never expose arbitrary local model or output paths;
- use model and LoRA allowlists;
- sanitize output prefixes and subfolders;
- MIME-check and safely decode uploaded images;
- cap uploaded bytes and decompressed pixels;
- authorize every job and artifact request;
- isolate tenant artifacts;
- treat embedded workflow metadata as potentially sensitive;
- use short retention for temporary previews;
- log contract and graph hashes for audit;
- reject unknown custom-node schemas in strict mode.

The contract itself must not contain secrets.

---

## 28. Resource-governance requirements

The contract should describe safe limits or reference an operator policy:

- maximum initial pixels;
- maximum final pixels;
- maximum batch size;
- maximum sampler steps;
- maximum upscale factor;
- maximum detected regions;
- maximum detail cycles;
- maximum SeedVR2 resolution;
- supported GPU profiles;
- per-stage and total timeouts;
- branch-specific concurrency limits.

The service computes predicted cost before queueing when possible.

---

## 29. Error model

Errors should be classified by stage and semantic field.

Recommended categories:

- `contract_invalid`
- `workflow_hash_mismatch`
- `binding_not_found`
- `binding_ambiguous`
- `runtime_dependency_missing`
- `asset_missing`
- `control_validation_failed`
- `branch_compilation_failed`
- `upload_failed`
- `comfyui_prompt_rejected`
- `execution_failed`
- `execution_interrupted`
- `output_missing`
- `output_unclassified`
- `artifact_persistence_failed`
- `progressive_artifact_unavailable`
- `cancellation_requested`
- `cancellation_race_completed`
- `cancelled_with_artifacts`
- `cancelled_without_artifacts`
- `resource_limit_exceeded`

Raw ComfyUI diagnostics should be retained internally and mapped to user-meaningful stage names.

---

## 30. How the two discovered workflows map to this design

### 30.1 Moody Krea 2 Simple workflow

The Moody workflow requires contract support for:

- prompt, resolution, batch, seed, LoRA, and two sampler stages;
- latent upscale and optional learned/tiled upscale;
- automatic detector/detail controls;
- manual mask/inpaint as a separate or interactive operation;
- optional SeedVR2 processing;
- rgthree node and group bypass state;
- multiple temporary previews and potentially multiple durable image outputs;
- an ordered progression such as first-pass preview → second-pass/detail output → optional detail/inpaint output → final SeedVR2/upscaled output;
- immediate publication of selected intermediate images while later stages continue;
- retention of the highest usable checkpoint if the user cancels during a later expensive stage;
- explicit terminal canonical output designation.

For this workflow, the earlier images should not be treated merely as debug output when they are intentionally exposed to show generation direction. The adapted graph should add or promote explicit checkpoint outputs after the meaningful stages selected by the workflow author. The final upscaled or SeedVR2-enhanced output remains `canonical_on_success`.

The manual `PreviewBridge` branch should not be declared as an ordinary Boolean one-shot feature. It should be represented as `separate_workflow` or `interaction_required`.

### 30.2 Krea 2 Uncensored workflow

The Krea 2 Uncensored workflow requires contract support for:

- manual, enhanced-text, image-to-prompt, and image-plus-text modes;
- a prompt-processing seed separate from the image-generation seed;
- simple and advanced size modes;
- model patch and LoRA controls;
- main sampling and optional second-pass refinement;
- color, grain, sharpen, and SeedVR2 branches;
- metadata-aware and standard saver policies;
- final prompt as a non-media output;
- base/refined/final image roles, while recognizing that only final is durable by default;
- optional adaptation of base and refined images into declared progressive checkpoints rather than relying on comparer cache files.

Its optional Noise group must be described as image-space grain, not a diffusion-variation generator. If the front-end promises live base/refined previews, the adapted workflow should include explicit checkpoint emitters at those graph positions.

---

## 31. Recommended phased implementation

### Phase 1: Read-only contracts

- implement the custom contract node;
- embed manifests in approved workflows;
- parse and validate manifests;
- expose contract and capability endpoints;
- do not yet compile arbitrary branch combinations.

### Phase 2: Scalar controls, fixed variants, and progressive outputs

- support prompt, dimensions, seeds, steps, CFG, denoise, and approved model choices;
- maintain tested API graph variants for optional branches;
- add explicit output emitters for meaningful base/refined/final stages;
- publish stage and `artifact.available` events;
- normalize provisional, best-available, and canonical artifacts;
- test cancellation after each declared checkpoint.

### Phase 3: Uploads and multi-step operations

- add reference-image uploads;
- add prompt-preview endpoint;
- add manual image-and-mask inpaint as a separate workflow;
- preserve lineage across jobs.

### Phase 4: Graph compiler

- implement validated graph transformations;
- reduce the number of precompiled variants;
- add stronger selector and topology assertions;
- add regression fixtures for every supported branch combination.

### Phase 5: Contract-authoring tools

- add a custom ComfyUI front-end editor for the contract node;
- provide a contract linter;
- generate draft controls from workflow discovery;
- generate test fixtures and adapter skeletons.

---

## 32. Acceptance criteria

The contract system is ready for its first production workflow when:

1. The workflow contains exactly one valid `FrontendWorkflowContract` node.
2. The original UI graph and approved API graph are both hashed and stored.
3. Every public control has a stable semantic ID.
4. Every binding resolves uniquely and passes structural assertions.
5. Every branch is represented by a tested strategy.
6. Every required node class and model asset is verified.
7. The front-end can render the control surface from the resolved contract alone.
8. A semantic request can be compiled without the client knowing node IDs.
9. All returned artifacts are enumerated and classified.
10. Every declared progressive checkpoint is delivered while the job is still running, not only after terminal history reconciliation.
11. The front-end can render the ordered base/refined/final progression from contract metadata alone.
12. One canonical output is selected explicitly only after the job type's terminal stage succeeds.
13. Cancellation after an intermediate output preserves the configured best-available artifact and does not mislabel it as final.
14. Cancellation races are reconciled against ComfyUI history and execution events.
15. Requested and effective values are persisted.
16. Unsupported controls fail clearly rather than being ignored.
17. Workflow or schema drift fails closed.
18. Cancellation, missing dependencies, no-output jobs, progressive-output jobs, and multiple-output jobs are tested.
19. The workflow passes fixed-seed regression tests in the pinned container.

---

## 33. Illustrative contract fragment

The following fragment demonstrates the intended style. It is illustrative rather than a complete manifest.

```json
{
  "kind": "comfyui.frontend.workflow-contract",
  "contract_schema_version": "1.1.0",
  "workflow": {
    "id": "krea2-uncensored-v1",
    "display_name": "Krea 2 Uncensored",
    "version": "1.0.0",
    "ui_graph_sha256": "REQUIRED",
    "api_graph_sha256": "REQUIRED"
  },
  "controls": [
    {
      "id": "prompt.mode",
      "label": "Prompt Mode",
      "type": "enum",
      "default": "manual",
      "tier": "basic",
      "options": {
        "values": [
          "manual",
          "enhance_text",
          "image_to_prompt",
          "image_plus_text"
        ]
      },
      "bindings": [
        {
          "strategy": "select_branch",
          "branch_id": "prompt_mode"
        }
      ]
    },
    {
      "id": "prompt.text",
      "label": "Prompt",
      "type": "multiline_string",
      "default": "",
      "tier": "basic",
      "bindings": [
        {
          "strategy": "patch_input",
          "selector": {
            "node_id": "46",
            "class_type": "PrimitiveStringMultiline",
            "title": "POSITIVE PROMPT"
          },
          "input": "text"
        }
      ]
    },
    {
      "id": "sampling.second_pass.enabled",
      "label": "Second Pass",
      "type": "boolean",
      "default": true,
      "tier": "basic",
      "bindings": [
        {
          "strategy": "select_branch",
          "branch_id": "second_pass"
        }
      ]
    }
  ],
  "branches": [
    {
      "id": "second_pass",
      "strategy": "precompiled_variant",
      "variants": {
        "true": "with_second_pass",
        "false": "without_second_pass"
      }
    }
  ],
  "outputs": [
    {
      "id": "base_image",
      "role": "image.base",
      "kind": "image",
      "selector": {
        "class_type": "FrontendWorkflowArtifact",
        "title": "Base Image Checkpoint"
      },
      "availability": "on_node_execution",
      "temporary": true,
      "canonical_on_success": false,
      "usable_on_cancel": true,
      "persist_on_cancel": true,
      "progression": {
        "sequence": 30,
        "quality_tier": "base"
      }
    },
    {
      "id": "refined_image",
      "role": "image.refined",
      "kind": "image",
      "selector": {
        "class_type": "FrontendWorkflowArtifact",
        "title": "Refined Image Checkpoint"
      },
      "availability": "on_node_execution",
      "temporary": true,
      "canonical_on_success": false,
      "usable_on_cancel": true,
      "persist_on_cancel": true,
      "progression": {
        "sequence": 50,
        "quality_tier": "refined",
        "supersedes": ["base_image"]
      }
    },
    {
      "id": "final_image",
      "role": "image.final",
      "kind": "image",
      "selector": {
        "node_id": "156",
        "title": "Image Saver"
      },
      "availability": "on_node_execution",
      "durable": true,
      "canonical_on_success": true,
      "progression": {
        "sequence": 90,
        "quality_tier": "final_upscaled",
        "supersedes": ["base_image", "refined_image"]
      },
      "batch_semantics": "one_per_batch_item"
    },
    {
      "id": "final_prompt",
      "role": "prompt.final",
      "kind": "text",
      "selector": {
        "node_id": "45",
        "title": "Final prompt"
      },
      "durable": false,
      "canonical_on_success": false
    }
  ],
  "progression": {
    "enabled": true,
    "ordered_output_ids": [
      "base_image",
      "refined_image",
      "final_image"
    ],
    "continue_automatically": true,
    "terminal_output_id": "final_image",
    "on_cancel": {
      "retain_available_outputs": true,
      "best_available_strategy": "highest_sequence_usable_on_cancel",
      "promote_to_canonical": false
    }
  }
}
```

---

## 34. Final recommendation

Adopt an embedded, versioned manifest node plus an application-owned compiler and workflow registry.

Do not make the node a universal parameter router. The durable contract is the semantic declaration: what can be controlled, how it maps to a pinned graph, what dependencies it requires, how branches are compiled, and what artifacts the workflow produces.

This approach supports both discovered Krea 2 workflows while remaining general enough for future still-image, image-to-image, inpaint, upscale, and eventually video workflows. Progressive checkpoints should be treated as first-class contract artifacts whenever they represent meaningful visual milestones, allowing the front-end to show direction early, preserve useful work, and give the user a genuine opportunity to stop expensive downstream processing.
