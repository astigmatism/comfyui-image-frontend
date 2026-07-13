# ComfyUI Image Generation Front-End
## Product and Implementation Requirements

**Version:** 1.0  
**Status:** Implementation-ready  
**Intended audience:** AI coding agents, application architects, full-stack engineers, and test engineers  
**Deployment context:** A small, trusted home network with one application instance, one ComfyUI service, one Ollama service, several users, and typically no more than thousands of retained images

---

## 1. Normative language and references

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative.

This application has two normative inputs:

1. This requirements document, which defines the product, user experience, persistence, security, deployment, and validation requirements.
2. **ComfyUI Front-End Workflow Contract Design, Revision 1.1**, which defines the workflow contract, semantic controls, graph validation, stages, outputs, progressive artifacts, cancellation, and ComfyUI integration semantics.

The workflow contract document is assumed to be supplied to the implementation model as a separate attachment.

The two documents are complementary. This document governs product behavior. The workflow contract governs how compatible workflows are discovered, validated, compiled, executed, and interpreted. Where this document makes a concrete product decision in an area the contract presents only as a recommendation, this document takes precedence. The implementation MUST NOT weaken the contract's validation, binding, output, cancellation, or security rules.

The implementation MUST NOT create the `FrontendWorkflowContract` or `FrontendWorkflowArtifact` custom-node package. That package, the compatible workflows, and their embedded contracts are prepared by a separate sibling effort and already exist in the ComfyUI environment.

---

## 2. Product summary

The product is a focused, Dockerized image-generation appliance. It provides a minimal authenticated web interface over a separately running ComfyUI container and an optional separately running Ollama container.

Its primary view is a private per-user gallery of generation attempts. A compact control panel on the left lets the user choose a compatible ComfyUI workflow, configure the controls declared by that workflow's contract, optionally use Ollama to compose or refine the main prompt, and queue a generation. The gallery updates as ComfyUI emits meaningful progressive artifacts and retains the complete application-owned history needed to inspect and recall a generation request.

The application deliberately abstracts away ComfyUI's graph editor and raw node concepts. Users interact with semantic workflow controls, not node IDs, sockets, or arbitrary ComfyUI widgets.

The application is not a ComfyUI replacement, workflow editor, model manager, public gallery, or large multi-tenant platform.

---

## 3. Goals

### 3.1 Product goals

- **PROD-001:** Provide a fast, minimal, easy-to-read interface for the image-generation portion of the user's ComfyUI workflows.
- **PROD-002:** Make the gallery the primary application view rather than a secondary history page.
- **PROD-003:** Let users queue any number of valid generation requests without waiting for earlier jobs to complete.
- **PROD-004:** Preserve a complete private history of every accepted generation attempt until the owning user deletes it or the owning account is deleted.
- **PROD-005:** Let a user recall a historical generation's source and inputs, including the resolved seed, into the left control panel without immediately generating.
- **PROD-006:** Show meaningful progressive artifacts from a workflow while later stages continue.
- **PROD-007:** Keep account management intentionally simple: local usernames and passwords, one bootstrap administrator, no email, and no external identity provider.
- **PROD-008:** Remain useful when Ollama is unavailable and remain usable for login and historical browsing when ComfyUI is temporarily unavailable.

### 3.2 Engineering goals

- **ENG-001:** Use the supplied workflow contract as the authoritative semantic integration surface.
- **ENG-002:** Keep all ComfyUI and Ollama communication on the server side. The browser MUST communicate only with the application backend.
- **ENG-003:** Persist structured data durably in SQLite and persist media in an application-owned filesystem volume.
- **ENG-004:** Package the frontend and backend as one deployable application container.
- **ENG-005:** Include automated tests and a repeatable validation workflow that the implementation model can execute before returning code.
- **ENG-006:** Fail closed when a workflow contract, workflow pair, graph hash, binding, capability, or output declaration is invalid.

---

## 4. Explicit non-goals

The first release MUST NOT include:

- Self-registration.
- Email addresses, email verification, email notifications, or password recovery by email.
- Two-factor authentication, social login, SSO, OAuth, or LDAP.
- Public or shared galleries.
- Cross-user sharing.
- Billing, credits, subscriptions, or quotas intended for commercial use.
- ComfyUI workflow editing or graph visualization.
- Creation, installation, or modification of ComfyUI custom nodes.
- Model, LoRA, checkpoint, or custom-node installation and administration.
- Arbitrary exposure of ComfyUI node inputs.
- A user-facing Ollama model selector or Ollama model-management UI.
- A shared filesystem mount used by the application to read ComfyUI workflows.
- Deleting ComfyUI history or ComfyUI-owned files when application records are deleted.
- Deleting or modifying anything in Ollama when application records are deleted.
- Search, filtering, favorites, comments, notes, social features, or public links unless later added as a separate requirement.
- A guarantee of bit-for-bit image reproduction after workflow, model, dependency, runtime, GPU, or hardware changes.

---

## 5. Terms

- **Application:** The Dockerized web application defined by this document.
- **ComfyUI:** The separately running ComfyUI service that stores compatible workflow files, queues prompt graphs, executes workflows, emits progress, and exposes artifacts.
- **Ollama:** The separately running Ollama service used only for optional prompt composition.
- **Generation source / workflow profile:** A compatible, validated ComfyUI UI/API workflow pair exposed in the source dropdown.
- **Accepted generation:** A request that has passed application validation, had its effective values and seed resolved, been durably stored, and entered the application queue.
- **Generation record:** The durable application record for one accepted generation attempt.
- **Requested controls:** Values supplied or selected at the semantic user-control layer.
- **Effective controls:** Values after defaults, transforms, derived values, branch compilation, snapping, and seed resolution.
- **Progressive artifact / checkpoint:** A contract-declared, meaningful output that becomes available while later workflow stages continue.
- **Canonical artifact:** The contract-declared final result after the terminal workflow stage succeeds.
- **Best-available artifact:** The highest eligible retained checkpoint after cancellation or later-stage failure. It is not canonical.
- **Recall settings:** The operation that replaces the current generation panel with the historical source and inputs from one generation record.

---

## 6. Deployment and runtime architecture

### 6.1 Container model

- **DEP-001:** The deliverable MUST build into one application container containing the backend and the production frontend assets.
- **DEP-002:** ComfyUI and Ollama remain external services and are not bundled into the application image.
- **DEP-003:** SQLite and all application-owned assets MUST be stored beneath one or more persistent mounted data paths so container replacement does not lose history.
- **DEP-004:** The deliverable MUST include a production `Dockerfile`, a practical Compose example, an environment/configuration example, health checks, startup documentation, and backup/restore guidance.
- **DEP-005:** The application MUST write structured operational logs to standard output. It MAY additionally support a persisted log path, but persisted logs are not required.
- **DEP-006:** Database migrations MUST run safely and automatically at startup or through a documented one-command migration step that is invoked by container startup.
- **DEP-007:** The container MUST shut down gracefully, stop accepting new dispatches, preserve queued records, and avoid corrupting SQLite or media files.

### 6.2 Configuration

The application MUST support server-side configuration for at least:

- Application title.
- Listen host and port.
- Persistent data directory.
- SQLite database path.
- Session secret or key material.
- Bootstrap administrator username.
- Bootstrap administrator temporary password.
- ComfyUI HTTP base URL.
- ComfyUI WebSocket location if it cannot be derived from the HTTP URL.
- ComfyUI workflow directory or namespace visible through ComfyUI's workflow/user-data API.
- Ollama base URL.
- Maximum application-to-ComfyUI concurrent jobs, defaulting to one.
- Upload byte and decompressed-pixel limits.
- Cookie security settings suitable for direct HTTP on a trusted LAN or HTTPS behind a reverse proxy.
- Log level.

- **CFG-001:** Secrets MUST NOT be committed to source control or embedded in frontend assets.
- **CFG-002:** Bootstrap administrator configuration MUST be used only when initializing an empty database. Restarting the application MUST NOT reset an existing administrator password.
- **CFG-003:** If no bootstrap administrator can be created on an empty database, startup MUST fail with a clear, actionable error.
- **CFG-004:** An unreachable ComfyUI or Ollama service MUST NOT prevent the application process from starting, authenticating users, or serving existing history.
- **CFG-005:** The application MUST periodically recheck external-service health and allow an administrator to refresh workflow discovery manually.

---

## 7. Authentication, accounts, and sessions

### 7.1 Account roles

The first release has exactly two roles:

- A pre-established application administrator.
- Ordinary users created by that administrator.

The application does not need to support creating additional administrators.

### 7.2 Bootstrap administrator

- **AUTH-001:** On an empty database, the application MUST create the bootstrap administrator from server-side configuration.
- **AUTH-002:** The bootstrap password MUST be treated as temporary, and the bootstrap administrator MUST change it before entering the normal application.
- **AUTH-003:** The bootstrap administrator account MUST NOT be deletable through the normal administrator interface.

### 7.3 User creation and temporary passwords

- **AUTH-004:** The administrator MUST be able to create an ordinary user with a unique username and a temporary password.
- **AUTH-005:** A newly created user MUST be forced to choose a new password on first login before accessing the gallery.
- **AUTH-006:** The administrator MUST be able to reset an ordinary user's password to a new temporary password.
- **AUTH-007:** A password reset MUST revoke the user's existing sessions and force a password change at the next login.
- **AUTH-008:** Usernames MUST be unique case-insensitively. The implementation MUST define and document a conservative allowed-character rule.
- **AUTH-009:** Passwords MUST be stored with a modern password-hashing function such as Argon2id or bcrypt. Passwords and temporary passwords MUST never be logged.
- **AUTH-010:** The password policy SHOULD prioritize reasonable length over composition rules. No email or external recovery mechanism exists.

### 7.4 Sessions

- **AUTH-011:** Authentication SHOULD use server-managed sessions and HTTP-only cookies rather than browser-local bearer tokens.
- **AUTH-012:** Cookies MUST use appropriate `HttpOnly`, `SameSite`, path, and configurable `Secure` attributes.
- **AUTH-013:** Login, password-change, password-reset, and destructive account actions MUST have CSRF protection appropriate to the selected framework.
- **AUTH-014:** Login attempts MUST receive basic rate limiting or temporary backoff.
- **AUTH-015:** A user MUST be able to sign out from the account menu.
- **AUTH-016:** A logged-in user SHOULD be able to change their own password from the account menu.

### 7.5 Administrator capabilities

The administrator interface MUST be deliberately narrow. It MAY show usernames, account creation dates, and account-management state, but it MUST NOT expose user-generation content.

The administrator can:

- List application users.
- Create ordinary users.
- Reset an ordinary user's password to a temporary password.
- Delete an ordinary user.
- Refresh workflow discovery and inspect workflow-registration diagnostics.
- Sign out.

The administrator does not gain permission to view another user's images, prompts, controls, uploaded assets, generation details, or history.

---

## 8. Authorization and privacy

- **PRIV-001:** Every generation, artifact, upload, prompt-assistant record, event, and preference MUST have an owning user.
- **PRIV-002:** Ordinary users MUST be able to access only their own records and files.
- **PRIV-003:** Application administrators MUST NOT be able to browse, search, fetch, or inspect another user's generation content through the UI or application API.
- **PRIV-004:** Authorization MUST be enforced in backend queries and media-serving endpoints, not merely by hiding frontend controls.
- **PRIV-005:** Guessing or modifying generation IDs, artifact IDs, upload IDs, or URLs MUST not disclose cross-user data.
- **PRIV-006:** Application-owned media MUST not be exposed as an unauthenticated static directory.
- **PRIV-007:** The operating-system or Docker host administrator is outside this application-level privacy guarantee and may naturally have filesystem access.
- **PRIV-008:** Tests MUST explicitly verify that the administrator role cannot use content endpoints as a privilege bypass.

---

## 9. Data persistence and ownership

### 9.1 Storage model

- **DATA-001:** SQLite MUST store users, password/session state, workflow registry data, queue state, generations, controls, provenance, service events, artifact metadata, deletion state, and user preferences.
- **DATA-002:** Generated images, progressive checkpoints, uploaded source images, masks, thumbnails, and other binary assets MUST be stored in an application-owned persistent filesystem, not as large database blobs.
- **DATA-003:** SQLite foreign keys MUST be enabled. Schema design and cleanup logic MUST prevent dangling database rows.
- **DATA-004:** Media filenames and paths MUST use opaque identifiers and MUST NOT be derived directly from prompts or arbitrary user text.
- **DATA-005:** Stored artifacts SHOULD include a cryptographic content hash, MIME type, byte size, dimensions, timestamps, contract role, batch index, sequence, state, and lineage where available.
- **DATA-006:** Timestamps MUST be stored in UTC and displayed in the user's browser-local time zone.
- **DATA-007:** SQLite SHOULD use WAL mode and sensible busy-timeout settings for the expected small concurrent workload.

### 9.2 Generation record contents

For every accepted generation, the application MUST preserve enough information to inspect and reconstruct the same generation request. At minimum this includes:

- Application generation ID and owning user ID.
- Status and status history.
- Submission, dispatch, start, stage, artifact, completion, cancellation, and failure timestamps where applicable.
- Queue order and scheduler state needed for recovery.
- ComfyUI `prompt_id` and application client/correlation identifiers.
- Workflow ID, display name, version, contract schema version, adapter version, and relevant hashes.
- A snapshot of the resolved workflow contract used for the request.
- A snapshot or durable representation of the immutable compiled API graph submitted for the request, along with its hash.
- Requested controls.
- Effective controls.
- Resolved seeds, including every seed used by the workflow.
- Selected branches, presets, dynamic options, and requested outputs.
- Source uploads and their hashes.
- The exact final prompt submitted to ComfyUI.
- Ollama provenance when prompt assistance was used.
- ComfyUI stage and output reconciliation data.
- Sanitized user-facing errors and sufficient internal diagnostics for troubleshooting without exposing secrets.
- Every application-retained declared artifact and its role, state, sequence, and lineage.

- **DATA-008:** Random-seed policies MUST be resolved to concrete integers before the request is accepted into the queue.
- **DATA-009:** The generation record created at queue acceptance MUST be immutable with respect to that execution. Later UI edits create a different future request and MUST NOT modify the queued record.
- **DATA-010:** The application MUST copy retained ComfyUI outputs into application-owned storage. Historical viewing MUST not depend on ComfyUI continuing to retain the original output file.
- **DATA-011:** All contract-declared user-visible progressive checkpoints that the application receives MUST be retained in the generation history until the generation or user is deleted. Incidental sampler frames and undeclared editor previews MUST not be stored as first-class history artifacts.
- **DATA-012:** The application SHOULD generate thumbnails or responsive derivatives for efficient gallery loading while retaining the original application-owned artifact.

### 9.3 Complete history and deletion

"Complete history" means complete until the owning user chooses deletion or the owning account is deleted.

- **DATA-013:** Successful, failed, cancelled, and interrupted accepted generations MUST remain in history.
- **DATA-014:** A request rejected during pre-queue validation MUST be reported in the control panel and MUST NOT create a permanent gallery record.
- **DATA-015:** A queued generation cancelled before dispatch still has a complete recallable record because validation, defaults, uploads, and seed resolution occurred before queue acceptance.
- **DATA-016:** No global automatic retention period is required for the first release.

---

## 10. ComfyUI connectivity and responsibility boundary

- **COMFY-001:** The application backend MUST communicate with ComfyUI through ComfyUI's network APIs only.
- **COMFY-002:** The browser MUST NOT connect directly to ComfyUI HTTP or WebSocket endpoints.
- **COMFY-003:** The application MUST NOT require a shared filesystem mount to the ComfyUI container.
- **COMFY-004:** The application MUST NOT create, edit, install, or delete source workflows in ComfyUI.
- **COMFY-005:** Generations submitted by the application are normal ComfyUI prompt jobs and may appear in ComfyUI queue/history. The application does not need to make the ComfyUI visual editor automatically load the corresponding graph onto its canvas.
- **COMFY-006:** Jobs created outside this application MUST NOT be imported automatically into application user histories.
- **COMFY-007:** The ComfyUI integration MUST be isolated behind a backend adapter so route or event differences can be capability-probed and tested.

The adapter is expected to use ComfyUI capabilities equivalent to workflow/user-data listing and retrieval, `/object_info`, prompt submission, queue inspection or deletion, interrupt, WebSocket execution events, history reconciliation, uploads, and output retrieval. Exact route details MUST be probed or isolated rather than scattered through UI code.

---

## 11. Workflow discovery and registration

### 11.1 Paired workflow representation

Each compatible generation source is prepared outside this application as a pair of files in a configured ComfyUI workflow directory:

```text
<profile-basename>.workflow.json
<profile-basename>.api.json
```

The files share the same basename.

- The `.workflow.json` file is the UI-format workflow containing exactly one valid `FrontendWorkflowContract` node and its manifest.
- The `.api.json` file is the approved executable API-format graph whose hash is declared by the contract.

The sibling workflow-preparation effort is responsible for creating and publishing these pairs. This application only discovers, retrieves, validates, registers, clones, patches, and submits them.

### 11.2 Discovery lifecycle

- **WF-001:** On application startup, the backend MUST probe ComfyUI connectivity and the API capabilities needed to list and retrieve files from the configured workflow directory.
- **WF-002:** It MUST enumerate matching UI/API pairs through ComfyUI's network API.
- **WF-003:** It MUST retrieve the candidate files and execute the static and runtime validation lifecycle defined by the workflow contract.
- **WF-004:** Only complete pairs that pass strict validation may appear in the user's generation-source dropdown.
- **WF-005:** A missing mate, malformed JSON document, missing or duplicate contract node, schema error, graph-hash mismatch, unresolved binding, missing required dependency, ambiguous selector, invalid output, or unsupported required capability MUST reject the profile.
- **WF-006:** Rejected profiles MUST be absent from ordinary users' source selectors.
- **WF-007:** Administrators MAY view a concise diagnostic list of accepted and rejected profile basenames, workflow IDs/versions, health state, and reasons for rejection. These diagnostics MUST NOT include user generations.
- **WF-008:** The administrator MUST be able to trigger workflow discovery again without restarting the container.
- **WF-009:** Discovery at startup and administrator refresh are required. Continuous filesystem watching is not required.
- **WF-010:** A profile already used by historical generations may later disappear or change. Historical records and images remain viewable, but the application MUST NOT silently substitute a different graph for recall.

### 11.3 Primary prompt convention

The product requires one principal natural-language prompt field.

- **WF-011:** Every application-supported workflow MUST expose exactly one semantic control whose stable ID is `prompt.text`.
- **WF-012:** `prompt.text` MUST be a string or multiline-string control suitable for image-generation prompting.
- **WF-013:** The UI MUST label it **Prompt**, not "positive prompt."
- **WF-014:** A profile that lacks a unique valid `prompt.text` control MUST be rejected as unsupported for this product rather than guessed from arbitrary node titles.

This convention avoids requiring a new custom-node schema field and aligns with the supplied contract's illustrative control IDs.

### 11.4 Contract-driven controls

- **WF-015:** The application MUST render controls from the resolved semantic contract, not from arbitrary raw ComfyUI widgets.
- **WF-016:** It MUST honor control labels, descriptions, order, groups, type, defaults, required state, constraints, options, tier, conditions, conflicts, capabilities, and bindings defined by the contract.
- **WF-017:** Basic controls MUST be readily visible. Advanced groups SHOULD be collapsible. Operator-only controls MUST NOT be exposed to ordinary users.
- **WF-018:** Dynamic options MUST be resolved according to the contract and current ComfyUI runtime capabilities.
- **WF-019:** Unknown controls and values outside allowed constraints MUST be rejected before queue acceptance.
- **WF-020:** Upload controls MUST use application-owned upload IDs and server-side upload/patch behavior. The browser MUST NOT receive arbitrary ComfyUI filesystem paths.
- **WF-021:** Changing the generation source replaces the dynamic control surface with the newly selected profile's defaults. No preservation dialog is required.

---

## 12. Ollama prompt assistance

### 12.1 Scope

Ollama is an optional prompt-composition aid. It does not generate images and it does not replace the visible workflow prompt.

- **OLL-001:** Ollama's base URL MUST be server-side configuration.
- **OLL-002:** The browser MUST communicate with Ollama only through the application backend.
- **OLL-003:** The application MUST NOT expose a model selector or model-management controls to users.
- **OLL-004:** The backend MUST discover an available Ollama model and choose one automatically. If multiple models are available, selection MUST be deterministic and the actual model MUST be recorded.
- **OLL-005:** If no usable model is available or Ollama is unreachable, prompt assistance MUST be disabled with a restrained inline explanation. Ordinary ComfyUI generation MUST continue to work.

### 12.2 User interface and behavior

- **OLL-006:** A collapsed **Prompt Assistant** section MUST appear adjacent to or immediately below `prompt.text`.
- **OLL-007:** The section MUST be collapsed by default to preserve the minimal interface.
- **OLL-008:** It MUST provide a multiline **Creative direction** field, a compact mode choice for **Refine current prompt** versus **Create from creative direction**, and a **Compose Prompt** action.
- **OLL-009:** Ollama MUST run only after the user explicitly chooses Compose Prompt. Pressing Generate MUST never silently invoke Ollama.
- **OLL-010:** In refine mode, the backend sends the current visible prompt plus the creative direction. In create mode, the creative direction can be sufficient even when the visible prompt is empty.
- **OLL-011:** The backend SHOULD request a structured response containing one finalized prompt, with a safe fallback for models that return plain text.
- **OLL-012:** The returned text MUST replace the visible `prompt.text` value so the user can inspect and edit exactly what will be submitted.
- **OLL-013:** Generate MUST submit the visible prompt field exactly as it exists at submission time, subject only to the selected workflow's explicit contract behavior.
- **OLL-014:** Using Ollama MUST NOT automatically modify unrelated workflow controls or invoke a second hidden Ollama pass.

### 12.3 Provenance

When prompt assistance contributes to a generation, the generation record MUST include:

- Prompt text before composition.
- Creative direction.
- Selected prompt-assistant mode.
- Actual Ollama model name.
- Version of the application's server-side prompt-assistant instruction/template.
- Ollama output.
- User edits made after Ollama output and before submission, represented by preserving both the Ollama output and final submitted prompt.
- Error and timing information when relevant.

- **OLL-015:** Recall settings MUST NOT call Ollama again. It restores the exact final submitted prompt and may restore the historical creative direction inside the collapsed assistant for reference.

---

## 13. Generation control panel

### 13.1 Layout

The left side of the main viewport contains a stable generation-control column.

- **CTRL-001:** The Generate button MUST be the first control at the top of the column.
- **CTRL-002:** The generation-source dropdown MUST appear immediately below the Generate action.
- **CTRL-003:** Workflow-defined controls MUST follow in contract-defined order and grouping.
- **CTRL-004:** The control area below the top action/source region MUST scroll independently when a workflow has many inputs.
- **CTRL-005:** The Generate action MUST remain in a predictable, readily accessible location while the form scrolls, such as a restrained sticky panel header.
- **CTRL-006:** The Generate button MUST look like a normal primary control in the shared design system. It MUST NOT be oversized, use unusually large text, glow, pulse, or resemble a marketing call-to-action.
- **CTRL-007:** Validation errors MUST appear near the relevant control and in a concise form-level summary when necessary.
- **CTRL-008:** Generate MUST be disabled when the selected request is invalid or when ComfyUI is known to be unavailable.
- **CTRL-009:** After a request is validated, durably accepted, and queued, the Generate button MUST become available again so the user can queue another request.
- **CTRL-010:** Repeated submissions create distinct immutable generation records.

### 13.2 Dynamic inputs

- **CTRL-011:** All workflow fields, including seeds, images, masks, branches, numbers, enums, booleans, and derived-resolution controls, MUST be represented according to the contract.
- **CTRL-012:** The application MUST resolve random seeds before queueing and present/store the concrete result for history and recall.
- **CTRL-013:** The form MUST distinguish unavailable controls from disabled controls and explain missing optional capabilities without exposing raw server internals.
- **CTRL-014:** Contract conditions MUST dynamically show, hide, enable, disable, require, or forbid controls.

---

## 14. Application queue and scheduling

The application owns a durable queue in front of ComfyUI.

- **QUEUE-001:** A user may submit any number of valid generation requests. The first release MUST NOT impose a small per-user queued-job count.
- **QUEUE-002:** Every accepted generation MUST be durably stored before it is eligible for dispatch.
- **QUEUE-003:** FIFO order MUST be preserved within each user's queue.
- **QUEUE-004:** Dispatch SHOULD use fair round-robin scheduling among users with queued jobs so one user's large backlog does not indefinitely block others.
- **QUEUE-005:** Maximum concurrent jobs sent by this application to ComfyUI MUST be configurable and default to one.
- **QUEUE-006:** Browser closure, navigation, or sign-out MUST NOT cancel an accepted generation.
- **QUEUE-007:** Queued jobs MUST survive application restarts.
- **QUEUE-008:** On startup, the application MUST resume dispatching queued jobs and reconcile previously dispatched jobs using stored ComfyUI identifiers, WebSocket events when possible, and history.
- **QUEUE-009:** If a previously running job cannot be reconciled after restart, it MUST enter a clear interrupted or failed terminal state while retaining its request and any artifacts already copied into application storage.
- **QUEUE-010:** If ComfyUI becomes unavailable after a job has been accepted but before dispatch, the job MUST remain queued and dispatch when service health returns.
- **QUEUE-011:** Jobs and edits outside this application MUST not mutate an already accepted execution snapshot.

---

## 15. Generation execution, progress, artifacts, and cancellation

### 15.1 Compilation and submission

For every accepted request, the backend MUST follow the workflow contract's validation and compilation pipeline. It must resolve effective controls, clone the approved graph, apply only contract-authorized patches and branch selections, validate the compiled graph, calculate its hash, and submit it to ComfyUI.

- **EXEC-001:** The browser MUST never patch workflow JSON.
- **EXEC-002:** The source API graph and each accepted compiled graph MUST be treated as immutable snapshots.
- **EXEC-003:** Raw ComfyUI node IDs and diagnostics MAY be retained internally but user-facing progress and errors MUST use semantic control, stage, and output names.
- **EXEC-004:** The application MUST track ComfyUI prompt IDs and correlate all events and outputs to the correct owning generation.

### 15.2 Client event stream

- **EXEC-005:** The application MUST provide an authenticated application-level event stream, using Server-Sent Events or an application WebSocket, for queue state, stages, progressive artifacts, cancellation, errors, and terminal state.
- **EXEC-006:** The browser MUST not subscribe directly to ComfyUI's WebSocket.
- **EXEC-007:** Reconnecting clients MUST be able to fetch the current generation state and continue receiving later events without losing the durable result.

### 15.3 Progressive artifacts

- **EXEC-008:** Contract-declared progressive artifacts MUST be published to the application as soon as they are retrievable, not only after the complete prompt finishes.
- **EXEC-009:** The gallery card MUST display the highest-sequence currently available artifact suitable for presentation.
- **EXEC-010:** Earlier declared checkpoints MUST remain available in the generation detail view.
- **EXEC-011:** A terminal canonical artifact MUST be marked final only after the contract's terminal success condition is satisfied.
- **EXEC-012:** If a later stage fails or is cancelled, the highest eligible checkpoint MAY become `best_available` but MUST NOT be mislabeled as final.
- **EXEC-013:** If one generation produces multiple canonical batch siblings, they MUST remain part of one generation record and one gallery card. The detail view MUST allow the user to inspect all of them.

### 15.4 Status model

At minimum, the persisted status model MUST distinguish:

- `queued`
- `dispatching`
- `running`
- `cancel_requested`
- `succeeded`
- `cancelled_with_artifacts`
- `cancelled_without_artifacts`
- `failed_with_artifacts`
- `failed_without_artifacts`
- `interrupted` when restart reconciliation cannot determine a more precise result

Names may vary in code, but the semantics MUST remain distinct.

### 15.5 Cancellation

- **EXEC-014:** Users MUST be able to cancel their own queued or running generations.
- **EXEC-015:** A queued cancellation removes the item from future dispatch but preserves the generation record and recall data.
- **EXEC-016:** A running cancellation MUST use the appropriate ComfyUI queue deletion or interrupt behavior and then continue reconciliation until a terminal outcome is known.
- **EXEC-017:** Cancellation is asynchronous. The UI MUST show `cancel_requested` and MUST not promise instantaneous stopping.
- **EXEC-018:** Artifacts emitted before or during a cancellation race MUST be reconciled and retained according to the contract and this document.
- **EXEC-019:** Cancel MUST be contextual to an active card or its detail view. It is not a permanent second action in the card footer.

---

## 16. Main application shell and visual design

### 16.1 Overall shell

The authenticated application uses a two-dimensional shell:

```text
+------------------------------------------------------------------+
| Configurable title                    Gallery scale   Account menu |
+----------------------+-------------------------------------------+
| Generation controls  |                                           |
|                      |              Generation gallery            |
|                      |                                           |
+----------------------+-------------------------------------------+
```

- **UI-001:** The top bar MUST be thin, visually quiet, and span the application width.
- **UI-002:** The configurable application title MUST appear at the upper left.
- **UI-003:** The gallery-scale control and account/profile menu MUST appear at the upper right, with the scale control adjacent to and before the account menu.
- **UI-004:** The account menu MUST provide Sign out, and it MAY provide Change password. Administrators also receive an Administration entry.
- **UI-005:** The application SHOULD avoid unused placeholder controls in the first release even though the toolbar leaves room for future additions.

### 16.2 Theme and component system

- **UI-006:** The interface MUST use a cohesive dark theme built from near-black, charcoal, dark slate, darker blue-gray, restrained blue accents, high-contrast neutral text, subdued borders, and subtle shadows.
- **UI-007:** The interface MUST be minimal, functional, easy to read, and well laid out. It MUST prioritize generated images and generation controls rather than decorative chrome.
- **UI-008:** Buttons, text inputs, text areas, selects, sliders, menus, dialogs, status badges, cards, focus rings, and validation messages MUST come from a consistent shared component system.
- **UI-009:** Reusable design tokens MUST define typography, colors, spacing, radii, control heights, shadows, focus appearance, and motion duration.
- **UI-010:** Every interactive component MUST have consistent default, hover, pressed, focused, disabled, loading, invalid, and selected states as applicable.
- **UI-011:** Primary, secondary, low-emphasis, and destructive actions may differ, but each category MUST be consistent across the application.
- **UI-012:** Motion MUST be restrained and functional. The application MUST avoid decorative pulsing, glowing, floating, or excessive animation.
- **UI-013:** Reduced-motion preferences MUST be honored.

---

## 17. Gallery requirements

### 17.1 Primary view and ordering

- **GAL-001:** The gallery is the main content area of the application.
- **GAL-002:** One accepted generation MUST produce exactly one primary gallery card, regardless of terminal status, number of progressive checkpoints, or number of final batch images.
- **GAL-003:** Newest submissions MUST appear first.
- **GAL-004:** The gallery MUST use lazy loading and cursor-based pagination or equivalent progressive loading suitable for thousands of images.
- **GAL-005:** Search, filters, favorites, tags, and alternate gallery modes are out of scope for the first release.

### 17.2 Scale slider

- **GAL-006:** A compact slider in the right side of the top toolbar MUST control gallery-card width.
- **GAL-007:** The slider MUST cover a broad, continuous or fine-grained range.
- **GAL-008:** At its largest setting, one card MUST occupy essentially the full available gallery width.
- **GAL-009:** At its smallest setting, cards MUST be thumbnail-like but remain large enough to identify the image and operate Recall settings.
- **GAL-010:** The slider MUST change gallery layout/card width only. It MUST NOT zoom the entire page, left panel, toolbar, typography, or dialogs.
- **GAL-011:** The chosen scale MUST persist as a per-user preference across reloads and sessions.
- **GAL-012:** Resizing SHOULD animate subtly without distracting from active generations.

### 17.3 Card media area

- **GAL-013:** Images MUST preserve their natural aspect ratio and MUST not be forcibly cropped into uniform squares.
- **GAL-014:** A queued card with no image MUST show a clean queue/status placeholder.
- **GAL-015:** A running card with no artifact MUST show semantic stage/progress information.
- **GAL-016:** As declared checkpoints arrive, the same card MUST update to the newest presentable checkpoint rather than creating additional gallery cards.
- **GAL-017:** A successful card MUST show the canonical final artifact.
- **GAL-018:** A cancelled or failed card with an artifact MUST show its best-available image with a restrained status treatment in or over the media area.
- **GAL-019:** A cancelled or failed card without an image MUST remain visible as a neutral status placeholder.
- **GAL-020:** Status indicators MUST not be added to the footer metadata line. They belong in the media/status area or detail view.
- **GAL-021:** If a generation has multiple final images, the card may show one primary image and a subtle count indicator outside the footer; all siblings MUST be available in the detail view.

### 17.4 Card footer/caption

The card footer is intentionally minimal.

- **GAL-022:** It MUST show only two pieces of informational metadata: the generation source display name and the generation submission date.
- **GAL-023:** Those values MUST appear in one horizontal metadata line separated by a centered dot, for example: `Portrait Workflow · Jul 12, 2026`.
- **GAL-024:** Long source names MUST truncate gracefully without breaking the card layout.
- **GAL-025:** The only persistent interactive control in the footer MUST be **Recall settings**.
- **GAL-026:** Status, prompt excerpts, seed, dimensions, queue position, and error text MUST NOT be added to the footer in the first release.

### 17.5 Card details

- **GAL-027:** Selecting the media/card outside the Recall button SHOULD open an accessible generation detail dialog or page.
- **GAL-028:** The detail view MUST provide the full-resolution retained artifacts, the ordered checkpoint timeline, current or terminal state, relevant user-facing error information, and deletion for the owning user.
- **GAL-029:** Technical provenance MAY appear in a collapsed section so it does not clutter the default gallery.
- **GAL-030:** The detail view is the appropriate place for permanent Delete and for contextual Cancel when applicable.

---

## 18. Recall settings

Recall is a core workflow, not a secondary convenience.

### 18.1 Normal recall behavior

- **RECALL-001:** Pressing Recall settings MUST immediately replace the current left-panel generation source and all current input values. There MUST be no unsaved-changes confirmation dialog.
- **RECALL-002:** Recall MUST NOT submit a generation automatically.
- **RECALL-003:** It MUST select the historical generation source, render that source's contract-defined controls, and populate every applicable control.
- **RECALL-004:** It MUST restore uploaded source images, masks, branches, presets, prompt values, dimensions, sampler controls, and all other semantic inputs needed by the workflow.
- **RECALL-005:** It MUST populate seed controls with the exact resolved seed integers used by the historical attempt, not with a randomize instruction.
- **RECALL-006:** It MUST restore the exact final prompt that was submitted to ComfyUI.
- **RECALL-007:** It MAY restore historical Ollama creative direction and mode inside the collapsed Prompt Assistant, but Generate MUST not call Ollama again.
- **RECALL-008:** After recall, the user must explicitly press Generate to create a new generation record.
- **RECALL-009:** A rerun MUST never overwrite or mutate the original history record.

### 18.2 Requested versus effective controls

- **RECALL-010:** The application MUST store both requested and effective controls.
- **RECALL-011:** Recall SHOULD restore the user-facing requested values when they still compile to the original effective values.
- **RECALL-012:** Resolved seeds MUST always use the original effective integers.
- **RECALL-013:** Where a derived or normalized requested value can no longer reproduce the original effective value, the application MUST prefer an explicit effective equivalent or reject exact recall rather than silently compile a different request.
- **RECALL-014:** Before enabling Generate for a recalled request, the backend MUST validate that the same registered workflow version/hash and recalled controls compile consistently.

### 18.3 Workflow unavailable or changed

- **RECALL-015:** Exact recall is available only when the exact workflow ID, version, UI graph hash, API graph hash, and compatible contract are currently registered and valid.
- **RECALL-016:** If that exact source is not available, the historical generation remains fully viewable, but Recall settings MUST be disabled with a concise explanation such as "Original workflow version is not currently available."
- **RECALL-017:** The first release MUST NOT silently load historical values into a newer or different workflow version.
- **RECALL-018:** A future explicit migration/load-into-current-version feature is permitted, but it is out of scope now.

### 18.4 Reproducibility statement

The product expectation is that recalling and submitting an unchanged deterministic environment will ordinarily produce the same output. The application MUST make the same validated request, not promise universal pixel identity.

- **RECALL-019:** Documentation and UI wording MUST state that exact results depend on the same workflow, graph, models, custom nodes, ComfyUI/runtime versions, hardware behavior, source assets, effective controls, and seeds remaining unchanged.
- **RECALL-020:** The original retained image remains the authoritative record of the original result even if a later rerun differs.

---

## 19. Deletion behavior

### 19.1 Generation deletion by owner

- **DEL-001:** A user MUST be able to permanently delete their own generation from the detail view.
- **DEL-002:** Deletion MUST require a clear destructive confirmation.
- **DEL-003:** Deleting a generation MUST remove all application-owned database records and files exclusive to that generation, including controls, provenance, images, checkpoints, uploads, masks, thumbnails, events, and errors.
- **DEL-004:** Shared deduplicated files, if the implementation uses them, MUST be reference-counted correctly so another retained generation is not damaged.
- **DEL-005:** A queued or running generation MUST first be cancelled and reconciled. The UI may show a pending-deletion state until safe cleanup completes.
- **DEL-006:** Deletion MUST NOT fan out to ComfyUI history, ComfyUI storage, or Ollama.

### 19.2 User deletion by administrator

- **DEL-007:** Deleting an ordinary user MUST revoke all application sessions immediately.
- **DEL-008:** The application MUST remove that user's queued jobs and request cancellation/reconciliation for running jobs.
- **DEL-009:** It MUST then permanently delete every application-owned generation, artifact, upload, thumbnail, prompt-assistant record, preference, event, and related record owned by that user.
- **DEL-010:** It MUST finally delete the user account and leave no dangling application database rows or orphaned application-owned files.
- **DEL-011:** User deletion MUST NOT inspect or present the deleted user's content to the administrator.
- **DEL-012:** User deletion MUST NOT delete from or notify ComfyUI or Ollama beyond the normal job cancellation required to stop an active application job.

---

## 20. External-service failure and recovery behavior

### 20.1 ComfyUI unavailable

- **FAIL-001:** The application MUST continue to serve login, account management, existing gallery data, images, and detail views when ComfyUI is unavailable.
- **FAIL-002:** New Generate actions MUST be disabled while ComfyUI is known to be disconnected or lacks required capabilities.
- **FAIL-003:** Accepted queued jobs MUST remain durable and resume dispatch after connectivity returns.
- **FAIL-004:** The UI MUST show a restrained, actionable service-unavailable message near generation controls rather than a catastrophic full-screen error.
- **FAIL-005:** Workflow discovery failures MUST not erase historical workflow identity or artifacts.

### 20.2 Ollama unavailable

- **FAIL-006:** Prompt Assistant controls MUST be disabled or show an inline unavailable state.
- **FAIL-007:** Manual prompt entry and ComfyUI generation MUST remain available.

### 20.3 Artifact persistence failure

- **FAIL-008:** If ComfyUI succeeds but copying an artifact into application storage fails, the generation MUST not be falsely shown as fully archived. It MUST record a distinct persistence error and retain any successfully copied artifacts.
- **FAIL-009:** Retriable persistence or retrieval operations SHOULD use bounded retry with clear terminal failure.

---

## 21. Security requirements

In addition to the supplied workflow contract's security rules:

- **SEC-001:** All user-supplied values MUST be validated server-side.
- **SEC-002:** Uploaded files MUST be size-limited, MIME-checked, safely decoded, and limited by decompressed pixel count.
- **SEC-003:** Paths, filenames, workflow directories, ComfyUI output references, and upload references MUST be normalized and protected from path traversal.
- **SEC-004:** Users MUST NOT be able to supply arbitrary ComfyUI or Ollama URLs.
- **SEC-005:** Users MUST NOT be able to choose arbitrary server-side filesystem paths, model paths, or output paths.
- **SEC-006:** The application MUST use the contract's model/asset allowlists and runtime capability checks.
- **SEC-007:** Raw exception traces, secrets, cookies, passwords, and full internal paths MUST not be exposed to ordinary users.
- **SEC-008:** Sensitive configuration and credentials MUST be redacted from logs.
- **SEC-009:** Destructive actions MUST be authorized and auditable at least through structured logs containing actor, target ID, action, and timestamp without logging private prompts or image contents.
- **SEC-010:** The implementation MUST include tests for IDOR/cross-user access, administrator content denial, CSRF or equivalent request protection, upload validation, and path traversal.

---

## 22. Accessibility and responsive behavior

- **A11Y-001:** All controls MUST be keyboard operable.
- **A11Y-002:** Visible focus states MUST be clear against the dark theme.
- **A11Y-003:** Inputs MUST have programmatic labels and validation associations.
- **A11Y-004:** Dialogs and menus MUST manage focus correctly and be dismissible by keyboard where appropriate.
- **A11Y-005:** Text and control contrast MUST meet WCAG AA expectations.
- **A11Y-006:** The scale slider MUST have an accessible name, current value, and keyboard support.
- **A11Y-007:** Live queue/progress changes SHOULD use restrained accessible announcements without flooding screen readers.
- **A11Y-008:** Images MUST have useful accessible labels based on source, date, and status when no human-authored alt text exists.
- **A11Y-009:** The application is desktop-first, but on narrower screens the left control panel MUST become a usable drawer or stacked region rather than shrinking controls below reasonable sizes.
- **A11Y-010:** The primary supported browsers SHOULD be current stable versions of Chromium-based browsers and Firefox. Safari support is desirable when the selected stack permits it without special architecture.

---

## 23. Performance expectations

- **PERF-001:** Initial authenticated load MUST not fetch every full-resolution image in history.
- **PERF-002:** Gallery images MUST use thumbnails/responsive sources and lazy loading.
- **PERF-003:** Pagination or progressive loading MUST keep memory and DOM size reasonable for thousands of records.
- **PERF-004:** Database queries MUST be indexed by owner and chronological ordering.
- **PERF-005:** Live progress events MUST update only the affected generation card rather than refetching the entire gallery.
- **PERF-006:** Expensive file hashing, thumbnail creation, and ComfyUI retrieval MAY run in backend worker tasks within the same container, but their state MUST be durable and recoverable.
- **PERF-007:** The application SHOULD remain responsive while one or more ComfyUI jobs are running.

---

## 24. Application API expectations

The exact internal route names are an implementation choice, but the application MUST provide authenticated capabilities equivalent to:

- Login, logout, password change, and forced first-login password change.
- Administrator user creation, password reset, user deletion, workflow refresh, and workflow diagnostics.
- List resolved workflow profiles and retrieve one profile's control surface.
- Validate a generation request.
- Create a generation.
- List the current user's generations with pagination.
- Retrieve one owned generation and its artifact timeline.
- Cancel an owned generation.
- Delete an owned generation.
- Recall data for an owned generation.
- Retrieve an owned artifact or thumbnail.
- Invoke Ollama prompt composition.
- Subscribe to owned generation events.
- Read and update the current user's gallery-scale preference.

- **API-001:** Every object-returning route MUST scope its query to the authenticated owner unless it is an explicitly non-content administrator route.
- **API-002:** The frontend MUST treat the backend as the sole product API and MUST not contain ComfyUI node-patching logic.
- **API-003:** Error responses MUST use a consistent machine-readable shape plus a safe user-facing message.

---

## 25. Testing and self-validation

Automated testing is a required product deliverable, not optional cleanup.

### 25.1 Test strategy

- **TEST-001:** The repository MUST include unit tests for domain logic, contract/profile registration, control validation, seed resolution, scheduler fairness, status transitions, recall reconstruction, authorization, cascade deletion, and Ollama selection/provenance behavior.
- **TEST-002:** It MUST include backend integration tests using deterministic fake or mock ComfyUI and Ollama services.
- **TEST-003:** The fake ComfyUI service MUST cover workflow listing/retrieval, `/object_info`-equivalent capability data, prompt acceptance/rejection, WebSocket or event behavior, progressive artifacts, multiple outputs, history reconciliation, cancellation races, service disconnects, and output retrieval.
- **TEST-004:** Test fixtures MUST include at least one valid paired workflow, one incomplete pair, one hash mismatch, one invalid binding, one missing dependency, and one valid progressive workflow.
- **TEST-005:** It MUST include frontend component or integration tests for dynamic contract controls, collapsed advanced groups, Prompt Assistant, gallery scaling, card updates, Recall settings overwrite behavior, and destructive confirmations.
- **TEST-006:** It MUST include browser end-to-end tests, preferably with Playwright or an equivalent mature tool, for the principal user journeys.
- **TEST-007:** It MUST include a container startup smoke test and a production frontend build test.
- **TEST-008:** It MUST include static analysis appropriate to the selected stack: formatting, linting, type checking, dependency/build validation, and database migration validation.
- **TEST-009:** Tests MUST not require a live household ComfyUI or Ollama server. Optional live integration tests MAY be gated by environment variables.
- **TEST-010:** Production code MUST not silently use mock integrations outside an explicit development/test mode.

### 25.2 Mandatory end-to-end scenarios

At minimum, automated tests MUST prove:

1. Bootstrap administrator creation and forced password change.
2. Administrator creation of a user with a temporary password and forced first-login change.
3. Ordinary user isolation from another user's generation and media IDs.
4. Administrator denial from another user's content endpoints.
5. Startup workflow discovery through the fake ComfyUI network API.
6. Rejection of invalid or incomplete workflow pairs.
7. Rendering of controls from the resolved contract without frontend knowledge of raw node IDs.
8. Prompt Assistant output replacing the visible prompt without auto-generation.
9. Ollama outage leaving manual ComfyUI generation available.
10. Multiple rapid Generate submissions creating separate durable queued records.
11. FIFO within one user's queue and fair interleaving between users.
12. One card per generation as progressive artifacts arrive.
13. Final artifact becoming canonical only after terminal success.
14. Cancellation after a checkpoint retaining a best-available non-final artifact.
15. Failed and cancelled generations remaining in the gallery and recallable.
16. Recall settings immediately overwriting the current panel with no confirmation.
17. Recall restoring the resolved seed and exact final submitted prompt.
18. Recall not invoking Ollama.
19. Recall being unavailable when the exact workflow version/hash is absent.
20. Gallery slider moving from a one-card-wide layout to compact thumbnails and persisting per user.
21. Card footer containing source, centered dot, date, and only the Recall settings action.
22. User deletion cascading through all application records and files without exposing content to the administrator.
23. Generation deletion removing application-owned content but not invoking external deletion.
24. Restart recovery for queued jobs and reconciliation behavior for dispatched jobs.
25. ComfyUI outage preserving history and pausing new submission/dispatch safely.

### 25.3 Validation before delivery

- **TEST-011:** The implementation model MUST run the complete automated validation suite available in its environment before returning the code.
- **TEST-012:** The repository MUST provide one documented command, script, or make target that runs formatting checks, linting, type checking, unit tests, integration tests, frontend tests, production build, and container smoke checks as far as practical.
- **TEST-013:** The final delivery MUST report the exact commands run and their results. It MUST not claim a test passed if it was not executed.
- **TEST-014:** Any environment-limited test that cannot run MUST be clearly identified with the reason and an exact command for the user to run.
- **TEST-015:** No core feature may be represented only by a placeholder, TODO, mocked production path, or untested scaffold.

---

## 26. Documentation and repository deliverables

The implementation MUST include:

- **DOC-001:** A clear `README` with local development, production build, Docker run/Compose, configuration, initial login, data persistence, workflow publishing convention, and troubleshooting instructions.
- **DOC-002:** An `.env.example` or equivalent configuration reference with safe placeholders and comments.
- **DOC-003:** A concise architecture document explaining frontend/backend boundaries, database and file storage, queue worker, ComfyUI adapter, Ollama adapter, event transport, and security model.
- **DOC-004:** A requirement traceability document mapping requirement IDs in this specification to implementation modules and automated tests.
- **DOC-005:** Database migration files and schema documentation sufficient for maintenance.
- **DOC-006:** Instructions for backing up and restoring the SQLite database and application-owned asset directory together.
- **DOC-007:** API documentation, generated or handwritten, for the application endpoints needed by the frontend.
- **DOC-008:** A test/validation guide with the one-command validation path and optional live-integration instructions.
- **DOC-009:** A documented statement that the ComfyUI custom-node package and workflow preparation are external prerequisites and are not part of this repository.

---

## 27. Acceptance criteria

The application is acceptable only when all of the following are true:

1. It builds and runs as one Dockerized web application with persistent SQLite and media storage.
2. An empty deployment creates the configured administrator and forces a password change.
3. The administrator can create, reset, and delete ordinary users without viewing their generation content.
4. Each user sees only their own gallery and assets.
5. The backend discovers paired workflows from the configured ComfyUI workflow directory through ComfyUI's network API.
6. It registers only workflows that strictly pass the supplied workflow contract's validation lifecycle.
7. It requires one `prompt.text` control and renders all other controls from the resolved contract.
8. It does not implement or install the ComfyUI contract custom-node package.
9. It never exposes ComfyUI or Ollama directly to the browser.
10. The left panel has a restrained Generate button at the top, source selector below it, and dynamic controls beneath.
11. Users can submit multiple generations and the application queues them durably and fairly.
12. The gallery is the main view and shows one card for every accepted generation, including failed and cancelled attempts.
13. The top-right slider scales cards from approximately one per row to compact thumbnails and persists per user.
14. Each card footer displays only `generation source · date` plus Recall settings.
15. A running card updates in place as contract-declared artifacts arrive.
16. Only a terminal successful output is marked canonical; cancelled/failed checkpoints remain visibly non-final.
17. Recall settings immediately overwrites the left panel, restores all inputs and the resolved seed, and never submits automatically.
18. Recall uses the exact final prompt and does not rerun Ollama.
19. The application does not silently substitute a changed workflow for historical recall.
20. Users can permanently delete their own generations, and administrators can delete users with complete application-side cascade cleanup.
21. ComfyUI and Ollama outages degrade only the affected functionality rather than destroying access to history.
22. The visual interface is consistently dark, minimal, readable, and accessible.
23. Automated unit, integration, browser, security, migration, build, and container checks are included and runnable.
24. The implementation model has run the available validation suite and truthfully reported the results.

---

## 28. Final design decisions and resolved ambiguities

The following decisions are deliberate and MUST not be reopened during implementation unless a real technical contradiction is discovered:

- The correct external service name is **Ollama**. There is no user-facing model choice.
- Workflow files are retrieved through ComfyUI's API, not a shared mount.
- Compatible generation sources are matching `.workflow.json` and `.api.json` files with one basename.
- Building the ComfyUI custom-node package is out of scope.
- The primary prompt control is the semantic contract ID `prompt.text`.
- Prompt assistance is explicit and never runs automatically on Generate.
- Users may queue any number of valid requests; the queue is durable and fairly scheduled.
- The deployment is for a home network and thousands, not millions, of images.
- SQLite plus application-owned filesystem storage is the selected persistence model.
- Administrators manage accounts but cannot inspect user generation content.
- There is one gallery card per generation.
- Progressive artifacts update the card; earlier declared checkpoints remain in the detail timeline.
- Failed and cancelled accepted generations remain visible and recallable.
- The card footer contains only source and date metadata, separated by a dot, plus Recall settings.
- Recall always overwrites current panel values immediately and without confirmation.
- Recall restores concrete resolved seeds.
- Exact recall requires the exact workflow version and hashes; no silent migration is permitted.
- Users may permanently delete their own generations.
- Deleting a user deletes all of that user's application-owned history and files but does not purge external systems.
- The Generate button stays at the top of the left panel but remains visually restrained and consistent with other controls.
- The UI uses a minimal dark blue/gray/black visual system with uniform components.
- Automated tests and self-validation are mandatory deliverables.
