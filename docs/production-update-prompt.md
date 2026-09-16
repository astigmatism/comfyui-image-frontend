# Prompt for the next production update

Give the following to the deployment agent after pushing reviewed changes:

> Update and restart ComfyUI Image Front-End using the latest reviewed
> `origin/main`, then verify the result. Operate as `astigmatism` in a native SSH
> session on Samus (`192.168.1.5`, accessible through `192.168.1.21`). The deployment
> root is `/home/astigmatism/comfyui-image-frontend`; the Compose project is
> `comfyui-image-frontend`, with `compose.yaml` then `compose.ordered-lora.yaml`.
>
> First inspect the actual app container, health, mounts, and any active deployment
> or backup process. Fetch `origin/main` in `<deployment-root>/source`, pin the full
> target SHA once, and read `docs/production-deployment-agent.md` from that SHA with
> `git show`. Follow its **Start here** section and **Path B** exactly. Keep frozen
> `source/main` unchanged; use a clean detached release worktree. Do not use the
> single-file updater or the Portal update button.
>
> Use native `/home/...` paths, never the agent container's `/host/...` paths in
> Compose. Verify resolved mounts against the running containers before changes.
> The current container's `/data` mount is authoritative; retained emergency
> volumes are stale recovery copies. Preserve data, private settings, certificates,
> ports, network, and external runtimes.
>
> Use the runbook's bounded `backup-production-bind.py` command for the consistent
> checkpoint. Do not split stop, archive, and restart across agent tool calls. It
> must restart the old app before backup verification or building. A tool timeout
> means inspect the existing process/status file, not start another job. If stopped
> before recreation, restore the exact existing container after excluding an active
> backup; do not investigate with the app left down.
>
> Build with the old app running, then use the exact two-file Compose command with
> `up -d --no-build --pull never --wait --wait-timeout 120`. Verify the running image
> ID, both services' health, database, running worker, explicit runtime configuration,
> trusted HTTPS, assets, and build manifest. Report the deployed SHA and evidence,
> backup path, interruption, and any remaining issue. This request authorizes the
> normal update and its brief maintenance interruptions.
