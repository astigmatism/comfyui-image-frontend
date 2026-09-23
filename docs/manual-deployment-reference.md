# Historical deployment reference — retired Samus layout

**This document records the deployment before the September 22, 2026 restore.
Do not execute its Samus paths, worktree creation, or two-file Compose commands
on the current host.** Use [the current runbook](production-deployment-agent.md)
and [the current installer](production-service-portal.md). Current Samus has one
JSON Compose file under `deployments/`, external credentials, and no checkout.
The older Path A/Path B distinction below is retained only as historical context.


**Routine Samus updates use [the one-command guide](production-deployment-agent.md).**
This long reference is retained for human-led recovery and other deployment layouts.
Its manual SSH sequence is not a prerequisite for the routine maintenance-container
runner; that runner verifies its identity and identical host-path mounts directly.

## Start here: the Samus production deployment

For the installation at `192.168.1.5` (Samus), reached through `192.168.1.21`:

1. Start a **native-host SSH session** as `astigmatism` on `.5`. The agent's
   `/host/home/...` view is not a valid Docker host path. Set
   `DEPLOY_ROOT=/home/astigmatism/comfyui-image-frontend` and
   `DEPLOY_PROJECT=comfyui-image-frontend`. Verify these facts against the live
   containers before changing anything.
2. Fetch `origin/main` in `source`, record its full SHA once, and read this file
   from that SHA: `git -C "$DEPLOY_ROOT/source" show
   "$TARGET_SHA:docs/production-deployment-agent.md"`. An old chat, an unmerged
   pull request, and frozen `source/main` are not the current instructions.
3. Use **Path B only**, in order: identify the live app/data → review the target →
   acquire the lock → prepare the release and configuration checkpoint → run the
   bounded backup helper → edit only image/context → build with the app running →
   recheck live mounts → reconcile → verify. Fetching is how this layout obtains
   changes; do not `git pull` or advance frozen `source/main`.
4. The authoritative data is the storage mounted at `/data` in the **current app
   container**. As of the September 15 recovery this is the host `data/` bind.
   Retained `cif-prod-data` and `cif-edge-cfg` volumes are historical recovery
   copies, not the source for a routine backup or restart. Inspect, do not infer
   freshness from their names or an earlier conversation.
5. If the old app is stopped before any recreation/migration, restore that exact
   container with `docker start <recorded-app-id>` immediately after excluding an
   active backup. Investigate with service restored. Never leave it stopped while
   comparing databases, installing dependencies, building, or waiting for advice.

A routine update includes a consistent recovery checkpoint before startup can
apply migrations. It does **not** include restoring old data, moving storage,
rebuilding runtimes, running a broad test suite on production, or investigating
unrelated infrastructure. Use the provided backup helper instead of improvising
separate stop/tar/start commands. A real scope discrepancy blocks the update;
it must not turn into an unbounded outage.

## Your role and scope

You are operating on the production host for **ComfyUI Image Front-End**. Use this
runbook when asked to update or deploy this application. Complete the authorized
update, verify the running application, and report evidence of the result.

A request to update and deploy authorizes the routine backup, build, and brief
application interruption described here. Do not ask for that authorization again.
If asked only for a review or dry run, perform the read-only checks and report the
plan; do not fetch, back up, build, restart, or change files. Ask for clarification
only when a missing fact prevents you from identifying the intended deployment or
when recovery would require an action outside the request, such as replacing data.

Read the applicable `AGENTS.md` files and these sources before proceeding:

- [README production setup, update, TLS, and backup instructions](../README.md)
- [Single-file checkout updater](../scripts/update-and-restart.sh) and its
  [root wrapper](../update_and_restart)
- **Every actual production Compose file in its original order**, plus the tracked
  [Compose example](../compose.example.yml) and [Dockerfile](../Dockerfile)
- [Database migration policy](database.md) and [testing instructions](testing.md)
- [TLS certificate script](../scripts/issue-local-cert.sh) when TLS is involved

Use the checked-out code and observed host configuration to verify this document.
Do not assume that example paths, addresses, or service names identify the live
deployment. If the implementation has changed, resolve the discrepancy before
running a command whose effect is uncertain.

## Choose the deployment path before changing anything

There are two supported deployment paths. Both are canonical for their own
layout; a routine update must preserve the existing topology.

| Invariant | Path A: single-file checkout updater | Path B: multi-file, commit-pinned worktrees |
| --- | --- | --- |
| Entry point | `./update_and_restart` | Explicit command sequence in **Path B** below |
| Compose inputs | One verified file, typically `compose.example.yml` | `compose.yaml` then `compose.ordered-lora.yaml`, both under the deployment root |
| Source checkout | Clean `main` advances by fast-forward | `source/main` remains at its existing SHA; fetch updates remote refs only |
| Build context | The updated checkout | A detached `ordered-lora-<sha>` worktree under the deployment root |
| App image | Typically mutable `:local` | `CIF_IMAGE_TAG=<deployed-commit-sha>` in the deployment-root `.env` |
| Private `.env` | Layout supported by the selected file and updater | `<deployment-root>/.env`; none inside `source/` or the release worktree |
| TLS material | Must resolve to the edge's actual certificate mount | `<deployment-root>/data/certificates`, passed as an absolute path |

**Use Path B for the reported production deployment. Do not run
`./update_and_restart` there, including through a Portal label pointing to the
generic `scripts/update-and-restart.sh`.** The supported
[production Portal entrypoint](production-service-portal.md) runs the same Path B
transaction as `update_production`. The generic script accepts one `-f`, advances
the checked-out branch, does not set
`CIF_IMAGE_TAG`, and defaults TLS paths relative to its checkout. Pointing it at
the example file can reconcile over the existing project with the wrong image,
build context, and mounts. Migrating from B to A is a separate task requiring
explicit scope; a request to pull and redeploy does not authorize it.

Follow sections 1–3 using the selected path, then the matching subsection of
section 4, then the shared verification and recovery sections. Do not execute
commands from both paths.

For Path B, read this runbook from the deployed release worktree (or a verified
target revision). Frozen `source/main` can predate this document; do not advance
that branch just to obtain the instructions. After verifying the repository and
fetching the intended release, `git -C "$SOURCE_DIR" show
"$TARGET_SHA:docs/production-deployment-agent.md"` can read the target document
without changing main.

## Shared deployment facts and boundaries

| Item | Repository default / required behavior |
| --- | --- |
| Release source | Reviewed commits from `origin/main`; distinguish the deployed commit from a frozen checkout's HEAD |
| Expected origin | `https://github.com/astigmatism/comfyui-image-frontend.git` |
| App service | `comfyui-image-frontend`; discover its actual image reference |
| Browser entry point | `cif-tls-edge`; default `https://image-studio.lan:8443` |
| App health endpoint | `GET /api/health`; internal HTTP port `8000` |
| Persistent data | `/data`, including SQLite and application-owned files; default volume `comfyui-image-frontend-data` |
| Private configuration | Gitignored `.env`; preserve existing values |
| TLS identity | `CIF_TLS_CERT_DIR`, default `./data/certificates`; preserve the existing root CA and keys |
| Database changes | Alembic upgrades to `head` automatically during app startup |

Preserve accounts, sessions, gallery content, uploads, favorites, folders, and
generation history. Keep one application instance writing to the production
database. Never start a test application against the production volume.

Keep the existing Compose project name, storage mounts, private settings, stable
ComfyUI instance IDs, and network bindings. Do not copy `.env.example` over `.env`,
rotate session secrets, reset passwords, or enable `CIF_TEST_MODE` in production.
The image bundles additional ComfyUI runtime defaults; a private
`CIF_COMFYUI_INSTANCES` list takes precedence. Preserve that operator choice and the
primary instance identity when updating.

An application deployment does not authorize restarting ComfyUI, Ollama, speech
services, or the Service Portal, or installing nodes, models, or publications.
The Path A updater reconciles **every service in its Compose project**, not only
`CIF_COMPOSE_SERVICE`. Verify the project contains only services in the requested
scope before using it. A shared project containing external runtimes requires a
deployment plan that preserves those runtimes.

Do not discard local work with `git reset --hard`, `git clean`, automatic stashing,
or force pushes. Do not run production `docker compose down`, prune Docker
resources, delete volumes, or remove persisted files to make an update pass.

Read private configuration only as needed. Do not print `.env`, container
environment dumps, unfiltered `docker inspect`, or expanded Compose configuration
into tool output or the final report. Use `config --quiet`, selected fields, and
redacted diagnostics. Keep backup archives and logs outside the checkout with
restricted permissions; they can contain credentials and private user data.

## 1. Identify and inspect the live deployment

Work as the checkout owner with access to the intended Docker daemon. Do not use
`sudo git` or change ownership recursively to bypass a permissions error.

1. Locate the production checkout. Record its absolute path, branch, full commit
   SHA, upstream, and whether tracked or untracked changes exist. Verify origin
   privately against the expected repository; redact credentials if present.
2. Identify the live app container and its Compose labels for project name,
   working directory, and configuration files. Match these to the deployment root and
   Docker context. Do not accidentally create another project alongside it.
3. Confirm Bash, Git, Docker CLI, Compose v2, OpenSSL, and Docker daemon access.
   OpenSSL is required by certificate issuance even though the updater checks it
   only later. Use `curl` for the host-side HTTPS verification below.
4. Inspect only the needed configuration fields: service names, image references,
   published ports, storage mounts, TLS hostname and certificate directory.
   Confirm adequate free space for a replacement build and a complete data backup.
5. Check current app and edge health. Record existing service failures separately
   from update regressions. Check for an active update process or Portal job;
   do not launch concurrent updaters or force-unlock an active job.

Compose-looking labels alone do not prove Compose created a container. A built
image can contain `com.docker.compose.*` labels, which a subsequent `docker run`
inherits. Compare the image's labels with the container's working-directory,
configuration-file, and config-hash labels, actual mounts, and the selected
project's `compose ps --all` results. When investigating an unexpected creation,
correlate its container ID and timestamp with your own commands and Docker events
before attributing it to another updater. Repeated starts of the same container
can be its restart policy, rather than another creation.

### Distinguish host paths from the agent container's paths

Docker resolves bind sources on the **daemon host**, not in the CLI container.
For example, when a Linux host's `/` is mounted into an agent at `/host`, the
agent's `/host/home/astigmatism/comfyui-image-frontend` corresponds to the host's
`/home/astigmatism/comfyui-image-frontend`. Passing the former as a bind source
asks Docker to use a different directory on the host. Short-form mounts can
silently create that missing source as an empty directory, including when a
Caddyfile was expected. This can cause `/data/assets` permission failures or
file-versus-directory mount errors without any daemon failure.

Use SSH or a documented host-execution helper to run the deployment on the
verified Docker host as the checkout owner. If a helper enters as root, switch
to that owner before Git or deployment commands. Alternatively, use a maintenance
container with the deployment root mounted at its identical absolute host path.
Do not run Compose or create Git worktrees through a translated `/host` prefix.
Check the host identity and `docker info --format
'Name={{.Name}} Root={{.DockerRootDir}}'` against the actual deployment.

`docker run --rm alpine ...` without explicit mounts inspects that new Alpine
container, **not the daemon's filesystem or mount namespace**. Its empty `/home`,
absent `/host`, or overlay root is not evidence that the daemon lost host mounts.
Inspect the actual host and the intended bind source instead. An authorized
disposable bind probe should use `--mount type=bind,...,readonly` without source
auto-creation, so a missing host path fails rather than creating another stub.
See [Docker's bind-mount documentation](https://docs.docker.com/engine/storage/bind-mounts/).

### Path A: initialize a single-file checkout deployment

Use Bash for the command examples. After verifying the actual deployment, set
the updater overrides **in the process environment** and use the same Compose
identity for every command:

```bash
# Run from the verified production repository root.
# These defaults are valid only if they match the live deployment.
export CIF_COMPOSE_FILE=compose.example.yml
export CIF_COMPOSE_SERVICE=comfyui-image-frontend
export CIF_UPDATE_EXPECTED_BRANCH=main
export CIF_UPDATE_EXPECTED_REMOTE=https://github.com/astigmatism/comfyui-image-frontend.git
export CIF_UPDATE_START_TIMEOUT=120

# Preserve the live COMPOSE_PROJECT_NAME if it was explicitly configured.
compose=(docker compose -f "$CIF_COMPOSE_FILE")
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'
docker compose version
docker info >/dev/null
"${compose[@]}" config --quiet
"${compose[@]}" config --services
"${compose[@]}" ps --all
```

Do not set the expected branch or remote merely to silence a failed check. An SSH
origin URL is valid only after verifying it is the intended repository and setting
the exact expected URL. Updater controls such as `CIF_COMPOSE_FILE` are shell
environment variables; placing them only in `.env` does not configure the script.
The script accepts one Compose file through this override. Do not silently omit
additional production overrides or substitute the example for a custom stack.
Use Path B when the deployment has the worktree layout below.

### Path B: initialize the deployment-root context

The reported layout is:

```text
<deployment-root>/
  .env                         # private configuration, including CIF_IMAGE_TAG
  compose.yaml                 # existing production base definition
  compose.ordered-lora.yaml    # existing override selecting the release context
  source/                      # main deliberately frozen at its current SHA
  ordered-lora-<previous-sha>/  # retained previous detached worktree
  ordered-lora-<target-sha>/    # new detached worktree
  data/certificates/           # existing root CA, leaf, and private keys
```

These production Compose files are host-maintained and are not supplied by this
repository. Inspect their existing contents; do not create replacements from the
example. Confirm the app's image uses `${CIF_IMAGE_TAG}` and its private `env_file`
resolves to the deployment-root `.env`. `--env-file` controls interpolation; it
does not inject application settings unless the service definition also does so.

Run in one persistent Bash session on the host as the deployment owner. Set
`DEPLOY_ROOT` to its verified absolute host path and `DEPLOY_PROJECT` to the live
Compose project label before this block. For a one-shot maintenance container,
mount the entire root at **the identical absolute host path**, with the same
Docker context/socket and numeric owner identity. A mount at `/workspace` is not
equivalent: worktree links and Docker bind sources must resolve on the host.
The standard Portal runner includes Bash/Git/Docker/OpenSSL but not Python; the
editing and comparison examples below also require Python 3 in the execution
environment. Do not switch to the incompatible Portal update script as a shortcut.

```bash
set -Eeuo pipefail
: "${DEPLOY_ROOT:?Set the verified absolute deployment-root path}"
: "${DEPLOY_PROJECT:?Set the existing Compose project name}"
DEPLOY_ROOT=$(cd "$DEPLOY_ROOT" && pwd -P)
SOURCE_DIR="$DEPLOY_ROOT/source"
export CIF_COMPOSE_SERVICE=comfyui-image-frontend
export DEPLOY_CERT_DIR="$DEPLOY_ROOT/data/certificates"
cd "$DEPLOY_ROOT"
compose=(docker compose --project-directory "$DEPLOY_ROOT"
  --env-file "$DEPLOY_ROOT/.env" -p "$DEPLOY_PROJECT"
  -f "$DEPLOY_ROOT/compose.yaml"
  -f "$DEPLOY_ROOT/compose.ordered-lora.yaml")
test "$(git -C "$SOURCE_DIR" branch --show-current)" = main
test -z "$(git -C "$SOURCE_DIR" status --porcelain)"
FROZEN_MAIN_SHA=$(git -C "$SOURCE_DIR" rev-parse HEAD)
"${compose[@]}" config --quiet
"${compose[@]}" config --services
"${compose[@]}" ps --all
```

Use this exact `compose` array for backups, build, reconciliation, logs, and
verification. Preserve file order and project identity. Check for inherited shell
overrides, especially `CIF_IMAGE_TAG` and TLS variables: shell values take precedence
over `.env`. Resolve any mismatch with the live deployment before proceeding; do
not silently change which value is authoritative.

Before any routine Path B update, define and run this gate in that verified
execution context. It compares resolved bind sources with the existing live
containers, rather than comparing two Compose configurations that could share
the same incorrect path prefix. It also checks the expected data and TLS files
without printing private configuration:

```bash
verify_live_bind_sources() {
  python3 - "${compose[@]}" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

compose = sys.argv[1:]
config = json.loads(subprocess.check_output(compose + ["config", "--format", "json"]))
for service, definition in config["services"].items():
    ids = subprocess.check_output(compose + ["ps", "--all", "-q", service], text=True).split()
    assert len(ids) == 1, f"Expected one live container for {service}; inspect deployment ownership"
    live = json.loads(subprocess.check_output(["docker", "inspect", ids[0]]))[0]
    assert live["State"]["Running"], f"{service} is not running; inspect existing failure"
    mounts = {mount["Destination"]: mount for mount in live["Mounts"]}
    for candidate in definition.get("volumes", []):
        if candidate["type"] != "bind":
            continue
        target = candidate["target"]
        actual = mounts.get(target, {})
        assert actual.get("Type") == "bind", f"{service} {target}: storage topology differs"
        assert actual["Source"] == candidate["source"], f"{service} {target}: host bind source differs"
        assert actual["RW"] == (not candidate.get("read_only", False)), f"{service} {target}: access mode differs"
        source = Path(candidate["source"])
        assert source.exists(), f"{service} {target}: bind source is missing"
        if target == "/data":
            assert (source / "app.db").is_file() and (source / "assets").is_dir(), "Production data is missing"
        elif target == "/etc/caddy/Caddyfile":
            assert source.is_file(), "Caddyfile bind source must be a file"
        elif target == "/etc/caddy/certificates":
            assert all((source / name).is_file() for name in ("ca.crt", "tls.crt", "tls.key")), "TLS files are missing"
print("Live bind sources and required data/TLS files verified.")
PY
}
verify_live_bind_sources
```

A refusal is a deployment discrepancy to investigate, not a reason to substitute
the example Compose file, advance frozen main, or erase a volume. In particular,
an emergency named-volume deployment needs the storage recovery below before a
routine bind-based reconciliation. Repeat the gate immediately before `up`.

## 2. Review and validate the incoming changes

### Path A: review the branch update

Proceed only with a clean checkout on the verified branch and upstream. If local
changes, a detached HEAD, an unexpected source, or divergent history prevent the
update, preserve the state and report the specific blocker.

For an authorized update, fetch and review without moving the checked-out branch:

```bash
git fetch origin "$CIF_UPDATE_EXPECTED_BRANCH"
git merge-base --is-ancestor HEAD "origin/$CIF_UPDATE_EXPECTED_BRANCH"
git rev-parse "origin/$CIF_UPDATE_EXPECTED_BRANCH"
git log --oneline "HEAD..origin/$CIF_UPDATE_EXPECTED_BRANCH"
git diff --stat "HEAD..origin/$CIF_UPDATE_EXPECTED_BRANCH"
git diff "HEAD..origin/$CIF_UPDATE_EXPECTED_BRANCH" -- \
  Dockerfile compose.example.yml deployment scripts backend/alembic backend/app/config.py
```

### Path B: select a release without advancing main

Identify `DEPLOYED_SHA` from the **currently deployed worktree and image record**,
not `source/HEAD`. Verify the current tag, image ID, and build context agree with
that record. Set it to the full deployed commit SHA. Then fetch and select once:

```bash
: "${DEPLOYED_SHA:?Set the verified full SHA of the running release}"
git -C "$SOURCE_DIR" fetch origin main
TARGET_SHA=$(git -C "$SOURCE_DIR" rev-parse --verify 'origin/main^{commit}')
git -C "$SOURCE_DIR" merge-base --is-ancestor "$DEPLOYED_SHA" "$TARGET_SHA"
git -C "$SOURCE_DIR" log --oneline "$DEPLOYED_SHA..$TARGET_SHA"
git -C "$SOURCE_DIR" diff --stat "$DEPLOYED_SHA..$TARGET_SHA"
git -C "$SOURCE_DIR" diff "$DEPLOYED_SHA..$TARGET_SHA" -- \
  Dockerfile deployment scripts backend/alembic backend/app/config.py
test "$(git -C "$SOURCE_DIR" rev-parse HEAD)" = "$FROZEN_MAIN_SHA"
```

For an explicitly requested commit, resolve that ref to a full SHA instead and
verify its provenance and release scope. Never run `pull`, `merge`, `checkout`, or
`reset` on `source/main` to deploy it. A clean detached release worktree is expected
in Path B and is not an error. Review from the deployed SHA so a deliberately old
main does not obscure the actual release diff. If the target is already deployed,
verify the existing image and health; do not overwrite its tag to manufacture an update.

### Validation for either path

Treat a nonzero check as a failure to resolve, not a reason to run the next step.
Review changed updater instructions, migrations, dependencies, configuration
defaults, and user-facing behavior. Identify any required configuration migration
and whether the old image can run against the resulting database schema.

Establish validation evidence for the incoming commit. Use existing results for
that commit when available; otherwise validate it in an isolated checkout and
test environment following [testing.md](testing.md). Use `make validate` for the
complete suite or focused checks appropriate to the change. Explicitly record
skipped checks; `make validate-available` can skip dependencies and is not proof
that the complete suite passed.

Keep production `.env`, volumes, certificates, ports, and credentials out of the
test environment. On a production Docker host, inspect test isolation before
running Docker/browser suites: TLS tests default to port `8443`, and the Compose
test stack can reuse the application image tag. Prefer another Docker daemon;
otherwise use isolated project, storage, ports, and image tags. Do not run tests
inside the live application container. For documentation-only changes, a diff
and link review are sufficient.

If asked to implement a change as well as deploy it, develop and validate it in a
separate checkout. Follow the authorized review/publication process to put it on
the deployment branch first. Keep the production checkout clean and use the same
deployment path below; do not edit a running container or build an uncommitted hotfix there.

The Path A updater fetches again and deploys the then-current branch tip. It has no
commit-pinning option. Record the reviewed SHA and check the actual SHA afterward;
if the branch advances, review and validate the additional commits before claiming
completion. If the task requires an exact SHA, resolve that requirement before
running the updater rather than claiming it will pin the release.
Path B deploys the recorded `TARGET_SHA` without resolving the branch again.

## 3. Prepare a recoverable checkpoint

For Path B, first exclude other maintenance jobs and hold a deployment-root lock
through backup, edits, and verification in the same Bash session:

```bash
# Path B only, for an authorized update (not a dry run).
DEPLOY_LOCK="$DEPLOY_ROOT/.deployment-update.lock"
mkdir "$DEPLOY_LOCK" || { printf '%s\n' 'Another deployment may be active; inspect it.' >&2; exit 1; }
trap 'rmdir "$DEPLOY_LOCK"' EXIT
```

This serializes agents following Path B; it does not coordinate with the Path A
script or other tools. Ensure no Portal/other update is active before acquiring
it. After a lost session, inspect the prior job before removing its lock. Check
for a running `backup-production-bind.py` process and its `backup-status.json`;
the detached backup can outlive the agent's shell. Do not run another update while
that process is active. A stale lock by itself is not an active deployment.

For Path B, now perform **B1 below** to prepare the target worktree and restricted
configuration checkpoint, then return here for the data backup. This preparation
does not stop the app or edit its live configuration. Set `DEPLOY_BACKUP_DIR` to
a new directory under `$DEPLOY_ROOT/.deployment-backups`, created with mode 700.

```bash
# Path B only; keep these variables in the same native-host Bash session.
umask 077
DEPLOY_BACKUP_DIR="$DEPLOY_ROOT/.deployment-backups/pre-$DEPLOYED_SHA-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEPLOY_ROOT/.deployment-backups"
mkdir -m 700 "$DEPLOY_BACKUP_DIR"
```

Before recreation or migrations:

1. Record the deployed Git SHA (and frozen-main SHA for Path B), app container ID,
   **running image ID**, edge image ID, Compose identity, and actual storage mount
   sources. The working-tree SHA
   alone does not prove which code was built into an existing container.
2. Retain the running app image's existing commit tag in Path B; in Path A, give
   it a unique local rollback tag before a build can replace `:local`.
   Use `docker inspect --format '{{.Image}}' <app-container-id>`
   to obtain the image ID, then `docker image tag <image-id> <unique-rollback-tag>`.
   Retain the deployment configuration needed to run it, including TLS config.
3. Create a timestamped, access-restricted backup directory outside the checkout.
   Back up private environment/configuration files and the actual TLS certificate
   directory, including the existing root CA and keys. Do not display their contents.
   In Path B, include both deployment-root Compose files and `.env`, and retain
   the previous worktree. The Path B helper operates on the exact container ID
   discovered through the two-file Compose array. Do not copy the README's
   single-file invocation into this layout.
4. Take a consistent backup of SQLite **and all application-owned files together**.
   In Path B use the helper below. In Path A follow the stopped-app archive approach
   in the README's **Back up and restore** section, substituting the verified
   storage and external backup directory. Include any separately mounted database,
   assets, or uploads. Never
   archive a live SQLite database and media as though they were an atomic snapshot.
5. Stop only the frontend for that backup and ensure it is started again even if
   archiving fails, using a trap or equivalent cleanup. Verify that it becomes
   healthy again before proceeding. Do not stop the external ComfyUI runtimes or
   cancel their work. Avoid interrupting active user work when timing permits;
   account for jobs that may need reconciliation after restart.
   Preserve the Path B lock cleanup when adding a backup cleanup handler; do not
   replace its trap and leave an unexplained lock behind.
6. Verify that the archive is nonempty and readable, record a checksum, and record
   the backup path and time. Do not continue with an incomplete required backup.

The backup stop introduces a brief interruption in addition to container
recreation. The app resumes while the replacement builds. A restore would lose
changes made after the backup; report that recovery point accurately. A tag alone
is not a data backup, and an archive listing alone is not a tested restore.

### Path B: run the bounded backup as one host process

Use the helper from the verified target worktree. It refuses translated host
paths, remote daemons, mismatched Compose identity/images/mounts, named volumes,
and unhealthy services **before stopping anything**. It archives the actual
`data/` bind, including SQLite sidecars and certificates. Its `finally` handler
restarts the same app after success, an archive failure, timeout, or handled
SIGINT/SIGTERM. `nohup` keeps this recovery running when the agent disconnects.
SIGKILL, a host failure, or an unavailable Docker daemon still requires operator
recovery; do not claim that a shell trap can cover those cases.

```bash
python3 "$WORKTREE_DIR/scripts/backup-production-bind.py" \
  --deploy-root "$DEPLOY_ROOT" --project "$DEPLOY_PROJECT" --check-only
nohup python3 "$WORKTREE_DIR/scripts/backup-production-bind.py" \
  --deploy-root "$DEPLOY_ROOT" --project "$DEPLOY_PROJECT" \
  --output "$DEPLOY_BACKUP_DIR" \
  > "$DEPLOY_BACKUP_DIR/backup.log" 2>&1 < /dev/null &
BACKUP_PID=$!
if wait "$BACKUP_PID"; then
  cat "$DEPLOY_BACKUP_DIR/backup-status.json"
else
  cat "$DEPLOY_BACKUP_DIR/backup-status.json"
  printf '%s\n' 'Backup failed; verify the original app is healthy. Do not deploy.' >&2
  exit 1
fi
```

The archive has a 180-second limit; restart health has a 120-second limit. It
restarts before reading/hashing the archive or checking SQLite integrity on a
disposable extracted copy. Require `phase=complete` and `exit_code=0`, and retain
`data.tar`, `data.tar.sha256`, the status file, and configuration checkpoint.
Failure retains a `.partial` archive; it is not an approved recovery checkpoint.
Do not increase limits repeatedly or rerun the backup while its process exists.
If a large dataset needs a different bound, establish it while the app is healthy.

If a tool times out, inspect the **existing** process and status file. A tool
timeout does not mean its command failed. Terminal text searches can match echoed
commands, and `^`/`$` may refer to the whole transcript instead of individual lines.
Neither a matched sentinel nor absence of a match proves completion. Use process
exit status and the helper's receipt. Do not put shell operators such as `>` in a
string variable and execute `$COMMAND`; pass arguments directly, with redirection
outside the command. Keep builds and all investigations outside the stopped-app
interval. For this installation a backup is normally seconds, not tens of minutes.

## 4. Deploy using the selected path

### Path A: run the single-file checkout updater

Recheck the clean tree, deployment identity, and absence of another updater. Run:

```bash
./update_and_restart
```

Capture the actual exit code and relevant output. If logging through `tee`, enable
`set -o pipefail` so a successful log write cannot conceal an updater failure.
Use a durable terminal/session if the tool has short execution timeouts; wait for
the existing job rather than launching another copy. Keep long-running progress
updates concise.

The script checks tools and source identity, locks the checkout, fast-forwards the
branch, validates Compose, builds while the current app is up, ensures TLS
material, and reconciles the complete project with a bounded health wait. It then
checks that the app resolved an explicit ComfyUI instance configuration. The app's
startup applies database migrations. A normal update needs no separate Alembic
command, host-side frontend build, `pip install` in the container, or manual restart.

`CIF_COMPOSE_SERVICE` selects the final runtime-configuration check; it does not
restrict the build or project reconciliation. `CIF_UPDATE_START_TIMEOUT` defaults
to 120 seconds. The obsolete `CIF_UPDATE_STOP_TIMEOUT` does not control recreation;
the example uses a 30-second Compose stop grace period.

The example Service Portal control invokes the same Path A script. Use one update
path for a deployment; that control is not compatible with Path B.
If its maintenance runner is in scope and `deployment/runner/Dockerfile` changed,
rebuild the configured runner image on the host before the next Portal update;
the Portal does not build it automatically. This does not require restarting the
Portal or external generation services.

### Path B: deploy a detached worktree with both Compose files

Perform B1 during section 3, then B2–B4 after the backup succeeds. Keep the initialized
two-file `compose` array, lock, and captured SHAs in the same Bash session. Use
`DEPLOY_BACKUP_DIR` for the verified, access-restricted backup directory from
section 3. Record `DEPLOY_TLS_HOSTNAME` from the live edge configuration. The
examples use a full commit SHA for both the new directory and image tag to avoid
ambiguous short hashes; retained older releases may use shorter tags.

#### B1. Preserve configuration and create the release worktree

```bash
: "${DEPLOY_BACKUP_DIR:?Set a new restricted backup directory outside the checkout}"
: "${DEPLOY_TLS_HOSTNAME:?Set the existing TLS hostname}"
test -d "$DEPLOY_BACKUP_DIR"
umask 077
cp -p "$DEPLOY_ROOT/.env" "$DEPLOY_BACKUP_DIR/deployment.env"
cp -p "$DEPLOY_ROOT/compose.yaml" "$DEPLOY_BACKUP_DIR/compose.yaml"
cp -p "$DEPLOY_ROOT/compose.ordered-lora.yaml" "$DEPLOY_BACKUP_DIR/compose.ordered-lora.yaml"
"${compose[@]}" config --format json > "$DEPLOY_BACKUP_DIR/compose.before.json"
OLD_EDGE_ID=$("${compose[@]}" ps -q cif-tls-edge)
test -n "$OLD_EDGE_ID"

WORKTREE_DIR="$DEPLOY_ROOT/ordered-lora-$TARGET_SHA"
if test ! -e "$WORKTREE_DIR"; then
  git -C "$SOURCE_DIR" worktree add --detach "$WORKTREE_DIR" "$TARGET_SHA"
fi
test "$(git -C "$WORKTREE_DIR" rev-parse HEAD)" = "$TARGET_SHA"
test -z "$(git -C "$WORKTREE_DIR" branch --show-current)"
test -z "$(git -C "$WORKTREE_DIR" status --porcelain)"
test "$(git -C "$SOURCE_DIR" rev-parse HEAD)" = "$FROZEN_MAIN_SHA"
```

If that worktree already exists, inspect it and reuse it only after verifying its
exact SHA, detached state, and clean tree; do not delete it or force-add over it.
Read the target revision's deployment instructions and complete any outstanding
validation before editing deployment configuration. Do not put `.env` or `data/`
in the worktree, and do not replace the production base Compose file with the
worktree's `compose.example.yml`.
Return to section 3 and complete the backup before B2.

#### B2. Change only the image tag and app build context

The intended edits are `CIF_IMAGE_TAG=<TARGET_SHA>` in the deployment-root `.env`
and `services.comfyui-image-frontend.build.context: ./ordered-lora-<TARGET_SHA>`
in the existing override. Preserve every other setting. The following targeted
editor supports the reported plain `context: ./ordered-lora-<hex-sha>` layout and
an existing SHA-valued `CIF_IMAGE_TAG` assignment (optionally quoted). It refuses
missing/duplicate keys, symlinks, or other layouts before writing. If it refuses,
inspect the actual structure and make these two field edits with a suitable
editor; never replace the entire override with a minimal example.

```bash
python3 - "$DEPLOY_ROOT" "$TARGET_SHA" <<'PY'
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

root, sha = Path(sys.argv[1]), sys.argv[2]
assert re.fullmatch(r"[0-9a-f]{40}", sha), "Expected a full commit SHA"
edits = [
    (root / ".env",
     rb"(?m)^([ \t]*CIF_IMAGE_TAG[ \t]*=[ \t]*)(?:[0-9a-f]{7,40}|\"[0-9a-f]{7,40}\"|'[0-9a-f]{7,40}')([ \t]*(?:#[^\r\n]*)?\r?)$",
     sha.encode()),
    (root / "compose.ordered-lora.yaml",
     rb"(?m)^([ \t]+context:[ \t]*)\./ordered-lora-[0-9a-f]{7,40}([ \t]*(?:#[^\r\n]*)?\r?)$",
     f"./ordered-lora-{sha}".encode()),
]
prepared = []
for path, pattern, value in edits:
    assert path.is_file() and not path.is_symlink(), "Expected regular deployment files"
    content = path.read_bytes()
    key = rb"(?m)^[ \t]*(?:export[ \t]+)?CIF_IMAGE_TAG[ \t]*=" if path.name == ".env" else rb"(?m)^[ \t]*context:"
    assert len(re.findall(key, content)) == 1, "Expected exactly one target key"
    updated, count = re.subn(pattern, lambda m: m[1] + value + m[2], content)
    assert count == 1, "Layout differs; inspect before editing"
    prepared.append((path, updated))
for path, content in prepared:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
print("Updated the deployment image tag and release build context.")
PY
"${compose[@]}" config --quiet
"${compose[@]}" config --format json > "$DEPLOY_BACKUP_DIR/compose.candidate.json"
```

Each file replacement is atomic, but the pair is not a transaction. If editing
or validation fails, the running app is still intact; restore both files from
the saved pair before attempting a restart. Do not print the expanded JSON files;
they contain resolved private settings. Compare them without displaying values:

```bash
TARGET_IMAGE=$(python3 - "$DEPLOY_BACKUP_DIR" "$WORKTREE_DIR" "$TARGET_SHA" "$CIF_COMPOSE_SERVICE" <<'PY'
import copy
import json
from pathlib import Path
import sys

backup, worktree, sha, service = sys.argv[1:]
before = json.loads((Path(backup) / "compose.before.json").read_text())
after = json.loads((Path(backup) / "compose.candidate.json").read_text())
old_app = before["services"][service]
new_app = after["services"][service]
assert Path(new_app["build"]["context"]).resolve() == Path(worktree).resolve(), "Wrong build context"
old_image, new_image = old_app["image"], new_app["image"]
assert "@" not in old_image and ":" in old_image.rsplit("/", 1)[-1], "Expected a tagged image"
assert new_image == old_image.rsplit(":", 1)[0] + ":" + sha, "Wrong image repository or tag"
expected = copy.deepcopy(before)
expected_app = expected["services"][service]
expected_app["image"] = new_image
expected_app["build"]["context"] = new_app["build"]["context"]
# env_file may also pass the interpolation setting into the app environment.
if "CIF_IMAGE_TAG" in expected_app.get("environment", {}):
    expected_app["environment"]["CIF_IMAGE_TAG"] = sha
assert expected == after, "Unexpected configuration change; inspect privately before deploying"
print(new_image)
PY
)
```

This gate requires the image repository, project, mounts, network bindings, private
settings, and all other services (including the edge) to stay the same. If it
fails, resolve the exact difference; do not weaken the comparison just to proceed.

#### B3. Build while the current app stays up

```bash
if docker image inspect "$TARGET_IMAGE" >/dev/null 2>&1; then
  printf '%s\n' 'Target tag already exists; verify its recorded provenance before reuse.' >&2
  exit 1
fi
"${compose[@]}" build "$CIF_COMPOSE_SERVICE"
TARGET_IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$TARGET_IMAGE")
test "$(git -C "$WORKTREE_DIR" rev-parse HEAD)" = "$TARGET_SHA"
test -z "$(git -C "$WORKTREE_DIR" status --porcelain)"
test "$(git -C "$SOURCE_DIR" rev-parse HEAD)" = "$FROZEN_MAIN_SHA"
```

Never overwrite a retained commit tag. For a retry with an already-built target,
verify the recorded SHA/context/image ID and skip the build when they agree;
otherwise investigate before reuse. Preserve the previous image and worktree.
Record the target SHA, tag, context, and built image ID together as deployment
evidence; the Dockerfile does not itself embed a Git revision label.
`build --pull` is a Boolean option; do not write `build --pull never` (Compose can
interpret `never` as a service). `--pull never` belongs on the `up` command below.

#### B4. Verify TLS at the deployment root and reconcile

Confirm the existing root CA is readable and valid before invoking issuance.
Pass the **absolute deployment-root certificate directory** to the script from
the target worktree; never allow its checkout-relative default in this layout:

```bash
test -r "$DEPLOY_CERT_DIR/ca.crt"
test -r "$DEPLOY_CERT_DIR/ca.key"
openssl verify -CAfile "$DEPLOY_CERT_DIR/ca.crt" "$DEPLOY_CERT_DIR/ca.crt"
CIF_TLS_HOSTNAME="$DEPLOY_TLS_HOSTNAME" CIF_TLS_CERT_DIR="$DEPLOY_CERT_DIR" \
  "$WORKTREE_DIR/scripts/issue-local-cert.sh"
"${compose[@]}" config --quiet
```

Before `up`, verify unchanged services' local image IDs still match their running
containers; `--pull never` prevents a pull but cannot undo a tag overwritten by
another local build. Reconcile only after that check passes:

```bash
verify_live_bind_sources
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 120
```

The expected reconciliation recreates only the app. If a leaf was renewed,
verify the certificate actually served by the edge; Caddy may
need a controlled reload using its existing configuration to adopt renewed files.
Keep its root CA, mount, hostname, and container identity. A requested edge/config
change needs its own reviewed scope instead of bypassing the B2 comparison.

Capture the exit status. If reconciliation fails, inspect logs and current state;
make at most one retry of the same bounded `up` command after a routine fix. This
manual path does not inherit the updater's automatic recovery or final runtime
check. Run the shared checks in section 5, including explicit runtime configuration.

```bash
APP_ID=$("${compose[@]}" ps -q "$CIF_COMPOSE_SERVICE")
test -n "$APP_ID"
test "$(docker inspect --format '{{.Image}}' "$APP_ID")" = "$TARGET_IMAGE_ID"
test "$("${compose[@]}" ps -q cif-tls-edge)" = "$OLD_EDGE_ID"
test "$(git -C "$SOURCE_DIR" branch --show-current)" = main
test "$(git -C "$SOURCE_DIR" rev-parse HEAD)" = "$FROZEN_MAIN_SHA"
test -z "$(git -C "$SOURCE_DIR" status --porcelain)"
```

These are additional deployment invariants, not substitutes for app health or
trusted HTTPS verification. Retain backups, both releases, and deployment records;
worktree/image cleanup is a separate operation.

## 5. Verify the deployed result

Do not report success based only on a completed build or a running container.

1. Record the updater (Path A) or Compose (Path B) exit code and deployed full Git
   SHA. For Path B, record `TARGET_SHA` separately from unchanged `FROZEN_MAIN_SHA`.
   Compare with the reviewed target. Confirm the release checkout is clean, the
   live containers still belong to the original Compose project, their mounts are
   preserved, and the running app image ID matches the newly built service image.
   An unchanged image ID is valid
   for an update that did not affect image inputs.
2. Check app and edge health and inspect recent logs for migrations, startup
   errors, crash loops, and reconciliation problems. Redact sensitive diagnostics:

   ```bash
   "${compose[@]}" ps --all
   "${compose[@]}" logs --since 10m --tail 150 "$CIF_COMPOSE_SERVICE" cif-tls-edge
   "${compose[@]}" exec -T "$CIF_COMPOSE_SERVICE" python - <<'PY'
   import json
   import urllib.request
   from app.config import get_settings

   with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as response:
       health = json.load(response)
       assert response.status == 200
   assert health["status"] == "ok" and health["database"] is True
   assert health["worker"]["ready"] is True
   assert health["worker"]["state"] == "running"
   assert health["worker"]["dispatcher_running"] is True
   assert health["worker"]["heartbeat_fresh"] is True
   settings = get_settings()
   assert settings.comfyui_instance_configuration_mode == "explicit"
   print("Application, database, worker, and runtime configuration checks passed.")
   PY
   ```

   The worker can briefly report `recovering` after startup. Wait for it to become
   `running` within the bounded verification period; do not equate `ready=true`
   alone with completion of recovery.

   Substitute the actual edge service and listener if the deployment differs.
   `/api/health` covers the database and local worker; it does not prove that every
   ComfyUI runtime, Prompt Assistant, or speech service is available.
3. Verify HTTPS through the real edge with hostname and CA validation. Set the
   following variables from the verified deployment, using an absolute certificate
   directory and the actual published IP/port:

   ```bash
   curl --fail --silent --show-error --max-time 10 \
     --cacert "$DEPLOY_CERT_DIR/ca.crt" \
     --resolve "$DEPLOY_TLS_HOSTNAME:$DEPLOY_TLS_PORT:$DEPLOY_TLS_IP" \
     "https://$DEPLOY_TLS_HOSTNAME:$DEPLOY_TLS_PORT/api/health"
   ```

   Require HTTP 200 and the healthy JSON fields checked above. `--resolve` verifies
   TLS routing without requiring host DNS; check normal hostname resolution
   separately. Do not use `-k` as proof of a trusted browser connection. The edge's
   internal health check uses it and therefore is insufficient for this check.
4. Fetch `/` and `/build.json` over the same HTTPS origin. Confirm the page and its
   referenced JavaScript/CSS assets load and that their versioned paths agree with
   `build.json`. Compare the served manifest with the manifest in the running app
   image. Its `asset_version` fingerprints frontend inputs; it is **not** a Git SHA
   and may remain unchanged for backend-only updates.
5. If an authorized browser session is available, reload the HTTPS app and verify
   the changed behavior and retained gallery/folder views. Do not reset an account
   to obtain access. Report browser checks as unverified if access is unavailable.
6. When external runtime reachability needs checking, follow the read-only live
   checks in [testing.md](testing.md), using the actual configured endpoints.
   Do not submit a generation, invoke Prompt Assistant, upload files, alter queues,
   or delete gallery content as a deployment smoke test unless the task explicitly
   includes that operation. Report pre-existing upstream outages separately.

## 6. Handle failure without destroying recovery options

- **Translated worktree paths:** back up the Git worktree metadata and each
  affected `.git` link, then run `git -C "$SOURCE_DIR" worktree repair <actual-host-path>...`
  on the host as the checkout owner. Verify every retained worktree's original
  commit and cleanliness and the unchanged frozen-main SHA. Do not prune entries
  just because paths created through the agent's `/host` view appear missing.
- **Emergency named-volume deployment:** the live volume is authoritative;
  the original host `data/` can be stale. For an authorized return to binds, save
  the actual container definitions and settings, stop the application and edge,
  and copy the complete stopped volume (including SQLite journal files) into a
  separate staging directory. Verify file inventories, hashes, numeric ownership,
  database integrity, and existing TLS material before promoting it. Retain the
  old host directory, both volumes, and a stopped-state archive. Remove only the
  stopped bridge containers needed to free their names, then reconcile the
  original Compose project. If the replacement has accepted writes, preserve its
  newest data before any rollback; never silently return to the older volume.
- **Failure before recreation:** the old container may still be serving while the
  checkout or local image tag has advanced. Inspect actual state. Preserve the
  running image, backup, and diagnostics; do not equate the checkout with the live
  release or reset it automatically.
- **Failed reconciliation:** the Path A updater makes one additional `up --no-build`
  attempt using the replacement image it just built. This is a retry, **not an
  automatic rollback to the previous image or database**. Check service health
  even if the command returns nonzero; do not loop indefinitely.
- **Path B configuration changed but deployment failed:** saved `.env` and override
  now determine what a future `up` would launch, even when the old app is still
  serving. If no recreation/migration occurred, restore that saved pair, validate
  it using the same two-file array, and verify it matches the still-running release.
  Do not run `up` just to restore the files. After any recreation or migration,
  inspect schema compatibility before returning to the old image. Retain the failed
  worktree/image for diagnosis; never advance frozen main as a recovery technique.
- **Lock refusal:** inspect the actual job/process. Use `CIF_UPDATE_FORCE_UNLOCK=1`
  only after proving no updater is active; elapsed time or a different container
  name alone is not proof. Do not delete a live job's lock.
- **TLS failure:** check hostname, chain, expiry, key match, mount path, and key
  readability by the edge's configured user. Preserve the original CA. Do not
  delete certificates to force regeneration or make private keys world-readable.
- **Rollback:** prepare the exact previous image/configuration and compare its
  schema expectations with the live database. Do not launch older code against an
  incompatible migrated database or run an unreviewed Alembic downgrade. If
  recovery requires data restoration, identify the backup, affected data, and
  recovery time, then obtain authorization for that replacement if not already
  given. Prefer restoration into a new volume with the failed volume retained;
  restore the database and files as one set. The README's destructive volume
  removal example is not authorization to delete production data.

Fix routine issues within the authorized scope and rerun the appropriate checks.
If blocked, state the failing command, concise error, current service health,
whether source/image/data changed, and the concrete action needed. Never label a
failed or unverified deployment successful.

## 7. Completion report

Return a concise report containing:

- Outcome: deployed and verified, deployed with verification gaps, or blocked/failed.
- Selected deployment path, deployment root, ordered Compose files, and project;
  previous and deployed Git SHAs (plus unchanged frozen-main SHA for Path B); running
  app image ID and frontend asset version where checked.
- A short summary of the changes and any migrations/configuration adjustments.
- Backup location, recovery point, and retained previous-image tag.
- Validation evidence, app/edge/HTTPS results, behavior checks, and explicit skips
  or pre-existing upstream failures.
- Any interruption, remaining issue, or required next action.

Do not include secrets, private user content, or full environment/log dumps.
