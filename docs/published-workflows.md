# Published ComfyUI workflows

This document is the current application-side contract for discovering and running ComfyUI workflows. It supersedes the retired embedded `FrontendWorkflowContract` node and two-file `.workflow.json` / `.api.json` convention. The historical design documents remain in this repository only to explain older stored records.

## Publication boundary

A workflow becomes a generation source only when its author deliberately chooses **File → Save & Publish for Image Frontend** in ComfyUI. A normal save is not publication. The external ComfyUI custom-node/publisher package owns publication; this repository only consumes its output and never repairs, rewrites, or republishes it.

A committed publication is one adjacent bundle beneath ComfyUI userdata `workflows/`:

```text
<stem>.json
<stem>.api.json
<stem>.interface.json
```

- `<stem>.json` is mutable editable-workflow metadata. It is parsed and retained for optional PNG workflow metadata, but its bytes are not the executable publication boundary.
- `<stem>.api.json` is the frozen executable API graph.
- `<stem>.interface.json` is the commit marker, public interface, trusted private bindings, hashes, dependency inventory, warnings, and runtime policy.

Only the `.interface.json` file creates a discovery candidate. Orphaned editable or API files are ignored. The current schemas are:

```text
comfyui-image-frontend.publication/v1
comfyui-image-frontend.interface/v1
```

The application does not infer controls from arbitrary workflow nodes, titles, class names, graph topology, or common image-generation conventions. In particular, it never invents a negative prompt or any other input absent from `interface.inputs`.

## Manifest shape

The publisher is responsible for producing the exact manifest. This abbreviated example shows the v1 structure; hashes and IDs are examples, not values to copy:

```json
{
  "schema_version": "comfyui-image-frontend.publication/v1",
  "contract_schema": "comfyui-image-frontend.interface/v1",
  "publication_id": "11111111-1111-4111-8111-111111111111",
  "published_at": "2026-07-13T19:00:00Z",
  "source_id": "workflows/team/example.json",
  "workflow": {
    "path": "workflows/team/example.json",
    "sha256": "<sha256-of-exact-workflow-bytes>"
  },
  "api": {
    "path": "workflows/team/example.api.json",
    "sha256": "<sha256-of-exact-api-bytes>",
    "node_count": 42
  },
  "manifest": {"path": "workflows/team/example.interface.json"},
  "interface": {
    "inputs": [
      {
        "id": "prompt",
        "type": "string",
        "instance_uuid": "22222222-2222-4222-8222-222222222222",
        "label": "Prompt",
        "description": "Positive image prompt.",
        "semantic_role": "positive_prompt",
        "required": true,
        "advanced": false,
        "group": "Basic",
        "order": 10,
        "default": "mountain lake",
        "bindings": [
          {"node_id": "10", "input": "value", "class_type": "CIFTextParameter"}
        ]
      }
    ],
    "outputs": [
      {
        "id": "first_pass",
        "instance_uuid": "33333333-3333-4333-8333-333333333333",
        "node_id": "40",
        "label": "First pass",
        "description": "Early prototype image.",
        "role": "preview",
        "type": "image",
        "cardinality": "many"
      },
      {
        "id": "final",
        "instance_uuid": "44444444-4444-4444-8444-444444444444",
        "node_id": "41",
        "label": "Final",
        "description": "Authoritative final image.",
        "role": "final",
        "type": "image",
        "cardinality": "many"
      }
    ],
    "unmapped_outputs_policy": "collect",
    "native_outputs": [
      {"node_id": "39", "class_type": "SaveImage"},
      {"node_id": "40", "class_type": "CIFPublishImage"},
      {"node_id": "41", "class_type": "CIFPublishImage"}
    ]
  },
  "dependencies": {"class_types": ["CIFPublishImage", "CIFTextParameter", "SaveImage"]},
  "warnings": [],
  "runtime": {"attach_workflow_as_extra_pnginfo": true}
}
```

Bindings, node IDs, artifact paths, and executable graphs are private compiler data. `GET /api/workflows/{source_key}` is built by allowlist and returns only safe input/output fields.

### Inputs

Publication v1 supports `string`, `integer`, `number`, `boolean`, `seed`, `choice`, and `image`. Every input has a valid public `id`, label, description, semantic role, required/advanced flags, group, order, and one or more trusted bindings. Scalar and choice inputs have defaults; an image input is required and deliberately has no default. Numeric inputs also have `minimum`, `maximum`, and `step`. A seed additionally declares `default_mode` as `fixed` or `random`.

An `image` input has semantic role `reference_image`, binds only to `CIFImageParameter.image`, and declares trusted media metadata: `/upload/image`, storage type `input`, a nonempty subset of PNG/JPEG/WebP MIME types, positive byte/width/height limits, `animated: false`, and a Boolean `returns_mask`. Its limits must match the frozen CIF node. The public request carries only an application-owned opaque asset ID.

A choice declares 1–100 entries containing a unique safe public `value`, nonblank public `label`, and optional finite `default_strength`; its string default must name exactly one entry. The frontend renders a single select and sends only the public value. The manifest and public API never expose the frozen choice node's `options_json`, installed filename, downstream binding, or node ID. Omitted or `null` optional choices resolve to the manifest default; empty strings and unknown values fail before ComfyUI submission.

When a choice has a companion number named `<choice-id>_strength`—or exactly one numeric input shares its semantic role—an explicit non-null number wins. Otherwise the selected option's `default_strength` applies, falling back to the number input's ordinary default. Both concrete public values are returned in effective parameters and patched only through their trusted declaration-node `value` bindings.

Exactly one input must have semantic role `positive_prompt`. The frontend orders non-advanced inputs before advanced inputs, then uses `order`, `group`, and `id` as deterministic fallbacks. The backend remains authoritative for all types, ranges, steps, defaults, and required fields.

Seed integers are serialized to the browser as canonical decimal strings where precision matters. Omitted, `null`, empty, or explicit random seed input resolves to one concrete request-local value when `default_mode` is `random`; a fixed decimal string is validated and preserved exactly. The effective seed is persisted and returned for recall.

### Outputs

Every output declaration has a stable public ID, unique publisher instance UUID, private publisher node binding, optional label, description, role (`final`, `preview`, `comparison`, or `auxiliary`), manifest `type: "image"`, and cardinality `many`. Its API node must be a connected `CIFPublishImage`. Each publication must declare at least one output and exactly one output with role `final`; IDs, instance UUIDs, and publisher node bindings are unique. Published `.interface.json` manifests use `type: "image"`; the validated private contract, public workflow interface, CIF publisher metadata, and generation results normalize that field to `kind: "image"`. The only v1 unmapped-output policy is the explicit value `collect`.

`interface.native_outputs` is required compile-time inventory for diagnostics and UI planning. It is never a runtime allowlist: history nodes missing from the inventory are still retained, and inventory membership never suppresses publisher or native results.

### Publisher history payload

Each `CIFPublishImage` returns ordinary ComfyUI locators plus an exact namespaced list. All publishers use this shape, including a batch of one:

```json
{
  "images": [
    {"filename": "result-00001.png", "subfolder": "example", "type": "output"}
  ],
  "comfyui_image_frontend": [
    {
      "schema_version": "comfyui-image-frontend.interface/v1",
      "output_id": "final",
      "instance_uuid": "44444444-4444-4444-8444-444444444444",
      "role": "final",
      "kind": "image",
      "cardinality": "many",
      "description": "Authoritative final image.",
      "artifacts": [
        {
          "batch_index": 0,
          "filename": "result-00001.png",
          "subfolder": "example",
          "type": "output"
        }
      ]
    }
  ]
}
```

The namespaced declaration is authoritative for stable ID, role, kind, cardinality, description, and batch order. Runtime normalization verifies it against the frozen private manifest binding. The ordinary `images` list remains unchanged in `raw_history`; it is a mirror, not a second declared batch inventory.

## Discovery and validation

Discovery runs at startup, through `POST /api/admin/workflows/refresh`, and once when health monitoring observes ComfyUI recover from offline to online. The recovery path also populates an empty catalog after an offline startup. Continuous online health checks do not periodically refetch publication bundles.

1. Probe ComfyUI and fetch `/object_info`.
2. Recursively list `CIF_COMFYUI_WORKFLOW_DIRECTORY` with `GET /v2/userdata?path=...`; use `GET /userdata?dir=...&recurse=true&full_info=true` as the compatibility fallback.
3. Keep only safe normalized paths ending in `.interface.json`.
4. Fetch the manifest and its adjacent editable and API documents. A nested userdata path is percent-encoded in full as one route segment, including `/` as `%2F`.
5. Parse strict UTF-8 JSON within the configured byte limits and validate the whole candidate.
6. Atomically publish the new immutable revision only after every hard check succeeds, retaining any nonfatal warnings.

When configured, `CIF_COMFYUI_USER` is forwarded as `Comfy-User` on the relevant HTTP and WebSocket operations.

Validation rejects absolute paths, backslashes, dot segments, traversal, encoded separators in manifest paths, mismatched stems, source/path disagreement, duplicate JSON keys, non-finite values, unsupported schemas, a frozen API raw-byte hash mismatch, wrong API node counts, invalid API graph structure, invalid input/output declarations, absent or duplicate publishers, disconnected publishers, zero or multiple final outputs, invalid cardinality, unsafe or missing binding targets, binding/class mismatches, uncovered or missing node dependencies, missing native-output inventory, and over-limit responses. `dependencies.class_types` must cover the frozen graph and each class must exist in `/object_info`.

The editable workflow is still required, path-checked, size-bounded, and parsed as strict JSON. If its current raw bytes differ from `workflow.sha256`, discovery adds a nonfatal editable-workflow drift warning and continues validating the frozen API and interface. This commonly follows a normal ComfyUI save, layout change, or unpublished authoring edit. Discovery does not rewrite either artifact, and the frozen `.api.json` hash comparison remains fail-closed.

Candidates are independent: one failure does not hide other valid sources. Warnings are preserved but do not become errors. An accepted source with editable drift is diagnosed as `ready_with_warnings`; administrator details contain both the manifest-recorded and currently observed editable hashes, while an API hash mismatch remains a rejected `api_hash_mismatch`. Other administrative diagnostic codes include `server_unreachable`, `listing_failed`, `manifest_fetch_failed`, `workflow_fetch_failed`, `api_fetch_failed`, `manifest_invalid`, `dependency_missing`, and `ready`.

## Source identity, revisions, and refresh behavior

`source_key` is an opaque stable SHA-256 derived from `CIF_COMFYUI_INSTANCE_ID` and manifest `source_id`. Keep the configured instance ID stable even if the server URL changes. A revision is the immutable tuple:

```json
{
  "publication_id": "...",
  "workflow_sha256": "...",
  "api_sha256": "...",
  "manifest_sha256": "..."
}
```

The current `display_name` is derived from the editable workflow filename stem; it is presentation metadata and not identity.

The manifest hash is calculated from the exact downloaded manifest bytes. `workflow_sha256` is the editable hash recorded by the deliberate publication and remains revision metadata even if a later ordinary save changes the current editable bytes. Execution and compilation use the exact accepted frozen API snapshot and its verified `api_sha256`. A generation optionally sends the selected revision; if the source was republished after selection, the backend returns `source_republished` instead of silently compiling against new controls.

Refresh behavior is deliberately conservative:

- A transport/listing failure leaves the last valid catalog intact. Entries are reported as `cached_offline`, `cached: true`, `available: false`; retained history is still usable, but new submission is disabled while ComfyUI is offline.
- Offline-to-online recovery triggers a full atomic discovery automatically; an operator refresh is not required to recover an empty startup catalog.
- A listed candidate whose replacement bundle is invalid keeps its previous accepted revision active.
- Editable-workflow byte drift alone does not make a candidate invalid: the source remains current and refresh updates its warning/readiness metadata.
- A successful authoritative listing retires sources whose manifests disappeared.
- A missing dependency creates an unavailable catalog entry rather than an executable source.
- Startup before health is known reports `loading`.
- Existing and in-flight generations retain their accepted revision snapshots. Only new jobs use a newly accepted revision.

If a selected source disappears after an authoritative refresh, it no longer resolves for generation; the browser clears or disables that selection and requires an available source. If it is republished, a request carrying the old revision receives `source_republished` and must reload/review the interface. If the catalog has no available sources, generation stays disabled while retained history remains accessible.

The first successful publication refresh also retires legacy embedded-contract profiles from current discovery. Their historical generation rows remain readable.

## Request compilation and submission

The current public request is a source reference plus public parameters:

```json
{
  "source_key": "<opaque-key>",
  "revision": {
    "publication_id": "...",
    "workflow_sha256": "...",
    "api_sha256": "...",
    "manifest_sha256": "..."
  },
  "parameters": {
    "prompt": "mist over a mountain lake",
    "width": 1024,
    "height": 1024,
    "seed": "1125899906842624"
  },
  "prompt_assistant_run_id": null
}
```

The backend rejects unknown IDs and private graph/binding/path fields, applies manifest and choice-specific defaults, resolves seeds and authorized image assets, deep-clones the accepted frozen graph, and patches only manifest-trusted bindings. A choice binding receives its stable public ID; the frozen `CIFChoiceParameter` resolves the private destination while `options_json` and downstream loader inputs remain unchanged. An image asset is decoded and validated, uploaded to an adapter-owned per-job ComfyUI input namespace, and patched only into `CIFImageParameter.image`. The compiler verifies that the cached graph was not mutated. When the publication runtime flag requires it, submission includes the accepted editable snapshot at `extra_data.extra_pnginfo.workflow`; this metadata never replaces or patches the verified frozen API graph. ComfyUI receives a request-specific client ID and returns the native `prompt_id`.

`POST /api/generations/validate` performs the same compilation checks without queuing. `POST /api/generations` commits the immutable source revision, requested/effective parameters, resolved seeds, and compiled graph before the durable queue accepts the job.

Temporary `profile_id` / `controls` request aliases and corresponding legacy response fields exist only so an old browser can complete its transition. They resolve to the same current validated publication and cannot revive embedded-contract discovery. New clients must use `source_key`, `revision`, and `parameters`.

## Results and retained assets

WebSocket events provide timely progress, while `/history/{prompt_id}` is the terminal source of truth. Monitoring and restart recovery use bounded history reconciliation because terminal events can precede persisted history and cached executions can omit ordinary events.

Generation detail preserves:

- native `prompt_id` and application status;
- stable `generation_source` revision data;
- `requested_parameters`, `effective_parameters`, and exact `resolved_seeds`;
- complete bounded history in server persistence, and public `raw_history` with the top-level submitted `prompt` and `extra_data` graph envelopes removed while actual history outputs, status, messages, errors, and execution metadata remain intact;
- publisher-mapped `declared_outputs` in manifest order, including role, kind, cardinality, description, and every authoritative batch reference;
- every nonpublisher node result copied untouched into node-keyed `unmapped_outputs`;
- nonfatal publication/result `warnings` and runtime `errors`;
- every archived image reference in `artifacts`, including all batch members;
- durable lifecycle `events`.

CIF publisher metadata is read from the top-level list-shaped `comfyui_image_frontend` payload (with legacy nested shapes accepted for stored-history compatibility) and verified against the frozen declaration. Its `artifacts` list is authoritative for batch membership, `batch_index`, and order; the ordinary `images` field stays in raw history and is not counted a second time. Unknown or mismatched publisher metadata is recorded as an error, not silently promoted. Native file references are restricted to ComfyUI's `filename` / `subfolder` / `type` tuple with types `input`, `output`, or `temp`; arbitrary paths are never accepted. Retrieved bytes are size-limited and archived in owner-scoped application storage. Each archived artifact exposes an authorized `content_url` and optional `thumbnail_url`. A retrieval failure leaves the logical locator in declared/unmapped/raw result data and reports a partial-output warning or required-final failure as appropriate.

The gallery uses the authored `final` result as its primary image when available. Detail presents previews as prototypes/earlier passes, comparisons as alternates, auxiliary publishers next, and native results as additional outputs. Every batch sibling remains independently inspectable and downloadable; presentation hierarchy never discards an earlier stage or native output.

The public JSON/history boundary removes submitted graph envelopes, not result metadata. Arbitrary JSON-safe custom UI fields, publisher declarations, output-node keys, batches, raw status/error detail, and execution metadata remain available. ComfyUI URLs, credentials, userdata discovery paths, executable graphs, and private manifest bindings stay server-side. Original owner-scoped artifact bytes may contain native ComfyUI prompt/workflow metadata; users should strip it before sharing files outside the appliance if that server-side metadata is sensitive.

## Configuration

All settings use the `CIF_` prefix:

| Setting | Default | Purpose |
|---|---:|---|
| `CIF_COMFYUI_BASE_URL` | `http://127.0.0.1:8188` | Server-only ComfyUI HTTP endpoint |
| `CIF_COMFYUI_WS_URL` | derived | Optional WebSocket override |
| `CIF_COMFYUI_INSTANCE_ID` | `default` | Stable identity used in source keys |
| `CIF_COMFYUI_USER` | unset | Optional `Comfy-User` value |
| `CIF_COMFYUI_WORKFLOW_DIRECTORY` | `workflows` | Recursive userdata listing root |
| `CIF_COMFYUI_CONCURRENCY` | `1` | Maximum active application jobs |
| `CIF_COMFYUI_LISTING_MAX_BYTES` | `4194304` | Listing response cap |
| `CIF_COMFYUI_OBJECT_INFO_MAX_BYTES` | `67108864` | `/object_info` capability response cap |
| `CIF_COMFYUI_MANIFEST_MAX_BYTES` | `1048576` | Interface manifest cap |
| `CIF_COMFYUI_WORKFLOW_MAX_BYTES` | `33554432` | Editable workflow cap |
| `CIF_COMFYUI_API_MAX_BYTES` | `33554432` | Frozen API graph cap |
| `CIF_COMFYUI_HISTORY_MAX_BYTES` | `33554432` | History response cap |
| `CIF_COMFYUI_OUTPUT_MAX_BYTES` | `134217728` | One retrieved output cap |

See [`.env.example`](../.env.example) for the complete application configuration.

## Security boundary

- ComfyUI URLs, the `Comfy-User` value, submitted graphs, private manifest input-binding node IDs, and discovery userdata paths stay server-side. Native output node keys and fields emitted by published custom nodes remain visible in result data.
- Complete native history is persisted server-side. Public history removes top-level submitted graph envelopes while preserving actual result/status/error/execution data; private manifest bindings, discovery paths, ComfyUI URLs, and credentials are never injected into those results.
- The browser can submit only a known `source_key`, optional exact revision, and manifest-declared public parameter map.
- Every candidate path is untrusted until normalized and constrained beneath `workflows/`.
- Exact frozen-API and manifest raw-byte hashes, together with the manifest-recorded editable hash, make the accepted revision identity immutable; current editable drift remains warning metadata.
- Compilation is request-local; cached source graphs are never patched in place.
- History and output responses have independent byte caps; file references use a narrow allowlist.
- Application asset routes are authenticated and owner-scoped.
- Application-generated diagnostics are safe summaries and never add credentials or authorization headers. Raw ComfyUI output/status is exhaustive authored result data and can contain custom diagnostics or paths, so operators must trust the workflows and custom nodes they publish.

## Compatibility and retirement policy

The publication catalog replaces embedded `FrontendWorkflowContract` / `FrontendWorkflowArtifact` discovery and the old `.workflow.json` / `.api.json` pair. Those artifacts are not accepted as new sources. A successful authoritative refresh marks old current profiles stale.

Database columns and API compatibility fields remain so pre-migration generations can still be viewed, downloaded, deleted, and, only when an exact current publication is available, recalled. Recall never substitutes a newer publication. Compatibility request aliases are transitional and must not be used as a long-term integration surface.

See [`migration-published-workflows.md`](migration-published-workflows.md) for the concise data/client/operator migration checklist.

## Operational checklist

1. Publish in ComfyUI; do not merely save.
2. Keep the three files adjacent beneath `workflows/`.
3. Configure a durable instance ID and optional ComfyUI user.
4. Refresh **Administration → Workflow diagnostics**.
5. Confirm `ready` or `ready_with_warnings`; investigate other codes per candidate.
6. Verify the public controls and warning text without exposing bindings.
7. Start with a low-cost request, then inspect `prompt_id`, effective parameters, raw history, declared/unmapped outputs, and every archived artifact.
8. Back up the application database and asset directory together; ComfyUI publication files remain external prerequisites.
