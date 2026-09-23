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
# This image is for checkout-based installations only. Samus uses the pinned
# deployment/production-runner/Dockerfile image documented in
# docs/production-service-portal.md.
#
# It provides Bash, Python 3, Git, the
# Docker CLI, the Compose v2 plugin, and OpenSSL (used by
# scripts/issue-local-cert.sh to keep the appliance-local TLS leaf current
# before the edge is restarted). It contains no application code and no
# secrets. If the Git remote were private, provision noninteractive
# least-privilege credentials inside this image (or in the host checkout)
# without committing them to source or exposing them to the browser.
#
# If you change this Dockerfile, rebuild the runner image once on the host
# (the command above) so the next "Update and restart" picks it up.

FROM docker:28-cli

RUN apk add --no-cache bash git docker-compose openssl python3

# The portal overrides the working directory with the mounted project
# directory; /tmp keeps the image default tidy (the portal sets HOME=/tmp).
WORKDIR /tmp
