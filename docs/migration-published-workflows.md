# Migration to published workflow sources

The application now treats ComfyUI's deliberately published three-file bundle as the only current generation-source format:

```text
<stem>.json
<stem>.api.json
<stem>.interface.json
```

## What changed

- Discovery filters recursive userdata listings to `.interface.json` commit markers instead of pairing `.workflow.json` and `.api.json` files.
- The embedded `FrontendWorkflowContract` / `FrontendWorkflowArtifact` node parser, selector/transform/preset/stage model, and fixed control assumptions are retired.
- The `.interface.json` manifest supplies the only public input/output schema and trusted private CIF parameter bindings.
- Public requests use `source_key`, optional immutable `revision`, and `parameters`; compilation clones the accepted frozen API graph per request.
- Source revision identity is publication UUID plus exact workflow/API/manifest SHA-256 values.
- Generation results retain native prompt ID, complete server-side history/status, effective parameters, manifest-ordered publisher outputs with authoritative batch indices, untouched node-keyed nonpublisher outputs, warnings/errors, and every archived batch image. Public history removes top-level submitted prompt/extra-data graph envelopes while preserving actual node results, publisher metadata, custom UI fields, raw status/errors, and execution metadata.
- New publications must have connected `CIFPublishImage` declarations with `cardinality: many`, unique public IDs/instance UUIDs/node bindings, exactly one `final`, explicit `unmapped_outputs_policy: collect`, and a native-output diagnostic inventory. Previously accepted publisher-less publication shapes must be republished.
- A convenient gallery image does not create a contract-declared final output.

## Existing database records

Alembic revision `b84f2d6a91c3` adds nullable publication identity fields to `workflow_profiles` and default-empty rich result fields to `generations`. It does not delete, rewrite, or require republishing old rows. Historical generations and files remain viewable, downloadable, favoritable, and deletable.

After the first successful authoritative publication listing, legacy embedded-contract profiles are marked non-current/stale and no longer appear as executable sources. Their immutable rows remain because generations reference them.

Exact recall never substitutes a new graph. A historical generation can be recalled only when an exact current published revision exists and the restored effective parameters compile to the same graph; otherwise detail remains readable and recall reports an explicit unavailable reason.

## Client transition

New clients must use:

```json
{"source_key": "...", "revision": {"publication_id": "...", "workflow_sha256": "...", "api_sha256": "...", "manifest_sha256": "..."}, "parameters": {}}
```

Temporary `profile_id` / `controls` request aliases and legacy identity/control response fields remain to bridge a pre-publication browser and stored rows. They resolve only to the current validated publication catalog and cannot accept an old embedded contract or arbitrary graph. They are not a stable integration surface and may be removed after all clients use publication fields.

## Operator action

1. Upgrade the application and database as one release.
2. Configure a stable `CIF_COMFYUI_INSTANCE_ID`, optional `CIF_COMFYUI_USER`, and the new response-size limits.
3. Publish workflows with **Save & Publish for Image Frontend**; a normal save does not qualify.
4. Run administrator refresh and resolve per-candidate diagnostics.
5. Confirm source controls/warnings and perform one low-cost history-reconciled generation before retiring an older deployment.

No ComfyUI workflow, model, custom-node installation, credential, or server file is mutated by this migration.
