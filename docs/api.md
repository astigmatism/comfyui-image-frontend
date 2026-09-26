# Application API

All routes are same-origin and served beneath `/api`. The application never injects ComfyUI/Ollama credentials or URLs, raw workflow/API graphs, private manifest input bindings, or discovery userdata paths into browser responses. Native output node keys and arbitrary fields actually emitted by ComfyUI remain visible in `unmapped_outputs` and graph-envelope-safe raw history so results are not flattened. Interactive OpenAPI is available at `/api/docs`; `backend/app/schemas.py` is the response-model source of truth.

## Authentication and errors

`GET /api/auth/session` returns an anonymous signed login-CSRF token or the authenticated user and session CSRF token. The opaque session token is an `HttpOnly` cookie. Every authenticated mutation includes:

```http
X-CSRF-Token: <csrf_token>
```

Generation, upload, and gallery content lookups are scoped to the current owner. Cross-user requests, including administrator attempts, return not found rather than revealing existence. Workflow LoRA thumbnails are a deliberate exception: they are shared with all signed-in users of that workflow.

Errors use a safe machine-readable envelope:

```json
{
  "error": {
    "code": "parameter_validation_failed",
    "message": "One or more published parameters are invalid.",
    "fields": {"width": "Value must be at most 2048."},
    "details": {},
    "request_id": "..."
  }
}
```

Application-internal traces, secrets, cookies, private manifest bindings, and executable graphs are not returned. Raw ComfyUI history/status is treated as authored result data and may itself contain custom diagnostics or paths; operators must trust the local workflow/custom nodes they publish.

Every response carries a sanitized `X-Request-ID` that matches the structured `http_request_completed` log record. A safe `Server-Timing` metric reports application time to response headers. Logs use normalized route templates rather than query strings or caller-provided content.

## ComfyUI execution instances

`GET /api/comfyui-instances` requires a fully initialized authenticated session and returns the read-only stage assignments and configured instances plus cached per-instance health. It never returns `base_url`, `ws_url`, `user`, credentials, or concurrency:

```json
{
  "default_instance_id": "primary",
  "text_instance_id": "promptgen",
  "configuration_mode": "explicit",
  "items": [
    {
      "id": "primary",
      "label": "Primary",
      "description": null,
      "is_default": true,
      "available": true,
      "message": null,
      "checked_at": "2026-08-06T20:00:00Z"
    },
    {
      "id": "promptgen",
      "label": "CPU Prompt Generator",
      "description": null,
      "is_default": false,
      "available": false,
      "message": "ComfyUI is unreachable.",
      "checked_at": "2026-08-06T20:00:00Z"
    }
  ]
}
```

Items follow deployment-configuration order. `configuration_mode` is `explicit` when `CIF_COMFYUI_INSTANCES` supplied the catalog or `CIF_COMFYUI_ADDITIONAL_INSTANCES` was supplied (including an empty deliberate opt-out), and `legacy` when the backend synthesized only the one-item **Primary** fallback. Before the first background check, an item is unavailable with a null `checked_at` and an explicit not-yet-checked message. This route is a database/configuration projection, not a request-time external probe. Clients cannot select runtimes. `default_instance_id` fixes the image service and `text_instance_id` fixes the prompt service. A null text assignment disables prompt generation. Validated cached publications may queue during an outage; accepted jobs wait for their recorded service.

## Published generation sources

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/workflows` | List current ready, cached/offline, and known unavailable publication summaries |
| `GET` | `/api/workflows/{source_key}` | Get the selected source's allowlisted public interface |
| `GET` | `/api/workflows/{source_key}/lora-images/{control_id}` | Get versioned shared images for one published `lora_stack` control |
| `POST` | `/api/workflows/{source_key}/lora-images/{control_id}` | Atomically set/remove selected LoRA images with per-item version checks |
| `GET` | `/api/workflows/{source_key}/lora-images/{control_id}/{item_id}/content?v={version}` | Read one authenticated WebP thumbnail |
| `GET` | `/api/services` | Restrained ComfyUI/Ollama availability state |
| `POST` | `/api/admin/workflows/refresh` | Administrator: rediscover and atomically validate publications |
| `GET` | `/api/admin/workflows/diagnostics` | Administrator: safe per-transport/per-candidate diagnostics |

The historical route name `workflows` is retained, but objects now represent deliberately published sources.

### Shared LoRA images

Image metadata is separate from the public workflow interface and generation parameters. A signed-in user may read or update images for LoRAs in the selected workflow's published `lora_stack` catalog. The lookup returns every published item in catalog order:

```json
{
  "items": [
    {"id": "tifa", "version": "<opaque-version-token>", "image_url": "/api/workflows/<source-key>/lora-images/loras/tifa/content?v=<opaque-version-token>"},
    {"id": "claire", "version": "<opaque-version-token>", "image_url": null}
  ]
}
```

To save staged changes, send one `multipart/form-data` POST with a `changes` JSON field and a named file part for each `set` action. Example `changes` value:

```json
[
  {"id": "tifa", "version": "<version-from-GET>", "action": "set", "file_key": "image_0"},
  {"id": "claire", "version": "<version-from-GET>", "action": "remove"}
]
```

The multipart part named `image_0` contains the local file. Static PNG, JPEG, and WebP files are accepted within the configured upload byte and pixel limits; the server stores only a small normalized WebP thumbnail. The request requires the session CSRF header and returns the same shape as the metadata GET. The batch is conditional on every item's opaque `version`. If any changed item has a stale version, the entire update returns HTTP 409 with `lora_image_conflict`; the client must reload and let the user review the latest images. Unknown control or catalog IDs and malformed parts are rejected. Content URLs require authentication and use a versioned private cache policy. Images are scoped to the logical published workflow and retained across compatible revisions; a removed or rebound catalog member loses its prior image.

The `instance_id` on a workflow summary identifies its authoritative catalog and execution service: the image assignment for `/api/workflows`, the prompt assignment for `/api/workflows?output_kind=text`. Other copies appear only in safe `replicas` metadata. Old source keys may resolve to the authoritative profile by logical identity; controls and submitted revisions are checked against that profile.

### Source summary

The selector portion of this example uses the current Moody Krea 2 public values. Revision hashes and the remaining catalog/inventory fields are illustrative; clients must use the values returned by their own publication.

```json
{
  "source_key": "<opaque-sha256>",
  "display_name": "Moody Krea 2 Mix V4",
  "instance_id": "home",
  "readiness": "ready",
  "available": true,
  "cached": false,
  "warnings": [],
  "revision": {
    "publication_id": "<publication-id>",
    "workflow_sha256": "...",
    "api_sha256": "...",
    "manifest_sha256": "..."
  },
  "model_selectors": [
    {
      "parameter_id": "checkpoint",
      "label": "Checkpoint",
      "description": "Selects the Moody Krea 2 diffusion checkpoint. V4 INT8 remains the default; V5 BF16 is the highest-precision V5 option and is substantially heavier on VRAM/RAM.",
      "default": "v4_int8",
      "choices": [
        {"value": "v4_int8", "label": "Moody Krea 2 V4 INT8 ConvRot", "released_month": "2026-07"},
        {"value": "tyjr_mxfp8", "label": "Moody Krea 2 TYJR MXFP8", "released_month": "2026-07"},
        {"value": "v4_bf16", "label": "Moody Krea 2 V4 BF16", "released_month": "2026-07"},
        {"value": "cutie_x_int8", "label": "Moody Krea 2 Cutie X INT8", "released_month": "2026-07"},
        {"value": "v5_bf16", "label": "Moody Krea 2 V5 BF16", "released_month": null}
      ]
    }
  ],
  "generation_source": {
    "schema_version": "comfyui-image-frontend.generation-source/v1",
    "inference_method": "deterministic_graph_analysis",
    "generation_type": "text_to_image",
    "prompt_guided": true,
    "input_media": ["text"],
    "output_media": ["image"],
    "dimension_policy": "explicit",
    "summary": "Prompt-guided image generation.",
    "base_model": {
      "family": "krea2",
      "family_label": "Krea 2",
      "architecture": "krea2",
      "architecture_label": "Krea 2",
      "primary_artifacts": ["model.safetensors"],
      "timeline": {
        "architecture": {
          "introduced_month": "2026-05",
          "source": {
            "source_type": "official_announcement",
            "publisher": "Krea",
            "title": "Introducing Krea 2",
            "url": "https://www.krea.ai/blog/krea-2-image-model"
          }
        },
        "model_variants": [
          {
            "parameter_id": "checkpoint",
            "value": "v4_int8",
            "label": "Moody Krea 2 V4 INT8 ConvRot",
            "released_month": "2026-07",
            "source": {"source_type": "creator_release", "publisher": "Creator", "title": "V4 INT8 release", "url": "https://publisher.example/v4-int8"}
          },
          {
            "parameter_id": "checkpoint",
            "value": "tyjr_mxfp8",
            "label": "Moody Krea 2 TYJR MXFP8",
            "released_month": "2026-07",
            "source": {"source_type": "creator_release", "publisher": "Creator", "title": "TYJR release", "url": "https://publisher.example/tyjr"}
          },
          {
            "parameter_id": "checkpoint",
            "value": "v4_bf16",
            "label": "Moody Krea 2 V4 BF16",
            "released_month": "2026-07",
            "source": {"source_type": "creator_release", "publisher": "Creator", "title": "V4 BF16 release", "url": "https://publisher.example/v4-bf16"}
          },
          {
            "parameter_id": "checkpoint",
            "value": "cutie_x_int8",
            "label": "Moody Krea 2 Cutie X INT8",
            "released_month": "2026-07",
            "source": {"source_type": "creator_release", "publisher": "Creator", "title": "Cutie X release", "url": "https://publisher.example/cutie-x"}
          }
        ]
      }
    },
    "technologies": [],
    "tags": ["text-to-image"]
  },
  "technical_inventory": {
    "schema_version": "comfyui-image-frontend.technical-inventory/v1",
    "node_counts": {
      "editable_root": 120,
      "subgraph_definitions": 5,
      "editable_subgraph_nodes": 43,
      "compiled_api": 71,
      "output_reachable": 63,
      "compiled_orphans": 8
    },
    "models": [],
    "loras": [],
    "text_encoders": [],
    "vaes": [],
    "upscalers": [],
    "detectors": [],
    "samplers": [],
    "technologies": [],
    "reachable_class_types": [],
    "orphan_class_types": [],
    "unclassified_loaders": [],
    "warnings": []
  }
}
```

`readiness` is `loading` before health is known, `ready`, `ready_with_warnings`, `cached_offline`, or a safe unavailable state such as `dependency_missing`. Recorded/observed workflow or API hash drift remains available as `ready_with_warnings`; the revision's `api_sha256` identifies the exact observed, validated graph used for execution. A last-valid cached/offline entry remains available from its frozen graph; dispatch waits for the assigned execution service reported by `/api/comfyui-instances`.

Recognized v1 `generation_source` and `technical_inventory` objects are typed, additive, and returned on both summary and detail responses so clients can plan later catalog/dropdown behavior without refetching every source. Older manifests and unrecognized/malformed section schemas return `null` for that section while the raw manifest remains retained server-side. Unknown v1 values, array entries, warning strings, and extra fields are preserved. Artifact basenames, class types, and counts are descriptive only and are never accepted as request selectors. `output_reachable + compiled_orphans = compiled_api` and the accepted API count are checked diagnostically, not as queue gates.

`model_selectors` is an additive, safe projection of executable public choice inputs for the prominent checkpoint control and source-picker use. It is derived after publication and is not an authored manifest section. A choice with canonical `semantic_role: "model"` is included; `semantic_role: "checkpoint"`, the exact historical parameter ID `checkpoint`, and an exact timeline `(parameter_id, value)` match are compatibility signals. The validated interface remains authoritative for selector IDs, labels, defaults, ordering, and the complete option inventory. Timeline metadata may add only a matching option's `released_month`; it never creates, removes, renames, or rebinds an option. Bindings, filenames, paths, `options_json`, node IDs, and timeline-only values are excluded. Sources without a recognized selector return an empty list, including older publications. Unavailable dependency-backed catalog entries retain this safe projection so the picker can explain their published options without making them executable.

The optional `generation_source.base_model.timeline` keeps three clocks distinct. `architecture.introduced_month` is the base architecture's `YYYY-MM` introduction, `default_model.released_month` is the exact fixed/default model release when known, and top-level `published_at` is the workflow-bundle publication time. Timeline dates include inert provenance objects that are returned losslessly and are never fetched during discovery. `model_variants` identify selectable releases only by public `parameter_id` plus `value`; they do not expose or require a private filename, binding, node ID, or filesystem path. A malformed timeline makes generation-source metadata unavailable with a nonfatal diagnostic, but does not reject or disable the executable source.

Ordinary source responses describe missing dependencies generically. `technical_inventory.reachable_class_types` and `orphan_class_types` are publisher-declared public inventory; current runtime dependency failures and exact missing classes remain restricted to administrator diagnostics.

During client migration, summaries also carry legacy `profile_id`, workflow/version/hash, contract-schema, and adapter fields. They are compatibility metadata, not the logical source/revision API; new clients use `source_key` and `revision`.

A source detail adds only its public interface projection. The independent example below uses an ordinary LoRA choice to show that non-checkpoint choices remain scalar; it is not the Moody summary above:

```json
{
  "interface": {
    "schema": "comfyui-image-frontend.interface/v1",
    "inputs": [
      {
        "id": "prompt",
        "type": "string",
        "label": "Prompt",
        "description": "The positive image prompt.",
        "semantic_role": "positive_prompt",
        "required": true,
        "advanced": false,
        "group": "Basic",
        "order": 10,
        "default": "mountain lake"
      },
      {
        "id": "lora",
        "type": "choice",
        "label": "LoRA",
        "description": "Selects the primary model-only LoRA.",
        "semantic_role": "lora",
        "required": false,
        "advanced": true,
        "group": "Advanced",
        "order": 55,
        "default": "knp_v4_1",
        "choices": [
          {"value": "knp_v4_1", "label": "KNP v4.1", "default_strength": 1.0},
          {"value": "knp_v3_1", "label": "KNP v3.1", "default_strength": 0.5}
        ]
      }
    ],
    "outputs": [
      {
        "id": "first_pass",
        "role": "preview",
        "kind": "image",
        "cardinality": "many",
        "label": "First pass",
        "description": "Early prototype image."
      },
      {
        "id": "final",
        "role": "final",
        "kind": "image",
        "cardinality": "many",
        "label": "Final",
        "description": "Authoritative final image."
      }
    ],
    "unmapped_outputs_policy": "collect"
  }
}
```

Numeric fields additionally include `minimum`, `maximum`, and `step`; seeds include `default_mode` and use a decimal-string default when fixed (or `null` when random). A choice contains only its stable public values, labels, and optional finite `default_strength` hints. Private option mappings, `options_json`, filenames, bindings, and destination nodes are never projected. Published manifests declare output `type: "image"`, but this public interface intentionally exposes the normalized field `kind: "image"`. Output descriptions contain public `id`, `role`, `kind`, `cardinality`, `label`, and `description`. Bindings, instance UUIDs, class types, node IDs, dependencies, paths, and graphs are never copied into the public source projection.

Administrator refresh returns diagnostic records with `basename`, `accepted`, optional source/revision hints, `code`, safe `message`, and `checked_at`. Important codes include transport failures (`server_unreachable`, `listing_failed`), candidate fetch/validation failures, `dependency_missing`, `ready_with_warnings`, and `ready`. Accepted warning details distinguish manifest-recorded and observed workflow/API hashes and include metadata diagnostic codes when optional sections cannot be recognized or their node counts are inconsistent.

## Validate and create a generation

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/generations/validate` | Validate parameters and compile a request without queuing |
| `POST` | `/api/generations` | Durably accept and queue a generation |

The independent request and validation examples below continue that ordinary LoRA source. A Moody checkpoint request has the same envelope but sends one scalar `checkpoint` value per fan-out request.

Canonical request:

```json
{
  "source_key": "<opaque-source-key>",
  "revision": {
    "publication_id": "<publication-id>",
    "workflow_sha256": "...",
    "api_sha256": "...",
    "manifest_sha256": "..."
  },
  "parameters": {
    "prompt": "mist over a mountain lake",
    "width": 1024,
    "height": 1024,
    "seed": "1125899906842624",
    "lora": "knp_v3_1",
    "enable_upscale": false
  },
  "prompt_assistant_run_id": null
}
```

New clients omit `comfyui_instance_id`. This deprecated compatibility field accepts only the assigned image ID from `GET /api/comfyui-instances`; any conflicting value is rejected with `runtime_assignment_conflict` (409). Prompt requests follow the same rule against `text_instance_id`. Missing prompt configuration returns `prompt_runtime_not_configured` (503). Unfinished discovery returns retryable `source_catalog_loading` (503); a validated cached publication can be accepted while its service is offline. Submission receipts are checked before these new-request rules, so accepted retries return their original result after configuration changes.

`revision` is optional for a fresh caller but recommended for a UI selection. If the selected source was republished, a mismatch returns HTTP 409 with `source_republished`; the backend never compiles against a silently changed graph.

`parameters` accepts only IDs in the accepted public interface. Unknown parameters and arbitrary graph/binding/path payloads fail. Optional non-seed values use manifest defaults. Optional choices treat omission or `null` as default selection, but reject empty strings, labels, private filenames, and values absent from the current publication. If a companion strength is omitted, the selected option's `default_strength` wins before the numeric input's ordinary default; an explicit non-null numeric value always wins. Random seeds may be omitted, `null`, empty, or the string `random`; fixed seeds should be canonical decimal strings so the full declared integer range survives JavaScript serialization. Seeds are returned as strings. A required image parameter is `{ "asset_id": "opaque-owner-scoped-id" }`; missing assets, unauthorized assets, paths, URLs, and ComfyUI locators fail validation.

A generation accepts one scalar value for each model selector. The first recognized selector per source is promoted to checkboxes immediately beneath **Generation source** and mirrored in the source-picker row; it is omitted from Advanced. Later model choices remain ordinary scalar Advanced controls, avoiding an implicit cross-product. Selecting several checkpoints queues one generation request per selected source/checkpoint pair. Same-source requests clone all other parameters, resolve a Random seed once, and reuse that concrete seed so only the checkpoint value changes. Timeline arrays and request arrays are not multi-checkpoint execution inputs.

Successful validation:

```json
{
  "valid": true,
  "effective_parameters": {
    "prompt": "mist over a mountain lake",
    "width": 1024,
    "height": 1024,
    "seed": "793486291720513",
    "lora": "knp_v3_1",
    "lora_strength": 0.5,
    "enable_upscale": false
  },
  "resolved_seeds": {"seed": "793486291720513"},
  "errors": {},
  "compiled_graph_sha256": "..."
}
```

Invalid compilation returns the standard error envelope with field errors rather than queuing. `POST /api/generations` returns HTTP 201 and a generation summary only after the generation/source snapshot, selected execution ID/current label, effective parameters, graph, instance-specific queue item, and initial event are committed. That execution ID pins input upload, prompt submission, WebSocket progress, queue/history polling, `/view` result retrieval, interruption/cancellation, and related errors. Changing the browser selector after HTTP 201 affects only later requests.

`collection_id` is an optional nullable field in the canonical generation request. A non-null value
must identify a collection owned by the current user and is frozen into the accepted row in the
same transaction as the request snapshot; omitted or null means unfiled at the gallery root.
Temporary migration aliases `profile_id`, `controls`, `preset_id`, `requested_outputs`, and
`expected_identity` remain in the envelope for the pre-publication browser. New clients must not use
them. They resolve only to current validated publications and do not restore legacy discovery.

## Collections and gallery scope

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/collections` | Flat owner collection list with direct counts and up to four direct image previews |
| `POST` | `/api/collections` | Create a named top-level or child collection |
| `PATCH` | `/api/collections/{id}` | Rename and/or move a collection |
| `DELETE` | `/api/collections/{id}` | Recursively delete a collection subtree and its generations; `202` while active deletion reconciles |
| `POST` | `/api/generations/{id}/move` | Move an owned generation to a collection or to unfiled root |
| `POST` | `/api/gallery/transfer` | Move or copy a mixed selection of generations and collection subtrees |
| `POST` | `/api/gallery/delete` | Delete a mixed selection using the existing cancellation and cleanup lifecycle |
| `POST` | `/api/gallery/favorite` | Add explicitly selected image and folder cards to Favorites |
| `POST` | `/api/gallery/download` | Download selected images and recursive folder contents as one ZIP |

Collections are returned as a flat `created_at, id` ordered list; clients construct the tree from
`parent_id`. Names are trimmed and must contain 1–100 characters. Duplicate sibling names are
allowed. Parents are owner-scoped, nesting is limited to five collection levels, and moves reject
self-parenting, descendants, and any placement that would push the moved subtree below level five.
Cross-owner collection, parent, generation, and move-target IDs return `not_found`/404 even for
administrators.

A collection response includes `generation_count` for direct, non-pending-delete generations and
`previews` containing at most four newest direct image artifact thumbnails. Descendant contents do
not contribute to either field. Collection deletion removes descendants deepest-first, reuses the
normal generation deletion lifecycle (including owned artifact/upload cleanup), and writes one
content-free `collection_deleted` audit record per removed collection.

`PATCH` distinguishes an omitted `parent_id` from explicit null: omission leaves the parent
unchanged, while `{"parent_id": null}` moves the collection to the top level. `GenerationMove`
uses the same explicit null as the unfiled root destination.

Gallery listing has three compatibility-preserving modes:

- omitted `collection_id`: the historical unscoped owner timeline;
- present but empty `collection_id=`: only unfiled root generations;
- non-empty `collection_id=<id>`: only direct generations in that owned collection.

The two scoped modes exclude `pending_delete` rows and preserve the existing newest-first
`(accepted_at, id)` cursor. The unscoped mode retains its prior pending-row behavior for existing
consumers.

### Bulk selection operations

All gallery endpoints require a ready authenticated user and `X-CSRF-Token`. The selection
contains `generation_ids` and `collection_ids` arrays, with at least one ID and at most 500
explicitly selected IDs in total. Repeated IDs are deduplicated. Every ID is checked for ownership
before any mutation or download; inaccessible IDs return 404, including for administrators.
For transfer, delete, and download, a selected folder
subsumes its descendants and separately selected generations inside it, so overlapping Favorites
selections affect each item once.

`POST /api/gallery/transfer` also takes `operation` (`"move"` or `"copy"`) and `collection_id`
(destination folder ID, or null for Home). The destination cannot be inside a selected subtree;
the five-level collection limit also applies to copies. Success returns 200 with `operation`,
`generation_ids`, and `collection_ids`. Move returns the directly moved generation and root folder
IDs; copy returns all new generation IDs and new root folder IDs.

Copy duplicates all retained artifacts into independent files, preserves controls, recall data,
folder structure, preview preferences and favorites, and does not submit a new ComfyUI job.
Input uploads retain the existing reference-counted lifetime. Active or pending-delete generations
anywhere in the selection reject the copy with 409 `copy_generation_active`. Move permits active
generations. Transfers commit once; a failed copy rolls back new rows and removes newly copied files.

`POST /api/gallery/delete` returns 200 with an `items` array, each containing `kind`
(`"generation"` or `"collection"`), `id`, and `status` (`"deleted"`, `"pending"`, or `"failed"`).
Failed items include a safe `message`; clients retain those items for retry. Pending items have
entered the existing active-generation cancellation/deletion lifecycle. Deletion is permanent
and applies to whole generation cards (all their images), including all contents of selected folders.

`POST /api/gallery/favorite` bookmarks each explicitly selected image or folder card, including
children selected alongside a parent. It does not favorite unselected folder contents. Existing
favorites remain set. The operation commits once and returns the deduplicated `generation_ids`
and `collection_ids` with status 200.

`POST /api/gallery/download` returns `application/zip` with attachment filename
`gallery-selection.zip`. The archive contains all currently stored image outputs, including every
batch image and nested folder contents, with overlapping selections included once. Folder paths
use sanitized names and IDs; generation and artifact IDs prevent filename collisions. Cards without
images contribute no files; a selection with no images returns 409 `download_empty`. Active
generations contribute only images already available. The archive is built on disk and the temporary
file is removed after the response or if archive creation fails.

## Generation summaries and detail

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/generations?limit=40&cursor=...&collection_id=` | Newest-first owner page; collection parameter scopes to unfiled or one collection |
| `GET` | `/api/generations/{id}` | Complete owner-scoped generation/result detail |
| `GET` | `/api/generations/{id}/recall` | Exact current-publication recall payload |
| `POST` | `/api/generations/{id}/cancel` | Request running cancellation, or cancel and delete a queued item (`204`) |
| `POST` | `/api/generations/{id}/move` | Move to an owned collection; null unfiles to root |
| `DELETE` | `/api/generations/{id}` | Delete owned history/files; may return 202 while active deletion reconciles |

A summary contains lifecycle status, source display name, `checkpoint_label` (null when the
source exposes no selectable checkpoint), `comfyui_instance_id`, the snapshotted
`comfyui_instance_label`, acceptance/stage state, one optional
active `progress` snapshot, total artifact count, image count, final-image count, one optional
`display_artifact`, expected dimensions, safe error text, recall/favorite/cancel state, native
`prompt_id`, `source_key`, and `publication_id`. The active snapshot may include a cached completion
estimate under `progress.eta`. The display artifact is a gallery convenience selected from the
workflow-authored final when available. `collection_id` is null for unfiled generations and otherwise
identifies the generation's current collection.

Determinate progress is explicitly local to the current ComfyUI node:

```json
{
  "progress": {
    "kind": "node",
    "node_id": "54",
    "display_node_id": "54",
    "real_node_id": "54",
    "parent_node_id": null,
    "label": "Main sampling",
    "value": 12,
    "maximum": 24,
    "fraction": 0.5,
    "eta": {
      "remaining_seconds": 18.4,
      "completion_at": "2026-07-17T12:35:15.189Z",
      "lower_seconds": 14.2,
      "upper_seconds": 25.8,
      "confidence": "high",
      "basis": "batch",
      "sample_count": 5,
      "model_version": 3,
      "updated_at": "2026-07-17T12:34:56.789Z"
    },
    "updated_at": "2026-07-17T12:34:56.789Z"
  }
}
```

Nodes without a valid positive maximum use `kind: "indeterminate"` with null numeric fields.
`eta` is independently optional: a running generation can have node progress without enough safe
historical evidence for a completion estimate. `remaining_seconds` is the point estimate at
`eta.updated_at`; `lower_seconds` and `upper_seconds` are the estimated remaining-time interval at
that same instant and bound the point estimate. `completion_at` is the corresponding absolute
timestamp, `confidence` is `low`, `medium`, or `high`, and `basis` is a safe diagnostic label for
the historical fallback selected by the estimator. `basis` values are additive diagnostic text;
clients must not branch on exact strings.

Confidence reflects sample count and variability. `sample_count` reports retained evidence and
`model_version` identifies the timing model. Version 3 uses verified native execution durations:
`batch`, `historical_exact`, or `historical_nearby`. One verified batch sample is usable at low
confidence. Similar history requires at least three samples. Broad runtime/checkpoint and node
landmark fallbacks are not used.

Clients count down toward the absolute `completion_at`, keeping a stable server/client clock
mapping. Replay must not restart the countdown. After an overrun, remaining seconds clamp to zero;
clients show “Taking longer than expected” and invalidate dependent totals.

The server does not write or broadcast timer-only ticks. Node fractions remain local to the current
node and must never be promoted into a workflow-wide percentage or used by the client to extrapolate
completion time. `progress` is null for fair-queued and terminal generations; requeue clears the old
attempt's ETA, and every terminal outcome clears both progress and its nested ETA.

List pages are bounded summary projections. They do not fetch generation compiled/submitted graphs, raw history, full result diagnostics, or full workflow-profile JSON. Related artifact, image-count, favorite, exact-revision, dependency-health data is resolved in a low constant number of batched statements while preserving owner and cursor ordering.

Generation detail adds:

```json
{
  "comfyui_instance_id": "worker-2",
  "comfyui_instance_label": "Secondary",
  "generation_source": {
    "source_key": "...",
    "instance_id": "home",
    "publication_id": "...",
    "workflow_sha256": "...",
    "api_sha256": "...",
    "manifest_sha256": "..."
  },
  "prompt_id": "native-comfyui-prompt-id",
  "requested_parameters": {"prompt": "...", "seed": "random"},
  "effective_parameters": {"prompt": "...", "seed": "793486291720513"},
  "input_definitions": [
    {"id": "prompt", "type": "string", "label": "Prompt", "semantic_role": "positive_prompt"},
    {"id": "width", "type": "integer", "label": "Width", "semantic_role": "width"}
  ],
  "resolved_seeds": {"seed": "793486291720513"},
  "declared_outputs": [
    {
      "id": "final",
      "label": "Final",
      "role": "final",
      "kind": "image",
      "cardinality": "many",
      "description": "Authoritative final image.",
      "artifacts": [
        {
          "batch_index": 0,
          "filename": "result-00001.png",
          "subfolder": "example",
          "type": "output",
          "artifact": {
            "id": "artifact-id",
            "output_id": "final",
            "role": "final",
            "kind": "image",
            "state": "final",
            "sequence": 4002,
            "batch_index": 0,
            "width": 1024,
            "height": 1024,
            "canonical": true,
            "best_available": true,
            "content_url": "/api/artifacts/artifact-id/content",
            "thumbnail_url": "/api/artifacts/artifact-id/thumbnail",
            "available_at": "2026-07-13T20:00:00Z"
          }
        }
      ]
    }
  ],
  "unmapped_outputs": {"156": {"images": [{"filename": "..."}]}},
  "raw_history": {"outputs": {}, "status": {}},
  "warnings": [],
  "errors": [],
  "comfyui_status": {},
  "artifacts": [],
  "events": []
}
```

`input_definitions` is the frozen, public-only presentation subset of the inputs used by that generation. It preserves labels and semantic roles for history UI while excluding private graph bindings. The complete response also carries compatibility `workflow`, `requested_controls`, and `effective_controls` fields so old stored rows remain readable, plus `final_prompt`, `error_code`, and `delete_pending`.

`declared_outputs` is an ordered list following the frozen manifest. Each item contains both `id` and the compatibility alias `output_id`, plus label, role, kind, `cardinality: "many"`, description, and authoritative ordered logical references. Each reference retains its native locator and nests a matching application-owned `artifact` summary when archival succeeded; `artifact` is `null` when no archive is available. Publisher `artifacts[].batch_index` determines logical batch order; the publisher's mirrored ordinary `images` field is retained in `raw_history` but is not counted again. Publisher node IDs and instance UUIDs are private declaration bindings, although their native history payload remains part of raw result metadata.

`unmapped_outputs` remains node-keyed and copies every nonpublisher node result without field or class filtering. `interface.native_outputs` never filters runtime history. Public `raw_history` removes only top-level submitted graph envelopes such as `prompt` and `extra_data`; it retains the actual node results, publisher metadata, raw status/messages/errors, and execution metadata. Top-level `artifacts` is the compact downloadable set: the latest semantic stage while active, the authored final batch after success, or one best eligible image after cancellation/failure/interruption. Pruned image references remain in declared/unmapped/raw metadata with no application artifact summary. If optional retrieval fails, its logical locator likewise remains and the response carries a warning.

Recall returns `available`, an unavailable reason when relevant, and—when exact—the `source_key`, full `revision`, and effective `parameters`. It also returns `comfyui_instance_id`, the historical `comfyui_instance_label`, `comfyui_instance_configured`, `comfyui_instance_available`, and an optional `comfyui_instance_warning`. A removed or unavailable runtime is reported instead of being replaced by the default. Recall never changes the server assignments, substitutes a newer publication, or submits automatically.

## Artifact, upload, and result access

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/uploads/images` | Decode/store owner-scoped source image |
| `POST` | `/api/uploads/reference-images` | Validate and preserve a static PNG/JPEG/WebP reference image |
| `POST` | `/api/uploads/reference-images/from-artifact/{artifact_id}` | Copy an authorized retained gallery image into an owner-scoped reference asset |
| `POST` | `/api/uploads/masks` | Decode/store owner-scoped mask |
| `GET` | `/api/uploads/{upload_id}/content` | Authorized source preview/original |
| `GET` | `/api/artifacts/{artifact_id}/content` | Authorized retained output |
| `GET` | `/api/artifacts/{artifact_id}/thumbnail` | Authorized WebP derivative |

Upload multipart field name is `file`. Uploads return opaque ID, kind, dimensions, SHA-256, MIME type, and `preview_url`. Published interface v1 currently has no public upload input type; these routes remain for historical records and unrelated application behavior.

ComfyUI file references are never accepted from callers. The worker extracts only bounded `filename` / `subfolder` / `type` tuples from history, with type restricted to `input`, `output`, or `temp`, retrieves them through `/view`, and archives selected bytes before exposing application URLs. Once terminal result processing is durable, successfully transferred `output`/`temp` sources are removed through the installed companion route; `input` files are never included. This backend-to-ComfyUI route is not part of the browser API.

## Prompt Assistant

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/prompt-assistant/status` | Availability without exposing server/model inventory |
| `POST` | `/api/prompt-assistant/compose` | Explicit `refine` or `create` operation |

Composition accepts `mode`, `prompt`, `creative_direction`, and an optional boolean `think` that defaults to `true` for compatibility. It returns `composition_id`, final prompt, selected model, and template version. Pass the owner-scoped ID as `prompt_assistant_run_id` when accepting a generation; the backend then replaces the source's positive-prompt parameter with that run's stored successful output before compilation, regardless of a stale caller-supplied prompt value. Prompt Assistant is never invoked implicitly by generation or recall. Refine requests redraw a normalized unchanged candidate with a new seed and progressively stronger sampling, up to three candidates before failing. Create requests reject any candidate whose normalized text is contained in the normalized Creative Direction (a verbatim echo, truncation, or fragment) and likewise redraw up to three bounded seeds before failing; a candidate must also stay distinct from the current prompt and prior successful outputs for the same direction. The application does not moderate or restrict prompt content.

The status route reads the background health monitor's cached row and never contacts Ollama. A missing or stale check reports unavailable; an old success is not trusted indefinitely. Compose remains authoritative. Transient generate statuses, transport failures, and malformed JSON receive bounded retries. Because Ollama's `num_predict` allowance covers both thinking and final output, a schema-incomplete response with `done_reason: "length"` receives bounded `2048 → 4096 → 8192` budget escalation. Each escalation preserves the requested thinking value, schema, instruction, temperature, and candidate seed; only `num_predict` changes. When thinking is enabled and all three escalations end in `length`, the candidate makes one extra attempt with `think: false` at the base `2048` allowance, reusing the candidate's seed and temperature; the fallback prompt still passes the same distinctness validation, and a failed fallback advances to the next candidate. A complete structured prompt in `response` or the compatibility `thinking` field is accepted even when the done reason is `length`.

Terminal generate errors distinguish rejection (`ollama_generate_rejected`), exhausted transient status (`ollama_generate_unavailable`), timeout (`ollama_generate_timeout`), transport failure (`ollama_generate_transport_error`), malformed JSON (`ollama_generate_invalid_json`), output-budget exhaustion (`ollama_output_budget_exhausted`, HTTP 503), three exhausted normalized-unchanged Refine candidates (`prompt_refinement_unchanged`, HTTP 422), and three Create candidates that never expanded the Creative Direction (`prompt_creation_unchanged`, HTTP 422). Safe `details` include model, HTTP status, `response`/`thinking` presence and lengths, done reason, validation stage, output-budget attempt count, allowances used, the selected allowance when successful, and the no-thinking fallback flag with per-candidate budget history when every candidate is exhausted; failed-run prompt, Creative Direction, and raw reasoning text are not retained.

## Speech to text

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/speech-to-text/status` | Authenticated voice-input configuration status |
| `POST` | `/api/speech-to-text/transcriptions` | CSRF-protected browser audio transcription |

The transcription request is multipart with one `file` field whose media type is `audio/*` or `video/webm`. The application rejects empty audio and recordings over `CIF_SPEECH_TO_TEXT_MAX_BYTES`, then forwards the bytes to the configured OpenAI-compatible endpoint. A successful response is `{ "text": "..." }`. Audio is held only for the request and is not persisted; the upstream URL, API key, and raw upstream response remain server-side.

## Favorites and preferences

| Method | Route | Purpose |
|---|---|---|
| `PUT` | `/api/generations/{id}/favorite` | Idempotently bookmark an owned generation |
| `DELETE` | `/api/generations/{id}/favorite` | Remove bookmark without deleting history |
| `PUT` | `/api/collections/{id}/favorite` | Idempotently bookmark an owned collection; returns updated `Collection` |
| `DELETE` | `/api/collections/{id}/favorite` | Remove collection bookmark; returns `204` |
| `GET` | `/api/preferences` | Read owner gallery scale and checkpoint tier/order preferences; legacy source rating/color fields remain for stored-data compatibility |
| `PUT` | `/api/preferences` | Persist a scale from 0 through 100 and/or per-workflow checkpoint tiers; legacy source rating/color updates remain accepted |

Favorites are private, binary bookmarks. List endpoints expose them as an `is_favorite` boolean:
every `GenerationSummary` (gallery pages and single-generation reads) and every `Collection`
response carry the flag, and the client filters the current view with it rather than fetching a
separate feed. All collection responses include `is_favorite` (default false).

Repeated PUTs are idempotent; removing and re-adding creates a new bookmark. Pending-delete
generations are omitted from collection-scoped gallery pages.
Both collection favorite writes require a ready authenticated user and CSRF token, and return 404
for missing or cross-owner IDs, including for administrators. Removing a bookmark preserves all
content; bookmarking a folder does not bookmark its children or generations. Deleting the target or
its owner cascades the bookmark.

`checkpoint_tiers` is keyed by opaque source key and public selector parameter ID. Each selector maps the fixed tier IDs `top_picks`, `preferred`, `occasional`, and `unsorted` to ordered arrays of stable public checkpoint values. A value may appear at most once per selector. The client reconciles this preference with the current publication so newly published values appear in Unsorted and values no longer published disappear from the dialog.

## Authentication and account routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/auth/session` | Anonymous/authenticated state and CSRF token |
| `POST` | `/api/auth/login` | Local login; signed login-CSRF required |
| `POST` | `/api/auth/logout` | Revoke current session |
| `POST` | `/api/auth/password` | Forced or voluntary password change |
| `GET` | `/api/admin/users` | Administrator: account records only |
| `POST` | `/api/admin/users` | Administrator: create ordinary account |
| `POST` | `/api/admin/users/{id}/reset-password` | Administrator: temporary password and revocation |
| `DELETE` | `/api/admin/users/{id}` | Administrator: cancel/reconcile and delete user data |

Administrator routes never return another user's prompts, parameters, uploads, results, or history.

## Generation activity

`GET /api/generation-activity` returns the signed-in owner's latest combined run and
activity across all of their execution runtimes, independent of gallery pagination.
`run` is null before the first run. Otherwise it includes `id`, `total_count`,
`resolved_count`, `remaining_count`, `succeeded_count`, `failed_count`,
`cancelled_count`, and `completed_at` (null while work remains). Failed submissions,
failed/interrupted jobs, and cancellations resolve planned work without counting as
successful generation. Deleting a card preserves its outcome and original denominator.

The top-level `remaining_count` includes queued, dispatching, running and
cancel-requested generations. `collection_remaining_counts` maps every owned folder
ID to its remaining jobs including descendants. `collection_generation_counts`
contains direct non-pending-delete item counts; absent entries mean zero. Stopping
jobs continue to count as remaining until cancellation reconciles.

`POST /api/generations/batch` accepts `{ "items": [GenerationCreate, ...] }` with
1–256 items and requires the same authentication and CSRF protection as individual
submission. It returns `201` with ordered `items`, each containing either a
`generation` summary or an `error` (`code`, `message`, `fields`, `details`, `status`).
Each item is validated independently. The full planned total, accepted jobs and
submission failures commit together before queued events are published. An unexpected
transaction failure rolls back the entire batch. Both submission endpoints require
`X-CIF-Generation-Protocol: 3` and a UUID `Idempotency-Key`. Older clients receive
`409 client_reload_required`; a missing or malformed key receives
`400 idempotency_key_required`. Authentication and CSRF remain required.

The key is unique within an account across both endpoints. Canonical validated
payloads and endpoint identity determine the receipt digest. Repeating a key with
the same payload returns the original accepted IDs and item failures without choosing
new seeds or consuming another prompt composition. A different payload or endpoint
returns `409 idempotency_conflict`. Receipts commit with all accepted generation rows,
run membership, batch failures, and durable events. Deleting generation results
retains the receipt; replay returns `410 submission_result_unavailable`. Account
deletion cascades its receipts.

`GET /api/generation-submissions/{key}` requires authentication and returns
`{ "key": "UUID", "endpoint": "single" | "batch", "result": ... }`. An unknown
account-scoped receipt returns `404 submission_not_found` and creates no work. Deleted
results return `410 submission_result_unavailable`. Summaries reflect current job
status; identity, seeds, and recorded item failures remain unchanged.

The browser persists the frozen payload and key in account-scoped session storage,
checks for a receipt after reload, and retries a submission at most five times within
60 seconds. Expired budgets retain the key with a visible unknown-status action.
Other mutations retain their existing retry behavior.
The single-generation endpoint preserves its existing contract and joins the same run.

New requests append to the current run while any member remains active; the first
request after completion starts a new run. Adding accepted work extends the total completion estimate. A run survives browser and server restarts. Migration adopts already
active generations into one run per owner without including historical completions.

## Server-Sent Events

`GET /api/events` is an authenticated `text/event-stream`. Reconnection passes `Last-Event-ID` or `?last_event_id=N`. The route replays durable owner events and then subscribes to owner-only live fan-out.

Authentication and replay are materialized in a short-lived database session before streaming begins. The long-lived iterator retains no ORM objects or checked-out database connection; keep-alive session validation uses fresh short-lived sessions.

```text
id: 123
event: generation.running
data: {"id":123,"type":"generation.running","generation_id":"...","created_at":"...","payload":{...}}
```

Events cover queue, dispatch, running/progress, artifact availability, persistence failure,
cancellation/reconciliation, terminal completion/error, requeue, and deletion. Coalesced
`generation.progress` events include the safe current snapshot under `payload.progress` and update
the affected card directly. Node transitions/final counters are durable replay events; intermediate
ticks may be live-only because the same latest snapshot is durable on the generation. Other
lifecycle and artifact events continue to trigger a single-generation fetch. When present, the
nested ETA travels with that same coalesced snapshot; a local countdown does not create additional
SSE traffic.


## Admission and overload

Ordinary API metadata work admits eight active requests, with at most four media
requests and 64 queued requests. Admission precedes authentication and waits at most
five seconds. Queue overflow or expiration returns `503 service_busy` with
`Retry-After: 1`. Cancelled/disconnected waiters release capacity. Long-lived file
and SSE responses release admission and all metadata database sessions before
streaming. Large request bodies retain ASGI transport backpressure while waiting.

`GET /api/health` bypasses admission and uses one coalesced, read-only database probe
with a one-second response deadline. Database or worker readiness failure returns
503. The `load` object exposes aggregate request activity, rejection and wait counts,
connection checkout pressure and duration, and event-loop lag, with no credentials,
request bodies, or SQL parameters. Browser safe reads and thumbnails make at most
four attempts, honoring `Retry-After` and their original overall deadline.


## Generation time projection

`GET /api/generation-activity` retains `run`, `remaining_count` and collection counts for compatibility.
It adds `current_eta` and `queue_eta` (nullable `GenerationEta` objects), `current_generation_id`,
`current_state`, `running_count`, `queued_count`, and `snapshot_at`. Counts and IDs are scoped to the
signed-in account. Estimates cover all accepted image work across pages/collections, including
preparing images, but exclude hypothetical future automatic cycles. Prompt-only work is not image
activity; an unmeasured shared preparation blocks the total without multiplying its duration.

A numeric total requires known durations and scheduling for every blocking stage. Offline services,
unknown external jobs, stale queue observations, cancellation or overdue work yield a null total.
Serial native queues use their actual order and the application's owner fairness; independent
recorded runtime pins are projected in parallel. For multiple executing images `current_eta` means
the next expected finish, labeled Next. No private blocking-job details are exposed.

The compact toolbar uses `Current ~1:24 | All ~8:12`; the browser title uses
`1:24 now · 8:12 all · ImageGen`. Unknown estimates use Estimating…, queued-only work uses Waiting,
and no accepted work hides the badge/title timing immediately. `generation_duration_seconds` uses
verified execution time for new measured successes, retaining the legacy interval for older rows.
