# Routine production update — Samus

Use the installed `update_production` command. It fetches `origin/main`, pins the
commit, and launches the reviewed deployment code as one durable Docker job.
**Do not translate this document into another script.**

## Run

From the production agent:

```bash
/host/home/astigmatism/comfyui-image-frontend/update_production
```

From an existing native host session, omit `/host` from that command. SSH setup
is not required: the launcher uses the agent's existing Docker socket and mounts
the project at its identical native host path inside the maintenance container.

The command prints a job name and exact commands to inspect its state and logs.
Wait 30 seconds between checks. Finish when `running=false`; require `exit_code=0`
and the final `complete` or `already-current` result. A tool timeout means check
the same job, not launch another one. The job survives an agent disconnect.

Use `update_production --check-only` to check live state without building the app,
backing up data, editing production configuration, or restarting services. The
launcher may build/cache its maintenance tool image, and it fetches remote refs.

## What the command does

It checks the existing project, configuration, images, mounts, certificates and
Git state; holds the deployment lock; prepares the detached target worktree;
builds with the old app running; starts the candidate as the production UID in an
isolated container with disposable data, no network or production mounts; takes a
consistent backup with restart cleanup
on handled failures; reconciles the original two-file Compose project; and checks
the running image, database, worker, explicit runtime configuration, trusted HTTPS,
frontend assets and manifest. It preserves frozen `source/main`, production data,
settings, certificates, network, external runtimes, and previous recovery copies.

Builds have a 10-minute limit, archive creation 180 seconds, and startup/worker
checks 120 seconds each. Most warm-cache updates should take a few minutes; these
are bounds, not a performance promise. Every phase is timestamped. Build output,
configuration checkpoints, backup and status are retained under `.deployment-backups`.
An already-current release only gets verified; it is not rebuilt or restarted.

## If it fails

Report the job's actual exit code, failed phase and concise error. A candidate that
cannot import its code, migrate a fresh database or serve its assets fails before
the live app is stopped. Before cutover,
the command restores its configuration edits and the backup routine restarts the
old app. After cutover it retains the current data and diagnostic records; it does
not risk launching old code against a newly migrated database. SIGKILL, host or
Docker failure can still require operator recovery.

Retain failed images and their receipts. Publish a fix as a new commit and deploy
its new image tag; do not overwrite an existing commit tag to retry a bad build.

Do not create SSH keys, modify `authorized_keys`, inspect unrelated cron/services,
benchmark disks, write replacement deploy/verification scripts, run test suites on
production, change storage, or retry indefinitely. Do not use `update_and_restart`
or the Portal update button for this two-file deployment. If access or an invariant
fails, report that specific blocker while preserving the current service.

The [manual reference](manual-deployment-reference.md) is for deliberate recovery
or a different deployment layout, **not required reading for routine updates**.
Release development, test evidence and review belong before merging to `main`.
