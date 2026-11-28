# CIF artifact cleanup companion

Copy or symlink `cif_artifact_cleanup` into the `custom_nodes` directory of every ComfyUI runtime
used by the image frontend, then restart that runtime. It adds one server-only route:

```text
POST /comfyui-image-frontend/artifacts/delete
```

The route accepts only bounded `filename` / `subfolder` / `type` locators for ComfyUI `output`
and `temp` storage. It resolves every path beneath ComfyUI's configured storage roots, treats an
already-missing file as success, and removes empty artifact subdirectories. It does not register
nodes or expose arbitrary filesystem paths.
