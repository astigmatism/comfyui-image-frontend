#!/bin/sh
set -eu

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required for the container smoke test." >&2
  exit 2
}

IMAGE=${CIF_SMOKE_IMAGE:-comfyui-image-frontend:smoke}
NAME="cif-smoke-$$"
DATA_VOLUME="${NAME}-data"
PORT=${CIF_SMOKE_PORT:-18080}
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm -f "$DATA_VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker build -t "$IMAGE" .
docker volume create "$DATA_VOLUME" >/dev/null
docker run -d --name "$NAME" \
  -p "127.0.0.1:${PORT}:8000" \
  -v "$DATA_VOLUME:/data" \
  -e CIF_SESSION_SECRET=smoke-session-secret-0123456789-abcdef \
  -e CIF_BOOTSTRAP_ADMIN_USERNAME=smoke-admin \
  -e CIF_BOOTSTRAP_ADMIN_TEMPORARY_PASSWORD=SmokeTemporary1234 \
  -e CIF_COMFYUI_BASE_URL=http://127.0.0.1:9 \
  "$IMAGE" >/dev/null

attempt=0
while [ "$attempt" -lt 40 ]; do
  if python3 - "$PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request
port = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
    payload = json.load(response)
assert response.status == 200 and payload["database"] is True
PY
  then
    echo "Container startup smoke test passed on port ${PORT}."
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 0.5
done

docker logs "$NAME" >&2
exit 1
