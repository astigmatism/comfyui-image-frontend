# Application API

All routes are same-origin and served beneath `/api`. The application never injects ComfyUI/Ollama credentials or URLs, raw workflow/API graphs, private manifest input bindings, or discovery userdata paths into browser responses. Native output node keys and arbitrary fields actually emitted by ComfyUI remain visible in `unmapped_outputs` and graph-envelope-safe raw history so results are not flattened. Interactive OpenAPI is available at `/api/docs`; `backend/app/schemas.py` is the response-model source of truth.

## Authentication and errors

`GET /api/auth/session` returns an anonymous signed login-CSRF token or the authenticated user and session CSRF token. The opaque session token is an `HttpOnly` cookie. Every authenticated mutation includes:

```http
X-CSRF-Token: <csrf_token>
```

Content lookups are scoped to the current owner. Cross-user requests, including administrator attempts, return not found rather than revealing existence.

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

## Published generation sources

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/workflows` | List current ready, cached/offline, and known unavailable publication summaries |
| `GET` | `/api/workflows/{source_key}` | Get the selected source's allowlisted public interface |
| `GET` | `/api/services` | Restrained ComfyUI/Ollama availability state |
| `POST` | `/api/admin/workflows/refresh` | Administrator: rediscover and atomically validate publications |
| `GET` | `/api/admin/workflows/diagnostics` | Administrator: safe per-transport/per-candidate diagnostics |

The historical route name `workflows` is retained, but objects now represent deliberately published sources.

### Source summary

```json
{
  "source_key": "<opaque-sha256>",
  "display_name": "Krea 2 NSFW V4",
  "instance_id": "home",
  "readiness": "ready",
  "available": true,
  "cached": false,
  "warnings": [],
  "revision": {
    "publication_id": "11111111-1111-4111-8111-111111111111",
    "workflow_sha256": "...",
    "api_sha256": "...",
    "manifest_sha256": "..."
  },
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
      "primary_artifacts": ["model.safetensors"]
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

`readiness` is `loading` before health is known, `ready`, `ready_with_warnings`, `cached_offline`, or a safe unavailable state such as `dependency_missing`. Recorded/observed workflow or API hash drift remains available as `ready_with_warnings`; the revision's `api_sha256` identifies the exact observed, validated graph used for execution. Cached/offline entries remain useful for history/source display but have `available: false`, so new submission is disabled.

Recognized v1 `generation_source` and `technical_inventory` objects are typed, additive, and returned on both summary and detail responses so clients can plan later catalog/dropdown behavior without refetching every source. Older manifests and unrecognized/malformed section schemas return `null` for that section while the raw manifest remains retained server-side. Unknown v1 values, array entries, warning strings, and extra fields are preserved. Artifact basenames, class types, and counts are descriptive only and are never accepted as request selectors. `output_reachable + compiled_orphans = compiled_api` and the accepted API count are checked diagnostically, not as queue gates.

Ordinary source responses describe missing dependencies generically. `technical_inventory.reachable_class_types` and `orphan_class_types` are publisher-declared public inventory; current runtime dependency failures and exact missing classes remain restricted to administrator diagnostics.

During client migration, summaries also carry legacy `profile_id`, workflow/version/hash, contract-schema, and adapter fields. They are compatibility metadata, not the logical source/revision API; new clients use `source_key` and `revision`.

A source detail adds only this public projection:

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

Canonical request:

```json
{
  "source_key": "<opaque-source-key>",
  "revision": {
    "publication_id": "11111111-1111-4111-8111-111111111111",
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

`revision` is optional for a fresh caller but recommended for a UI selection. If the selected source was republished, a mismatch returns HTTP 409 with `source_republished`; the backend never compiles against a silently changed graph.

`parameters` accepts only IDs in the accepted public interface. Unknown parameters and arbitrary graph/binding/path payloads fail. Optional non-seed values use manifest defaults. Optional choices treat omission or `null` as default selection, but reject empty strings, labels, private filenames, and values absent from the current publication. If a companion strength is omitted, the selected option's `default_strength` wins before the numeric input's ordinary default; an explicit non-null numeric value always wins. Random seeds may be omitted, `null`, empty, or the string `random`; fixed seeds should be canonical decimal strings so the full declared integer range survives JavaScript serialization. Seeds are returned as strings. A required image parameter is `{ "asset_id": "opaque-owner-scoped-id" }`; missing assets, unauthorized assets, paths, URLs, and ComfyUI locators fail validation.

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

Invalid compilation returns the standard error envelope with field errors rather than queuing. `POST /api/generations` returns HTTP 201 and a generation summary only after the generation/source snapshot, effective parameters, graph, queue item, and initial event are committed.

Temporary migration aliases `profile_id`, `controls`, `preset_id`, `requested_outputs`, and `expected_identity` remain in the envelope for the pre-publication browser. New clients must not use them. They resolve only to current validated publications and do not restore legacy discovery.

## Generation summaries and detail

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/generations?limit=40&cursor=...` | Newest-first owner page |
| `GET` | `/api/generations/{id}` | Complete owner-scoped generation/result detail |
| `GET` | `/api/generations/{id}/recall` | Exact current-publication recall payload |
| `POST` | `/api/generations/{id}/cancel` | Request running cancellation, or cancel and delete a queued item (`204`) |
| `DELETE` | `/api/generations/{id}` | Delete owned history/files; may return 202 while active deletion reconciles |

A summary contains lifecycle status, source display name, acceptance/stage state, total artifact count, image count, final-image count, one optional `display_artifact`, expected dimensions, safe error text, recall/favorite/cancel state, native `prompt_id`, `source_key`, and `publication_id`. The display artifact is a gallery convenience selected from the workflow-authored final when available.

List and favorites pages are bounded summary projections. They do not fetch generation compiled/submitted graphs, raw history, full result diagnostics, or full workflow-profile JSON. Related artifact, image-count, favorite, exact-revision, dependency-health data is resolved in a low constant number of batched statements while preserving owner and cursor ordering.

Generation detail adds:

```json
{
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

`unmapped_outputs` remains node-keyed and copies every nonpublisher node result without field or class filtering. `interface.native_outputs` never filters runtime history. Public `raw_history` removes only top-level submitted graph envelopes such as `prompt` and `extra_data`; it retains the actual node results, publisher metadata, raw status/messages/errors, and execution metadata. All retrievable image references from declared and unmapped outputs are archived; every batch member appears separately in top-level `artifacts` with `output_id`, role/kind/state, sequence/batch index, dimensions, canonical/best flags, and authorized URLs. If optional retrieval fails, its logical locator remains in declared/unmapped/raw data and the response carries a warning.

Recall returns `available`, an unavailable reason when relevant, and—when exact—the `source_key`, full `revision`, and effective `parameters`. It never substitutes a newer publication or submits automatically.

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

ComfyUI file references are never accepted from callers. The worker extracts only bounded `filename` / `subfolder` / `type` tuples from history, with type restricted to `input`, `output`, or `temp`, retrieves them through `/view`, and archives bytes before exposing application URLs.

## Prompt Assistant

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/prompt-assistant/status` | Availability without exposing server/model inventory |
| `POST` | `/api/prompt-assistant/compose` | Explicit `refine` or `create` operation |

Composition returns `composition_id`, final prompt, selected model, and template version. Pass the owner-scoped ID as `prompt_assistant_run_id` when accepting a generation. Prompt Assistant is never invoked implicitly by generation or recall.

The status route reads the background health monitor's cached row and never contacts Ollama. A missing or stale check reports unavailable; an old success is not trusted indefinitely. Compose remains authoritative and can return a safe 503 if Ollama fails after a recent successful health check.

## Speech to text

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/speech-to-text/status` | Authenticated voice-input configuration status |
| `POST` | `/api/speech-to-text/transcriptions` | CSRF-protected browser audio transcription |

The transcription request is multipart with one `file` field whose media type is `audio/*` or `video/webm`. The application rejects empty audio and recordings over `CIF_SPEECH_TO_TEXT_MAX_BYTES`, then forwards the bytes to the configured OpenAI-compatible endpoint. A successful response is `{ "text": "..." }`. Audio is held only for the request and is not persisted; the upstream URL, API key, and raw upstream response remain server-side.

## Favorites and preferences

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/favorites?limit=40&cursor=...` | Newest-first owner favorites |
| `PUT` | `/api/generations/{id}/favorite` | Idempotently bookmark an owned generation |
| `DELETE` | `/api/generations/{id}/favorite` | Remove bookmark without deleting history |
| `GET` | `/api/preferences` | Read owner gallery scale |
| `PUT` | `/api/preferences` | Persist scale from 0 through 100 |

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

## Server-Sent Events

`GET /api/events` is an authenticated `text/event-stream`. Reconnection passes `Last-Event-ID` or `?last_event_id=N`. The route replays durable owner events and then subscribes to owner-only live fan-out.

Authentication and replay are materialized in a short-lived database session before streaming begins. The long-lived iterator retains no ORM objects or checked-out database connection; keep-alive session validation uses fresh short-lived sessions.

```text
id: 123
event: generation.running
data: {"id":123,"type":"generation.running","generation_id":"...","created_at":"...","payload":{...}}
```

Events cover queue, dispatch, running/progress, artifact availability, persistence failure, cancellation/reconciliation, terminal completion/error, requeue, and deletion. The client fetches current durable generation state after relevant events, so correctness does not depend on transient event payload completeness.
