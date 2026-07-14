# Playbook: convert a community ComfyUI workflow into an Image Frontend generation source

## Purpose

Use this document as the operating prompt for an AI model that can inspect and
modify the user's actual ComfyUI workflow files or open ComfyUI graph.

Given the name or path of a community-made workflow, the model must return a
modified duplicate that:

1. preserves the original community workflow;
2. preserves the duplicate's processing behavior;
3. exposes a small, high-confidence public input surface with Image Frontend
   parameter nodes;
4. declares the authoritative final image and useful earlier image stages;
5. keeps every existing native output and metadata result available;
6. places all Image Frontend input and output declarations in one clearly
   labeled graph group;
7. is ready for the user to open, test, and publish from ComfyUI.

The expected product of this playbook is an editable ComfyUI workflow JSON, not
an API graph, generic adapter, application deployment, or platform redesign.

## Invocation template

The user should be able to provide this playbook with a short request such as:

```text
Apply the community-workflow conversion playbook.

Workflow: <workflow name or exact editable workflow path>
ComfyUI root: <known ComfyUI root, if not already established>
Original: <original workflow path, if different>
Output duplicate: <desired duplicate filename, if not already duplicated>

Prepare the duplicate for Image Frontend authoring. Do not publish it. Return
the modified workflow and a conversion report so I can test and publish it in
ComfyUI myself.
```

If the ComfyUI root and workflow location are already known from the current
session, do not ask for them again. Do not scan unrelated filesystem locations
for other ComfyUI installations.

## Role of the converting model

You are a workflow interface compiler working beside the user's real ComfyUI
installation. The existing graph determines what can safely be exposed.

Your job is not to improve the artwork, modernize the community graph, replace
custom nodes, add new creative features, or simplify the workflow. Your job is
to augment a duplicate with a deterministic public interface while preserving
the community graph's behavior.

The graph wiring answers where data goes. Human-readable Image Frontend metadata
answers what that data means to an external application.

## Non-negotiable boundaries

- Never modify the original community workflow.
- Work only on the named workflow or its named duplicate.
- If only an original exists, create a clearly named duplicate before graph
  changes.
- Never modify model files, workflow runtime data, generated images, secrets, API
  keys, environment files, or unrelated workflows.
- Do not install packages or custom nodes, restart ComfyUI, change startup flags,
  deploy the frontend, or publish the workflow unless the user separately asks.
- Do not enable bypassed, muted, optional, or dead community branches merely to
  make them public.
- Do not add a negative prompt, upscale toggle, second pass, model selector, or
  other feature that the saved workflow does not already use.
- Do not replace existing SaveImage, PreviewImage, comparer, mask, text, or
  metadata output nodes.
- Do not treat declared outputs as an allowlist. Existing native history results
  must remain available.
- Do not expose arbitrary node IDs, graph patches, filesystem paths, or binding
  paths as public caller-controlled values.
- Do not create a shared in-memory parameter dictionary or a `/set_parameters`
  endpoint. Values must remain prompt-local.
- Do not hand-edit the generated `.api.json` as a substitute for modifying and
  saving the editable workflow.

## Required inputs and output

### Required input

At minimum, receive one of:

- an exact editable workflow JSON path; or
- a unique workflow name in the already-known ComfyUI workflows directory.

### Optional input

The user may also supply:

- public controls they definitely want;
- controls they definitely do not want;
- an authoritative final image stage;
- important prototype or comparison stages;
- desired public IDs or labels;
- a desired duplicate filename.

User-supplied meanings override automatic suggestions when they remain type-safe
and consistent with the graph.

### Required output

Return:

1. the path to the modified editable duplicate;
2. the original and modified SHA-256 hashes;
3. a table of every public input and its graph destinations;
4. a table of every declared image output and its source stage;
5. a list of existing native outputs that remain untouched;
6. a list of ambiguous or skipped candidates;
7. structural validation results;
8. exact instructions for reopening and testing the workflow in ComfyUI;
9. publication status, which must remain “not published” unless separately
   authorized.

## Operating modes

### Guided mode

Use guided mode when the user is available and two or more graph paths have
plausible but materially different meanings. Ask the user to confirm only the
ambiguous semantics, not facts that can be discovered from the graph.

Examples requiring confirmation include:

- two unrelated positive prompts;
- several independent seeds;
- several active SaveImage branches with no clear final stage;
- width and height that can come from either text controls or an uploaded image;
- a Boolean that may mean branch selection, preview selection, or node bypass;
- two LoRA strengths with different artistic purposes.

### Autonomous conversion mode

If the user asks for a completed conversion without a dialogue, proceed using
only high-confidence mappings.

In autonomous mode:

- expose one primary positive prompt only when its conditioning path is clear;
- expose primary width and height only when their active source and destination
  are clear;
- expose one shared seed only when the existing graph already couples those
  consumers;
- declare a final image only when the active durable-save or explicit final path
  is clear;
- declare earlier image stages only when they already execute and are visibly
  used by the active graph, preview nodes, comparers, or downstream processing;
- skip ambiguous advanced controls instead of guessing;
- report every skipped candidate and the reason;
- still return the fully modified duplicate with the safe subset.

## Phase 1: inspect the actual environment

Before changing the workflow, record:

1. ComfyUI backend version or commit;
2. ComfyUI frontend version;
3. Python version and executable used by ComfyUI;
4. workflow path and user/workflow root;
5. whether `/object_info`, `/prompt`, `/ws`, `/history/{prompt_id}`, `/view`, and
   upload routes are present;
6. whether the Image Frontend custom node pack is loaded;
7. the installed definitions returned by `/object_info` for every CIF node you
   intend to use;
8. custom node packs required by the workflow;
9. referenced models, LoRAs, VAEs, CLIPs, control models, and upscalers;
10. whether the workflow uses subgraphs, old groups, reroutes, Set/Get nodes,
    Anything Everywhere, Any Switch, custom serialization, or frontend-only
    extensions.

Use `/object_info` as the installed-instance type system. Do not assume a node's
inputs, choices, bounds, or output types from memory.

The required Image Frontend nodes for the common scalar/image-output path are:

- `CIFTextParameter` — Image Frontend Text Parameter;
- `CIFIntegerParameter` — Image Frontend Integer Parameter;
- `CIFDecimalParameter` — Image Frontend Decimal Parameter;
- `CIFBooleanParameter` — Image Frontend Boolean Parameter;
- `CIFSeedParameter` — Image Frontend Seed Parameter;
- `CIFChoiceParameter` — Image Frontend Choice Parameter;
- `CIFPublishImage` — Publish Image to Image Frontend;
- optionally `CIFPublishText` for explicitly authored text results.

If a required node is absent from `/object_info`, do not install or invent it.
Report the missing capability and prepare only the mappings supported by the
installed instance.

The current pack provides a finite mapped choice parameter for destination
`COMBO` inputs, but it does not provide a media-upload parameter. Do not fake
sampler/model choices with an unrestricted string and do not expose arbitrary
image paths. A choice node must contain an explicit public-ID-to-private-binding
map, and publication must expose only the public IDs and labels.

## Phase 2: protect the original and snapshot the duplicate

1. Resolve the exact original and duplicate paths.
2. Confirm they are not the same file.
3. If a duplicate must be created, choose a descriptive filename and preserve
   the original byte-for-byte.
4. Record SHA-256, size, modification time, node count, link count,
   `last_node_id`, and `last_link_id` for both.
5. Create a reversible backup of the duplicate before editing.
6. Parse the duplicate as JSON and verify that its root structure is a ComfyUI
   editable workflow, not merely an API prompt.
7. If the workflow is currently open in a browser, warn that the user must reload
   it from disk after filesystem edits and must not save the stale browser copy.

Do not normalize, reformat, or rewrite unrelated workflow content merely for
convenience. Preserve unknown keys and extension-owned data.

## Phase 3: build a graph inventory

Construct explicit node and link indexes for the root graph and every subgraph.
For each node, capture:

- node ID and class type;
- title;
- position and group membership by bounding box;
- mode, including active, muted, bypassed, or otherwise disabled;
- inputs, widgets, values, and incoming links;
- output types and outgoing links;
- whether `/object_info` marks it as an output node;
- whether it is part of a frontend extension with special serialization.

Create an execution-oriented inventory rather than relying on node titles.
Trace paths through reroutes, Set/Get pairs, global-value nodes, switch nodes,
subgraph boundaries, arithmetic nodes, and custom helpers.

Do not infer that a node titled “Final” is active or that a node titled “Seed”
controls every sampler. Verify links and modes.

## Phase 4: discover candidate inputs

Inspect the following categories in order.

### Positive prompt

Trace every literal or upstream `STRING` that contributes to positive
conditioning consumed by an active sampler, refiner, detailer, or upscaler.

Determine whether multiple prompt encoders are:

- the same prompt fanned out;
- base and refiner versions of one prompt;
- independent subject/style prompts;
- detailer-specific prompts;
- fixed internal enhancement prompts that should remain private.

Expose only the confirmed public prompt path. Preserve the workflow's existing
dynamic-prompt setting. Do not introduce a negative prompt when the workflow
currently obtains negative conditioning through fixed text, zero conditioning,
or another private path.

### Width and height

Trace active dimension values to their actual consumers. Candidate sources may
include:

- `EmptyLatentImage` widgets;
- a resolution helper;
- a Resolution Master node;
- an Image Size subgraph;
- aspect ratio or megapixel calculations;
- an image-derived width and height;
- an Any Switch or another branch selector.

Expose width and height only at the authoritative source for the active base
generation. Preserve downstream arithmetic, upscale factors, tile calculations,
and aspect handling.

If dimensions are image-derived, do not force direct width/height controls into
the graph without user approval.

An Any Switch is not “disabled” merely because it has unused sockets. Determine
which connected input wins under that installed node's semantics. Do not add a
new input to a switch unless doing so preserves the intended selection order.

### Seed

Inventory every seed consumed by active samplers, refiners, detailers, and
upscalers. Trace arithmetic offsets and fan-out.

The existing wiring defines coupling:

- one upstream seed feeding several consumers means one public seed should fan
  out to the same places;
- arithmetic offsets should remain downstream of the public seed;
- independent seeds should remain separate unless the user explicitly asks to
  couple them.

For a common shared seed, use `CIFSeedParameter` with:

- `parameter_id`: `seed`;
- `semantic_role`: `seed`;
- `required`: `false`;
- `default_mode`: `random`;
- native ComfyUI seed control: `randomize`;
- minimum: `0`;
- maximum: the installed node's maximum;
- step: `1`.

The visible numeric value is a concrete local execution value, not a declaration
that the seed is fixed. ComfyUI must receive an integer. The eventual adapter
must resolve an omitted, blank, or `null` public seed to a fresh concrete integer
before `POST /prompt`; an explicit integer means fixed/reproducible.

Do not replace offset nodes with duplicated public seed nodes.

### Batch size

Expose batch size only when the active generation path has a direct, safe batch
control and the resulting output nodes correctly handle every batch item.

### Steps, CFG, denoise, LoRA strength, and similar numeric controls

Expose an advanced numeric control only when:

- its destination is unambiguous;
- the existing value is a genuine user-facing control rather than an internal
  tuned constant;
- safe bounds can be derived from `/object_info` and the workflow;
- exposing it will not bypass workflow-specific safety or branch logic.

Preserve the current value as the local/default value. Use exact meaningful IDs.
For a strength that always controls one fixed LoRA, a model-specific ID such as
`knpv4_1_strength` is appropriate. If a public choice selects which LoRA the
same loader applies, use a selection-neutral companion ID such as
`lora_strength`; the choice and strength must feed the same loader.

### Boolean switches

A public Boolean must connect to a real execution-safe Boolean input or switch
node already present in the graph.

Do not use a Boolean declaration as a promise to mutate node mode, bypass, mute,
or frontend state after API compilation. If the workflow lacks an actual switch,
skip the Boolean or prepare separate workflow variants.

### Model, LoRA, sampler, scheduler, and other finite choices

Expose a finite choice only through `CIFChoiceParameter`. Never substitute an
unrestricted string parameter for a model name, LoRA filename, sampler,
scheduler, VAE, or another installed destination choice.

A choice has two distinct identities:

- a stable public option ID used by the Image Frontend and API caller;
- a private binding value accepted by the destination ComfyUI input.

The node's `value` is the current and default **public option ID**. It is not a
private filename and it is not an override layered on top of another selection.
For an optional choice with a usable default, set `required` to `false`; an
omitted request then resolves to this saved `value`.

The node's trusted `options_json` maps every public ID to one private binding:

```json
[
  {
    "id": "knp_v4_1",
    "label": "KNP v4.1",
    "binding": "Krea2/KNPV4.1_pre.safetensors",
    "default_strength": 1.0
  },
  {
    "id": "knp_v3_1",
    "label": "KNP v3.1",
    "binding": "Krea2/KNPV3_1.safetensors",
    "default_strength": 0.5
  }
]
```

Field meanings are:

- `id`: safe, stable public value sent by the application;
- `label`: human-readable text for the Image Frontend;
- `binding`: exact trusted value sent to the ComfyUI destination;
- `default_strength`: optional finite companion-strength hint, not an automatic
  graph mutation.

Public IDs follow the same lowercase identifier rules as `parameter_id` and
must be unique. Labels must be nonempty and no longer than 120 characters.
Private bindings must be nonempty, unique, no longer than 1,000 characters, and
must not contain a NUL character. `options_json` is limited to 50,000 characters
and 100 options. The saved `value` must identify exactly one declared option.

Obtain private bindings from the installed destination definition in
`/object_info`, not from memory or only from text visible in the graph. Every
binding must still be admitted by every connected destination at publication
time. If a model or LoRA is missing from the installed destination choices,
report it and do not publish the declaration as valid.

The runtime node deliberately emits `*`: legacy ComfyUI combo inputs are typed
as their literal option arrays rather than a portable symbolic `COMBO` type.
This wildcard is safe only because publication validates the finite mapping
against each connected installed destination. Do not reuse it as a general
`ANY` input or bypass that validation.

When the community workflow presents several apparent choices inside a custom
frontend widget, inspect its actual node inputs before wiring. For example,
rgthree Power LoRA Loader rows are independent toggle records, even when the
title says “enable one only”; they are not one mutually exclusive combo input.
Do not connect a choice node to opaque extension-owned widget records.

Prefer an existing connectable destination such as
`LoraLoaderModelOnly.lora_name`. Adding a canonical loader or switch changes the
execution graph and is permitted only when the requested conversion requires
it, the default effective behavior can be proven equivalent, and the user has
approved that augmentation. Never leave both an old enabled LoRA row and a new
loader applying the same LoRA, because that stacks effects instead of exposing
one selection.

If a choice has a companion strength:

- the choice output and numeric strength output must feed the same loader;
- an explicitly supplied strength wins;
- if strength is omitted, the adapter may use the selected option's
  `default_strength`;
- otherwise the numeric parameter's ordinary default applies;
- local ComfyUI selection does **not** automatically rewrite the numeric
  strength widget, so test nondefault choices with the intended strength;
- ComfyUI must receive a concrete public choice ID at the choice node and a
  concrete numeric strength at the numeric node for every queued prompt.

## Phase 5: choose the public input surface

Favor the smallest useful surface. A typical high-confidence first conversion
contains:

- positive prompt;
- width;
- height;
- shared primary seed.

Add toggles or advanced parameters only when their graph meaning is clear.

Do not expose a control merely because it exists. The public surface should
represent what an ordinary frontend user needs, not every tunable constant in
the community workflow.

In autonomous mode, use these metadata defaults:

| Parameter kind | Required | Advanced | Group | Typical order |
| --- | --- | --- | --- | --- |
| Positive prompt | `true` | `false` | `Basic` | 10 |
| Width | `true` | `false` | `Basic` | 20 |
| Height | `true` | `false` | `Basic` | 30 |
| Seed | `false` | `false` | `Basic` | 40 |
| Primary Boolean | `false` | `false` | `Basic` | 50+ |
| Finite model/LoRA choice | `false` | `true` | `Advanced` | 50+ |
| Expert numeric tuning | `false` | `true` | `Advanced` | 60+ |

Adjust order to keep the UI intentional and stable.

## Phase 6: create parameter declarations

Use one typed parameter node per public value unless an existing confirmed
fan-out should share one node.

Every parameter declaration must have:

- `parameter_id`: stable public key;
- `instance_uuid`: unique canonical lowercase UUID for that node instance;
- `label`: concise human-readable UI label;
- `description`: what the value changes in this specific workflow;
- `semantic_role`: one installed supported role;
- `required`: public request policy;
- `advanced`: frontend placement hint;
- `group`: usually `Basic` or `Advanced`;
- `order`: stable nonnegative display order;
- a local `value` copied from the workflow's current effective value;
- numeric minimum, maximum, and step where applicable.

Public IDs must:

- start with a lowercase letter;
- contain only lowercase letters, digits, and underscores;
- contain at most 64 characters;
- remain stable when a node title or layout changes.

Installed semantic roles are currently:

```text
custom
positive_prompt
negative_prompt
seed
width
height
batch_size
steps
cfg
denoise
sampler
scheduler
model
lora
upscale
```

Use `custom` only when no more specific installed role applies.

### Wiring rules

- Connect the typed output to the existing authoritative destination input.
- Fan out one parameter to all destinations that already share the same source.
- Preserve seed-offset and dimension-arithmetic nodes downstream.
- Remove or replace only the input link or literal that the public node now
  supplies.
- Do not delete the replaced community node unless deletion is necessary and
  explicitly approved; leaving an unconnected original control is often safer
  and more reversible.
- Respect exact Comfy types. Do not route `INT` into a destination requiring a
  dynamic COMBO or custom type.
- Keep declarations at the workflow root. Publication v1 does not accept CIF
  declaration nodes hidden inside subgraphs.

### Choice declaration and wiring procedure

For each `CIFChoiceParameter`, perform these steps in order:

1. Identify one real, connectable destination widget or input and read its
   installed finite choice list from `/object_info`.
2. Record the destination's current effective private value before changing
   links.
3. Create stable public IDs and labels. Do not derive a public ID from a full
   path in a way that exposes directory structure.
4. Build `options_json` with exact private bindings. Add
   `default_strength` only when the community workflow provides trustworthy
   option-specific strength defaults.
5. Set the choice node's `value` to the public ID whose binding reproduces the
   destination's current effective value. This preserves default behavior.
6. Use a valid unique `instance_uuid` and normal parameter metadata. For a LoRA
   selector, a typical declaration is:
   - `parameter_id`: `lora`;
   - `label`: `LoRA`;
   - `semantic_role`: `lora`;
   - `required`: `false`;
   - `advanced`: `true`;
   - `group`: `Advanced`.
7. Convert the destination widget to an input through ComfyUI when possible,
   then connect the choice output to it. In structured JSON, preserve the
   destination widget metadata and add the normal reciprocal link references.
8. If the choice fans out, compute the intersection of accepted destination
   values. Every declared private binding must be accepted by every consumer.
9. If a numeric companion controls the selected item, give it a neutral public
   ID such as `lora_strength` and connect it to that same destination node.
10. Leave unrelated custom selector records, extension serialization, and
    downstream model wiring unchanged. If their active behavior would stack or
    conflict with the new selection, stop for user confirmation.

The ComfyUI frontend extension converts the local `value` widget into a finite
selector after valid `options_json` is loaded. The local selector may display
public IDs; the external Image Frontend should display the manifest labels.
After filesystem editing, reopen the workflow from disk so this initialization
runs. Do not save a stale browser copy over the edited file.

Publication compiles a choice input similar to:

```json
{
  "id": "lora",
  "type": "choice",
  "default": "knp_v4_1",
  "label": "LoRA",
  "semantic_role": "lora",
  "required": false,
  "advanced": true,
  "group": "Advanced",
  "choices": [
    {
      "value": "knp_v4_1",
      "label": "KNP v4.1",
      "default_strength": 1.0
    }
  ]
}
```

The public manifest exposes option IDs, labels, and optional strength hints. It
must not expose private `binding` strings in `interface.inputs[].choices`. The
trusted frozen API graph retains `options_json` because the runtime choice node
needs the private mapping. The public caller may patch only the choice node's
declared `value`, never `options_json`, the destination widget, a private
filename, or an arbitrary node path.

## Phase 7: discover candidate image outputs

Inventory every existing output node and every important active `IMAGE` edge.
Include:

- SaveImage and custom durable image savers;
- PreviewImage nodes;
- image comparers;
- decoded images after each sampler stage;
- refiner/detailer results;
- SD-upscaled results;
- optional final-upscale results;
- alternate branches;
- masks and preview bridges;
- images produced before a later stage that may fail.

Trace actual edges and modes. Do not decide from titles alone.

### Determine the final image

The best evidence for the authoritative final image is usually, in order:

1. the image feeding the active durable save node the workflow treats as final;
2. an explicit `FINAL_IMG` Set/Get path;
3. the output of an active final branch selector;
4. the last clearly active refinement stage feeding an existing final preview or
   save.

If a switch chooses between base and upscaled results, declare the image after
the switch as `final`. Do not implement the selection again in the frontend.

If several active save nodes represent unrelated finals and the meaning is not
clear, ask in guided mode or skip a final declaration and report the blocker in
autonomous mode. Never guess merely from the highest node ID or graph position.

### Determine earlier stages

Useful earlier outputs often include:

- first decoded/base image;
- second sampler or refiner result;
- color-matched result;
- pre-detailer upscale;
- an alternate image already shown in a comparer;
- a prototype that remains useful if a later node fails.

Prefer stages that already execute in the active graph. Adding an output sink to
an otherwise dead branch can cause that branch to execute and change runtime,
VRAM use, and storage. Do so only with explicit approval.

## Phase 8: create image publishers

Add one root-level `CIFPublishImage` node for each confirmed authored image.
Fan it out from the existing image edge; do not replace the community consumer.

Each publisher requires:

- `output_id`: stable lowercase public key;
- `instance_uuid`: unique canonical lowercase UUID;
- `role`: one of `final`, `preview`, `comparison`, or `auxiliary`;
- `description`: specific stage meaning;
- `cardinality`: normally `many`;
- `filename_prefix`: safe relative output prefix;
- an `IMAGE` connection.

Use a prefix such as:

```text
comfyui-image-frontend/<workflow-slug>
```

Recommended stage IDs include:

- `base` or `first_pass`;
- `second_pass`;
- `refined`;
- `sd_upscaled`;
- `comparison` with a more specific qualifier;
- `final`.

Role meanings:

- `final`: the authoritative workflow result;
- `preview`: a prototype or earlier pass;
- `comparison`: an alternate/intermediate intended for comparison;
- `auxiliary`: another intentionally authored image.

Declare exactly one current image as role `final` when the graph supports a
clear final. All publisher nodes should use cardinality `many` unless the graph
provably produces exactly one image and the contract intentionally rejects
batches.

Publisher nodes durably save their images. Existing SaveImage and preview nodes
remain intact, so duplicate logical representations are expected. The runtime
adapter may de-duplicate physical downloads by exact locator or hash, but it must
preserve every logical result reference.

## Phase 9: preserve every native result

Declared publishers classify important images; they never define everything the
workflow may return.

The published manifest must retain:

```json
{
  "interface": {
    "unmapped_outputs_policy": "collect"
  }
}
```

The eventual runtime must preserve:

- the complete node-keyed `history.outputs` object;
- raw history status, messages, errors, and execution metadata;
- every declared publisher and every batch artifact;
- all unclassified native node results;
- text, hashes, dimensions, seeds, masks, previews, comparisons, videos, audio,
  files, and JSON-safe custom UI structures actually present in history;
- partial outputs produced before a failure or interruption.

No output node class or result field may be discarded because the adapter does
not recognize it. `interface.native_outputs` is a compile-time inventory, not a
runtime allowlist.

ComfyUI history does not contain every internal tensor or ordinary internal
edge. “Return everything” means every value ComfyUI actually persists in
history, plus every authored publisher artifact.

## Phase 10: create the dedicated graph group

Create or reuse one root-level group titled exactly:

```text
comfyui-image-frontend
```

Place every CIF parameter and publisher inside its bounding rectangle.

Recommended layout:

- basic inputs first, ordered prompt, width, height, seed, then simple toggles;
- advanced inputs in a separate row or column;
- authored output publishers in a clearly separated row labeled by their node
  titles, usually above or below the controls;
- final output placed at the visually prominent end of the output row;
- enough padding that node bodies and group title are fully visible;
- no overlap with community groups or nodes.

Give publisher nodes recognizable titles such as:

- `Image Frontend Output — First Pass`;
- `Image Frontend Output — Second Pass`;
- `Image Frontend Output — SD Upscaled`;
- `Image Frontend Output — Final`.

The group is a visual authoring aid, not a serialization namespace. Verify
containment geometrically after editing because ComfyUI groups generally do not
own nodes as children.

Long fan-out wires are acceptable when they preserve the community graph, but
arrange nodes consistently and avoid needless crossings when possible.

## Phase 11: editing invariants

Prefer editing through the actual ComfyUI frontend when reliable because it
produces native serialization. If structured filesystem editing is necessary,
enforce all of the following.

- Parse and write the editable workflow as structured JSON.
- Preserve all unknown top-level, node, link, group, subgraph, and extension
  fields.
- Allocate noncolliding root node IDs and link IDs.
- Update `last_node_id` and `last_link_id`.
- Add reciprocal link references:
  - one entry in the workflow `links` list;
  - the link ID on the source output;
  - the same link ID on the destination input.
- Preserve source and destination slot indexes and exact link type.
- Keep declaration nodes at the root even when their source compiles from a
  subgraph or Set/Get path.
- Give every declaration its contract-schema property and valid UUID.
- Do not mutate node mode, community widget values, model selections, sampler
  settings, or save paths unless the public binding specifically replaces that
  one value.
- Write atomically and retain a pre-edit backup.

Before accepting the edit, reconstruct the original duplicate by removing the
added CIF nodes, added fan-out links, CIF source-link references, and group
layout changes, then compare the reconstructed structure with the pre-edit
snapshot. Any unexplained difference is a validation failure.

## Phase 12: structural validation

Validate the modified duplicate without running an expensive generation.

### Original preservation

- Original workflow bytes and SHA-256 are unchanged.
- Only the duplicate changed.
- No other workflow, secret, model, or runtime file changed.

### JSON and graph integrity

- Workflow JSON parses.
- Node IDs are unique.
- Link IDs are unique.
- Every link references existing nodes and valid slots.
- Source and destination link references are reciprocal.
- `last_node_id` and `last_link_id` cover all new IDs.
- Subgraph definitions and extension data remain intact.

### Parameter declarations

- Every CIF parameter exists in `/object_info`.
- Every parameter is connected to at least one consumer.
- Public IDs are valid and unique.
- Instance UUIDs are valid and unique.
- Numeric defaults are within bounds and steps are positive.
- Prompt metadata uses `positive_prompt` only for the actual primary prompt.
- Seed default mode is random unless the user explicitly requested fixed.
- Seed fan-out and offset behavior match the original graph.
- Unconnected controls are not advertised.
- Boolean controls connect to real Boolean logic.
- Every choice contains valid JSON, at least one option, unique safe public IDs,
  unique nonempty private bindings, and nonempty labels.
- Every choice default is a public ID present in its own option list.
- Every private choice binding is present in every connected destination's
  current installed `/object_info` choice list.
- Choice outputs connect only to validated finite destination inputs; wildcard
  transport is not treated as general `ANY` wiring.
- A choice paired with a strength parameter feeds the same loader or processing
  node, and the strength metadata is selection-neutral.
- Opaque multi-toggle extension widgets have not been misclassified as one
  mutually exclusive combo.

### Output declarations

- Every publisher exists in `/object_info` and is an output node.
- Every publisher is connected to an `IMAGE` source.
- Public output IDs and instance UUIDs are unique.
- Descriptions are nonempty.
- Roles and cardinalities are valid.
- Exactly one clear image has role `final`, when the workflow has a confirmed
  final.
- Earlier-stage roles match actual stage meaning.
- Publisher sources already execute or their new execution cost was explicitly
  approved.
- Existing native output nodes remain unchanged.

### Layout

- Every CIF declaration lies within the `comfyui-image-frontend` group bounds.
- Input and output rows are visually distinct.
- The group does not hide or overlap essential community controls.

## Phase 13: hand the workflow to the user for local testing

Do not publish automatically.

Tell the user to:

1. avoid saving an already-open stale browser copy;
2. hard-refresh ComfyUI if the custom extension changed;
3. reopen the modified duplicate from disk;
4. focus or zoom to the `comfyui-image-frontend` group;
5. inspect every parameter value, label, description, role, required flag,
   advanced flag, group, and order;
6. inspect every image publisher's ID, role, description, cardinality, and
   source wire;
7. run one normal local generation with default controls;
8. run a second generation and confirm a random seed changes;
9. optionally repeat with an explicit fixed seed to confirm reproducibility;
10. for every choice input, verify the saved default runs and at least one
    nondefault public ID resolves to the intended destination value;
11. when a choice has per-option strength hints, explicitly set the companion
    local strength while testing because local selection does not change it
    automatically;
12. verify existing community previews and saves still appear;
13. verify every declared image publisher saves the expected stage.

If local execution fails, inspect ComfyUI logs and correct only the CIF
declarations or their new fan-out links unless evidence proves an existing
community issue.

## Phase 14: user-controlled publication

After local testing, the user publishes through:

```text
Save & Publish for Image Frontend
```

The official publication operation must compile the loaded editable workflow
through the frontend's normal graph-to-prompt boundary and write sibling files:

```text
<workflow>.json
<workflow>.api.json
<workflow>.interface.json
```

Do not create the API graph by deleting layout fields from the editable JSON.

After publication, verify:

- publication timestamp and ID exist;
- source path is correct;
- source SHA-256 matches the editable workflow bytes;
- API SHA-256 matches the API file bytes;
- every intended input appears in `interface.inputs`;
- every choice input has `type: "choice"`, a valid public default, and the
  expected public values, labels, and optional finite strength hints;
- no private choice binding is exposed in the public manifest choice array;
- every intended publisher appears in `interface.outputs`;
- exactly one output has role `final` when expected;
- `unmapped_outputs_policy` is `collect`;
- native outputs remain inventoried;
- API declaration nodes match editable root declaration nodes;
- there are no validation errors;
- there is no “workflow has no CIF publisher” warning;
- any other warning is explained rather than ignored.

The user asked for a testable modified workflow, so stop before this phase unless
publication is separately authorized.

## Phase 15: optional API smoke validation after publication

Perform this only after the user separately authorizes runtime testing.

1. Load the frozen API and interface files.
2. Clone the API prompt in memory.
3. Patch only declared public inputs.
4. For a choice, reject unknown public IDs, patch only the trusted choice
   node's `value`, and never accept a caller-supplied private binding or
   `options_json`.
5. Resolve option-specific companion defaults before submission: explicit
   strength wins, then the selected option's `default_strength`, then the
   numeric parameter's ordinary default.
6. Resolve random/omitted seed semantics to a concrete integer.
7. Submit with `POST /prompt` and a unique `client_id`.
8. Monitor `/ws` or poll, then reconcile `/history/{prompt_id}`.
9. Retrieve every declared artifact and every native file locator through
   `/view`.
10. Preserve the complete raw history.
11. Normalize declared outputs additively.
12. Return every remaining native result as `unmapped_outputs` without field or
    class whitelisting.

Return the effective public choice ID and every effective companion value, such
as resolved LoRA strength, in the generation result. The result should not use a
private filename as the public selected value.

Do not claim full success until one real generation confirms the final image,
earlier stages, complete metadata, batches, and native outputs.

## Conversion report template

Return a report in this form.

```markdown
# Image Frontend workflow conversion report

## Files

- Original: ...
- Original SHA-256: ...
- Modified duplicate: ...
- Before SHA-256: ...
- After SHA-256: ...
- Publication status: Not published

## Public inputs

| ID | Type | Role | Default | Required | Advanced | Destination(s) |
| --- | --- | --- | --- | --- | --- | --- |
| prompt | string | positive_prompt | ... | true | false | ... |

For every `choice` row, follow the table with its public option inventory:

| Choice ID | Label | Default | Companion default | Validated destination(s) |
| --- | --- | --- | --- | --- |
| knp_v4_1 | KNP v4.1 | yes | 1.0 | LoraLoaderModelOnly.lora_name |

## Declared image outputs

| ID | Role | Stage | Source | Cardinality |
| --- | --- | --- | --- | --- |
| final | final | ... | ... | many |

## Preserved native outputs

- ...

## Skipped or ambiguous candidates

- ...

## Structural validation

- Original unchanged: pass/fail
- JSON and links: pass/fail
- CIF declarations: pass/fail
- Group containment: pass/fail
- Runtime generation: not run / result

## User test instructions

1. ...

## Limitations and future review triggers

- ...
```

## Final acceptance checklist

Do not describe the duplicate as ready for user testing until all applicable
items pass.

### Scope and reversibility

- [ ] Original is unchanged.
- [ ] Duplicate and backup are clearly identified.
- [ ] No unrelated files changed.
- [ ] Before/after hashes are reported.

### Inputs

- [ ] Primary positive prompt is correctly traced and wired.
- [ ] Width and height control the intended base dimensions.
- [ ] Seed coupling and offsets match the original.
- [ ] Random seed is the default local and external policy.
- [ ] Every exposed advanced control has a clear purpose and safe bounds.
- [ ] Every finite selection uses `CIFChoiceParameter`, not unrestricted text.
- [ ] Every choice default is a declared public ID that preserves the saved
      workflow's effective default.
- [ ] Every private binding is accepted by every connected installed
      destination and is absent from the public manifest choice array.
- [ ] Choice and companion strength feed the same processing node, with explicit
      and omitted-strength behavior documented.
- [ ] Custom multi-toggle widgets were not mistaken for one combo input.
- [ ] No invented negative prompt or fake switch was added.
- [ ] Public IDs and UUIDs are unique and valid.

### Outputs

- [ ] Final image is sourced from the actual authoritative path.
- [ ] Useful earlier/prototype stages are declared where clear.
- [ ] Publishers are root output nodes and save every batch item.
- [ ] Publisher descriptions and roles are meaningful.
- [ ] Existing native outputs and metadata remain intact.
- [ ] Output declarations classify but do not whitelist runtime results.

### Graph integrity

- [ ] Node and link IDs are valid and reciprocal.
- [ ] Existing community modes, values, and branches remain intact.
- [ ] New publishers do not activate unintended dead branches.
- [ ] All CIF nodes are visible inside the dedicated group.
- [ ] The modified workflow loads without missing nodes.

### Handoff

- [ ] Modified editable workflow path is provided.
- [ ] Mapping and validation report is complete.
- [ ] Skipped ambiguities are explicit.
- [ ] User knows to reload from disk before saving.
- [ ] Workflow remains unpublished unless the user approved publication.

## Default completion statement

When the conversion is complete, report the outcome plainly:

```text
The duplicated workflow has been augmented with a root-level
comfyui-image-frontend group containing the verified public parameter and image
publisher nodes. The original workflow is unchanged. Structural validation
passed. No publication, deployment, restart, or API generation was performed.
Reopen the duplicate from disk in ComfyUI, inspect the dedicated group, and run
a local test before using Save & Publish for Image Frontend.
```
