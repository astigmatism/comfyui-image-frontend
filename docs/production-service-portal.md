# Service Portal button for Samus production

Samus uses `/home/astigmatism/deployments/comfyui-image-frontend`, with one
JSON-formatted `compose.yaml`, pinned local images, and no source checkout. The
app's private environment and TLS files live in
`/home/astigmatism/credentials/comfyui-image-frontend/`. The old `source/main`,
`.env`, `compose.ordered-lora.yaml`, and deployment-root worktree flow is retired.

The portal runs the installed `update_production_portal` as `1000:1000`. That
entrypoint starts a durable job using the **installed, pinned release runner** and
waits for verified completion. Source for the application is fetched anonymously
from the public repository into that job's temporary directory. The updater does
not extract or execute replacement deployment scripts from moving `main`.
Updating the runner itself is an explicit reinstallation from a reviewed revision.

## One-time installation

Run on native Samus as `astigmatism` with existing Docker socket access. Review
this source revision first. `tooling_sha` must be its full 40-character commit ID
on `main`, including the restored-layout tooling. Do not substitute the old
`3501bab` application revision for the tooling revision.

The following builds the runner from unmodified, pinned source in a temporary
workspace, retains its source archive, and installs the integration. The script's
trap removes only the newly created temporary workspace. If your production
policy supplies a scratch-workspace helper, run this block inside that helper.
No permanent checkout or new home-level entry is created.

```bash
set -euo pipefail
umask 077
root=/home/astigmatism/deployments/comfyui-image-frontend
tooling_sha=REPLACE_WITH_REVIEWED_FULL_COMMIT_ID
[[ "$tooling_sha" =~ ^[0-9a-f]{40}$ ]]
stage=$(mktemp -d /tmp/cif-install.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
git init -q "$stage/source"
git -C "$stage/source" remote add origin https://github.com/astigmatism/comfyui-image-frontend.git
git -C "$stage/source" fetch --no-tags origin main
git -C "$stage/source" merge-base --is-ancestor "$tooling_sha" FETCH_HEAD
(umask 022; git -C "$stage/source" checkout --detach "$tooling_sha")
runner="local/comfyui-image-frontend-release:$tooling_sha"
# Never overwrite an existing pinned tag. A prior installation can reuse its image.
if ! docker image inspect "$runner" >/dev/null 2>&1; then
  docker build --build-arg "RUNNER_REVISION=$tooling_sha" \
    -f "$stage/source/deployment/production-runner/Dockerfile" \
    -t "$runner" "$stage/source"
fi
artifact="$root/releases/runner-$tooling_sha"
mkdir -p "$artifact"
git -C "$stage/source" archive --format=tar.gz \
  --output="$artifact/source.tar.gz" "$tooling_sha"
docker image inspect --format '{{.Id}}' "$runner" > "$artifact/image-id.txt"
printf '%s\n' "$tooling_sha" > "$artifact/revision.txt"
socket_gid=$(stat -c %g /var/run/docker.sock)
docker run --rm --init --pull never --user "$(id -u):$(id -g)" --group-add "$socket_gid" \
  --mount "type=bind,source=$root,target=$root" \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --entrypoint python3 "$runner" /opt/cif/launch-production-update.py \
  --deploy-root "$root" --install "$tooling_sha"
```

The durable installer acquires `.deployment-update.lock`, validates the current
layout and trusted HTTPS, saves the previous Compose/configuration, installs the
two entrypoints and `release-config.json`, and adds these four labels **only to the
app service** in the existing Compose document:

| Label | Installed value |
| --- | --- |
| `io.service-portal.update.enabled` | `true` |
| `io.service-portal.update.script` | `update_production_portal` |
| `io.service-portal.update.image` | `local/comfyui-image-frontend-release:<tooling_sha>` |
| `io.service-portal.update.user` | `1000:1000` |

All existing Compose settings and other labels are preserved. The edge remains
opted out. The installer recreates **only the app with its existing image**, using
`--no-deps --no-build --pull never`, then verifies the revision, published labels,
health, trusted HTTPS, and frontend assets. It seeds `release-state.json` from the
running revision label, including the restored image's original non-SHA tag.
Installation does not archive, replace, or rewrite `data/`. Normal app startup can
perform its usual database writes. If installation fails after recreation, it
restores the previous operational files and app image and still exits nonzero.

Inspect the app's live labels and open the existing portal. **Update and restart**
should appear for the app; the edge remains excluded. The portal discovers live
labels, so it needs no restart or rebuild. Source-side tests cannot establish
that a button is visible on the production portal; confirm this on Samus.

## Runtime contract and release transaction

The portal mounts only the deployment and Docker socket into the outer runner.
The launcher adds the existing credential directory read-only to its durable
child. It needs no host checkout, home mount, deploy key, or extra portal mounts.
Its scratch checkout lives inside the child container and is removed on normal
completion and handled failures. Images, source archives, receipts, and recovery
artifacts remain in the service directory; exited child containers retain logs.

Every mode uses the same atomic deployment lock. Structural preflight checks the
Samus daemon, Compose identity, current image IDs, revision/state, numeric user,
exact data and certificate binds, runtime environment, and TLS certificate/key.
Operational preflight allows 60 seconds for a degraded app to recover, then at
most one recovery restart with bounded readiness verification. A ready app with
broken TLS fails without an app restart. Installation and `--check-only` do not
attempt operational recovery.

For an update, the runner fetches `main` once, resolves its full SHA, and verifies
that it descends from the deployed revision. Optional `--sha <full-sha>` selects a
reviewed ancestor of that fetched `main`; it cannot downgrade the deployed app.
It archives exact source, builds `local/comfyui-image-frontend:<full-sha>`, and
smoke-tests that image as the production UID with tmpfs data, no network, no
production mounts, and disposable credentials supplied via a private env file.
An existing SHA image can be reused only with a matching retained image receipt.

Only after preparation succeeds does it stop the app, take a consistent full
`data.tar`, and verify SQLite integrity including WAL. The app stays stopped
between this backup and cutover. It updates only the app image and revision label,
recreates only the app, and verifies database/worker readiness, the unchanged edge
container, trusted `https://192.168.1.5:8443/`, and the served asset manifest against
the running image. A successful result records the image ID and SHA in
`release-state.json` and `releases/<timestamp>-<sha-prefix>/receipt.json`.

If cutover fails, it stops the candidate, retains the post-cutover database,
restores the pre-cutover SQLite database and previous operational files, and
starts/verifies the previous app once. The `data/` directory, assets, and uploads
are retained in place. Failure remains a failed portal job even after successful
rollback. If candidate shutdown or recovery cannot be verified, it stops with
`operator-required`; it does not run old code against an uncertain database.
There are no prune, volume removal, edge restart, or backend restart operations.

An unchanged release gets verified; the portal also restarts its existing app
container. A preflight recovery restart satisfies that request. It does not build
or back up an unchanged release. Exit 0 requires a final `complete`, `exit_code=0`,
`https=verified` JSON result **and** successful child exit. Failed jobs emit one
sanitized `Error:` line naming the phase, recovery result, and retained job. Full
command output stays in restricted `deployment.log`, not portal logs. A monitoring
timeout fails the portal job; the durable child continues. Inspect that same job
before doing anything else.

## Acceptance on Samus

1. Install tooling without changing the selected app image; verify the four live
   labels, unchanged edge container ID, health, and portal button.
2. Click the button and wait for its final verified result. With `main` ahead, this
   deploys the fetched SHA, which includes the saved-LoRA reconciliation fix from
   `3501bab3abf109e366c61c23ee1d8e22b22157d5`. To validate that historical app commit
   separately **before** advancing further, run
   `update_production --sha 3501bab3abf109e366c61c23ee1d8e22b22157d5 --wait`.
   New tooling can deploy it without requiring deployment scripts in that commit.
3. Click again with unchanged `main`: require `restarted`, verified HTTPS, unchanged
   image/configuration, and preserved user data. The no-restart CLI equivalent
   reports `already-current`. Installing tooling does not make an older deployed
   app already current with today's `main`.
4. Load the 16-LoRA **Moody Krea2 Simple v31** panel and submit one authorized test
   generation. Confirm the old “Include every LoRA exactly once.” failure is gone.
5. Exercise failure injections in an isolated replica first: candidate failure
   preserves the original app; cutover failure restores config/image/database but
   reports failure; concurrent jobs are rejected. Keep receipts and archives.
6. Check source scratch cleanup and retained release provenance. Do not delete
   restore records, old images, or backups as part of acceptance.

SIGKILL, host loss, or Docker daemon failure can require manual recovery. Consult
the [current runbook](production-deployment-agent.md), inspect the lock owner and
retained job, and preserve all evidence before resolving a stale lock.
