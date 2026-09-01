# syntax=docker/dockerfile:1.7
#
# Local Service Portal maintenance runner for this repository.
#
# Service Portal never pulls or builds this image; it must already exist on
# the Docker host when the "Update and restart" button is pressed. Build it
# once on the host (any tag is fine as long as PROJECT_RUNNER_IMAGE in the
# deployment .env matches):
#
#   docker build -t comfyui-image-frontend-portal-runner:latest deployment/runner
#
# It provides exactly the toolchain scripts/update-and-restart.sh needs:
# bash, Git, the Docker CLI, and the Compose v2 plugin. It contains no
# application code and no secrets. If the Git remote were private, provision
# noninteractive least-privilege credentials inside this image (or in the
# host checkout) without committing them to source or exposing them to the
# browser.

FROM docker:28-cli

RUN apk add --no-cache bash git docker-compose

# The portal overrides the working directory with the mounted project
# directory; /tmp keeps the image default tidy (the portal sets HOME=/tmp).
WORKDIR /tmp
