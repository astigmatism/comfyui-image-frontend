> **Vendored copy — do not edit.** LLM Router client contract, version 1.6, copied from
> llm-router commit `54d9517fa51de39216573d8faae1c82818c32206`. Canonical source:
> https://github.com/astigmatism/llm-router/blob/main/docs/CLIENT_CONTRACT.md
> Replace this copy only when the router maintainer announces a new contract version.

# LLM Router client contract

**Version 1.6 · 2026-11-12.** Applies to LLM Router `87517ef` and later, with AI Runtime `e0530db` and later. Version 1.1 added the benchmark exception in §3; version 1.2 records the opt-in `trial` service ID and states what a published configuration really guarantees; version 1.3 describes what a router that spans several machines publishes, and what it does when one of them is missing; version 1.4 states how an alias is titled in the model listings, so that several names for one model read as the several names they are; version 1.5 adds `display_name_qualified`, the one label per row a client may render and match on when two publishers chose the same words; version 1.6 writes down the reading rules a multi-host deployment was already exercising — what each `hosts[]` field licenses a client to conclude, the four ways a model stops being available and the one of them that carries a reason, what a null `capability_score` does and does not mean, and `nsfw` as an attestation rather than a forecast. No client change is required; see [changes](#15-changes).

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
| LAN | `http://192.168.1.23:11434/v1` | `http://192.168.1.23:11434` |
| Container on Rosalina's `local-ai-ollama_default` network | `http://ai-router:11434/v1` | `http://ai-router:11434` |

The router instance clients use is the multi-host one at `192.168.1.23`. Rosalina's original single-host router on `192.168.1.4` is retired and refuses connections: a client still configured for it must be repointed (a container that maps `ai-router` through `extra_hosts` only needs the address in that mapping changed).

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
| `daytime` | The Daytime service. Present in every **normal** configuration: every configuration that runs a Daytime resident. The AI Runtime may publish a configuration in which no catalog entry declares `daytime`, and then there is none to send. That is not a licence for a client to invent one, or to send something else instead of it. | Stable; make it your default. |
| `nighttime` | The Nighttime service. Present only in **paired** configurations; a solo configuration stops it. | Stable; use it. |
| `trial` | The Strata trial service. Present **only** while a trial configuration is selected, and only under that name: it is opt-in, it is never the configuration the operator falls into, it is never an alias of `daytime` or of any other service, and in every other configuration it is **404 `MODEL_NOT_FOUND`**. | Opt-in. A client sends it only when its operator selected a trial configuration. |
| `local-active` | The reserved compatibility alias: not a service, but "the model this router currently has active" (see **Two kinds of names** below). | Kept for compatibility. Do not adopt it. |
| Canonical IDs, such as `qwen3.8-27b-abliterated-q6_k` | The specific model currently behind a service. | **Can change** with any configuration change. |

- A client MUST send service IDs (`daytime`, `nighttime`, or another service ID the current document advertises). It MUST NOT configure, persist or compare canonical IDs. Responses report the canonical ID in `model`; record it as information only.
- A client SHOULD always send a model. An omitted model selects the default of the **active configuration**, which this contract does not promise is `daytime`: while a trial configuration is selected, an omitted model is answered by the trial. A client that must not be served by a particular model MUST send an ID.
- An unknown ID returns **404 `MODEL_NOT_FOUND`**. This includes a name no entry of the published catalog declares any more, so a service ID is not a promise that outlives the configuration that declared it.
- A known service that the current configuration stops returns **503 `SERVICE_OFFLINE`**. It is then listed in `offline_services` (§4).
- **An opt-in service is never a fallback, in either direction.** A client reaches `trial` only by naming it, when its operator chose a trial configuration, and it steps away from `trial` to a service the operator chose — never the other way round. When `trial` does not answer, the client reports that; continuing on a model nobody selected is not recovery.
- **Exception for benchmarking and evaluation clients.** A client whose results must belong to one exact model MAY pin it for a run. At the start of the run it resolves the service ID to its canonical ID (`ids[service]`), records the canonical ID with the model's `revision`, and sends the canonical ID for the rest of the run. The router then never lets that run reach a different model. While pinned, the client treats each response like this:
  - 404 `MODEL_NOT_FOUND`: the model changed;
  - 503 `SERVICE_OFFLINE`: the model is offline;
  - neither is ever a reason to switch models.

  Such a client still discovers models from the capabilities document (§4). It MUST NOT keep canonical IDs as configuration across runs: new runs start from a service ID.

### Two kinds of names

The router distinguishes sharply between a name that means a service and a name that means "whatever is running", and a client must too. This is exactly how a model the operator did not choose can end up serving a session.

- **Catalog service IDs** — `daytime`, `nighttime`, `trial`, and every other alias the published catalog declares — are governed by this contract. A service ID exists only while some entry of the published catalog declares it: absent means 404 `MODEL_NOT_FOUND`, and a service the configuration deliberately stops means 503 `SERVICE_OFFLINE`. `ids` in the capabilities document (§4) lists every name the router currently accepts and the canonical model behind it, so a client never has to guess which kind it is holding.
- **The compatibility alias** — `local-active`, configured by `ROUTER_MODEL_ALIAS` — is **reserved**, and it is not a service. In the router's single-marker mode it is accepted on `POST /v1/chat/completions`, `/api/chat`, `/api/generate`, `/api/embed`, `/api/show` and `/v1/responses` whenever a model is active, whether or not that model declared the name, and the router always publishes a synthetic `/v1/models` row for it with `alias: true` and `upstream_model` naming whatever answers. In catalog mode it is an ordinary declared alias, and only the configuration's default entry may declare it. In both modes it resolves to one thing: **the model this router has active right now**. It is therefore an alias of the active slot, not an alias of `daytime`, and it follows every switch the operator makes.
- That is the entire reason a client keeps `local-active` — it works without knowing the configuration — and the entire reason not to adopt it: a session that keeps sending it is served by whatever the operator selects next, including a model the operator would not have chosen for it. Send a service ID, and re-read the document when the router says the state changed.
- A catalog entry that declares **no** service alias has no name a conformant client may send: the router lists it with `service` set to its canonical ID (§4) because it will not invent one. Such a model is **not selectable under this contract and MUST NOT be surfaced as a model a person can choose**. When no entry of the active catalog has a service ID at all, the capabilities document warns `CATALOG_HAS_NO_SERVICE_ID` and is not `complete` (§4).

## 4. Knowing what is available

### Capabilities document

`GET /v1/router/capabilities` returns one document describing the whole deployment, with an `ETag` (send `If-None-Match` to get a 304 when nothing has changed). A client MUST read it at startup, and MUST NOT fail to start when the router is unreachable: start degraded and retry. The fields a client relies on:

| Field | Meaning |
|---|---|
| `revision` | Content hash of everything below except load. If `revision` is unchanged, nothing relevant has changed — and a recheck that found the same deployment changes nothing, however many rechecks ran, which is what makes `If-None-Match` polling cheap. The converse is only half a rule: a moved `revision` means something in the document moved, not that *availability* moved, because the hash covers metadata a client never reads. Act on the fields, never on the fact of a change. |
| `router.accepting_requests` | False while the router drains for a configuration switch, or during maintenance. |
| `configuration.id`, `configuration.exclusive` | The AI Runtime configuration of the machine that answers an unnamed request, and whether it is solo. An exclusive configuration loads that one model and nothing beside it on that machine (see **NSFW flag and capability score** below). A multi-host deployment's other machines publish their own configurations, in `hosts[].configuration`. |
| `models[]` | Each usable model: `service` (the ID to send), `display_name`, `display_name_qualified`, `aliases`, `available`, `slots`, `context_window`, `input_modalities`, `capabilities`, `nsfw`, `capability_score`, and full `metadata`. In a deployment whose models live on more than one machine, each also carries `host`. |
| `hosts[]` | Only when the deployment spans more than one machine: each one's `id`, `display_name`, `default`, `published`, `available`, `draining`, `drain_reason`, `configuration`, `model_ids` and `error`. A client whose router declares one machine never sees this field, and reads its absence as one machine — the one serving the document. What each field may be relied on for is in **Several machines, one router** below. |
| `offline_services[]` | Services this configuration deliberately stops: `model`, `aliases`, `display_name`, `reason`. It is the runtime's own declaration of what it chose not to run, and the router never adds an entry of its own: a machine it cannot reach produces no entry here, and a service this configuration never declared produces none either (§3). |
| `ids` | Every accepted ID → its current canonical model. |
| `complete`, `warnings` | Whether this router trusts the picture it is publishing. See below. |
| `load` (only with `?include=load`) | Per model: `active`, `queued`, `free_slots`. |

Clients MUST ignore unknown fields. `metadata` is identical to the model's `x_ollama_router` object in `/v1/models`. The OpenAI and Ollama listings (`/v1/models`, `/api/tags`) show only models that can be selected right now, so an offline service is absent from them; only the capabilities document says why.

**An alias row is titled by its own name.** The listings publish an alias as a row whose `id` is the alias and whose
`x_ollama_router` is identical to its model's except for `alias: true` and `display_name`: the title is the alias
itself, unless the published catalog stated a title for that name. This is what makes one model answering several
service names listable at all — before it, `daytime` and `nighttime` on a single model produced rows reading alike, and
a client that labels a choice with `display_name` was offering the same choice twice. Two rules follow, and both are
cheap: identify a row by `id`, never by its title, and never de-duplicate rows by title — rows that name the same
model are deliberately distinct, because the name a client sends is what differs.

A client that lists **services** rather than rows — one option per name a person may send — does not read those rows;
it reads the capabilities document, which lists each model once. That document publishes the same fact per model in
`metadata.alias_display_names`, a title for every name the model answers to, derived exactly as the alias rows are.
Use it as the label of the option that sends that name: two service IDs whose canonical model is the same are then
offered under two different labels, which is what the picker is for. `metadata.alias_display_names[name]` always exists
for a name the model declares.

**When two rows are titled alike, `display_name_qualified` is the label to render.** Two machines may
describe the model they run in the same words, and one machine may offer two services under one title; the
router publishes what each publisher chose, so the list a client builds from this document can hold two rows
that read the same — and a client that finds the chosen option again by its exact label cannot state which one
it meant. `display_name_qualified`, a plain string on every row, is what such a client renders and matches on:
the published title, plus the machine in parentheses where two rows would otherwise collide
(`hosts[].display_name`, or the machine's ID when it published no label of its own), and the service ID in
parentheses instead when naming the machine would publish the same label twice. It is unique among the rows a
client can select, and it is the published title byte for byte wherever nothing collides, so a quiet
deployment repeats `display_name` exactly and a cached label never churns. Two rules follow:

- The label qualifies a **row**, whose sendable ID is that row's `service`. A client that offers one option
  per row uses this label; a client that offers one option per *name* a model answers to labels those options
  from `metadata.alias_display_names` as above and needs nothing new. Either way a client MUST NOT construct
  the qualified label itself, and MUST NOT assume it takes more than one machine for it to differ — one
  machine offering two services under one title collides too.
- `display_name` stays what the publisher called this model, and it is what a client records, logs and
  compares across a configuration change: a model the runtime moves to another machine keeps its name, and
  only the qualified label may mention a machine, and only where a real collision forced it. A router older
  than this field omits it, and a client MUST then fall back to `display_name`. A client MAY keep matching on
  `display_name` everywhere; what it MUST NOT do is de-duplicate or address rows by a title the router never
  promised was unique. `offline_services[].display_name` has no qualified twin — a service the configuration
  stops is not a row a client can select.

The router never invents a service ID. When the published catalog gives a model no service alias, `service` repeats that model's canonical ID: `service === id` is how a model with **no addressable ID** reads, and under §3 a client MUST NOT offer it as a choice — it may only be reached by a canonical ID, which clients may not configure. When no model of the active catalog has a service ID, the document carries `warnings: ["CATALOG_HAS_NO_SERVICE_ID"]` and `complete: false`: the router is saying out loud that a §3-conformant client has nothing to send in this configuration. Treat that as the runtime's business, report it to your operator, and keep the models you do have addressable in mind. It is a different fact from the runtime's own `deployment_warnings`, which the publisher writes and which the router passes through unchanged.

### Several machines, one router

A router may serve models that run on different machines in the same network.
This is invisible to model selection: `hosts[]` says which machine runs what, and
each model carries the same `host`, so a client can show it or ignore it. Nothing
else changes — one model list, one set of IDs, one queue per model, one set of
rules below. A model that moves between machines keeps its service ID and its
contract; a client that cached nothing about location is unaffected.

What a client may find out is that a machine is missing: `hosts[].published`
false, or `hosts[].available` false, or `hosts[].error` naming why. Treat that
the way you treat a model being unavailable — the model is not `available`, and
§5's fallback is the response. It is never a reason to stop sending requests to
the router: an unnamed request answers from the machine the router was told is
its default, whatever else is down.

**What each `hosts[]` field licenses a client to conclude.**

| Field | You may rely on it for |
|---|---|
| `id` | The key that matches a model's `host`. Key a table by it, display it; it is never a model ID to send (§3). |
| `display_name` | Display only. Two machines may describe themselves in the same words, so it is not unique, and the labelling rules above hold for it unchanged. |
| `default` | Which machine answers a request that named no model (§3). Diagnostic: a client that must not be served by a particular model sends an ID. |
| `published` | That the router holds a usable catalog from this machine. It is a publication fact, not a heartbeat: a machine switched off keeps `published: true` on the catalog it left behind, for as long as that catalog stands. |
| `available` | That this machine can serve something right now: it is not drained, and at least one of its models answers its own health check. It is an aggregate the router computes **from** `models[].available`, so the field a client acts on remains the model's — and while the whole router is draining it reads `false` on a healthy machine too. |
| `draining`, `drain_reason` | A maintenance window, which may be scoped to this machine or be the whole router's — a router-wide drain appears on every host, and `router.draining` says which. Where `router.accepting_requests` is false the answer is §7: wait, rather than fall away from a machine that is merely being tended. |
| `configuration` | What this machine publishes. It may differ between machines and may be `null`; the document-level `configuration` is the default machine's, not everybody's. Presentation and diagnosis only, never a selection key. |
| `model_ids[]` | The canonical IDs this machine publishes, for display and diagnosis. §3 forbids sending them; map a model to its machine with `models[].host` rather than by matching canonical IDs, which a client MUST NOT persist as configuration. |
| `error` | That this machine contributes **nothing usable** because its catalog is missing, unreadable or invalid — a code and a message. It is a publication fault. It is never how a switched-off machine reads, and a client MUST NOT wait for it before treating a machine's models as unavailable. |

**The four ways a model stops being available, and what each one reads as.**

| Situation | What the document says | What you do |
|---|---|---|
| The machine is switched off, or its backend stopped answering | Its models keep their rows with `available: false`; `offline_services[]` names nothing; `hosts[].error` stays `null` | Fall back (§5). Say unavailable, and **name no cause** — nothing in the document states one. A request that names the model answers 503 `BACKEND_UNAVAILABLE`, not `SERVICE_OFFLINE` (§10) |
| This configuration deliberately stops the service | An `offline_services[]` entry naming the model and its aliases, with a `reason` | Fall back (§5). The request answers 503 `SERVICE_OFFLINE`, which costs no retry allowance or budget (§10) |
| The machine never published, or published something the router cannot use | `hosts[].published` false or `hosts[].error` set, and its models **absent** from `models[]` | Those models are not offered. This is the one case whose reason the document carries: report it to your operator and keep using the rest |
| The router drains or enters maintenance | `router.accepting_requests` false, and every host `draining: true` | Wait (§7). Nothing is down, and nothing may be substituted |

Only the third of those carries a reason, and the absence of one is not an invitation to supply it: a client MUST NOT render a cause it was not given, and SHOULD reach for neutral wording ("not answering", "unavailable") over a diagnosis it cannot support ("crashed", "offline", "misconfigured"). `observed_at` is when this router first saw the revision it stamps — a content-change stamp for the whole document. It is not a per-machine last-seen-alive, and it is not a stale-data warning: an unchanged `revision` is already the contract's statement that no availability verdict has moved, and the verdict behind `available` is re-derived from the machine on every recheck (see **Change events**).

### Change events

`GET /v1/router/events` is a Server-Sent Events stream.

- **Each connection** starts with the complete document as `event: capabilities` (`id:` is its `revision`), then `event: load`.
- **A new `capabilities` event**, always the complete document, follows every change: immediately for a runtime publication, drain or maintenance change; within about 10 s for a backend health change. A machine becoming unreachable, and a machine coming back, *are* backend health changes: they arrive on this path within the same bound, including the case where the machine stopped answering below HTTP rather than at it. The recheck that finds them runs while at least one client is subscribed, which is what makes §4's subscriber rule also the freshness rule — with nobody watching, the deployment is re-read only when someone fetches it, and an unavailable verdict is then as old as the last reader.
- **`load` events** carry slot occupancy, at most once per second.
- **`: keepalive` comments** arrive every 15 s.

During a switch, a subscriber sees: draining (`accepting_requests: false`), then the new configuration, then `accepting_requests: true`.

A long-running client SHOULD hold one subscription for its lifetime and replace its copy of the document whenever `revision` changes. While disconnected it MUST fall back to polling the capabilities endpoint (every 30 s, with `If-None-Match`). It reconnects with backoff starting at the stream's `retry:` value (3 s), up to 30 s. A connection with no bytes for 60 s, keepalives included, is dead. A short-lived or request-scoped client MAY instead fetch the document before each request.

### NSFW flag and capability score

- **`nsfw`** is `true` for models the runtime declares abliterated (refusals removed), `false` for models it declares not, and `null` if undeclared. Treat `null` as "not known to be NSFW".
- **`nsfw` is an attestation, not a forecast.** A `true` means the runtime has attested this model's abliteration — nothing more, and nothing less. It is deliberately conservative, and it can therefore lag observed behaviour in both directions: a model that answers every explicit prompt politely while declaring `false` is an ordinary state of a deployment and not a fault, and no model's `true` promises it never declines. So a client that finds no `nsfw: true` model in the document has found a **gap in what has been attested**, and describes it as that; it MUST NOT forecast refusals from a `false`, and MUST NOT soften the fact that its NSFW-first choice fell back (§5's refusal rule is about a refusal that happened, not about one that might).
- **An exclusive configuration cannot answer this gap by adding a model.** Where `configuration.exclusive` is true, the configuration loads that one model and gives it every text GPU of its machine, so no second model — abliterated or not — can run beside it there. A missing `nsfw: true` row under an exclusive configuration is a property of the selected configuration, and only selecting a different configuration (a switch, with §7's drain) can change it. A client sees this coming from the `exclusive` flag it already reads.
- **`capability_score`** is an automatic 0–100 ranking; higher is more capable. It is comparable only between models of this router. It is built from three parts:
  - parameter count, scaled down for heavier quantization;
  - context window;
  - vision, tools and reasoning support.

  Availability, load and speed never change it. It cannot measure answer quality: it can't account for what abliteration costs, or compare a mixture-of-experts model fairly with a dense one.
- **A null `capability_score.value` means the router could not compute it, and says nothing about the model.** The quantization-scaled parameter part needs the model's parameter count and file size, and the router learns those from the process serving the model, never from a name or a claim; an engine that reports neither leaves `value: null` with `basis: "incomplete"`, `components.size: null` and null `inputs.parameters` — on every model it serves, at once. `metadata.capability_score` is where that shows, and it is the field that says whether a row was scored at all. **`complete: true` with `warnings: []` has never meant that scores exist**: a fact the router cannot observe is not a document it distrusts (§4). A client MUST NOT read an unscored deployment as a degraded one.
- **Ranking when a score is missing is defined, not improvised.** The order the reference clients implement, and the one to copy: keep `available` rows that serve the request's features (§5's feature checks come before any ranking); rows with a score before rows without; then higher `capability_score`; then higher `context_window`. The last tie breaks in the order the document publishes the rows, which is stable for a given roster. A client MAY break that final tie another way — it MUST be deterministic, and MUST NOT reshuffle a list a person is reading — and where it presents a choice it SHOULD say what it ranked by, because "most capable" is not a claim a null score supports.

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
- **Opt-in services are never candidates.** A service the operator opts into by configuration — `trial` today — is not in the pool a capability-based choice draws from, and a name-based fallback list never acquires it. It is used by a client whose operator selected the matching configuration, under that name, and never as a way of getting an answer: falling back to a trial the current user never asked for, or away from one to a model that would have been refused, are both wrong. Report it instead.
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
- **Draining:** during a configuration switch or maintenance, new requests receive 503 `BACKEND_DRAINING` or `MAINTENANCE_MODE`. A drain may cover one machine rather than the whole router: requests for models running elsewhere continue normally, and the refusal says which machine is draining. Requests already accepted, including queued ones, finish normally. A switch usually takes one to two minutes. A client MUST keep waiting and retrying (backoff from 2 s up to 30 s) for at least 10 minutes, then choose its model again: the configuration may have changed.
- **Health checks:** a client SHOULD NOT use `GET /health` to judge model availability; use the capabilities document.

## 8. Context

- **Budget:** each request must fit the target model's per-request `context_window`. The router counts the actual formatted prompt with the model's tokenizer, including the chat template, tools and images. On a `strata` backend the engine's count route reports a placeholder for image blocks, so each image in the prompt — including images in earlier turns — is charged a measured per-image allowance instead of an exact projector count; the allowance is an upper bound, so budget arithmetic is conservative, never optimistic. A request is admitted when **formatted input + requested output + `metadata.context_safety_reserve` (1024) ≤ `context_window`**. A request with unrestricted output needs room for at least one generated token.
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

  Managed generation failures preserve their specific code through Responses:
  `EMPTY_UPSTREAM_RESPONSE` means the backend finished without visible content
  or a tool call; `INCOMPLETE_UPSTREAM_STREAM` means a required terminal event
  or finish reason was missing. Neither is replaced with a generic transport
  failure. In JSON errors and streaming `response.failed`, `error.diagnostics`
  includes the archive `record_id`, backend model/kind/host/origin, observed
  `finish_reason`, terminal `done` flag, available token usage, channel character
  counts and whether tool calls were seen. Unavailable termination facts stay
  null/false. Diagnostics contain no prompt or reasoning text; the backend
  origin excludes credentials and query strings. Request metadata and the
  archive terminal retain these diagnostics too. This does not detect every
  semantically unfinished non-empty answer or repair a backend completion.

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
| `MODEL_NOT_FOUND` (404) | The ID isn't offered: no entry of the active configuration declares it (§3) | Fall back if configured, and log a warning: usually a stale or misspelled ID, or a configuration that stopped offering the service. Never fall back from an opt-in service such as `trial` (§5). |
| `BACKEND_DRAINING`, `MAINTENANCE_MODE` (503) | Switch or maintenance in progress, on the whole router or on the machine running that model | Wait (§7), then choose again. |
| `context_length_exceeded` (400) | The request doesn't fit | Shrink the request, or choose a model with a larger window. Don't retry unchanged. |
| `TOO_MANY_SUBSCRIBERS` (503, events only) | Event-stream limit reached | Poll the capabilities endpoint instead. |
| Other 5xx; 408, 429; network errors and timeouts | Transient | Retry the same service with backoff. |
| Other 4xx | The request is invalid | Fix the request. Don't retry and don't fall back. |

New codes may be added. An unknown 5xx code is transient; an unknown 4xx code is a request error. The reference clients' `classify_error(status, body)` implements this table.

## 11. Change and compatibility

- **Schema versions:** the capabilities document has `schema_version: 2` (version 1 documents remain valid: version 2 only adds `hosts[]` and the per-model `host`, and clients MUST ignore fields they don't know); model metadata (`x_ollama_router`) has `schema_version: 2`, unchanged; the capability score carries its own `version`. Removing a field or changing its meaning increments the schema version. A client SHOULD warn when it sees a schema version it wasn't written for.
- **Contract versions:** this contract is versioned at the top. A change that requires client changes increments the major version (2, 3, …) and is announced to client maintainers. A minor version (1.1, 1.2, …) only clarifies or adds permissions and never requires client changes, so copies of an earlier minor version stay valid until the next update handoff.
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

- [ ] Sends service IDs only, or, for a benchmark, a canonical ID pinned per run (§3). Persists no canonical IDs as configuration. Sends `X-Client-Name`.
- [ ] Never falls back to or away from an opt-in service such as `trial`, and never offers a model the document gives no service ID (§3, §5).
- [ ] Reads the capabilities document at startup without failing when the router or a model is unavailable.
- [ ] Long-running: subscribes to `/v1/router/events` and polls while disconnected. Request-scoped: reads the document before each request.
- [ ] Chooses the model per request by capability or by name, with configurable fallback (or, for benchmarks, deliberately none). Returns to the preferred model when it is available again.
- [ ] Waits, without switching, while the router drains or is in maintenance.
- [ ] Classifies errors by `error.code`, as in §10. `SERVICE_OFFLINE` doesn't consume retry or budget allowances.
- [ ] Sets `stream` explicitly; handles queue keepalives and errors inside streams; never treats an incomplete response as complete.
- [ ] Takes context, reserve, slots and features from the serving model, and recomputes them when it changes. Hard-codes no context size.
- [ ] Uses read timeouts that allow for queueing, or streams. Doesn't abandon and resubmit queued requests.
- [ ] Logs model changes and fallbacks, and surfaces refusals from a fallback model instead of retrying them.
- [ ] If it shows where models run, reads it from `hosts[]`/`host` and treats a missing machine as that machine's models being unavailable, naming no cause the document did not give (§4).
- [ ] Keeps a verbatim copy of this contract at `docs/llm-router-contract.md`, a conformance map, and an `AGENTS.md` rule that upholds it (§11).

## 14. References

- [Deployment capabilities and change events](CAPABILITIES.md): the full document schema, event timing and score formula.
- [API reference](API.md), including [llama.cpp sampling controls](API.md#llamacpp-sampling-controls).
- [Model discovery](MODEL_DISCOVERY.md): the `x_ollama_router` metadata fields.
- [Multi-host deployments](MULTI_HOST.md): what a router spanning several machines publishes, and what stays the same.
- [Runtime integration](RUNTIME_INTEGRATION.md): the other side of this contract — how a machine that runs models gets them into the router. A client never needs it, and never talks to one.
- [Primary integration](PRIMARY_INTEGRATION.md): output policy, queueing, context recovery and archives.
- Reference clients, tested against the router: [Python](clients/router_watch.py) (standard library) and [JavaScript](clients/router-watch.mjs) (Node 18+ and browsers).
- [Client handoff](handoffs/2026-10-07-capability-subscribers.md): how each current project should change to meet this contract.

## 15. Changes

| Version | Change |
|---|---|
| 1.6 | §4: what a client may rely on each `hosts[]` field for, and what is display-only — `published` is a publication fact and not a heartbeat, `available` is an aggregate computed from `models[].available`, a router-wide drain appears on every host, `model_ids[]` are canonical IDs it must not send, and `error` is a publication fault rather than a down machine. §4 tabulates the four ways a model stops being available and states that only the machine that never published (or published something unusable) carries a reason: a machine that simply stopped answering keeps its rows at `available: false`, names nothing in `offline_services[]`, leaves `hosts[].error` null, and a client MUST NOT supply the cause the document withholds. §4 states that `observed_at` is a document-wide content stamp rather than a per-machine last-seen-alive, that an unchanged `revision` is the freshness statement, and that a moved `revision` need not mean an availability change; §4's change-event bound is confirmed to cover a machine going away and coming back, including failure below HTTP. §4 states what a null `capability_score` means (inputs the router could not observe, fleet-wide, with `complete: true` never claiming scores exist) and fixes the ranking order when scores are missing. §4 restates `nsfw` as an attestation rather than a behaviour forecast — a deployment with no `nsfw: true` model has a gap in what is attested, not a prediction of refusals — and records that an `exclusive` configuration loads nothing beside its model, so no second resident can appear without a switch. §13's machine line names the no-invented-cause rule. No client change is required: nothing any client reads by name moved, no field changed meaning, and every rule above states what a conformant client already does. |
| 1.5 | §4: every capabilities row carries `display_name_qualified`, the label a client may render and match on when two publishers titled their model alike — the published title, qualified with the machine or, when one machine offers two services under one title, with the service ID. `display_name` is unchanged and still what a client records; where nothing collides the two strings are equal, so no client needs to change. `schema_version` stays 2: the field is additive and unknown fields are ignored. |
| 1.4 | §4: an alias row in `/v1/models` and `/api/tags` is titled by the alias rather than by its model's title, unless the catalog states one, and the capabilities document publishes `metadata.alias_display_names` so a client that offers one option per service can label each name distinctly; clients identify rows by `id` and MUST NOT de-duplicate them by title. A client that sends service IDs needs no change: nothing it sends or reads by `id` moved, and the capabilities document still lists each model once, under its own title. |
| 1.3 | §4: the capabilities document is `schema_version: 2`, adding `hosts[]` and a per-model `host` when a deployment serves models from more than one machine; a missing machine means those models are not `available`, and unnamed requests still answer. §7: a drain may scope to one machine. §11: schema note. Clients that send service IDs need no change; a client written against 1.2 keeps working untouched. |
| 1.2 | §3: the `daytime` row states the guarantee a real published configuration actually provides; `trial` is added as an opt-in service ID; **Two kinds of names** distinguishes catalog service IDs from the reserved compatibility alias `local-active`; an omitted model is the active configuration's default, which is not necessarily `daytime`. §4 documents what `service` reports for a model the catalog gives no alias, and the `CATALOG_HAS_NO_SERVICE_ID` warning. §5, §10 and §13 follow. A client that sends service IDs and does not adopt `trial` needs no change: the added §13 lines record what §3 and §5 already required. |
| 1.1 | §3: benchmarking and evaluation clients may pin a canonical ID for the duration of a run. §11: minor versions defined. |
| 1 | Initial contract. |
## How ComfyUI Image Frontend upholds this contract

This section is ComfyUI Image Frontend's own; the contract text above it is the router maintainer's and is never edited here. Keep this map current with every change to router requests, model selection or discovery ([`AGENTS.md`](../AGENTS.md)). The copy above is version 1.6 of the contract, from llm-router commit `54d9517fa51de39216573d8faae1c82818c32206`; §15 lists what has changed since the version this project first upheld, and the map below is current with it. The router-facing code is [`backend/app/services/llm_router.py`](../backend/app/services/llm_router.py) (discovery, selection, error classification and the event-stream subscriber, ported from the reference client `router_watch.py` at llm-router `d5edba8`) and [`backend/app/services/ollama.py`](../backend/app/services/ollama.py) (Prompt Assistant composition and vision requests on `/api/chat`).

**Selection rule (fixed by the owner).** For every compose and vision request the model is chosen by capability from the current document: the `available` models with every feature the request needs (`image` in `input_modalities` for vision checks); the highest `capability_score` among those with `nsfw: true`; otherwise the highest-scoring model of any kind, reported as a non-NSFW fallback; and, while `router.accepting_requests` is false, wait without choosing. This is `pick_service(doc, nsfw=True, require=[...], fallback_any=True)`. Settings: `CIF_OLLAMA_SELECTION` (`capability` default, or `named`: `CIF_OLLAMA_MODEL` then `CIF_OLLAMA_FALLBACK_MODELS`, default `nighttime`, `daytime`), `CIF_OLLAMA_NSFW` (`prefer` default, `require`, `avoid`, `any`), and `CIF_OLLAMA_ROUTER_WAIT_SECONDS` (default and minimum 600).

Test paths below are relative to `backend/tests/`.

**Account choice (owner-approved addition).** On top of that rule an account may name the model its Creative Direction requests ask for. Precedence is: the account's chosen service, then this instance's `CIF_OLLAMA_SELECTION` rule, then the contract defaults. The choice is stored in the account's own settings blob with the usual revision conflict handling (`SharedSettings.assistant_model`; `null`, `""` and `"auto"` all mean "let the application choose"), it holds a service ID and nothing else, and an unknown-but-well-formed service ID is kept rather than rejected — the router's catalog changes underneath it, and a model that returns answers again. It changes nothing about what is sent to the router: still one service ID in `model`, still no canonical ID anywhere, and still one choice per request. What the control panel shows is kept honest to the same document it is listing: the status route reports `automatic_service`, the service this application would choose with no choice of its own, separately from the `service` answering now, so naming "Automatic" does not move when an account's choice is served; and a choice the router answers under an alias it publishes with a service is shown as that service, because that is the model answering. The list commits the moment it changes, in the control panel or in the focused prompt editor alike — the choice is a saved setting and not part of the prompt draft that editor reviews — which is what keeps the background refresh of the status row, rewriting every model list from the saved state, from moving a choice back to Automatic after it was made.

| # | Checklist item | Code | Tests |
|---|---|---|---|
| 1 | Sends service IDs only, or on a benchmark a canonical ID pinned per run (§3); persists no canonical IDs as configuration; sends `X-Client-Name` | `pick_service`/`resolve` return a model's `service`; `OllamaAdapter._generate` sends `RouterSelection.service` as `model`; the shared `httpx.AsyncClient` sends `X-Client-Name: comfyui-image-frontend` on every request (capabilities, events, chat). Stored provenance (`raw_response_json.router`, status row) holds service IDs only; the canonical ID a response reports is kept as the informational `model_name`/`model`, never sent, configured or compared. The catalog projection this application shows its own control panel (`catalog_view`) is built to the same rule: service IDs, aliases, the router's own labels and what each can do, with every entry's canonical identities dropped as it is built. A catalog row the router published with `service === id` — no service alias, so nothing a client may send (§3) — is dropped by `has_service_id` before any of that: not selected, not projected, not offered, so a canonical ID cannot reach the wire even from such a catalog. | `unit/test_ollama_router.py::test_every_router_request_identifies_the_client_and_sends_service_ids_only`, `::test_the_read_timeout_allows_for_queueing_behind_long_generations`; `unit/test_llm_router.py::test_fetch_sends_if_none_match_and_keeps_the_document_on_304`; `unit/test_llm_router_catalog.py::test_the_catalog_never_carries_a_canonical_model_id`; `integration/test_llm_router_contract.py::test_compose_follows_configuration_switches_without_sticky_fallback` |
| 2 | Never falls back to or away from an opt-in service such as `trial`, and never offers a model the document gives no service ID (§3, §5) | `llm_router.OPT_IN_SERVICES` (`trial`) with `is_opt_in_service`: `pick_service` and `pick_from_catalog` keep opt-in services out of their pools, so a capability choice can never fall back to one, and `resolve` and `resolve_from_catalog` skip them in every name after the first — a name-based fallback list never acquires one, while naming it as the preferred service remains the opt-in §3 describes. This application adopts no opt-in service, so `api/prompt_assistant.py::_catalog_models` keeps one out of the control panel's list too: a choice a person made could not then fall back from it as §10 requires (§13 item 2). `has_service_id` is the other half, and it is a shape test on the document rather than a name: `service === id` leaves `catalog_models`, `catalog_view`, `pick_service`, `resolve` and `pin_status`, so such a model is never selected, never shown and never sent, and an account's saved choice of a model the router has stopped naming reads as the ordinary `PIN_ABSENT` case | `unit/test_llm_router_catalog.py::test_the_opt_in_trial_service_is_never_a_capability_candidate_or_fallback`, `::test_a_model_with_no_service_id_is_never_offered_and_never_selected`; `integration/test_workflows_and_prompt_assistant.py::test_prompt_assistant_offers_no_model_without_a_service_id_and_never_the_opt_in_one` |
| 3 | Reads the capabilities document at startup without failing when the router or a model is unavailable | `QueueWorker.start` starts `_router_watch_loop` (lifespan task beside `_health_loop`) → `OllamaAdapter.watch_router` → `RouterWatch.run_forever`, whose first step is the tolerant `RouterWatch.refresh`. Without a document the assistant reports `router_unreachable` and sends nothing by capability; an unavailable model is simply not a candidate. Unknown fields are ignored, and a `schema_version` outside the versions this application reads (`1` from this contract and `2`, which the home router publishes) is warned about once. | `integration/test_llm_router_contract.py::test_router_unreachable_at_startup_does_not_prevent_startup`; `unit/test_llm_router.py::test_subscriber_survives_an_unreachable_router_at_startup`, `::test_refresh_tolerates_an_unreachable_router_and_keeps_the_last_document`, `::test_invalid_documents_are_rejected_and_unknown_fields_ignored`, `::test_unexpected_schema_versions_are_warned_about_once`, `::test_every_published_schema_version_this_application_understands_is_used_quietly`; `unit/test_ollama_router.py::test_router_unreachable_reports_unavailable_without_sending`, `::test_unhealthy_nsfw_model_falls_back_to_daytime` |
| 4 | Long-running: subscribes to `/v1/router/events` and polls while disconnected | `RouterWatch._follow` holds one SSE subscription (60 s read timeout = dead stream; `: keepalive` comments and `load` events ignored; `capabilities` events replace the document when `revision` changes); `RouterWatch.run_forever` polls `GET /v1/router/capabilities` with `If-None-Match` after every disconnect and reconnects with backoff from the `retry:` hint (3 s) to 30 s, including on `TOO_MANY_SUBSCRIBERS`. Each change rewrites the status row at once (`QueueWorker._on_router_change`), including the catalog projection (`catalog_view`), so anything reading the row — the Creative Direction panel's model list — follows the router's published catalogs without another request; a probe that fails clears it rather than keeping a stale one. When no subscriber runs (background worker disabled), `OllamaAdapter._ensure_document` reads the document before each request instead. `complete` and `warnings` are read by nothing: §4 makes them the router's confidence in its own picture, never a substitute for `models[].available`, so a document whose `capability_score` is `null` throughout — an engine reporting neither parameter count nor file size — is complete as far as this client is concerned and selects normally. | `unit/test_llm_router.py::test_subscriber_follows_events_replaces_the_document_and_ignores_keepalives`, `::test_subscriber_polls_while_disconnected_and_backs_off_from_3_to_30_seconds`, `::test_subscriber_polls_when_the_event_stream_has_too_many_subscribers`, `::test_a_connection_that_delivered_reconnects_after_the_retry_hint`; `unit/test_ollama_router.py::test_the_published_catalog_is_what_the_panel_and_the_next_request_use`; `integration/test_llm_router_contract.py::test_subscriber_keeps_the_status_endpoint_current`, `::test_subscriber_polls_while_the_event_stream_is_refused`; `integration/test_workflows_and_prompt_assistant.py::test_prompt_assistant_lists_the_published_catalog_and_keeps_the_chosen_model` |
| 5 | Chooses the model per request by capability or by name, with configurable fallback; returns to the preferred model when it is available again | `OllamaAdapter.select_model` (no network) and `OllamaAdapter._choose`, called for every `/api/chat` request; §5's feature checks run before any ranking (`_has_features`, `entry_has_features`) — which is why a vision request has exactly one candidate whatever the scores say, and why "no NSFW model" and "no NSFW model that can inspect images" stay separate reasons (`_unavailable_reason`). Ranking is §4's: rows with a score before rows without (`capability_score: null` says nothing about the model), then higher score, then larger `context_window`, and a final tie falls to the order the document lists them, because `max` keeps the first best row (`_rank`, `_entry_rank`). capability mode maps `CIF_OLLAMA_NSFW` to `pick_service(nsfw=True, fallback_any=True)` (`prefer`), `nsfw=True` (`require`), `nsfw=False` (`avoid`) or `nsfw=None` (`any`); named mode uses `resolve(doc, CIF_OLLAMA_MODEL, CIF_OLLAMA_FALLBACK_MODELS)` with the same feature checks (an empty fallback list disables fallback). Nothing is sticky: every request chooses again. The same rules read either a capabilities document or the catalog projection stored from one (`pick_from_catalog`, `resolve_from_catalog`, `model_for`), so the service a control panel offered as Automatic is the service the next request uses. An account's own chosen service, when it has one, is answered before either mode (`OllamaAdapter._honor_pinned_service` over `pin_status`): while it can serve the request it answers with reason `pinned_service`, and when it cannot — absent, stopped, or unable to serve this kind of request — the automatic choice answers, the request says which model answered and why, and the choice is kept for the next request rather than discarded. | `unit/test_llm_router.py::test_paired_configuration_prefers_the_most_capable_nsfw_model`, `::test_solo_configuration_falls_back_to_the_most_capable_model`, `::test_two_nsfw_models_choose_the_higher_score_then_the_larger_context`, `::test_vision_requirement_selects_the_only_model_with_image_input`, `::test_named_selection_uses_the_preferred_service_then_fallbacks`, `::test_nsfw_null_is_not_known_to_be_nsfw`; `unit/test_llm_router_catalog.py::test_the_catalog_lists_every_published_service_best_capable_first`, `::test_a_service_the_router_stopped_is_listed_as_unavailable_with_its_reason`, `::test_selection_by_name_and_by_capability_read_the_same_catalog_either_way`, `::test_chosen_model_status_is_explained_against_the_published_catalog`; `unit/test_ollama_router.py::test_paired_configuration_uses_nighttime`, `::test_solo_configuration_uses_daytime_and_reports_the_fallback`, `::test_unhealthy_nsfw_model_falls_back_to_daytime`, `::test_two_nsfw_models_use_the_higher_score`, `::test_require_nsfw_with_none_available_is_unavailable_without_sending`, `::test_nsfw_preference_settings`, `::test_named_mode_sends_the_preferred_service_then_its_fallbacks`, `::test_named_mode_with_fallback_disabled_reports_unavailable`, `::test_named_mode_requires_a_service_name`, `::test_the_nsfw_model_is_used_again_as_soon_as_it_returns`, `::test_a_chosen_model_answers_even_when_a_more_capable_one_is_published`, `::test_a_chosen_model_the_router_stopped_explains_what_answered_instead`, `::test_a_chosen_model_the_router_no_longer_publishes_keeps_its_place`; `unit/test_expectations.py::test_vision_check_uses_the_only_model_that_accepts_images`; `integration/test_llm_router_contract.py::test_compose_follows_configuration_switches_without_sticky_fallback`; `integration/test_workflows_and_prompt_assistant.py::test_prompt_assistant_lists_the_published_catalog_and_keeps_the_chosen_model` |
| 6 | Waits, without switching, while the router drains or is in maintenance | `pick_service`/`resolve` return `WAIT` while `accepting_requests` is false and `_choose` waits; `BACKEND_DRAINING`/`MAINTENANCE_MODE` answers wait too. Both share one `_RouterWaitBudget` per request: 2 → 30 s backoff (waking early when the document changes) for at least `CIF_OLLAMA_ROUTER_WAIT_SECONDS` (≥ 600), then the model is chosen again with exclusions cleared. The status row reports `state: "waiting"`. A service an account chose waits with the request rather than being moved to another model, and stays chosen when the wait ends. | `unit/test_llm_router.py::test_draining_router_waits_instead_of_choosing`; `unit/test_llm_router_catalog.py::test_a_router_that_is_draining_or_maintaining_is_a_wait_not_an_alternative_model`; `unit/test_ollama_router.py::test_draining_waits_without_switching_then_chooses_again`, `::test_drain_and_maintenance_codes_wait_then_choose_again`, `::test_waiting_lasts_at_least_ten_minutes_with_backoff_up_to_30_seconds`, `::test_draining_status_reports_waiting`, `::test_a_chosen_model_waits_with_the_request_during_a_configuration_switch`; `integration/test_llm_router_contract.py::test_draining_router_holds_the_request_until_the_switch_finishes` |
| 7 | Classifies errors by `error.code` (section 10); `SERVICE_OFFLINE` doesn't consume retry or budget allowances | `classify_error`/`error_code` (object form first; a string `error` falls back to the status) drive `OllamaAdapter._generate`: `SERVICE_OFFLINE`, `BACKEND_UNAVAILABLE`, `MODEL_NOT_FOUND` refresh the document and choose again at once with the failed service excluded, consuming none of the three transient attempts (`MODEL_NOT_FOUND` is logged as a warning; `BACKEND_UNAVAILABLE` with nothing to fall back to is retried with backoff); drain codes wait (item 6); other 5xx, 408, 429 and connection failures retry the same service; other 4xx fail (`context_length_exceeded` → `ollama_context_exceeded`). Only when no service at all is usable does the request report `ollama_unavailable`, which callers retry later. A service an account chose that fails this way is excluded and chosen past for this request (`pinned_service_failed`), with the choice kept for the next one. | `unit/test_llm_router.py::test_errors_are_classified_by_code_then_status`, `::test_error_code_reads_the_object_form_only`; `unit/test_ollama_router.py::test_service_offline_falls_back_at_once_and_costs_no_retries`, `::test_unusable_services_fall_back_to_the_next_candidate`, `::test_backend_unavailable_without_a_fallback_retries_with_backoff`, `::test_service_offline_with_nothing_left_is_unavailable`, `::test_other_server_errors_retry_and_other_client_errors_fail`, `::test_a_request_the_chosen_model_cannot_answer_says_who_answered`; `integration/test_workflows_and_prompt_assistant.py::test_prompt_assistant_retries_a_transient_generate_failure` (string `error`) |
| 8 | Sets `stream` explicitly; handles queue keepalives and errors inside streams; never treats an incomplete response as complete | Every inference request sends `"stream": false` (`_generate_payload`, `evaluate_image`), so it receives no queue keepalives and no in-stream errors; the event-stream subscriber ignores keepalive comments. An answer with `x_router.status: "incomplete"` or `done_reason: "error"` is never accepted (`_router_incomplete`): an output-limit stop escalates the output budget even when its text parses, anything else is retried as transient and then reported as `ollama_generate_incomplete`. | `unit/test_ollama_router.py::test_an_incomplete_answer_is_never_treated_as_complete`, `::test_a_length_limited_router_answer_escalates_instead_of_being_accepted`, `::test_every_router_request_identifies_the_client_and_sends_service_ids_only`; `unit/test_llm_router.py::test_subscriber_follows_events_replaces_the_document_and_ignores_keepalives` |
| 9 | Takes context, reserve, slots and features from the serving model and recomputes them when it changes; hard-codes no context size | All limits are read from the document's entry for the service chosen for that request (`model_for`): features in `pick_service`/`resolve`; reasoning effort in `thinking_value` (`xhigh` only when listed in `metadata.reasoning.efforts`, else the closest listed effort, else `true`; `false` for a model without thinking); `num_predict` reduced to fit `context_window − context_safety_reserve − input` (`_fit_output_limit`); concurrency per service limited to its `slots` (`_ServiceSlots`). No context size is hard-coded. The same entry decides whether a service an account chose can serve the request at all: an image check asks `pin_status(..., require=["vision"])`, so a chosen model that cannot read images leaves the check on the best vision-capable model, with the reason reported. | `unit/test_ollama_router.py::test_the_output_limit_fits_the_serving_models_context_window`, `::test_requests_to_one_service_never_exceed_its_slots`, `::test_thinking_effort_is_checked_against_the_chosen_model`, `::test_a_model_without_thinking_receives_think_false`, `::test_a_chosen_model_that_cannot_inspect_images_is_not_used_for_an_image_check`; `unit/test_llm_router.py::test_thinking_effort_is_sent_only_when_the_model_lists_it`, `::test_model_for_returns_the_limits_of_the_model_behind_a_service`; `unit/test_expectations.py::test_vision_check_without_any_image_model_is_unavailable_without_sending` |
| 10 | Uses read timeouts that allow for queueing, or streams; doesn't abandon and resubmit queued requests | The client's read timeout is 900 s (`OllamaAdapter.__init__`). Only a connect timeout is retried; a read timeout is reported (`ollama_generate_timeout`) and never resubmitted, because that would lose the request's place in the queue. | `unit/test_ollama_router.py::test_the_read_timeout_allows_for_queueing_behind_long_generations`; `unit/test_ollama.py::test_read_timeout_is_classified_without_retrying_or_retaining_prompt_text` |
| 11 | Logs model changes and fallbacks; surfaces refusals from a fallback model instead of retrying them | `RouterWatch._set` logs `llm_router_capabilities_changed`; `OllamaAdapter._note_selection` logs every change of chosen service as `llm_router_model_changed` with its reason (warning while on a fallback); the startup log `prompt_assistant_router_selection` names the mode and preference. `GET /api/prompt-assistant/status` returns `router` (service, `nsfw`, configuration ID, `fallback`, `notice`), shown in the Creative Direction panel and focused editor (`frontend/src/render.mjs` `promptAssistantModelNoticeMarkup`). A non-NSFW model that refuses, answers outside the schema, or returns `MALFORMED_STRUCTURED_OUTPUT`/`EMPTY_UPSTREAM_RESPONSE` raises `ollama_model_declined` ("No NSFW model is available; Daytime declined this request.") with no redraw or retry; Auto-generate and expectation checks treat that code as terminal. It also returns the router's current catalog as `models`, the account's own choice as `pinned_model`, and the answered pair as `router.pinned_service`/`pin_honored` — all computed from the stored catalog, so the status row still says nothing about a live router call — and the panel offers them as its model list (`promptAssistantModelOptionsMarkup`), holding a choice the router stopped publishing as a disabled line. The list is offered above the Creative Direction input it shapes, and a change to it is saved there and then, from the control panel or from the focused editor alike (`frontend/src/app.mjs`, the `prompt-assistant-model` / `prompt-editor-model` change handler). Where a model an account chose did not answer, the notice names both models and the reason (`OllamaAdapter._pinned_notice`). The fallback notice is §4's declaration gap, not a forecast: `_fallback_notice` reads "No published model is attested for NSFW, so Prompt Assistant is using <model>." and the option hint says "Attested for NSFW directions" / "Not attested for NSFW" (`frontend/src/render.mjs` `assistantModelHint`); nothing here predicts a refusal from a `false`, and `ollama_model_declined` still reports one when it actually happens. | `unit/test_ollama_router.py::test_selection_changes_are_logged_with_their_reason`, `::test_a_fallback_refusal_is_reported_plainly_and_never_retried`, `::test_fallback_output_that_fails_the_schema_is_a_decline`, `::test_named_fallback_refusal_names_the_unavailable_preference`, `::test_the_nsfw_model_is_not_second_guessed_for_refusal_wording`, `::test_a_chosen_model_the_router_stopped_explains_what_answered_instead`, `::test_a_chosen_model_the_router_no_longer_publishes_keeps_its_place`; `unit/test_llm_router_catalog.py::test_a_catalog_larger_than_the_panel_shows_is_say_so_truncated`, `::test_two_models_publishing_one_service_id_answer_as_one_line`, `::test_every_feature_the_panel_labels_is_checked_from_the_catalog`; `unit/test_expectations.py::test_a_declining_non_nsfw_reviewer_is_reported_without_redraws`, `::test_capabilities_come_from_the_router_document_never_from_names`; `integration/test_llm_router_contract.py::test_a_declining_fallback_is_reported_plainly_and_recorded`, `::test_subscriber_keeps_the_status_endpoint_current`; `integration/test_workflows_and_prompt_assistant.py::test_prompt_assistant_status_uses_bounded_cached_health_without_contacting_ollama`; `frontend/test/render.test.mjs` "Creative Direction says when the LLM Router serves it from a non-NSFW fallback" and "Creative Direction offers the LLM Router's current catalog as the model to choose"; `frontend/e2e/principal-journeys.spec.mjs` "Creative Direction offers the LLM Router's models and remembers the one chosen" and "Creative Direction keeps a model chosen in the focused prompt editor" |
| 12 | If it shows where models run, reads it from `hosts[]`/`host` and treats a missing machine as that machine's models being unavailable, naming no cause the document did not give (§4) | This client shows one model list, not a machine list: the only label it renders is the entry's published `label` (`_display_label` prefers `display_name_qualified`, §4's one label per row a client may render and match on), and it reads no `hosts[]` field, so nothing in the interface can put a model on a machine the document did not put it on. A machine that stops answering therefore needs no rule of its own — its models keep their rows with `available: false` (a host's `available` is an aggregate the router computes from them), and that field is the only availability this code reads; the response is §5's fallback. Unavailability is named neutrally wherever it appears: the option label adds "(unavailable)" and its hint reads "Not available on the LLM Router right now" (`frontend/src/render.mjs`), `offline_reason` carries `offline_services[].reason` when the router gives one and stays `"unavailable"` when it does not, and no message in this project says a machine crashed, went offline or was misconfigured. The one cause the document does carry — a machine that never published, whose models are simply absent — reaches this code as a missing row and is reported as "not in the LLM Router's current catalog" | `unit/test_llm_router_catalog.py::test_a_service_the_router_stopped_is_listed_as_unavailable_with_its_reason`, `::test_a_model_with_no_service_id_is_never_offered_and_never_selected`; `unit/test_ollama_router.py::test_a_chosen_model_the_router_no_longer_publishes_keeps_its_place`; `frontend/test/render.test.mjs` "Creative Direction offers the LLM Router's current catalog as the model to choose" |
| 13 | Keeps a verbatim copy at `docs/llm-router-contract.md`, a conformance map, and an `AGENTS.md` rule | This file (header, verbatim version 1 text, this map) and [`AGENTS.md`](../AGENTS.md). The traceability prose in `scripts/generate_traceability.py` points here. | `unit/test_ollama_router.py::test_the_contract_is_vendored_verbatim_with_a_conformance_map_and_agent_rule` (SHA-256 of the vendored text, every map row, the agent rule) |

### Interpretations and permitted deviations

- **Effort shape.** The router publishes `metadata.reasoning.efforts` as an object keyed by effort name (`{"default": …, "off": …, "xhigh": …}`); both that and a plain list are accepted, and the efforts are matched by name after the section 6 aliases.
- **Thinking is not a selection filter.** Thinking is optional quality, while NSFW status is the owner's selection rule, so `reasoning` is not added to `require`; the effort is adapted to the chosen model instead (never an unlisted effort, `false` for a model that cannot think).
- **`length` versus incomplete.** Section 9 lists `length` as a completed finish reason, but the router marks an output-limit stop `x_router.status: "incomplete"` (`stop_reason: "max_output_tokens"`). Following the MUST, such an answer is never accepted, even when its text parses; the existing `2048 → 4096 → 8192` budget ladder escalates instead. A plain Ollama answer without `x_router` keeps the earlier behavior.
- **Timeouts.** Section 10 lists timeouts as transient, but section 7 forbids abandoning and resubmitting a queued request; read timeouts are therefore reported, not retried. Connect timeouts and connection failures are retried.
- **Status while draining.** The status endpoint reports `available: false` with `state: "waiting"` during a switch, so the browser does not start new compositions; requests already sent, Auto-generate, and expectation checks wait as item 6 describes.
- **Named mode without a document.** As in the reference `resolve`, named mode sends the preferred service when no document could be read and lets error classification decide, while the status endpoint reports the router unreachable until a document arrives. Capability mode sends nothing without a document.
- **Opt-in services.** This application adopts none. `trial` is kept out of the capability pool, out of every name-based fallback list and out of the control panel's list, so no request of ours can fall back to or away from one (§3, §5, §10); a catalog publishing only `trial` therefore reports no usable model rather than quietly serving it, and `CIF_OLLAMA_MODEL=trial` remains the way an operator who has chosen a trial configuration opts in.
- **Where models run.** The interface is one model list, not a machine list: the label it renders is the entry's published `label` (`display_name_qualified` where the router gives one) and no `hosts[]` field is read, so nothing here can attribute a model to a machine the document did not attribute it to. §4's `hosts[]` table is available and deliberately unused, including `hosts[].model_ids[]`, which §3 forbids sending anyway.
- **Panel order.** `_entry_sort_key` lists unavailable services last and breaks a display tie by service ID; that is presentation only. Selection follows §4's order, where the final tie is the order the document lists the rows (item 5).
- **Refusal detection.** Only models not declared NSFW (`nsfw` false or null) are checked for refusals: a prompt or review summary that opens like a refusal ("I'm sorry", "I can't", …), output that fails the schema, or the router's `MALFORMED_STRUCTURED_OUTPUT`/`EMPTY_UPSTREAM_RESPONSE`.
- **Context.** Requests are small (bounded instructions, prompt, Creative Direction and one 1024 px image), so `context_length_exceeded` is reported (`ollama_context_exceeded`) rather than retried on a larger model; the output limit is fitted to the serving model's window before sending.
- **Credentials.** The router needs none. `CIF_OLLAMA_API_KEY` defaults to unset and the example uses the `local-only` placeholder; any other value logs `prompt_assistant_router_credential_configured` at startup.
- **Data handling (section 12).** The README tells operators that Prompt Assistant requests (Creative Direction, prompts, instructions and probe images) pass through the router, which archives every generation for up to seven days; the application shows no separate notice to users and sends no secrets to the router. The application is a private appliance with administrator-provisioned accounts, so who may see NSFW output is the owner's decision.
- **Benchmark exception.** Not applicable: this is not a benchmarking client, so fallback is enabled by default.
