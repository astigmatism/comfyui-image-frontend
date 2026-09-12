#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

preview_name="cif-gallery-preview"
preview_image="comfyui-image-frontend:gallery-preview"
preview_assets="$(pwd)/backend/data/gallery-preview-input"
mkdir -p "$preview_assets"
docker build -t "$preview_image" .
if docker container inspect "$preview_name" >/dev/null 2>&1; then
  if [[ "$(docker inspect --format '{{index .Config.Labels "comfyui.preview"}}' "$preview_name")" != gallery-selection ]]; then
    printf 'Container name is already in use by another service.\n' >&2
    exit 1
  fi
  docker stop "$preview_name" >/dev/null
  docker rm "$preview_name" >/dev/null
fi
docker run -d --name "$preview_name" \
  --label comfyui.preview=gallery-selection \
  -p 127.0.0.1:8765:8000 \
  --mount type=volume,source=cif-gallery-preview-data,target=/data \
  --mount "type=bind,source=$preview_assets,target=/preview-assets,readonly" \
  "$preview_image" python -m tests.gallery_preview
printf 'Preview: http://127.0.0.1:8765\nLogin: preview / GalleryPreview123!\n'
