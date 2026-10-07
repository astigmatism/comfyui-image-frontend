> **Vendored copy — do not edit.** LLM Router client contract, version 1, copied from
> llm-router commit `d5edba88089070e82bb72600e860fd204df88481`. Canonical source:
> https://github.com/astigmatism/llm-router/blob/main/docs/CLIENT_CONTRACT.md
> Replace this copy only when the router maintainer announces a new contract version.

# LLM Router client contract

**Version 1 · 2026-10-07.** Applies to LLM Router `57e4096` and later, with AI Runtime `e0530db` and later.

This is the contract between the LLM Router and every application that sends it inference requests. It states what the router guarantees and what a client must do to keep working while models, contexts and configurations change underneath it. When another document disagrees with this one, this one wins. Detailed schemas and examples are in the [references](#14-references).

**MUST**, **MUST NOT**, **SHOULD** and **MAY** are used in their usual sense: **SHOULD** means "do this unless you have a specific, documented reason not to".

## 1. Roles

| Party | Owns |
|---|---|
| **AI Runtime** | Which models run, on which GPUs, with what context and slot count. Configurations are switched by hand at any time, without notice to clients. It declares each model's qualified capabilities, including whether it is NSFW. |
| **LLM Router** | One stable API in front of whatever is running. It handles admission and queueing, enforces context and capability limits, translates between protocols, describes what is available (`/v1/router/capabilities`), and pushes changes (`/v1/router/events`). |
| **Client** | Choosing a model for each request, managing conversation history, and reacting to changes and errors as this contract describes. |

The router never does any of these:

- substitute one model for another (a request for an unavailable model fails rather than being redirected);
- download, load or switch models;
- keep conversation state between requests;
- impose a total generation or queue deadline;
- infer anything about a model from its name.

## 2. Connecting

| Client location | OpenAI-compatible base URL | Ollama-compatible base URL |
|---|---|---|
| LAN | `http://192.168.1.4:11434/v1` | `http://192.168.1.4:11434` |
| Container on Rosalina's `local-ai-ollama_default` network | `http://ai-router:11434/v1` | `http://ai-router:11434` |

| Route | Use |
|---|---|
| `POST /v1/chat/completions` | OpenAI Chat Completions |
| `POST /v1/responses` (alias `/responses`) | OpenAI Responses, stateless |
| `POST /api/chat` | Ollama chat |
| `POST /api/generate` | Ollama text completion; send `think: false` (reasoning is unsupported on this route); prefer `/api/chat` |
| `GET /v1/models`, `GET /v1/models/{id}`, `GET /api/tags`, `POST /api/show`, `GET /api/ps` | Model listings (§4) |
| `GET /v1/router/capabilities`, `GET /v1/router/events` | Deployment state and change events (§4) |

- **Authentication:** none. A client MUST NOT send the router's admin token or any real credential. If an SDK insists on an API key, send the placeholder `local-only`. The router is a trusted-LAN service and MUST NOT be exposed beyond it.
- **Identification:** a client SHOULD send `X-Client-Name: <application-name>` on every request. The router records it in request history and on its dashboard; without it, requests are identified by user agent or IP address.
- **Not provided:** embeddings (400 `UNSUPPORTED_PROFILE_CAPABILITY`) and model management, meaning pull, create, copy, push and delete (`MODEL_MANAGEMENT_DISABLED`).

## 3. Model identity

| ID | Meaning | Stability |
|---|---|---|
| `daytime` | The Daytime service. Present in **every** configuration. | Stable; use it. |
| `nighttime` | The Nighttime service. Present only in **paired** configurations; a solo configuration stops it. | Stable; use it. |
| `local-active` | Legacy alias of `daytime`. | Kept for compatibility. |
| Canonical IDs, such as `qwen3.8-27b-abliterated-q6_k` | The specific model currently behind a service. | **Can change** with any configuration change. |

- A client MUST send service IDs (`daytime`, `nighttime`). It MUST NOT configure, persist or compare canonical IDs. Responses report the canonical ID in `model`; record it as information only.
- A client SHOULD always send a model. An omitted model selects `daytime`.
- An unknown ID returns **404 `MODEL_NOT_FOUND`**.
- A known service that the current configuration stops returns **503 `SERVICE_OFFLINE`**. It is then listed in `offline_services` (§4).

## 4. Knowing what is available

### Capabilities document

`GET /v1/router/capabilities` returns one document describing the whole deployment, with an `ETag` (send `If-None-Match` to get a 304 when nothing has changed). A client MUST read it at startup, and MUST NOT fail to start when the router is unreachable: start degraded and retry. The fields a client relies on:

| Field | Meaning |
|---|---|
| `revision` | Content hash of everything below except load. If `revision` is unchanged, nothing relevant has changed. |
| `router.accepting_requests` | False while the router drains for a configuration switch, or during maintenance. |
| `configuration.id`, `configuration.exclusive` | The AI Runtime configuration, and whether it is solo. |
| `models[]` | Each usable model: `service` (the ID to send), `aliases`, `available`, `slots`, `context_window`, `input_modalities`, `capabilities`, `nsfw`, `capability_score`, and full `metadata`. |
| `offline_services[]` | Services this configuration deliberately stops: `model`, `aliases`, `display_name`, `reason`. |
| `ids` | Every accepted ID → its current canonical model. |
| `load` (only with `?include=load`) | Per model: `active`, `queued`, `free_slots`. |

Clients MUST ignore unknown fields. `metadata` is identical to the model's `x_ollama_router` object in `/v1/models`. The OpenAI and Ollama listings (`/v1/models`, `/api/tags`) show only models that can be selected right now, so an offline service is absent from them; only the capabilities document says why.

### Change events

`GET /v1/router/events` is a Server-Sent Events stream.

- **Each connection** starts with the complete document as `event: capabilities` (`id:` is its `revision`), then `event: load`.
- **A new `capabilities` event**, always the complete document, follows every change: immediately for a runtime publication, drain or maintenance change; within about 10 s for a backend health change.
- **`load` events** carry slot occupancy, at most once per second.
- **`: keepalive` comments** arrive every 15 s.

During a switch, a subscriber sees: draining (`accepting_requests: false`), then the new configuration, then `accepting_requests: true`.

A long-running client SHOULD hold one subscription for its lifetime and replace its copy of the document whenever `revision` changes. While disconnected it MUST fall back to polling the capabilities endpoint (every 30 s, with `If-None-Match`). It reconnects with backoff starting at the stream's `retry:` value (3 s), up to 30 s. A connection with no bytes for 60 s, keepalives included, is dead. A short-lived or request-scoped client MAY instead fetch the document before each request.

### NSFW flag and capability score

- **`nsfw`** is `true` for models the runtime declares abliterated (refusals removed), `false` for models it declares not, and `null` if undeclared. Treat `null` as "not known to be NSFW".
- **`capability_score`** is an automatic 0–100 ranking; higher is more capable. It is comparable only between models of this router. It is built from three parts:
  - parameter count, scaled down for heavier quantization;
  - context window;
  - vision, tools and reasoning support.

  Availability, load and speed never change it. It cannot measure answer quality: it can't account for what abliteration costs, or compare a mixture-of-experts model fairly with a dense one.

## 5. Choosing a model

A client chooses the service for **each request** from its current document. It chooses in one of two ways and SHOULD make the choice configurable:

- **By capability:** the most capable `available` model that has the features the request needs, optionally restricted to `nsfw: true`, with or without falling back to any model. Prefer this when a service wants Nighttime *because* it is uncensored. The reference clients implement it as `pick_service(doc, nsfw=True, require=[...], fallback_any=True)`.
- **By name:** a preferred service ID plus an ordered fallback list, for example `nighttime` then `daytime`. The reference clients implement it as `resolve(doc, preferred, fallbacks)`.

Apply these rules whichever way you choose:

| Situation | Client action |
|---|---|
| `router.accepting_requests` is false | **Wait** and retry the same choice later (§7). MUST NOT fall back: every model is draining. |
| Chosen model listed and `available` | Use it. |
| Chosen model offline, unavailable or absent | Use the next candidate that is `available` and supports the request's needs. A client MAY disable fallback; it then reports the service as temporarily unavailable. |
| Nothing usable | Report "temporarily unavailable" and retry later. |
| The preferred model returns | Use it again from the next request. Fallback MUST NOT be sticky. |

- **Feature checks:** before sending, check the target model. Images need `image` in `input_modalities`; tools need `tools` in `capabilities`; reasoning needs `thinking` in `capabilities` and the requested effort in `metadata.reasoning.efforts`. A missing capability makes that model unsuitable for the request; don't send and hope.
- **Behavioral differences:** Daytime is not abliterated and may refuse what Nighttime answers. A client that falls back MUST surface a refusal clearly and MUST NOT retry it in a loop.
- **Logging:** a client SHOULD log every change of served model and its reason, and show it where it already shows model status.

Benchmarking and evaluation clients are the exception. A benchmark's identity is its model, so such a client MUST NOT fall back. It treats an offline or unavailable target as "waiting", not as a changed or invalid run.

## 6. Sending requests

- **Streaming:** a client MUST set `stream` explicitly. `/v1/chat/completions` and `/api/chat` **stream when `stream` is omitted**; `/v1/responses` does not.
- **Statelessness:** every request MUST carry the full conversation the model needs. Responses `previous_response_id` and `store: true` are rejected with 400 `STATEFUL_REQUEST_UNSUPPORTED`.
- **Output limits:** output is unrestricted unless the client asks for a limit. To limit it, send a positive integer: `max_tokens` or `max_completion_tokens` (Chat Completions), `max_output_tokens` (Responses), or `options.num_predict` (Ollama, where `-1` means unrestricted). A client MUST NOT send `-1` or `0` on OpenAI routes, and MUST NOT send two conflicting limits. A requested limit is honored exactly, subject to context (§8).
- **Reasoning:** thinking is **on by default**, at the chat template's default effort, with no separate budget. It uses output tokens and time.
  - Choose an effort with `think` (Ollama: `false`, `true`, or an effort), `reasoning_effort` (Chat Completions), or `reasoning.effort` (Responses).
  - The efforts are `off`, `default`, `low`, `medium` and `xhigh`. The aliases `none` → off, `minimal` → low, and `high`/`max` → xhigh are accepted.
  - A client that wants short, fast answers SHOULD turn thinking off explicitly.
- **Tools:** only function tools are supported. The client executes calls and returns results with matching call IDs and the complete preceding history. Provider-executed tools, such as web search, are rejected. Send tools only to models whose `capabilities` include `tools`.
- **Images:** only inline base64 is accepted:
  - Ollama: `messages[].images`;
  - Chat Completions: `image_url` with a `data:image/...;base64,...` URL;
  - Responses: `input_image` with a data URL.

  Remote URLs and file paths are rejected.
- **Structured output:** `response_format` (OpenAI) or `format` (Ollama) is supported. Output that isn't valid JSON ends with an error (§9), never as a success.
- **Sampling:** sampling controls (`temperature`, `top_p`, `top_k`, `seed`, `stop` and similar, top-level or in Ollama `options`) are forwarded or rejected with 400; never silently dropped. Backend controls such as `options.num_ctx` are rejected (`BACKEND_CONTROL_FORBIDDEN`). `seed` accepts 0–4294967295, or -1 for random.

## 7. Capacity, queueing and time

- **Slots:** each model admits `slots` generations at once (usually 1). Aliases of one model share its slots. Extra requests wait in a first-in, first-out queue for that model; Daytime and Nighttime queue independently. A client SHOULD NOT send more concurrent requests to a model than its `slots`. Fallback traffic joins the fallback model's queue, behind its regular users, such as the coding agents on Daytime.
- **Waiting while queued:**
  - Streaming Chat Completions receive `: waiting for inference slot` comments every 15 s.
  - Streaming Ollama chat receives keepalive frames every 15 s: `{"message":{"role":"assistant","content":""},"done":false,"x_router":{"status":"in_progress"},...}`. A client MUST ignore them.
  - Streaming Responses receive `response.created` and `response.in_progress` immediately.
  - Non-streaming requests receive nothing until the result.
- **Deadlines:** there is no queue or total-generation deadline. A client's read timeout MUST allow for queueing plus generation, or the client MUST stream. A backend that stops making progress for 120 s ends the request as an error.
- **Cancellation:** closing the connection cancels the request immediately, including while it is queued. A client SHOULD NOT abandon and resubmit a queued request, because that loses its place.
- **Draining:** during a configuration switch or maintenance, new requests receive 503 `BACKEND_DRAINING` or `MAINTENANCE_MODE`. Requests already accepted, including queued ones, finish normally. A switch usually takes one to two minutes. A client MUST keep waiting and retrying (backoff from 2 s up to 30 s) for at least 10 minutes, then choose its model again: the configuration may have changed.
- **Health checks:** a client SHOULD NOT use `GET /health` to judge model availability; use the capabilities document.

## 8. Context

- **Budget:** each request must fit the target model's per-request `context_window`. The router counts the actual formatted prompt with the model's tokenizer, including the chat template, tools and images. A request is admitted when **formatted input + requested output + `metadata.context_safety_reserve` (1024) ≤ `context_window`**. A request with unrestricted output needs room for at least one generated token.
- **Admission:** a request that doesn't fit, with a requested limit, is rejected with 400 `context_length_exceeded` and the exact arithmetic. Nothing is truncated silently.
- **Recovery for unrestricted requests:** an unrestricted request whose history doesn't fit, or whose generation reaches the end of the context, triggers lossy recovery. The router keeps the system and developer instructions and the latest user turn, fits an excerpt of earlier work, and inserts a visible notice beginning `[Physical context boundary reached.` into the answer. This is **not** lossless memory. `x_router.context_transitions` counts how many times it happened.
- **Managing history:** a client that needs exact history MUST manage it itself: trim or summarize before sending, or send an explicit output limit so overflow is rejected rather than recovered. Tool-call history is shortened only when that is safe; otherwise the request fails with `CONTEXT_RECOVERY_UNAVAILABLE`.
- **Model changes:** a client MUST take limits from the model that will actually serve the request, and MUST recompute its budget when the served model changes. Daytime and Nighttime windows differ, and either may be larger. A client MUST NOT hard-code a context size.

## 9. Results and terminal states

- **Success:** a completed response has a finish reason: `stop`, `tool_calls`, or `length` (the requested output limit was reached). Chat Completions results carry `x_router: {status, stop_reason, record_id, context_transitions}`; Ollama results carry `done_reason`; Responses carry `status` and `incomplete_details`.
- **Incomplete:** a response with `x_router.status: "incomplete"`, Responses `status: "incomplete"`, or Ollama `done_reason: "error"` is **not** a completed answer, even when it contains text. A client MUST NOT treat it as one. In particular:
  - a thinking-only or empty answer fails with `EMPTY_UPSTREAM_RESPONSE`;
  - partial tool arguments fail with `MALFORMED_UPSTREAM_TOOL_ARGUMENTS` and MUST NOT be executed;
  - invalid structured output fails with `MALFORMED_STRUCTURED_OUTPUT`.
- **Errors inside a stream:** once a streaming response has started, including while queued, a later error is delivered **inside the stream** after an HTTP 200:
  - Chat Completions: a `data:` frame with an `error` object (including `code`) and `x_router.status: "incomplete"`, possibly followed by `data: [DONE]`;
  - Ollama: a final line with `"done": true`, `"done_reason": "error"`, a **string** `error` and `x_router.status: "incomplete"`, with the code in `x_router.stop_reason` when known;
  - Responses: `response.failed` or `error` events.

  A streaming client MUST check the final frame, and MUST treat a stream that ends without a normal finish as incomplete.
- **Tracing:** `x-router-generation-id` (a response header) and `x_router.record_id` identify the router's archive record. Include them in bug reports.

## 10. Errors

Every router error carries a machine-readable code:

- **Ollama and Chat Completions:** `{"error": {"code": "…", "message": "…"}}`.
- **Responses, and some Chat Completions errors:** the OpenAI shape `{"error": {"message": "…", "type": "…", "param": "…", "code": "…"}}`.
- **Inside a stream:** see §9.

A client MUST read `error.code` from the object form, and SHOULD tolerate a string `error`. React to the code first, then the HTTP status:

| Code (HTTP status) | Meaning | Client action |
|---|---|---|
| `SERVICE_OFFLINE` (503) | The current configuration deliberately stops this service | Fall back now (§5). Not a failure: don't count it toward retry caps, budgets or circuit breakers. |
| `BACKEND_UNAVAILABLE` (503) | The service's backend isn't healthy | Fall back if possible; otherwise retry with backoff. |
| `MODEL_NOT_FOUND` (404) | The ID isn't offered | Fall back if configured, and log a warning: usually a stale or misspelled ID. |
| `BACKEND_DRAINING`, `MAINTENANCE_MODE` (503) | Switch or maintenance in progress | Wait (§7), then choose again. |
| `context_length_exceeded` (400) | The request doesn't fit | Shrink the request, or choose a model with a larger window. Don't retry unchanged. |
| `TOO_MANY_SUBSCRIBERS` (503, events only) | Event-stream limit reached | Poll the capabilities endpoint instead. |
| Other 5xx; 408, 429; network errors and timeouts | Transient | Retry the same service with backoff. |
| Other 4xx | The request is invalid | Fix the request. Don't retry and don't fall back. |

New codes may be added. An unknown 5xx code is transient; an unknown 4xx code is a request error. The reference clients' `classify_error(status, body)` implements this table.

## 11. Change and compatibility

- **Schema versions:** the capabilities document has `schema_version: 1`; model metadata (`x_ollama_router`) has `schema_version: 2`; the capability score carries its own `version`. Fields may be **added** at any time without a version change, and clients MUST ignore fields they don't know. Removing a field or changing its meaning increments the schema version. A client SHOULD warn when it sees a schema version it wasn't written for.
- **Contract versions:** this contract is versioned at the top. A change that requires client changes increments that version and is announced to client maintainers.
- **Configurations:** models, context windows, slot counts, the canonical IDs behind service IDs, and the existence of `nighttime` all change with configuration and are not part of the contract. Only the rules for discovering them are.
- **Copies in client projects:** every client project keeps this contract in its own repository, so whoever works on that project, person or AI agent, sees it there:
  - **Copy:** keep a verbatim copy at `docs/llm-router-contract.md`. Begin it with this header, then the contract text unchanged:

    ```markdown
    > **Vendored copy — do not edit.** LLM Router client contract, version <N>, copied from
    > llm-router commit `<sha>`. Canonical source:
    > https://github.com/astigmatism/llm-router/blob/main/docs/CLIENT_CONTRACT.md
    > Replace this copy only when the router maintainer announces a new contract version.
    ```

  - **Conformance map:** follow the copy with a section `## How <project> upholds this contract`, or put it in its own file linked from there. It maps each item of the conformance checklist (§13) to the code and tests that meet it, and records any deviation the contract permits, such as fallback disabled under §5.
  - **Agent rule:** add a rule to the project's `AGENTS.md`, or its equivalent: any change that touches router requests, model selection or discovery must uphold `docs/llm-router-contract.md` and keep the conformance map current.
  - **Updates:** when this contract's version changes, the router maintainer sends each project a handoff to replace its copy and review its map.

## 12. Data handling

- **Archiving:** the router archives every generation in full — messages, images, tools, reasoning and output — for up to seven days or 1 GiB, for diagnosis and recovery. Clients MUST NOT send secrets they don't want retained, and SHOULD tell their own users that conversations pass through a logged service.
- **NSFW content:** output from an `nsfw: true` model may be explicit. A client that exposes it to people is responsible for any age, audience or consent controls its context needs.

## 13. Conformance checklist

A client conforms when it meets every item below and has a test for each MUST:

- [ ] Sends service IDs only; persists no canonical IDs; sends `X-Client-Name`.
- [ ] Reads the capabilities document at startup without failing when the router or a model is unavailable.
- [ ] Long-running: subscribes to `/v1/router/events` and polls while disconnected. Request-scoped: reads the document before each request.
- [ ] Chooses the model per request by capability or by name, with configurable fallback (or, for benchmarks, deliberately none). Returns to the preferred model when it is available again.
- [ ] Waits, without switching, while the router drains or is in maintenance.
- [ ] Classifies errors by `error.code`, as in §10. `SERVICE_OFFLINE` doesn't consume retry or budget allowances.
- [ ] Sets `stream` explicitly; handles queue keepalives and errors inside streams; never treats an incomplete response as complete.
- [ ] Takes context, reserve, slots and features from the serving model, and recomputes them when it changes. Hard-codes no context size.
- [ ] Uses read timeouts that allow for queueing, or streams. Doesn't abandon and resubmit queued requests.
- [ ] Logs model changes and fallbacks, and surfaces refusals from a fallback model instead of retrying them.
- [ ] Keeps a verbatim copy of this contract at `docs/llm-router-contract.md`, a conformance map, and an `AGENTS.md` rule that upholds it (§11).

## 14. References

- [Deployment capabilities and change events](CAPABILITIES.md): the full document schema, event timing and score formula.
- [API reference](API.md), including [llama.cpp sampling controls](API.md#llamacpp-sampling-controls).
- [Model discovery](MODEL_DISCOVERY.md): the `x_ollama_router` metadata fields.
- [Primary integration](PRIMARY_INTEGRATION.md): output policy, queueing, context recovery and archives.
- Reference clients, tested against the router: [Python](clients/router_watch.py) (standard library) and [JavaScript](clients/router-watch.mjs) (Node 18+ and browsers).
- [Client handoff](handoffs/2026-10-07-capability-subscribers.md): how each current project should change to meet this contract.

## How ComfyUI Image Frontend upholds this contract

This section is ComfyUI Image Frontend's own; the contract text above it is the router maintainer's and is never edited here. Keep this map current with every change to router requests, model selection or discovery ([`AGENTS.md`](../AGENTS.md)). The router-facing code is [`backend/app/services/llm_router.py`](../backend/app/services/llm_router.py) (discovery, selection, error classification and the event-stream subscriber, ported from the reference client `router_watch.py` at llm-router `d5edba8`) and [`backend/app/services/ollama.py`](../backend/app/services/ollama.py) (Prompt Assistant composition and vision requests on `/api/chat`).

**Selection rule (fixed by the owner).** For every compose and vision request the model is chosen by capability from the current document: the `available` models with every feature the request needs (`image` in `input_modalities` for vision checks); the highest `capability_score` among those with `nsfw: true`; otherwise the highest-scoring model of any kind, reported as a non-NSFW fallback; and, while `router.accepting_requests` is false, wait without choosing. This is `pick_service(doc, nsfw=True, require=[...], fallback_any=True)`. Settings: `CIF_OLLAMA_SELECTION` (`capability` default, or `named`: `CIF_OLLAMA_MODEL` then `CIF_OLLAMA_FALLBACK_MODELS`, default `nighttime`, `daytime`), `CIF_OLLAMA_NSFW` (`prefer` default, `require`, `avoid`, `any`), and `CIF_OLLAMA_ROUTER_WAIT_SECONDS` (default and minimum 600).

Test paths below are relative to `backend/tests/`.

| # | Checklist item | Code | Tests |
|---|---|---|---|
| 1 | Sends service IDs only; persists no canonical IDs; sends `X-Client-Name` | `pick_service`/`resolve` return a model's `service`; `OllamaAdapter._generate` sends `RouterSelection.service` as `model`; the shared `httpx.AsyncClient` sends `X-Client-Name: comfyui-image-frontend` on every request (capabilities, events, chat). Stored provenance (`raw_response_json.router`, status row) holds service IDs only; the canonical ID a response reports is kept as the informational `model_name`/`model`, never sent, configured or compared. | `unit/test_ollama_router.py::test_every_router_request_identifies_the_client_and_sends_service_ids_only`, `::test_the_read_timeout_allows_for_queueing_behind_long_generations`; `unit/test_llm_router.py::test_fetch_sends_if_none_match_and_keeps_the_document_on_304`; `integration/test_llm_router_contract.py::test_compose_follows_configuration_switches_without_sticky_fallback` |
| 2 | Reads the capabilities document at startup without failing when the router or a model is unavailable | `QueueWorker.start` starts `_router_watch_loop` (lifespan task beside `_health_loop`) → `OllamaAdapter.watch_router` → `RouterWatch.run_forever`, whose first step is the tolerant `RouterWatch.refresh`. Without a document the assistant reports `router_unreachable` and sends nothing by capability; an unavailable model is simply not a candidate. Unknown fields are ignored and an unexpected `schema_version` is warned about once. | `integration/test_llm_router_contract.py::test_router_unreachable_at_startup_does_not_prevent_startup`; `unit/test_llm_router.py::test_subscriber_survives_an_unreachable_router_at_startup`, `::test_refresh_tolerates_an_unreachable_router_and_keeps_the_last_document`, `::test_invalid_documents_are_rejected_and_unknown_fields_ignored`, `::test_unexpected_schema_versions_are_warned_about_once`; `unit/test_ollama_router.py::test_router_unreachable_reports_unavailable_without_sending`, `::test_unhealthy_nsfw_model_falls_back_to_daytime` |
| 3 | Long-running: subscribes to `/v1/router/events` and polls while disconnected | `RouterWatch._follow` holds one SSE subscription (60 s read timeout = dead stream; `: keepalive` comments and `load` events ignored; `capabilities` events replace the document when `revision` changes); `RouterWatch.run_forever` polls `GET /v1/router/capabilities` with `If-None-Match` after every disconnect and reconnects with backoff from the `retry:` hint (3 s) to 30 s, including on `TOO_MANY_SUBSCRIBERS`. Each change rewrites the status row at once (`QueueWorker._on_router_change`). When no subscriber runs (background worker disabled), `OllamaAdapter._ensure_document` reads the document before each request instead. | `unit/test_llm_router.py::test_subscriber_follows_events_replaces_the_document_and_ignores_keepalives`, `::test_subscriber_polls_while_disconnected_and_backs_off_from_3_to_30_seconds`, `::test_subscriber_polls_when_the_event_stream_has_too_many_subscribers`, `::test_a_connection_that_delivered_reconnects_after_the_retry_hint`; `integration/test_llm_router_contract.py::test_subscriber_keeps_the_status_endpoint_current`, `::test_subscriber_polls_while_the_event_stream_is_refused` |
| 4 | Chooses the model per request by capability or by name, with configurable fallback; returns to the preferred model when it is available again | `OllamaAdapter.select_model` (no network) and `OllamaAdapter._choose`, called for every `/api/chat` request; capability mode maps `CIF_OLLAMA_NSFW` to `pick_service(nsfw=True, fallback_any=True)` (`prefer`), `nsfw=True` (`require`), `nsfw=False` (`avoid`) or `nsfw=None` (`any`); named mode uses `resolve(doc, CIF_OLLAMA_MODEL, CIF_OLLAMA_FALLBACK_MODELS)` with the same feature checks (an empty fallback list disables fallback). Nothing is sticky: every request chooses again. | `unit/test_llm_router.py::test_paired_configuration_prefers_the_most_capable_nsfw_model`, `::test_solo_configuration_falls_back_to_the_most_capable_model`, `::test_two_nsfw_models_choose_the_higher_score_then_the_larger_context`, `::test_vision_requirement_selects_the_only_model_with_image_input`, `::test_named_selection_uses_the_preferred_service_then_fallbacks`, `::test_nsfw_null_is_not_known_to_be_nsfw`; `unit/test_ollama_router.py::test_paired_configuration_uses_nighttime`, `::test_solo_configuration_uses_daytime_and_reports_the_fallback`, `::test_unhealthy_nsfw_model_falls_back_to_daytime`, `::test_two_nsfw_models_use_the_higher_score`, `::test_require_nsfw_with_none_available_is_unavailable_without_sending`, `::test_nsfw_preference_settings`, `::test_named_mode_sends_the_preferred_service_then_its_fallbacks`, `::test_named_mode_with_fallback_disabled_reports_unavailable`, `::test_named_mode_requires_a_service_name`, `::test_the_nsfw_model_is_used_again_as_soon_as_it_returns`; `unit/test_expectations.py::test_vision_check_uses_the_only_model_that_accepts_images`; `integration/test_llm_router_contract.py::test_compose_follows_configuration_switches_without_sticky_fallback` |
| 5 | Waits, without switching, while the router drains or is in maintenance | `pick_service`/`resolve` return `WAIT` while `accepting_requests` is false and `_choose` waits; `BACKEND_DRAINING`/`MAINTENANCE_MODE` answers wait too. Both share one `_RouterWaitBudget` per request: 2 → 30 s backoff (waking early when the document changes) for at least `CIF_OLLAMA_ROUTER_WAIT_SECONDS` (≥ 600), then the model is chosen again with exclusions cleared. The status row reports `state: "waiting"`. | `unit/test_llm_router.py::test_draining_router_waits_instead_of_choosing`; `unit/test_ollama_router.py::test_draining_waits_without_switching_then_chooses_again`, `::test_drain_and_maintenance_codes_wait_then_choose_again`, `::test_waiting_lasts_at_least_ten_minutes_with_backoff_up_to_30_seconds`, `::test_draining_status_reports_waiting`; `integration/test_llm_router_contract.py::test_draining_router_holds_the_request_until_the_switch_finishes` |
| 6 | Classifies errors by `error.code` (section 10); `SERVICE_OFFLINE` doesn't consume retry or budget allowances | `classify_error`/`error_code` (object form first; a string `error` falls back to the status) drive `OllamaAdapter._generate`: `SERVICE_OFFLINE`, `BACKEND_UNAVAILABLE`, `MODEL_NOT_FOUND` refresh the document and choose again at once with the failed service excluded, consuming none of the three transient attempts (`MODEL_NOT_FOUND` is logged as a warning; `BACKEND_UNAVAILABLE` with nothing to fall back to is retried with backoff); drain codes wait (item 5); other 5xx, 408, 429 and connection failures retry the same service; other 4xx fail (`context_length_exceeded` → `ollama_context_exceeded`). Only when no service at all is usable does the request report `ollama_unavailable`, which callers retry later. | `unit/test_llm_router.py::test_errors_are_classified_by_code_then_status`, `::test_error_code_reads_the_object_form_only`; `unit/test_ollama_router.py::test_service_offline_falls_back_at_once_and_costs_no_retries`, `::test_unusable_services_fall_back_to_the_next_candidate`, `::test_backend_unavailable_without_a_fallback_retries_with_backoff`, `::test_service_offline_with_nothing_left_is_unavailable`, `::test_other_server_errors_retry_and_other_client_errors_fail`; `integration/test_workflows_and_prompt_assistant.py::test_prompt_assistant_retries_a_transient_generate_failure` (string `error`) |
| 7 | Sets `stream` explicitly; handles queue keepalives and errors inside streams; never treats an incomplete response as complete | Every inference request sends `"stream": false` (`_generate_payload`, `evaluate_image`), so it receives no queue keepalives and no in-stream errors; the event-stream subscriber ignores keepalive comments. An answer with `x_router.status: "incomplete"` or `done_reason: "error"` is never accepted (`_router_incomplete`): an output-limit stop escalates the output budget even when its text parses, anything else is retried as transient and then reported as `ollama_generate_incomplete`. | `unit/test_ollama_router.py::test_an_incomplete_answer_is_never_treated_as_complete`, `::test_a_length_limited_router_answer_escalates_instead_of_being_accepted`, `::test_every_router_request_identifies_the_client_and_sends_service_ids_only`; `unit/test_llm_router.py::test_subscriber_follows_events_replaces_the_document_and_ignores_keepalives` |
| 8 | Takes context, reserve, slots and features from the serving model and recomputes them when it changes; hard-codes no context size | All limits are read from the document's entry for the service chosen for that request (`model_for`): features in `pick_service`/`resolve`; reasoning effort in `thinking_value` (`xhigh` only when listed in `metadata.reasoning.efforts`, else the closest listed effort, else `true`; `false` for a model without thinking); `num_predict` reduced to fit `context_window − context_safety_reserve − input` (`_fit_output_limit`); concurrency per service limited to its `slots` (`_ServiceSlots`). No context size is hard-coded. | `unit/test_ollama_router.py::test_the_output_limit_fits_the_serving_models_context_window`, `::test_requests_to_one_service_never_exceed_its_slots`, `::test_thinking_effort_is_checked_against_the_chosen_model`, `::test_a_model_without_thinking_receives_think_false`; `unit/test_llm_router.py::test_thinking_effort_is_sent_only_when_the_model_lists_it`, `::test_model_for_returns_the_limits_of_the_model_behind_a_service`; `unit/test_expectations.py::test_vision_check_without_any_image_model_is_unavailable_without_sending` |
| 9 | Uses read timeouts that allow for queueing, or streams; doesn't abandon and resubmit queued requests | The client's read timeout is 900 s (`OllamaAdapter.__init__`). Only a connect timeout is retried; a read timeout is reported (`ollama_generate_timeout`) and never resubmitted, because that would lose the request's place in the queue. | `unit/test_ollama_router.py::test_the_read_timeout_allows_for_queueing_behind_long_generations`; `unit/test_ollama.py::test_read_timeout_is_classified_without_retrying_or_retaining_prompt_text` |
| 10 | Logs model changes and fallbacks; surfaces refusals from a fallback model instead of retrying them | `RouterWatch._set` logs `llm_router_capabilities_changed`; `OllamaAdapter._note_selection` logs every change of chosen service as `llm_router_model_changed` with its reason (warning while on a fallback); the startup log `prompt_assistant_router_selection` names the mode and preference. `GET /api/prompt-assistant/status` returns `router` (service, `nsfw`, configuration ID, `fallback`, `notice`), shown in the Creative Direction panel and focused editor (`frontend/src/render.mjs` `promptAssistantModelNoticeMarkup`). A non-NSFW model that refuses, answers outside the schema, or returns `MALFORMED_STRUCTURED_OUTPUT`/`EMPTY_UPSTREAM_RESPONSE` raises `ollama_model_declined` ("No NSFW model is available; Daytime declined this request.") with no redraw or retry; Auto-generate and expectation checks treat that code as terminal. | `unit/test_ollama_router.py::test_selection_changes_are_logged_with_their_reason`, `::test_a_fallback_refusal_is_reported_plainly_and_never_retried`, `::test_fallback_output_that_fails_the_schema_is_a_decline`, `::test_named_fallback_refusal_names_the_unavailable_preference`, `::test_the_nsfw_model_is_not_second_guessed_for_refusal_wording`; `unit/test_expectations.py::test_a_declining_non_nsfw_reviewer_is_reported_without_redraws`, `::test_capabilities_come_from_the_router_document_never_from_names`; `integration/test_llm_router_contract.py::test_a_declining_fallback_is_reported_plainly_and_recorded`, `::test_subscriber_keeps_the_status_endpoint_current`; `frontend/test/render.test.mjs` "Creative Direction says when the LLM Router serves it from a non-NSFW fallback" |
| 11 | Keeps a verbatim copy at `docs/llm-router-contract.md`, a conformance map, and an `AGENTS.md` rule | This file (header, verbatim version 1 text, this map) and [`AGENTS.md`](../AGENTS.md). The traceability prose in `scripts/generate_traceability.py` points here. | `unit/test_ollama_router.py::test_the_contract_is_vendored_verbatim_with_a_conformance_map_and_agent_rule` (SHA-256 of the vendored text, every map row, the agent rule) |

### Interpretations and permitted deviations

- **Effort shape.** The router publishes `metadata.reasoning.efforts` as an object keyed by effort name (`{"default": …, "off": …, "xhigh": …}`); both that and a plain list are accepted, and the efforts are matched by name after the section 6 aliases.
- **Thinking is not a selection filter.** Thinking is optional quality, while NSFW status is the owner's selection rule, so `reasoning` is not added to `require`; the effort is adapted to the chosen model instead (never an unlisted effort, `false` for a model that cannot think).
- **`length` versus incomplete.** Section 9 lists `length` as a completed finish reason, but the router marks an output-limit stop `x_router.status: "incomplete"` (`stop_reason: "max_output_tokens"`). Following the MUST, such an answer is never accepted, even when its text parses; the existing `2048 → 4096 → 8192` budget ladder escalates instead. A plain Ollama answer without `x_router` keeps the earlier behavior.
- **Timeouts.** Section 10 lists timeouts as transient, but section 7 forbids abandoning and resubmitting a queued request; read timeouts are therefore reported, not retried. Connect timeouts and connection failures are retried.
- **Status while draining.** The status endpoint reports `available: false` with `state: "waiting"` during a switch, so the browser does not start new compositions; requests already sent, Auto-generate, and expectation checks wait as item 5 describes.
- **Named mode without a document.** As in the reference `resolve`, named mode sends the preferred service when no document could be read and lets error classification decide, while the status endpoint reports the router unreachable until a document arrives. Capability mode sends nothing without a document.
- **Refusal detection.** Only models not declared NSFW (`nsfw` false or null) are checked for refusals: a prompt or review summary that opens like a refusal ("I'm sorry", "I can't", …), output that fails the schema, or the router's `MALFORMED_STRUCTURED_OUTPUT`/`EMPTY_UPSTREAM_RESPONSE`.
- **Context.** Requests are small (bounded instructions, prompt, Creative Direction and one 1024 px image), so `context_length_exceeded` is reported (`ollama_context_exceeded`) rather than retried on a larger model; the output limit is fitted to the serving model's window before sending.
- **Credentials.** The router needs none. `CIF_OLLAMA_API_KEY` defaults to unset and the example uses the `local-only` placeholder; any other value logs `prompt_assistant_router_credential_configured` at startup.
- **Data handling (section 12).** The README tells operators that Prompt Assistant requests (Creative Direction, prompts, instructions and probe images) pass through the router, which archives every generation for up to seven days; the application shows no separate notice to users and sends no secrets to the router. The application is a private appliance with administrator-provisioned accounts, so who may see NSFW output is the owner's decision.
- **Benchmark exception.** Not applicable: this is not a benchmarking client, so fallback is enabled by default.
