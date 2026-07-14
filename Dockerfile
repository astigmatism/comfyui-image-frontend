# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/index.html frontend/package.json ./
COPY frontend/src ./src
COPY frontend/scripts ./scripts
RUN node scripts/build.mjs

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    CIF_DATA_DIR=/data \
    CIF_DATABASE_PATH=/data/app.db \
    CIF_FRONTEND_DIST=/app/frontend/dist \
    CIF_LISTEN_HOST=0.0.0.0 \
    CIF_LISTEN_PORT=8000

RUN addgroup --system app && adduser --system --ingroup app --home /app app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir .
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
RUN mkdir -p /data && chown -R app:app /data /app
USER app
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3); p=json.load(r); assert r.status == 200 and p['database'] and p['worker']['ready']" || exit 1
CMD ["python", "-m", "app"]
