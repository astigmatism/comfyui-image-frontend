# Production update and deployment instructions for AI agents

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
- [Canonical updater](../scripts/update-and-restart.sh) and its
  [root wrapper](../update_and_restart)
- The **actual production Compose file**, plus the tracked
  [Compose example](../compose.example.yml) and [Dockerfile](../Dockerfile)
- [Database migration policy](database.md) and [testing instructions](testing.md)
- [TLS certificate script](../scripts/issue-local-cert.sh) when TLS is involved

Use the checked-out code and observed host configuration to verify this document.
Do not assume that example paths, addresses, or service names identify the live
deployment. If the implementation has changed, resolve the discrepancy before
running a command whose effect is uncertain.

## Deployment facts and boundaries

| Item | Repository default / required behavior |
| --- | --- |
| Update command | `./update_and_restart`, which delegates to `scripts/update-and-restart.sh` |
| Source | `main`, tracking `origin/main` |
| Expected origin | `https://github.com/astigmatism/comfyui-image-frontend.git` |
| Compose file | `compose.example.yml` |
| App service / local image | `comfyui-image-frontend` / `comfyui-image-frontend:local` |
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
The updater reconciles **every service in its Compose project**, not only
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
   working directory, and configuration files. Match these to the checkout and
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

## 2. Review and validate the incoming changes

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
updater below; do not edit a running container or build an uncommitted hotfix there.

The updater fetches again and deploys the then-current branch tip. It has no
commit-pinning option. Record the reviewed SHA and check the actual SHA afterward;
if the branch advances, review and validate the additional commits before claiming
completion. If the task requires an exact SHA, resolve that requirement before
running the updater rather than claiming it will pin the release.

## 3. Prepare a recoverable checkpoint

Before recreation or migrations:

1. Record the current Git SHA, app container ID, **running image ID**, edge image
   ID, Compose identity, and actual storage mount sources. The working-tree SHA
   alone does not prove which code was built into an existing container.
2. Give the running app image a unique local rollback tag before a build can
   replace `:local`. Use `docker inspect --format '{{.Image}}' <app-container-id>`
   to obtain the image ID, then `docker image tag <image-id> <unique-rollback-tag>`.
   Retain the deployment configuration needed to run it, including TLS config.
3. Create a timestamped, access-restricted backup directory outside the checkout.
   Back up private environment/configuration files and the actual TLS certificate
   directory, including the existing root CA and keys. Do not display their contents.
4. Take a consistent backup of SQLite **and all application-owned files together**.
   Follow the stopped-app archive approach in the README's **Back up and restore**
   section, substituting the verified volume or bind mount and the external backup
   directory. Include any separately mounted database, assets, or uploads. Never
   archive a live SQLite database and media as though they were an atomic snapshot.
5. Stop only the frontend for that backup and ensure it is started again even if
   archiving fails, using a trap or equivalent cleanup. Verify that it becomes
   healthy again before proceeding. Do not stop the external ComfyUI runtimes or
   cancel their work. Avoid interrupting active user work when timing permits;
   account for jobs that may need reconciliation after restart.
6. Verify that the archive is nonempty and readable, record a checksum, and record
   the backup path and time. Do not continue with an incomplete required backup.

The backup stop introduces a brief interruption in addition to container
recreation. The app resumes while the replacement builds. A restore would lose
changes made after the backup; report that recovery point accurately. A tag alone
is not a data backup, and an archive listing alone is not a tested restore.

## 4. Run the canonical deployment

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

The Service Portal invokes the same script. Use one update path for a deployment.
If its maintenance runner is in scope and `deployment/runner/Dockerfile` changed,
rebuild the configured runner image on the host before the next Portal update;
the Portal does not build it automatically. This does not require restarting the
Portal or external generation services.

## 5. Verify the deployed result

Do not report success based only on a completed build or a running container.

1. Record the updater exit code and new full Git SHA. Compare with the reviewed
   target. Confirm the checkout is clean, the live containers still belong to the
   original Compose project, their mounts are preserved, and the running app
   image ID matches the newly built service image. An unchanged image ID is valid
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
   settings = get_settings()
   assert settings.comfyui_instance_configuration_mode == "explicit"
   print("Application, database, worker, and runtime configuration checks passed.")
   PY
   ```

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

- **Failure before recreation:** the old container may still be serving while the
  checkout or local image tag has advanced. Inspect actual state. Preserve the
  running image, backup, and diagnostics; do not equate the checkout with the live
  release or reset it automatically.
- **Failed reconciliation:** the updater makes one additional `up --no-build`
  attempt using the replacement image it just built. This is a retry, **not an
  automatic rollback to the previous image or database**. Check service health
  even if the command returns nonzero; do not loop indefinitely.
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
- Production checkout and Compose project; previous and deployed Git SHAs; running
  app image ID and frontend asset version where checked.
- A short summary of the changes and any migrations/configuration adjustments.
- Backup location, recovery point, and retained previous-image tag.
- Validation evidence, app/edge/HTTPS results, behavior checks, and explicit skips
  or pre-existing upstream failures.
- Any interruption, remaining issue, or required next action.

Do not include secrets, private user content, or full environment/log dumps.
