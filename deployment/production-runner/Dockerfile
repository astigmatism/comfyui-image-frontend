FROM docker:28-cli
RUN apk add --no-cache bash git docker-compose openssl python3 curl tar
ARG RUNNER_REVISION
LABEL org.opencontainers.image.revision=$RUNNER_REVISION
ENV DOCKER_CONFIG=/tmp/cif-docker PYTHONDONTWRITEBYTECODE=1 COMPOSE_DISABLE_ENV_FILE=1
COPY scripts/production-update.py scripts/backup-production-bind.py scripts/launch-production-update.py /opt/cif/
COPY update_production update_production_portal /opt/cif/
WORKDIR /tmp
ENTRYPOINT ["python3", "-u", "/opt/cif/production-update.py"]
