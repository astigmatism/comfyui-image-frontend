# Prompt for the next update

> This file lives in the repository, not on Samus: the production host keeps no
> permanent checkout, so hand these instructions over as text, whole. Relative
> paths below point at this repository.

The control panel now holds a workflow's optional extras in a section titled
**Optional**; the *Moody Krea2 Advanced v1* bundle still has to gain the
`upscale_denoise` control that section is meant to hold. The application side is
deployed; the bundle side has never left the experiment folder it was written in.

## 1. Deploy the application — done 2026-10-17

Deployed through the Samus service portal's **Update and restart**; the panel
section that said **Upscaling** now reads **Optional**, and *Moody Krea2 Minimal
v1* shows neither control nor section. Kept here as the standing instruction for
the next application update:

> Run `/host/home/astigmatism/deployments/comfyui-image-frontend/update_production --wait`
> to deploy the latest `main` (omit `/host` on the native host). Follow
> `docs/production-deployment-agent.md`. If tooling has not been installed since
> the restored-layout change, first follow `docs/production-service-portal.md`
> using a reviewed tooling revision. Wait for the same durable job to finish and
> report its exit code, deployed SHA, recovery outcome, and verified HTTPS/assets.
> Do not write replacement deployment scripts, configure SSH, or create a
> permanent checkout. The command owns source fetch, backup, build, cutover,
> rollback, and verification.

Only step 2 remains.

## 2. Put `upscale_denoise` in the Moody Krea2 Advanced v1 bundle

Verified result: publication `af8b4339-c6d8-430d-89f1-0e5ed487301a` (128 nodes),
derived from the published production bundle `5e39f76f-34d8-4162-bfa7-1aef4a76455d`.
The three files from that experiment — `Moody Krea2 Advanced v1.json`,
`.api.json`, `.interface.json` — are a compiling result and may be placed as-is
over the current trio. They were written on Samus and exist nowhere else; if the
folder has already been deleted, make the minimal edit instead:

- Add node `915` `CIFDecimalParameter` with
  `widgets_values: [0.18, 0, 0.5, 0.01, "upscale_denoise", "da8574f4-3b45-468f-8e2d-76c1c7dac8c3",
  "Upscale denoise", "<description>", "denoise", false, false, "Advanced", 55]`.
- Link `915.output[0]` to node `663` (`UltimateSDUpscale`) input `denoise`, so
  `663.inputs.denoise == ["915", 0]`. `0.18` replaces the constant previously
  baked into node `663`, so the upgrade changes nothing until the value moves.
- Add to `interface.inputs`: `{ "id": "upscale_denoise", "type": "number",
  "default": 0.18, "minimum": 0, "maximum": 0.5, "step": 0.01,
  "semantic_role": "denoise", "group": "Advanced", "order": 55, "required": false,
  "advanced": false }`.

Keep it **non-required** with default `0.18` — omitting the field must keep
working, and `0.18` must keep reproducing today's output. `enable_upscale`
stays as published (`group: "Basic"`, `order: 50`). Section membership is the
application's decision: no `group` string moves a control, so do not chase the
panel layout in the bundle.

Discovery is not periodic. After the files are in place, trigger
`POST /api/admin/workflows/refresh` with an administrator session, or restart the
application service (about 20 seconds) — an in-place bundle edit is invisible
until one of those runs.

Then report:

- the interface now exposes it — `GET /api/workflows/<advanced source key>` lists
  `upscale_denoise` with the bounds above;
- three distinct graphs — `POST /api/generations/validate` at `0.18`, `0.05` and
  `0.30` each return `valid: true` with different `compiled_graph_sha256`
  (`f9ff066b…`, `c35c96b0…`, `9c806c3c…` for the byte-identical verified bundle);
- omission still works — the same call without the field is still `valid: true`.

The hashes deliberately move when the bundle is re-authored rather than copied,
because node `663` changes from a constant to a link; equality with the recorded
values proves the bytes are the verified ones, and is not required for the change
to be correct. Free the other ComfyUI worker (`POST :8188/free` with
`unload_models` and `free_memory`) before queueing anything on the shared 5080,
and audit before and after. Delete the experiment folder only once production
carries `upscale_denoise`.
