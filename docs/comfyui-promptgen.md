# CPU prompt-generation runtime

The `comfyui-promptgen` container runs ComfyUI with `--cpu`, concurrency one.
Its host endpoint is `http://192.168.1.5:8189`; the frontend reaches it as
`http://comfyui-promptgen:8188` on the shared `comfyui_default` network.
The GPU image runtime remains `http://comfyui:8188`.

After deploying the source change, ops sets these values in
`credentials/comfyui-image-frontend/runtime.env` and recreates the app container:

```dotenv
CIF_COMFYUI_INSTANCES=[{"id":"primary","label":"Primary ComfyUI","base_url":"http://comfyui:8188","ws_url":"ws://comfyui:8188/ws","user":"default","concurrency":1},{"id":"promptgen","label":"Prompt Generator","description":"CPU-only prompt generation runtime (port 8189); publication catalog source","base_url":"http://comfyui-promptgen:8188","ws_url":"ws://comfyui-promptgen:8188/ws","user":"default","concurrency":1}]
CIF_COMFYUI_DEFAULT_INSTANCE_ID=primary
CIF_COMFYUI_TEXT_INSTANCE_ID=promptgen
```

Keep both the Moody v31 and StableLlama v1 bundles published on both instances.
The administrator workflow refresh discovers all configured instances. Verify
one current row for each workflow on each instance in the registry (four total),
one image workflow in the image picker, and one text workflow in the prompt picker.
Each API workflow descriptor's `replicas` lists the registered copies. The image
runtime should default to Primary ComfyUI and the prompt runtime to Prompt Generator.

## Updating copied bundles

Copy the three matching artifacts (`.json`, `.api.json`, `.interface.json`) from
the authoritative bundle together, under the same relative userdata paths and
ComfyUI user on both instances. Compare publication IDs and all three revision
hashes after refreshing. Do not independently republish an unchanged replica:
that creates a different publication revision. Editable-only drift retains the
accepted graph; API or manifest drift can produce a mismatched executable revision.

If the app reports a missing or mismatched text publication, re-copy the complete
bundle to the selected runtime and refresh the catalogs. It will not reroute the
request or execute a different revision. Existing accepted jobs retain their
frozen graph and instance. Catalog outages retain the last accepted copies;
queued work waits for its runtime to recover.

The dataset parquet is loaded once at ComfyUI boot. After replacing it, restart
**both** ComfyUI containers before checking results. Publication hashes do not
verify external dataset contents.

## Routing and validation

The prompt-runtime selector is independent of image runtime selection. API callers
can set `prompt_generation.comfyui_instance_id` in image preparations and automatic
snapshots, or `comfyui_instance_id` on standalone prompt requests. If omitted,
the server uses the text-instance setting, then the source instance, then the
global default. Automatic snapshots persist both resolved stage pins. Legacy
snapshots adopt the new text default before their next unstarted cycle; jobs
already created keep their original instance.

For an operational execution check, use a fresh subject or seed. Identical
resubmissions can finish in milliseconds through ComfyUI's `execution_cached`.
Measure `execution_start` to `execution_success` message timestamps; `/history`
status can lag by roughly two seconds. The text workflow performs deterministic
dataset composition (approximately 13 ms–2 s), not LLM inference. The app-side
LLM Prompt Assistant is a separate stage and is unaffected by this routing.
