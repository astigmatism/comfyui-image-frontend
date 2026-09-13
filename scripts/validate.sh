#!/bin/sh
set -eu

STRICT=${VALIDATE_STRICT:-1}
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

have_module() {
  python3 -c "import $1" >/dev/null 2>&1
}

optional_python_check() {
  module=$1
  shift
  if have_module "$module"; then
    "$@"
  elif [ "$STRICT" = "1" ]; then
    echo "Missing required Python module: $module" >&2
    exit 2
  else
    echo "SKIP: $module is not installed in this environment." >&2
  fi
}

optional_python_check ruff python3 -m ruff format --check backend/app backend/tests comfyui_extension
optional_python_check ruff python3 -m ruff check backend/app backend/tests comfyui_extension
optional_python_check mypy env PYTHONPATH=backend python3 -m mypy backend/app
python3 scripts/generate_traceability.py --check
python3 -m compileall -q backend/app comfyui_extension
PYTHONPATH=backend python3 -m pytest -q
(
  cd frontend
  node scripts/format-check.mjs
  node scripts/lint.mjs
  node --test test/*.test.mjs
  node scripts/build.mjs
)

if [ -x frontend/node_modules/.bin/playwright ]; then
  (cd frontend && ./node_modules/.bin/playwright test)
  # TLS-edge e2e (second Playwright project). The runner (scripts/e2e-tls-stack.sh)
  # starts the real Compose stack when a Docker daemon is reachable, otherwise a
  # local Caddy in front of the in-process app. Same degrade behavior as above.
  (cd frontend && ./node_modules/.bin/playwright test -c playwright.tls.config.mjs)
elif [ "$STRICT" = "1" ]; then
  echo "Missing Playwright installation. Run 'cd frontend && npm install && npx playwright install chromium'." >&2
  exit 2
else
  echo "SKIP: Playwright package/browser is unavailable in this environment." >&2
fi

# The container smoke test needs a reachable Docker daemon. In strict mode that
# is required; in validate-available it degrades like the other optional steps
# (a present CLI with an unreachable daemon must not hard-fail the run).
if [ "$STRICT" = "1" ]; then
  command -v docker >/dev/null 2>&1 || { echo "Missing required command: docker" >&2; exit 2; }
  docker info >/dev/null 2>&1 || { echo "Docker daemon is not reachable." >&2; exit 2; }
  ./scripts/container-smoke.sh
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ./scripts/container-smoke.sh
else
  echo "SKIP: Docker daemon not available; container smoke test skipped." >&2
fi
