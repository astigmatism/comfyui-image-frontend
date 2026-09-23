# Routine production update — Samus

Samus now uses `/home/astigmatism/deployments/comfyui-image-frontend/compose.yaml`
(single JSON-formatted Compose file), local pinned images, external credentials,
and no permanent source checkout. Install the reviewed release runner once using
[Service Portal installation](production-service-portal.md). The previous
multi-file/worktree deployment instructions do not apply to this restored layout.

## Choose the correct updater

| Deployment | Entry point | Source and configuration |
| --- | --- | --- |
| Samus restored deployment | `update_production` or `update_production_portal` | Pinned runner; temporary source fetch; existing single deployment Compose file |
| Other checkout-based deployments (historical Path A) | `update_and_restart` | Clean checkout; supported example Compose layout |
| Retired Samus worktree layout (historical Path B) | Historical reference only | Frozen `source/main`, two Compose files, `.env`, detached release worktrees |

The path-selection and preservation guidance from
`codex/restore-production-deployment-workflow` is incorporated here. Do not merge
that older runbook over these instructions. The production portal entrypoint
supports the **current Samus layout**. The generic `update_and_restart` entrypoint
still does not support Samus. Never substitute the example Compose file.

## Run

From the native Docker host as its deployment owner:

```bash
/home/astigmatism/deployments/comfyui-image-frontend/update_production --wait
```

An agent with the existing `/host` view can use the same path prefixed by `/host`.
No SSH setup is needed. The installed wrapper launches its pinned tool image using
the Docker socket. `--wait` is accepted for compatibility; the command always
waits for verified completion. `--restart` also restarts an unchanged release.
The portal uses `--wait --restart`.

`--check-only` checks structural invariants and live health/HTTPS without fetching,
building, backing up data, or restarting. It still takes the deployment lock and
retains a diagnostic receipt. It cannot be combined with `--restart`.
`--sha <full-sha>` optionally selects a reviewed ancestor of fetched `main` that is
at least as new as the deployed revision. Default updates resolve `main` once.
Application releases do not silently upgrade the pinned release runner; repeat
installation at a new reviewed tooling revision when changing deployment code.

The launcher prints the durable job name. On a tool or browser disconnect, inspect
that **same job** with `docker inspect --format '{{json .State}}' <job>` and
`docker logs --tail 20 <job>`. Poll at most every 30 seconds. A successful launch
is not completion: require terminal exit 0 and the final verified JSON result.
The portal waits for that same result and stays independent of the restarted app.

## Preservation and recovery

The transaction builds and smoke-tests while the old app stays available, then
stops only the app for a consistent backup and app-only cutover. It preserves the
Compose project, runtime environment, ComfyUI identity/network, external TLS,
`Caddyfile`, deployment `data/`, uploads/assets, restore records, edge, ComfyUI,
and portal. Do not modify application source on the host, create a permanent
checkout, run production tests, reconfigure SSH, rotate credentials, or prune.

Artifacts live in restricted `releases/<timestamp>-<sha-prefix>/`: source archive,
image receipt, previous Compose/operational files, data archive and checksum when
cutover is needed, `deployment.log`, and `receipt.json`. Preflight failures use a
`<timestamp>-preflight` directory. Logs/configuration may contain private data;
report only sanitized phase, SHA, image ID, outcome, and job reference.

Before cutover, a failed preparation leaves the original app selected; backup
errors attempt to restart it. After cutover, rollback stops the candidate, retains
its database, restores the pre-cutover database and previous operational files,
and starts/verifies the previous image once. Assets/uploads and the data directory
remain in place. Rollback success still returns failure for the attempted update.
Report `operator-required` if rollback cannot be verified; do not retry indefinitely.

A deployment lock is never automatically force-cleared. For an interrupted job,
read `.deployment-update.lock/owner.json`, inspect that container and its receipt,
and establish that it has stopped before any operator-directed recovery. Preserve
both pre- and post-cutover databases. Never run the old image against a database
whose migration state is uncertain. Host loss/SIGKILL can bypass normal cleanup.

When done, report the actual deployed SHA/image, HTTPS/asset verification, outcome,
retained release reference, and any operator action. Production acceptance includes
the browser and LoRA-generation checks in the installation guide; repository tests
do not replace those live checks.
