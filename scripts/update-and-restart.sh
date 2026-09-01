#!/usr/bin/env bash
#
# Canonical "Update and restart" entry point for this repository.
#
# This script is the single update path for both operators and Service
# Portal: the root `update_and_restart` wrapper delegates here, and the
# `io.service-portal.update.*` labels in compose.example.yml point at
# this file. It is deliberately noninteractive and safe for unattended
# use:
#
#   1. Resolves the repository root from its own location.
#   2. Acquires an atomic per-checkout lock and removes it on every exit.
#   3. Verifies tools, repository files, Docker access, and Compose
#      availability before making any change.
#   4. Verifies the expected branch, upstream, and remote; rejects a
#      detached HEAD or an unexpected source repository.
#   5. Refuses a dirty working tree; never resets, stashes, discards, or
#      overwrites local work.
#   6. Fetches the expected upstream branch and merges fast-forward only,
#      rejecting divergent or rewritten history.
#   7. Re-validates the Compose configuration after the update, before any
#      running service is recreated.
#   8. Builds the replacement image while the current application stays
#      available (there is no external application image to pull; the
#      build pulls the base images it needs).
#   9. Reconciles the complete Compose project (here: a single long-lived
#      service) instead of touching one container ad hoc.
#  10. Recreates and health-checks with `docker compose up -d --wait` and a
#      bounded wait timeout; never `docker compose down`, which would create
#      unnecessary downtime.
#  11. Preserves named volumes, bind mounts, and user data; never runs any
#      prune command.
#  12. Exits 0 only after the updated application is healthy; every failure
#      prints a concise line containing "Error" or "Refusing" so Service
#      Portal can surface the cause.
#
# When Service Portal runs this script it does so from a detached
# maintenance container based on an existing local runner image and supplies:
#
#   HOME=/tmp
#   SERVICE_PORTAL_UPDATE_DELEGATED=1
#   SERVICE_PORTAL_UPDATE_JOB_ID=<job UUID>
#   DSH_UPDATE_DELEGATED=1            (compatibility input)
#   DSH_UPDATE_CONTAINER_NAME=<name>  (compatibility input)
#
# The script never launches another updater, so those flags only confirm
# the delegated context. It never requires secrets; for a private remote the
# operator provisions noninteractive least-privilege Git credentials in the
# runner image or host checkout without committing them to source.

set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
cd -- "$REPO_ROOT"

COMPOSE_FILE=${CIF_COMPOSE_FILE:-compose.example.yml}
SERVICE=${CIF_COMPOSE_SERVICE:-comfyui-image-frontend}
START_TIMEOUT=${CIF_UPDATE_START_TIMEOUT:-120}
EXPECTED_BRANCH=${CIF_UPDATE_EXPECTED_BRANCH:-main}
EXPECTED_REMOTE=${CIF_UPDATE_EXPECTED_REMOTE:-https://github.com/astigmatism/comfyui-image-frontend.git}

# Never block on an interactive credential prompt when unattended.
export GIT_TERMINAL_PROMPT=0

log() { echo "update-and-restart: $*"; }
die() {
  echo "Error: $*" >&2
  exit 1
}
refuse() {
  echo "Refusing: $*" >&2
  exit 1
}

if [[ "${SERVICE_PORTAL_UPDATE_DELEGATED:-0}" == "1" || "${DSH_UPDATE_DELEGATED:-0}" == "1" ]]; then
  log "Running in delegated maintenance mode (job: ${SERVICE_PORTAL_UPDATE_JOB_ID:-unknown}, container: ${DSH_UPDATE_CONTAINER_NAME:-unknown})."
fi

# --- Verification before any change -----------------------------------------

command -v git >/dev/null 2>&1 || die "git is required but was not found."
command -v docker >/dev/null 2>&1 || die "docker is required but was not found."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 (docker compose) is required but unavailable."
docker info >/dev/null 2>&1 || die "The Docker daemon is not accessible from this environment."

[[ -f "$COMPOSE_FILE" ]] || die "Compose file not found: $COMPOSE_FILE"
[[ "$START_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "CIF_UPDATE_START_TIMEOUT must be a positive integer."
[[ -n "$EXPECTED_BRANCH" ]] || die "CIF_UPDATE_EXPECTED_BRANCH must not be empty."
[[ -n "$EXPECTED_REMOTE" ]] || die "CIF_UPDATE_EXPECTED_REMOTE must not be empty."

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Repository root is not a Git work tree: $REPO_ROOT"

if [[ -d "$REPO_ROOT/.git" ]]; then
  LOCK_DIR="$REPO_ROOT/.git/service-portal-update.lock"
else
  LOCK_DIR="$REPO_ROOT/.service-portal-update.lock"
fi

# --- Atomic per-checkout lock ------------------------------------------------

LOCK_HELD=0
cleanup_lock() {
  trap - EXIT INT TERM
  if [[ "$LOCK_HELD" == "1" ]]; then
    rm -rf -- "$LOCK_DIR"
  fi
}
acquire_lock() {
  local holder_pid holder_container stale_reason=""
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    LOCK_HELD=1
    return 0
  fi
  holder_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
  holder_container=$(cat "$LOCK_DIR/container" 2>/dev/null || true)
  if [[ "${CIF_UPDATE_FORCE_UNLOCK:-0}" == "1" ]]; then
    stale_reason="CIF_UPDATE_FORCE_UNLOCK=1 operator override"
  elif [[ -n "${DSH_UPDATE_CONTAINER_NAME:-}" \
      && -n "$holder_container" \
      && "$holder_container" != "${DSH_UPDATE_CONTAINER_NAME:-}" ]]; then
    # A different maintenance container is recorded as the holder, so this
    # lock cannot belong to the current job (each portal job gets a fresh
    # container name).
    stale_reason="held by a different maintenance container ($holder_container)"
  elif [[ -n "$holder_pid" ]] && ! kill -0 "$holder_pid" 2>/dev/null; then
    stale_reason="holder pid $holder_pid is not running"
  fi
  if [[ -n "$stale_reason" ]]; then
    log "Removing stale update lock ($stale_reason)."
    rm -rf -- "$LOCK_DIR"
    if ! mkdir "$LOCK_DIR"; then
      die "Could not recreate the update lock after removing a stale lock: $LOCK_DIR"
    fi
    LOCK_HELD=1
  else
    die "Another update is already running (lock: $LOCK_DIR). If the other update is dead, remove that directory or re-run with CIF_UPDATE_FORCE_UNLOCK=1."
  fi
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  printf '%s\n' "${DSH_UPDATE_CONTAINER_NAME:-local}" > "$LOCK_DIR/container"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$LOCK_DIR/started"
}

trap cleanup_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

acquire_lock
log "Update lock acquired ($LOCK_DIR)."

compose=(docker compose -f "$COMPOSE_FILE")

# The currently committed configuration must be valid before the update
# touches anything.
"${compose[@]}" config --quiet \
  || die "Current Compose configuration is invalid: $COMPOSE_FILE"

# --- Source verification ------------------------------------------------------

BRANCH=$(git branch --show-current)
[[ -n "$BRANCH" ]] || refuse "Detached HEAD; updates require a checked-out branch."
[[ "$BRANCH" == "$EXPECTED_BRANCH" ]] \
  || refuse "Current branch '$BRANCH' does not match expected branch '$EXPECTED_BRANCH'."

git remote get-url origin >/dev/null 2>&1 \
  || refuse "Git remote 'origin' is not configured."
REMOTE_URL=$(git remote get-url origin)
[[ "$REMOTE_URL" == "$EXPECTED_REMOTE" ]] \
  || refuse "Remote 'origin' is '$REMOTE_URL'; expected '$EXPECTED_REMOTE'."

UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) \
  || refuse "Branch '$BRANCH' has no upstream branch; configure one before updating."
[[ "$UPSTREAM" == "origin/$BRANCH" ]] \
  || refuse "Upstream for '$BRANCH' is '$UPSTREAM'; expected 'origin/$BRANCH'."

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  refuse "The working tree is not clean. Commit, stash, or remove local changes before updating."
fi

# --- Fast-forward-only update ---------------------------------------------------

log "Fetching origin/$EXPECTED_BRANCH..."
git fetch origin "$EXPECTED_BRANCH"

git merge-base --is-ancestor HEAD "origin/$EXPECTED_BRANCH" \
  || refuse "Local branch '$BRANCH' has diverged from origin/$EXPECTED_BRANCH or its history was rewritten; fast-forward is not possible."

if ! git merge --ff-only "origin/$EXPECTED_BRANCH"; then
  refuse "Fast-forward-only merge of origin/$EXPECTED_BRANCH failed."
fi

log "Updated $BRANCH from origin/$EXPECTED_BRANCH."

# Validate the newly pulled configuration before building or recreating
# anything.
"${compose[@]}" config --quiet \
  || die "Updated Compose configuration is invalid: $COMPOSE_FILE"

# --- Prepare the replacement while the current app stays up ---------------------

# The image is built locally, so the build (which also pulls its base
# images) is the replacement preparation step. The running service is not
# stopped until the final reconcile below.
log "Building replacement image(s) while the current application remains available..."
"${compose[@]}" build || die "Compose build of the replacement image failed."

# --- Reconcile the complete project with a bounded health wait -------------------

log "Reconciling the Compose project with a bounded wait of ${START_TIMEOUT}s..."
UP_OK=0
if "${compose[@]}" up -d --wait --wait-timeout "$START_TIMEOUT"; then
  UP_OK=1
fi
if [[ "$UP_OK" != "1" ]]; then
  echo "Error: initial compose up failed; attempting one recovery pass with the last built image." >&2
  if "${compose[@]}" up -d --no-build --wait --wait-timeout "$START_TIMEOUT"; then
    UP_OK=1
  fi
fi
if [[ "$UP_OK" != "1" ]]; then
  die "Update failed and recovery could not confirm a healthy application. Inspect: docker compose -f $COMPOSE_FILE logs"
fi

# --- Final gates ------------------------------------------------------------------

log "Verifying explicit ComfyUI runtime configuration..."
if ! "${compose[@]}" exec -T "$SERVICE" python -c \
  'from app.config import get_settings; raise SystemExit(0 if get_settings().comfyui_instance_configuration_mode == "explicit" else 1)'; then
  die "Frontend started without an explicit ComfyUI runtime configuration."
fi

# The lock is released by the EXIT trap.
log "$SERVICE is updated and healthy on $BRANCH."
