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
  -e CIF_COMFYUI_INSTANCE_ID=smoke-primary \
  -e CIF_COMFYUI_BASE_URL=http://127.0.0.1:9 \
  "$IMAGE" >/dev/null

attempt=0
ready=0
while [ "$attempt" -lt 40 ]; do
  if python3 - "$PORT" <<'PY' >/dev/null 2>&1
import json
import re
import sys
import urllib.request
from urllib.parse import urljoin

port = sys.argv[1]
origin = f"http://127.0.0.1:{port}"
with urllib.request.urlopen(f"{origin}/api/health", timeout=1) as response:
    payload = json.load(response)
assert response.status == 200 and payload["database"] is True
assert payload["worker"]["ready"] is True
assert payload["worker"]["dispatcher_running"] is True
assert payload["worker"]["heartbeat_fresh"] is True

with urllib.request.urlopen(f"{origin}/", timeout=1) as response:
    html = response.read().decode("utf8")
    assert response.headers.get("Cache-Control") == "no-cache, must-revalidate"
entrypoints = re.findall(
    r'(?:src|href)="(/assets/[0-9a-f]{64}/(?:app\.mjs|styles\.css))"', html
)
assert len(entrypoints) == 2
for path in entrypoints:
    with urllib.request.urlopen(f"{origin}{path}", timeout=1) as response:
        body = response.read().decode("utf8")
        assert response.headers.get("Cache-Control") == "public, max-age=31536000, immutable"
    if path.endswith("app.mjs"):
        app_url = f"{origin}{path}"
        assert 'from "./render.mjs"' in body
        with urllib.request.urlopen(urljoin(app_url, "render.mjs"), timeout=1) as response:
            render = response.read().decode("utf8")
            assert "activeSourceModelChoicesMarkup" in render
            assert response.headers.get("Cache-Control") == "public, max-age=31536000, immutable"
        with urllib.request.urlopen(urljoin(app_url, "lib.mjs"), timeout=1) as response:
            library = response.read().decode("utf8")
            assert "projectedParameterIds" in library
            assert response.headers.get("Cache-Control") == "public, max-age=31536000, immutable"

with urllib.request.urlopen(f"{origin}/build.json", timeout=1) as response:
    build = json.load(response)
    assert response.headers.get("Cache-Control") == "no-cache, must-revalidate"
assert build["asset_version"] in entrypoints[0]
PY
  then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 0.5
done

if [ "$ready" -ne 1 ]; then
  docker logs "$NAME" >&2
  exit 1
fi

# Exercise the actual image settings path. The smoke container supplies only the
# legacy primary URL; the image must still contribute the configured worker.
docker exec "$NAME" python -c '
from app.config import get_settings
settings = get_settings()
instances = settings.configured_comfyui_instances
assert settings.comfyui_instance_configuration_mode == "explicit"
assert [item.id for item in instances] == ["smoke-primary", "worker-2"]
assert instances[0].base_url == "http://127.0.0.1:9"
assert instances[1].label == "Worker 1 · RTX 3080"
assert instances[1].base_url == "http://192.168.1.21:8189"
'

# A full runtime list supplied by an operator remains authoritative over the
# bundled additional-worker default.
docker run --rm --entrypoint python \
  -e CIF_TEST_MODE=true \
  -e 'CIF_COMFYUI_INSTANCES=[{"id":"custom","label":"Custom","base_url":"http://127.0.0.1:9"}]' \
  "$IMAGE" -c '
from app.config import get_settings
settings = get_settings()
assert [item.id for item in settings.configured_comfyui_instances] == ["custom"]
'

# An explicit empty additional list is the deliberate single-runtime opt-out.
docker run --rm --entrypoint python \
  -e CIF_TEST_MODE=true \
  -e CIF_COMFYUI_INSTANCE_ID=intentional-single \
  -e 'CIF_COMFYUI_ADDITIONAL_INSTANCES=[]' \
  "$IMAGE" -c '
from app.config import get_settings
settings = get_settings()
assert settings.comfyui_instance_configuration_mode == "explicit"
assert [item.id for item in settings.configured_comfyui_instances] == ["intentional-single"]
'

echo "Container startup and two-runtime configuration smoke tests passed on port ${PORT}."
