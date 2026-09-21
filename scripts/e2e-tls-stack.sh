#!/usr/bin/env bash
#
# e2e-tls-stack.sh — start (up) / stop (down) the TLS edge for the Playwright
# "tls-edge" project.
#
# Two backends are supported, chosen automatically (or forced via
# CIF_E2E_TLS_MODE):
#
#   docker  The committed compose stack (compose.example.yml + the e2e overlay)
#           with the real, unprivileged cif-tls-edge service. Used when a Docker
#           daemon is reachable. Exercises the committed service, its
#           hardening, and the committed Caddyfile in-container.
#
#   local   No Docker. Runs the committed Caddyfile (with the certificate paths
#           re-pointed at the local cert dir and a loopback bind inserted so the
#           host run never listens on all interfaces) via a local caddy binary
#           in front of backend/tests/e2e_server.py, which runs the application
#           in-process against deterministic fakes. Exercises the same Caddyfile
#           logic, locally issued certificate, and secure-context behavior
#           without a daemon.
#
# The Playwright tls global setup calls `up`; the global teardown calls `down`.
# A state file under the repo records the backend, ports, and PIDs so `down`
# tears down exactly what `up` started.
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CADDYFILE="$ROOT/deployment/tls/Caddyfile"

STATE_DIR="${CIF_E2E_TLS_STATE_DIR:-$ROOT/.cache/e2e-tls-stack}"
STATE_FILE="$STATE_DIR/state.env"

log() { printf '[e2e-tls] %s\n' "$*" >&2; }
die() { log "Error: $*"; exit 1; }

usage() {
  cat >&2 <<'EOF'
Usage: e2e-tls-stack.sh <up|down>

  up    Start the TLS edge (compose stack if a daemon is reachable, otherwise a
        local Caddy in front of the in-process app), wait until the edge reports
        healthy, and record state for the Playwright project.
  down  Stop whatever `up` started and clear the recorded state.

Environment (all optional):
  CIF_E2E_TLS_MODE        "docker" | "local" | "auto" (default: auto)
  CIF_TLS_HOSTNAME        edge hostname (default: image-studio.lan)
  CIF_E2E_TLS_CERT_DIR    certificate dir (default: <state>/certificates)
  CIF_E2E_TLS_HOST_PORT   edge host port (default: 8443)
  CIF_E2E_CADDY_BIN       caddy binary for local mode
  CIF_E2E_START_TIMEOUT   health-wait budget in seconds (default: 120)
EOF
  exit 64
}

[ $# -eq 1 ] || usage
case "$1" in up) ;; down) ;; *) usage ;; esac
COMMAND="$1"

# --- helpers -------------------------------------------------------------

find_python() {
  if [ -n "${PYTHON:-}" ]; then printf '%s' "$PYTHON"
  elif [ -x "$ROOT/.venv/bin/python" ]; then printf '%s' "$ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then command -v python3
  else die "no python interpreter found"; fi
}

pick_free_port() {
  local py; py="$(find_python)"
  "$py" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

port_free() {
  local py; py="$(find_python)"
  # SO_REUSEADDR so a lingering TIME_WAIT socket (from a just-stopped edge) does
  # not look like an active listener: the bind only fails if something is
  # actually LISTENing on the port.
  "$py" -c 'import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
    s.close(); sys.exit(0)
except OSError:
    sys.exit(1)' "$1"
}

# Wait for a port to become free. A just-killed caddy can take a moment for the
# kernel to release its listening socket, so poll briefly before giving up.
wait_port_free() {
  local port="$1" tries="${2:-10}" i=0
  while [ "$i" -lt "$tries" ]; do
    if port_free "$port"; then return 0; fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

find_caddy() {
  if [ -n "${CIF_E2E_CADDY_BIN:-}" ] && [ -x "${CIF_E2E_CADDY_BIN}" ]; then
    printf '%s' "$CIF_E2E_CADDY_BIN"; return 0
  fi
  local cached="$ROOT/.cache/caddy/caddy"
  if [ -x "$cached" ]; then printf '%s' "$cached"; return 0; fi
  if command -v caddy >/dev/null 2>&1; then command -v caddy; return 0; fi
  # Last resort: download the pinned Caddy version into the gitignored cache.
  local ver="2.11.4" os arch url tmp
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) return 1 ;; esac
  case "$os" in linux|darwin) ;; *) return 1 ;; esac
  command -v curl >/dev/null 2>&1 || return 1
  url="https://github.com/caddyserver/caddy/releases/download/v$ver/caddy_${ver}_${os}_${arch}.tar_xz"
  tmp="$(mktemp -d)"
  log "no local caddy found; downloading v$ver ..."
  curl -fsSL "$url" -o "$tmp/caddy.tar.xz" || { rm -rf "$tmp"; return 1; }
  tar -xf "$tmp/caddy.tar.xz" -C "$tmp" caddy || { rm -rf "$tmp"; return 1; }
  mkdir -p "$ROOT/.cache/caddy"
  mv "$tmp/caddy" "$ROOT/.cache/caddy/caddy"
  chmod +x "$ROOT/.cache/caddy/caddy"
  rm -rf "$tmp"
  printf '%s' "$ROOT/.cache/caddy/caddy"
}

# Wait until the edge reports healthy over TLS. The locally issued root is
# validated here with --cacert: a broken or wrong chain fails the whole run.
wait_healthy() {
  local hostname="$1" edge_port="$2" cert_dir="$3"
  local ca="$cert_dir/ca.crt" url="https://$hostname:$edge_port/api/health"
  local timeout="${CIF_E2E_START_TIMEOUT:-120}" waited=0
  log "waiting up to ${timeout}s for $url ..."
  until curl -fsS --max-time 3 --cacert "$ca" --resolve "$hostname:$edge_port:127.0.0.1" "$url" >/dev/null 2>&1; do
    waited=$((waited + 1))
    if [ "$waited" -ge "$timeout" ]; then
      log "TLS edge did not become healthy within ${timeout}s (logs/state in $STATE_DIR)"
      return 1
    fi
    sleep 1
  done
  log "TLS edge is healthy"
}

save_state() {
  local mode="$1" hostname="$2" cert_dir="$3" edge_port="$4" app_port="$5" app_pid="$6" caddy_pid="$7"
  {
    printf 'MODE=%s\n' "$mode"
    printf 'HOSTNAME=%s\n' "$hostname"
    printf 'CERT_DIR=%s\n' "$cert_dir"
    printf 'EDGE_HOST_PORT=%s\n' "$edge_port"
    printf 'APP_HOST_PORT=%s\n' "$app_port"
    printf 'APP_PID=%s\n' "$app_pid"
    printf 'CADDY_PID=%s\n' "$caddy_pid"
  } > "$STATE_FILE"
  log "state written: mode=$mode edge=127.0.0.1:$edge_port app=127.0.0.1:$app_port"
}

# --- up ------------------------------------------------------------------

up_docker() {
  local hostname="$1" cert_dir="$2" edge_port="$3"
  command -v docker >/dev/null 2>&1 || die "docker CLI not found"
  docker info >/dev/null 2>&1 || die "docker daemon not reachable; re-run with CIF_E2E_TLS_MODE=local"

  local app_host_port
  app_host_port="$(pick_free_port)" || die "no free loopback port for the app"

  # Publish only to loopback; isolate the project and data volume. These bind
  # variables control the compose port bindings (see the overlay comments).
  export COMPOSE_PROJECT_NAME=cif-e2e-tls
  export CIF_BIND_IP=127.0.0.1
  export CIF_HOST_PORT="$app_host_port"
  export CIF_TLS_BIND_IP=127.0.0.1
  export CIF_TLS_HOST_PORT="$edge_port"
  export CIF_TLS_HOSTNAME="$hostname"
  export CIF_TLS_CERT_DIR="$cert_dir"

  local -a compose=(docker compose -f "$ROOT/compose.example.yml" -f "$ROOT/frontend/e2e/tls-edge.overlay.yml")
  log "building + starting compose stack (project cif-e2e-tls) ..."
  if ! "${compose[@]}" up -d --build --wait --wait-timeout "$(( ${CIF_E2E_START_TIMEOUT:-120} + 60 ))"; then
    "${compose[@]}" logs --no-color || true
    die "docker compose up failed"
  fi
  wait_healthy "$hostname" "$edge_port" "$cert_dir" || die "TLS edge unhealthy"
  save_state docker "$hostname" "$cert_dir" "$edge_port" "$app_host_port" "" ""
}

up_local() {
  local hostname="$1" cert_dir="$2" edge_port="$3"
  command -v curl >/dev/null 2>&1 || die "curl not found for local mode"
  command -v node >/dev/null 2>&1 || die "node not found for local mode (builds the frontend)"
  local caddy_bin py
  caddy_bin="$(find_caddy)" || die "no caddy binary available (set CIF_E2E_CADDY_BIN)"
  py="$(find_python)"

  wait_port_free "$edge_port" 10 || die "edge port $edge_port is in use; set CIF_E2E_TLS_HOST_PORT"
  local app_port
  app_port="$(pick_free_port)" || die "no free loopback port for the app"

  mkdir -p "$STATE_DIR"
  local app_log="$STATE_DIR/e2e_server.log" caddy_log="$STATE_DIR/caddy.log"
  local local_caddyfile="$STATE_DIR/Caddyfile.local"

  log "building frontend ..."
  ( cd "$ROOT/frontend" && node scripts/build.mjs ) >/dev/null || die "frontend build failed"

  log "starting in-process app on 127.0.0.1:$app_port ..."
  # `exec` replaces the subshell, so $! is the real python PID (not a wrapper),
  # which is what `down` later kills.
  (
    cd "$ROOT/frontend"
    exec env CIF_E2E_PORT="$app_port" PYTHONPATH="$ROOT/backend" "$py" "$ROOT/backend/tests/e2e_server.py"
  ) >"$app_log" 2>&1 &
  local app_pid=$!
  disown "$app_pid" 2>/dev/null || true

  # Derive a local Caddyfile from the committed one: certificate paths point at
  # the local cert dir, and a loopback bind is inserted so this host run never
  # listens on all interfaces. The site logic (address, tls, reverse_proxy,
  # flush) is unchanged.
  awk -v certdir="$cert_dir" '
    { gsub("/etc/caddy/certificates", certdir) }
    /^[ \t]*tls / { print "\tbind 127.0.0.1" }
    { print }
  ' "$CADDYFILE" >"$local_caddyfile"

  log "starting caddy ($caddy_bin) on 127.0.0.1:$edge_port -> 127.0.0.1:$app_port ..."
  # `exec` replaces the subshell, so $! is the real caddy PID (not a wrapper),
  # which is what `down` later kills.
  (
    cd "$ROOT"
    exec env CIF_TLS_HOSTNAME="$hostname" CIF_TLS_UPSTREAM="127.0.0.1:$app_port" \
      "$caddy_bin" run --config "$local_caddyfile" --adapter caddyfile
  ) >"$caddy_log" 2>&1 &
  local caddy_pid=$!
  disown "$caddy_pid" 2>/dev/null || true

  wait_healthy "$hostname" "$edge_port" "$cert_dir" || {
    log "--- e2e_server.log ---"; tail -n 40 "$app_log" 2>/dev/null || true
    log "--- caddy.log ---"; tail -n 40 "$caddy_log" 2>/dev/null || true
    die "TLS edge unhealthy"
  }
  save_state local "$hostname" "$cert_dir" "$edge_port" "$app_port" "$app_pid" "$caddy_pid"
}

do_up() {
  local mode="${CIF_E2E_TLS_MODE:-auto}"
  if [ "$mode" = "auto" ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      mode=docker
    else
      mode=local
    fi
  fi
  case "$mode" in docker|local) ;; *) die "CIF_E2E_TLS_MODE must be docker, local, or auto" ;; esac
  log "mode=$mode"

  # Tear down any prior state so `up` always starts from a clean slate.
  if [ -f "$STATE_FILE" ]; then
    log "found prior state; tearing it down first"
    do_down || true
  fi

  local hostname="${CIF_TLS_HOSTNAME:-image-studio.lan}"
  local cert_dir="${CIF_E2E_TLS_CERT_DIR:-$STATE_DIR/certificates}"
  case "$cert_dir" in /*) ;; *) cert_dir="$ROOT/$cert_dir" ;; esac
  local edge_port="${CIF_E2E_TLS_HOST_PORT:-8443}"

  mkdir -p "$STATE_DIR"
  # Issue/reuse the local root+leaf BEFORE starting anything (the same code
  # path the production update script uses).
  CIF_TLS_HOSTNAME="$hostname" CIF_TLS_CERT_DIR="$cert_dir" \
    "$SCRIPT_DIR/issue-local-cert.sh" >/dev/null || die "certificate issuance failed"

  case "$mode" in
    docker) up_docker "$hostname" "$cert_dir" "$edge_port" ;;
    local)  up_local "$hostname" "$cert_dir" "$edge_port" ;;
  esac
}

# --- down ----------------------------------------------------------------

do_down() {
  [ -f "$STATE_FILE" ] || { log "no state file; nothing to stop"; return 0; }
  # shellcheck disable=SC1090
  . "$STATE_FILE"
  case "${MODE:-local}" in
    docker)
      # -v removes the e2e-only data volume so every run starts from a fresh
      # database (mirroring the in-process server's temporary data dir).
      COMPOSE_PROJECT_NAME=cif-e2e-tls docker compose \
        -f "$ROOT/compose.example.yml" -f "$ROOT/frontend/e2e/tls-edge.overlay.yml" \
        down --remove-orphans --volumes --timeout 30 2>/dev/null || true
      ;;
    local)
      [ -n "${CADDY_PID:-}" ] && kill "$CADDY_PID" 2>/dev/null || true
      [ -n "${APP_PID:-}" ] && kill "$APP_PID" 2>/dev/null || true
      sleep 1
      [ -n "${CADDY_PID:-}" ] && kill -9 "$CADDY_PID" 2>/dev/null || true
      [ -n "${APP_PID:-}" ] && kill -9 "$APP_PID" 2>/dev/null || true
      ;;
  esac
  rm -f "$STATE_FILE"
  log "stopped (${MODE:-local})"
}

case "$COMMAND" in
  up) do_up ;;
  down) do_down ;;
esac
