# Application API

All routes are same-origin. The browser never receives ComfyUI/Ollama credentials, URLs, raw workflow graphs, node selectors, or filesystem paths.

Interactive OpenAPI is available at `/api/docs` on a running instance. The source of truth for response models is `backend/app/schemas.py`.

## Authentication model

`GET /api/auth/session` returns either an anonymous signed login CSRF token or the authenticated user plus the server-session CSRF token. The opaque session token is an `HttpOnly` cookie.

Every authenticated mutation requires:

```http
X-CSRF-Token: <csrf_token returned by /api/auth/session or /api/auth/login>
```

Content routes are owner-scoped. Cross-user and administrator attempts against another user's content return not found rather than disclosing existence.

## Error shape

Application and validation errors use one safe shape:

```json
{
  "error": {
    "code": "control_validation_failed",
    "message": "Some workflow controls are invalid.",
    "fields": {"size.width": "Maximum 2048."},
    "details": {},
    "request_id": "..."
  }
}
```

Internal traces, secrets, passwords, cookies, and full server paths are not returned.

## Authentication and account

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/auth/session` | Anonymous/authenticated shell state and CSRF token |
| POST | `/api/auth/login` | Local username/password login; signed login-CSRF required |
| POST | `/api/auth/logout` | Revoke current session |
| POST | `/api/auth/password` | Forced or voluntary password change |

## Administrator-only non-content routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/admin/users` | List account records |
| POST | `/api/admin/users` | Create ordinary user with temporary password |
| POST | `/api/admin/users/{id}/reset-password` | Set temporary password and revoke sessions |
| DELETE | `/api/admin/users/{id}` | Cancel/reconcile work and delete all application-owned user data |
| POST | `/api/admin/workflows/refresh` | Re-run network discovery and validation |
| GET | `/api/admin/workflows/diagnostics` | Safe accepted/rejected profile diagnostics |

No administrator endpoint returns prompts, controls, uploads, artifacts, or histories for another user.

## Workflows and service state

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/workflows` | Current valid semantic workflow summaries |
| GET | `/api/workflows/{profile_id}` | Resolved public contract with controls/capabilities/stages/outputs |
| GET | `/api/services` | Restrained ComfyUI/Ollama availability state |

The public contract strips bindings, selectors, node IDs, graph documents, internal dependencies, and operator-only controls.

## Uploads

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/uploads/images` | Decode/store an owner-scoped source image |
| POST | `/api/uploads/masks` | Decode/store an owner-scoped mask |
| GET | `/api/uploads/{upload_id}/content` | Authorized preview/original retrieval |

Multipart field name is `file`. Responses return an opaque application upload ID, dimensions, hash, MIME type, and same-origin preview URL. Clients never provide a server path.

## Prompt Assistant

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/prompt-assistant/status` | Availability and safe explanation |
| POST | `/api/prompt-assistant/compose` | Explicit refine/create operation |

Example request:

```json
{
  "mode": "refine",
  "prompt": "portrait in window light",
  "creative_direction": "35mm film, restrained color"
}
```

The response contains the finalized prompt, composition ID, selected model, and template version. Generate does not call Ollama. Passing the composition ID with generation acceptance links provenance; the visible submitted prompt remains authoritative.

## Generation validation and acceptance

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/generations/validate` | Compile/validate without creating history |
| POST | `/api/generations` | Durably accept one immutable request |

Representative acceptance request:

```json
{
  "profile_id": "<registry UUID>",
  "controls": {
    "prompt.text": "cinematic portrait",
    "generation.seed": "random",
    "size.resolution": {"width": 1024, "height": 1024}
  },
  "preset_id": null,
  "requested_outputs": [],
  "prompt_assistant_run_id": null,
  "expected_identity": null
}
```

`expected_identity` is populated after recall and contains exact workflow/version/UI/API/contract hashes. A mismatch returns conflict rather than compiling a replacement.

Validation rejection creates no gallery record. Acceptance resolves random seeds to integers, stores all snapshots, inserts a queue event, and returns one summary immediately.

## Gallery and details

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/generations?limit=24&cursor=...` | Newest-first owner cursor page |
| GET | `/api/generations/{id}` | Full owner detail, events, controls, artifact timeline |
| GET | `/api/generations/{id}/recall` | Exact-recall payload or unavailable reason |
| POST | `/api/generations/{id}/cancel` | Queue/running cancellation request |
| DELETE | `/api/generations/{id}` | Immediate terminal/queued delete or `202` pending running reconciliation |
| GET | `/api/artifacts/{id}/content` | Authorized original artifact |
| GET | `/api/artifacts/{id}/thumbnail` | Authorized WebP derivative |

A summary contains only one `display_artifact` for card rendering even when the detail timeline contains multiple checkpoints or final batch siblings.

## Preferences

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/preferences` | Current owner's gallery scale |
| PUT | `/api/preferences` | Persist integer scale from 0 through 100 |

## Server-Sent Events

`GET /api/events` is an authenticated `text/event-stream`. Reconnection can pass either `Last-Event-ID` or `?last_event_id=N`. The route first replays durable owner events after that ID and then subscribes to owner-only live fan-out.

Event envelope:

```text
id: 123
event: artifact.available
data: {"id":123,"type":"artifact.available","generation_id":"...","created_at":"...","payload":{...}}
```

Published types include queue/dispatch/running/stage/progress, artifact availability/persistence failure, cancel request/reconciliation, terminal error/completion, requeue, and deletion. The client fetches current durable generation state after an event, so reconnection does not depend on transient payload completeness.
