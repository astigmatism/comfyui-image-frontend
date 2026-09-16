FROM docker:28-cli
RUN apk add --no-cache bash git docker-compose openssl python3 curl tar
ENV DOCKER_CONFIG=/tmp/cif-docker PYTHONDONTWRITEBYTECODE=1
COPY scripts/production-update.py scripts/backup-production-bind.py /opt/cif/
WORKDIR /tmp
ENTRYPOINT ["python3", "-u", "/opt/cif/production-update.py"]
