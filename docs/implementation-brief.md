# Implementation Prompt: Build the ComfyUI Image Generation Front-End

You are the senior full-stack engineer responsible for producing a complete, working implementation of a Dockerized web application that serves as a simplified image-generation front end for ComfyUI.

You are being given two normative attachments:

1. **ComfyUI Image Generation Front-End - Product and Implementation Requirements, Version 1.0**
2. **ComfyUI Front-End Workflow Contract Design, Revision 1.1**

Read both documents completely before designing or writing code. Do not reduce them to a superficial summary. Build the application they describe.

## Mission

Deliver a production-quality, self-contained repository for a small home-network appliance. The application must authenticate local users, discover compatible ComfyUI workflow profiles through ComfyUI's API, render contract-defined controls, queue and monitor image generations, optionally use Ollama to compose prompts, display one progressively updating gallery card per generation, preserve private per-user history, and support exact request recall as defined in the requirements.

Do not stop at architecture, wireframes, pseudocode, a scaffold, or a partial prototype. Produce working code, migrations, tests, Docker packaging, configuration examples, and documentation.

## Normative priority

Treat both attachments as requirements, not inspiration.

- The product requirements document governs application behavior, UX, account management, persistence, deployment, privacy, and testing.
- The workflow contract document governs ComfyUI workflow discovery, validation, semantic controls, bindings, graph compilation, stages, progressive artifacts, canonical output, cancellation, and error semantics.
- When the product requirements make a concrete decision in an area the contract labels as a recommendation, follow the product requirements.
- Never weaken strict workflow validation or silently substitute graph versions.
- If you find a genuine contradiction that makes implementation impossible, document it precisely. Do not invent a broad reinterpretation to avoid a difficult requirement.

## Architectural freedom

You may choose the frontend, backend, ORM, migration tool, test framework, and build tooling. Choose mature, maintainable technologies appropriate for a single-container application with SQLite, server-side sessions, an internal durable queue, live browser updates, and a rich but restrained gallery UI.

Keep the architecture proportionate to a home-network appliance. Avoid unnecessary distributed infrastructure, message brokers, microservices, Kubernetes, or a separate database container. A backend plus compiled frontend in one application image is expected.

Document the chosen stack and the reasons it fits the requirements.

## Non-negotiable boundaries

1. **Do not implement, package, or install the ComfyUI `FrontendWorkflowContract` or `FrontendWorkflowArtifact` custom nodes.** They and the prepared workflows are external prerequisites.
2. **Do not use a shared filesystem mount to discover ComfyUI workflows.** Retrieve the configured workflow directory through ComfyUI's network API and isolate route/version differences in a capability-probed adapter.
3. **Do not expose ComfyUI or Ollama directly to the browser.** All external-service communication belongs in the application backend.
4. **Do not expose arbitrary ComfyUI widgets.** Render the semantic resolved contract only.
5. **Do not add a user-facing Ollama model selector.** Select an available model automatically and record what was used.
6. **Do not let administrators inspect another user's prompts, images, controls, uploads, or history.** Enforce this in backend queries and artifact delivery.
7. **Do not silently replace a missing historical workflow with a newer graph during Recall settings.** Exact identity and hashes must match.
8. **Do not promise universal pixel-for-pixel reproduction.** Reconstruct the exact request and explain the deterministic-environment qualification.
9. **Do not put extra metadata or actions into the gallery-card footer.** It contains source, centered dot, date, and Recall settings only.
10. **Do not return code that has not been validated as far as the execution environment permits.** Run and report the tests.

## Required implementation outcomes

### Application shell and UI

Build the dark, minimal, consistent interface described by the product requirements:

- Thin top bar with configurable title at left.
- Gallery scale slider at the upper right immediately before the account menu.
- Account menu with sign out, appropriate password management, and administrator navigation for the bootstrap administrator.
- Stable left generation panel with the restrained Generate button at the top, workflow source selector directly below, and contract-defined controls beneath.
- Independently scrolling control area with basic and advanced grouping.
- Collapsed Prompt Assistant adjacent to the `prompt.text` field.
- Primary gallery viewport, newest first, lazy-loaded and paginated for thousands of images.
- One card per accepted generation.
- Card footer with `generation source · localized submission date` and only the Recall settings action.
- Natural image aspect ratios, no forced square crops.
- Running cards updated in place as progressive artifacts arrive.
- Accessible detail view for full images, checkpoint timeline, status/error information, contextual cancel, and owner-only deletion.
- Responsive narrow-screen behavior without sacrificing desktop usability.

Implement a coherent design-token/component system. Use dark blue-gray, charcoal, and near-black surfaces with restrained blue accents, consistent control geometry, clear focus states, WCAG-AA contrast, and reduced-motion support. The Generate button must be easy to find because of its fixed placement, not because it is oversized or gaudy.

### Authentication and account administration

Implement:

- Bootstrap administrator initialization from configuration only on an empty database.
- Mandatory password change for the bootstrap administrator and every account using a temporary password.
- Administrator creation of ordinary users with temporary passwords.
- Administrator password reset for ordinary users, with session revocation and a new forced change.
- Administrator deletion of ordinary users with complete application-side cascade cleanup.
- Secure password hashing, server-side sessions, cookie security, CSRF protection appropriate to the stack, and basic login throttling.
- No self-registration, email, two-factor authentication, SSO, or external recovery.

Administrators may manage account records and workflow diagnostics. They may not query or retrieve another user's generation content. Write tests that prove the administrator receives an authorization failure from content endpoints.

### Persistence

Use SQLite for structured records and an application-owned persistent filesystem for images and uploads. Enable foreign keys, use migrations, and configure SQLite appropriately for modest concurrency.

Persist every accepted generation before dispatch, including:

- Owner and lifecycle state.
- Workflow identity/version and all required hashes.
- Resolved contract snapshot.
- Immutable compiled API graph snapshot and hash.
- Requested and effective controls.
- Every resolved seed.
- Prompt and Ollama provenance.
- Upload/source-asset references and hashes.
- Queue and ComfyUI identifiers.
- Stage/events/errors.
- Progressive, best-available, and final artifacts with lineage and metadata.

Copy retained outputs into application-owned storage so history does not depend on ComfyUI retention. Generate thumbnails or responsive derivatives. Never serve the asset directory publicly without authorization.

### ComfyUI workflow discovery

At startup and on administrator refresh:

1. Probe ComfyUI and the required workflow/user-data capabilities.
2. List the configured workflow directory through ComfyUI's API.
3. Pair files by basename using:

   ```text
   <profile-basename>.workflow.json
   <profile-basename>.api.json
   ```

4. Parse the UI workflow and locate exactly one `FrontendWorkflowContract`.
5. Apply the full static and runtime validation lifecycle from the contract document.
6. Verify the UI graph and API graph hashes, selectors, bindings, dependencies, runtime node schemas, capabilities, branches, stages, and outputs.
7. Require exactly one valid `prompt.text` semantic control.
8. Register only valid pairs.
9. Exclude invalid profiles from ordinary users and expose concise diagnostics only to the administrator.

Do not generate API graphs from arbitrary UI workflows. Do not modify the source files. Store validated immutable source representations and clone them for each compilation.

### Contract-driven generation controls

Render all controls from the resolved contract. Support the contract's initial types, constraints, conditions, dynamic options, uploads, derived controls, presets, branches, and capability states. Do not hard-code workflow-specific node IDs into frontend code.

Validation must happen before queue acceptance. Resolve random seeds to concrete integers at acceptance time. A rejected form validation does not create a gallery record. An accepted request immediately creates a durable card and queue entry.

### Ollama Prompt Assistant

Implement a backend Ollama adapter and the collapsed Prompt Assistant:

- Server-configured base URL.
- Automatic deterministic selection of an available model, with no user-facing model control.
- Creative direction text area.
- Compact mode choice: Refine current prompt or Create from creative direction.
- Explicit Compose Prompt action.
- Structured finalized-prompt response when available, with a safe plain-text fallback.
- Returned prompt replaces the visible `prompt.text` value and remains editable.
- Generate never invokes Ollama automatically.
- Ollama failure disables only Prompt Assistant.
- Persist the model, template version, prompt before composition, direction, Ollama output, and exact final submitted prompt when used.
- Recall restores the final submitted prompt and does not invoke Ollama.

### Durable queue and scheduling

Implement an application-owned persistent queue in SQLite:

- No small per-user job-count limit.
- FIFO per user.
- Fair round-robin dispatch across users.
- Configurable ComfyUI concurrency, default one.
- Browser disconnect/sign-out does not cancel jobs.
- Queue survives restart.
- Queued jobs wait through ComfyUI outages and resume when possible.
- Previously dispatched jobs reconcile by stored `prompt_id`, events, and history after restart.
- Unknown restart outcomes become an explicit interrupted/failed state without losing recall data.

Submitting one job must not leave the Generate button unavailable for the duration of execution. Once the request is durably accepted, the user can submit another.

### Execution, live updates, and cancellation

Implement the workflow contract's compilation and execution pipeline. Keep ComfyUI details inside the backend adapter and semantic compiler.

Provide an authenticated application event stream using SSE or an application WebSocket. It must publish queue state, semantic stages, artifact availability, cancellation state, errors, and terminal completion. Reconnection must recover current durable state.

Handle declared progressive outputs immediately when retrievable. Update the same gallery card to the newest meaningful checkpoint while retaining earlier checkpoints for the detail timeline. Mark a final artifact canonical only after terminal success. Keep best-available artifacts explicitly non-final after cancellation or later-stage failure.

Support queued and running cancellation. Model `cancel_requested` separately, reconcile races, retain emitted artifacts, and preserve the record for Recall settings.

### Recall settings

Implement Recall settings exactly:

- It is the sole persistent card-footer action.
- It immediately overwrites all current left-panel values without asking about unsaved changes.
- It selects the exact historical source and repopulates all semantic inputs, uploads, branches, prompt, and resolved seeds.
- It never queues a job automatically.
- It never calls Ollama.
- A later Generate creates a new immutable generation record.
- Before enabling Generate, validate that the exact workflow ID/version/hashes are currently registered and that the recalled controls compile consistently.
- If the exact source is unavailable, leave history viewable but disable Recall settings with a concise explanation. Do not silently migrate to a newer workflow.

Use requested controls for normal user-facing fields when they reproduce the same effective request; use the historical effective seed integers and other explicit effective equivalents where required for reproducibility.

### Gallery and lifecycle behavior

Every accepted generation remains in the gallery whether it succeeds, fails, is cancelled, or becomes interrupted.

- Queued: status placeholder.
- Running before an artifact: semantic stage/progress placeholder.
- Running with artifacts: newest declared checkpoint.
- Succeeded: canonical final artifact.
- Cancelled/failed with artifacts: best-available image and restrained non-final status in the media area.
- Cancelled/failed without artifacts: neutral status placeholder.

Do not put status in the footer. If one generation returns multiple final images, keep one card and expose all siblings in the detail view.

### Deletion

Users can permanently delete their own generations from the detail view after confirmation. Active jobs must be cancelled/reconciled before cleanup. Delete application-owned records and files only; do not purge ComfyUI or Ollama.

Deleting a user must revoke sessions, remove queued work, cancel/reconcile running work, delete all application-owned content, and then remove the account without orphan rows or files. The administrator performs this operation without being shown the user's content.

## Testing and quality gate

Tests are part of the implementation, not a later suggestion.

Build deterministic fake ComfyUI and Ollama services for tests. The fake ComfyUI must support workflow listing and retrieval, runtime capability information, prompt submission, stage events, progressive artifacts, multiple outputs, cancellation races, history, output retrieval, and failure/disconnect modes. Include valid and invalid paired workflow fixtures.

Use an appropriate test pyramid:

- Unit tests for domain and adapter logic.
- Database/repository tests.
- Backend integration/API tests.
- Frontend component/integration tests.
- Browser end-to-end tests for the critical journeys.
- Security tests for cross-user access and administrator content denial.
- Migration tests.
- Production build and Docker startup smoke tests.
- Optional live integration tests gated by environment variables.

At minimum, cover every mandatory scenario listed in the requirements document.

Create one documented validation command, script, or make target that runs formatting checks, linting, type checking, unit tests, integration tests, frontend tests, production build, and container smoke validation as far as practical.

Run the validation suite before delivery. Fix failures rather than merely listing them. In the final response, state exactly which commands ran and their outcomes. Never claim a test ran when it did not. For a check blocked by the execution environment, provide the precise reason and the exact command the user should run.

## Required repository artifacts

Return a repository containing at least:

- Complete frontend and backend source.
- Production Dockerfile.
- Practical Compose example for connecting the app to already-running ComfyUI and Ollama services.
- `.env.example` or equivalent configuration template.
- SQLite migrations.
- Automated tests and fake external services/fixtures.
- `README.md` with setup, first login, development, production, troubleshooting, and backup/restore.
- Architecture documentation.
- Application API documentation.
- Test/validation documentation.
- `docs/traceability.md` mapping every requirement ID to implementation files and one or more tests.
- A clear note that the ComfyUI custom-node package and prepared workflow pairs are external prerequisites.

## Working method

1. Read both attachments fully.
2. Extract a requirement checklist and create the traceability matrix before or alongside implementation.
3. Choose and document the technology stack.
4. Implement in cohesive vertical slices, keeping the application runnable.
5. Add tests as each slice is implemented rather than postponing validation.
6. Exercise migrations and restart/recovery behavior.
7. Build the production container and run the available complete validation suite.
8. Review the final implementation against every requirement ID and close omissions before returning it.

Do not ask for stylistic preferences already defined in the requirements. Make reasonable low-level choices and document them. Ask a question only if an actual contradiction prevents a safe implementation; otherwise proceed and provide the completed repository.

## Final response format

When returning the completed work, provide:

1. A concise architecture and technology summary.
2. The repository/file-tree overview.
3. Exact local and Docker startup commands.
4. Initial administrator/configuration instructions.
5. Exact validation commands executed and results.
6. Any environment-limited checks that were not executable, without overstating completion.
7. A short list of genuine known limitations only; do not label missing required functionality as a limitation.

The result must be a working application, not merely a plan.
