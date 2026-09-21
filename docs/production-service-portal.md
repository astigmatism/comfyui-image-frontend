# Service Portal button for Samus production

The production **Update and restart** button runs `update_production_portal` from
the deployment root. That small entrypoint calls `update_production --wait --restart`: fetch
the latest `origin/main`, select its reviewed deployer, and stream the durable
deployment job until it exits. The Portal stays running through build, backup,
restart and verification. A failed child job returns its actual nonzero exit code;
exit zero also requires the deployer's final verified result. A monitoring timeout
fails the Portal job and names the retained deployment job to inspect, rather than
starting another deployment or stopping an in-progress one.

An unchanged release restarts the existing application container, preserving its
image and configuration, and then verifies health, trusted HTTPS, and frontend assets.
Structural safety failures still block the operation. Operational degradation gets a
60-second grace period followed by at most one application recovery restart under
the deployment lock. That restart also satisfies an unchanged-release restart request.
A healthy application with broken TLS reports the TLS failure. The Portal receives a
sanitized `Error:` summary with the failed phase, recovery result, and retained job
reference; private diagnostic records also cover preflight failures.

This uses the existing production transaction and lock. It preserves the frozen
`source/main` checkout by deploying detached release worktrees. It preserves the
ordered `compose.yaml`, `compose.ordered-lora.yaml` project, current data and
configuration. The generic single-file `scripts/update-and-restart.sh` integration
in `compose.example.yml` is for a different installation layout.

## One-time installation on the native Docker host

Run as the owner (`astigmatism`, UID:GID `1000:1000`) on Samus, with no other update
running. Keep a restricted copy of the existing deployment configuration first.
Fetch the reviewed commit without changing frozen main, and extract only the
installation files:

```bash
root=/home/astigmatism/comfyui-image-frontend
git -C "$root/source" fetch origin main
sha=$(git -C "$root/source" rev-parse origin/main)
stage=$(mktemp -d)
git -C "$root/source" archive "$sha" update_production update_production_portal \
  deployment/runner deployment/production-portal | tar -x -C "$stage"
install -m 0755 "$stage/update_production" "$root/update_production"
install -m 0755 "$stage/update_production_portal" "$root/update_production_portal"
docker build -t comfyui-image-frontend-portal-runner:latest "$stage/deployment/runner"
docker run --rm --entrypoint /bin/sh comfyui-image-frontend-portal-runner:latest \
  -c 'bash --version >/dev/null && git --version && python3 --version && docker compose version'
```

Merge the four labels in
`deployment/production-portal/compose-labels.yaml` into the **existing app service**
in `$root/compose.ordered-lora.yaml`. Preserve any existing labels and every other
setting. Do not supply the fragment as a third Compose file. The effective values
must be:

| Label | Value |
| --- | --- |
| `io.service-portal.update.enabled` | `true` |
| `io.service-portal.update.script` | `update_production_portal` |
| `io.service-portal.update.image` | `comfyui-image-frontend-portal-runner:latest` |
| `io.service-portal.update.user` | `1000:1000` |

The fragment allows `PROJECT_RUNNER_IMAGE` and `PUID:PGID` overrides. If the private
`.env` already sets them, build the selected local image and verify the user is the
deployment owner. The runner requires Bash, Git, Python 3, Docker CLI and Compose.
No home mount or additional credentials are needed for this public repository.

Validate the resolved two-file configuration privately (it contains secrets).
Confirm the only differences are the four app labels, and all bind sources remain
native `/home/...` paths. Under the existing `.deployment-update.lock`, recreate
only the app with its **current** image to publish its labels:

```bash
docker compose --project-directory "$root" --env-file "$root/.env" \
  -p comfyui-image-frontend -f "$root/compose.yaml" \
  -f "$root/compose.ordered-lora.yaml" up -d --no-deps --no-build --pull never \
  --wait --wait-timeout 120 comfyui-image-frontend
```

Verify health, then release the installation lock. The Portal discovers live
labels; no Portal rebuild is needed. Its **Update and restart** action appears on
the ComfyUI Image Frontend service. Confirm one real update, wait for the Portal's
terminal result, and verify the reported deployed SHA is the selected `origin/main`.
Keep the deployment records and backups. Rebuild this runner if its Dockerfile
changes; the launcher automatically selects the versioned deployer for each update.

## Why there are two maintenance containers

The Portal owns a short-lived runner and removes it after completion. The existing
production launcher owns the durable deployment job and its recovery records. The
Portal runner waits for that specific child; the child continues if the Portal or
browser disconnects. The launcher rejects another active deployment, and the
production job also acquires the atomic deployment lock. Do not equate a successful
`docker run -d` with a successful update.
