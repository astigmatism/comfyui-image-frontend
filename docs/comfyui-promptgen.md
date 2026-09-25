# CPU prompt-generation runtime

The `comfyui-promptgen` container runs ComfyUI with `--cpu`, concurrency one.
Its host endpoint is `http://192.168.1.5:8189`; the frontend reaches it as
`http://comfyui-promptgen:8188` on the shared `comfyui_default` network.
The GPU image runtime remains `http://comfyui:8188`.

Before deploying the fixed-assignment release, ops sets these values in
`credentials/comfyui-image-frontend/runtime.env`. Its explicit instance list overrides
image-bundled additions, so a GPU-only private list must be updated first:

```dotenv
CIF_COMFYUI_INSTANCES=[{"id":"primary","label":"Primary ComfyUI","base_url":"http://comfyui:8188","ws_url":"ws://comfyui:8188/ws","user":"default","concurrency":1},{"id":"promptgen","label":"Prompt Generator","description":"CPU-only prompt generation runtime (port 8189); publication catalog source","base_url":"http://comfyui-promptgen:8188","ws_url":"ws://comfyui-promptgen:8188/ws","user":"default","concurrency":1}]
CIF_COMFYUI_DEFAULT_INSTANCE_ID=primary
CIF_COMFYUI_TEXT_INSTANCE_ID=promptgen
```

The installed portal runner checks that saved environment values match the running
app before starting an update. Changing `runtime.env` alone is insufficient: ops
must recreate **only the current app with its existing pinned image**, preserving
the Compose file, image/revision labels, data bind and edge. Verify health and
`update_production --check-only` after this one-time configuration preparation.
The later **Update and restart** button deploys the new application release.

No runner upgrade is required for the fixed-assignment release. Stage assignments
are deployment settings, so the installed runner's isolated single-instance smoke
container starts without a text assignment. Explicit unknown assignments still
fail validation. Repository validation runs the frozen smoke function from the
installed runner against the actual candidate Docker image.

Keep both the Moody v31 and StableLlama v1 bundles published on both instances.
The administrator workflow refresh discovers all configured instances. Verify
one current row for each workflow on each instance in the registry (four total),
one image workflow in the image picker, and one text workflow in the prompt picker.
Each API workflow descriptor's `replicas` lists the registered copies. Image
catalogs and execution must use primary; prompt catalogs and execution must use promptgen.
Neither runtime has a selector. Copies on other instances do not supply fallback catalogs.

## Updating copied bundles

Copy the three matching artifacts (`.json`, `.api.json`, `.interface.json`) from
the authoritative bundle together, under the same relative userdata paths and
ComfyUI user on both instances. Compare publication IDs and all three revision
hashes after refreshing. Do not independently republish an unchanged replica:
that creates a different publication revision. Editable-only drift retains the
accepted graph; API or manifest drift can produce a mismatched executable revision.

If the app reports a missing or mismatched text publication, re-copy the complete
bundle to the assigned runtime and refresh the catalogs. It will not reroute the
request or execute a different revision. Existing accepted jobs retain their
frozen graph and instance. Catalog outages retain the last accepted copies;
queued work waits for its runtime to recover.

The dataset parquet is loaded once at ComfyUI boot. After replacing it, restart
**both** ComfyUI containers before checking results. Publication hashes do not
verify external dataset contents.

## Routing and validation

Both stage assignments are fixed by environment configuration. New API clients omit
`comfyui_instance_id`; an older client supplying a conflicting value receives a
`runtime_assignment_conflict` error. There is no profile-instance or GPU fallback
for text. An absent text assignment disables prompt generation; unknown or equal
image/text assignments fail configuration validation.

Automatic snapshots record both assignments. Before each unstarted cycle, the server
normalizes old saved pins to these assignments and validates the requested revisions.
Already accepted jobs and preparations retain their recorded runtime. Receipt retries
return the original result. Saved preferences and recalled images cannot alter routing.

Publish image workflows on the GPU and prompt workflows on the CPU. Keeping complete
catalog copies on both is supported for operational convenience, but a differing copy
on the other service cannot change the picker or make a broken authoritative copy usable.

For rollout, preserve the production Compose file, external credentials, stable IDs,
and `comfyui_default` network attachment. Update the external environment before app
replacement, then refresh catalogs. Verify `/api/comfyui-instances` reports primary
and promptgen as the assignments and workflow descriptors identify their stage's
assigned catalog. Exercise both stages and check their recorded instance IDs. A source
commit or a passing app health check alone does not verify this configuration.

For an operational execution check, use a fresh subject or seed. Identical
resubmissions can finish in milliseconds through ComfyUI's `execution_cached`.
Measure `execution_start` to `execution_success` message timestamps; `/history`
status can lag by roughly two seconds. The text workflow performs deterministic
dataset composition (approximately 13 ms–2 s), not LLM inference. The app-side
LLM Prompt Assistant is a separate stage and is unaffected by this routing.
