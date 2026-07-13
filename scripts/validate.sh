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

optional_command() {
  name=$1
  shift
  if command -v "$name" >/dev/null 2>&1; then
    "$@"
  elif [ "$STRICT" = "1" ]; then
    echo "Missing required command: $name" >&2
    exit 2
  else
    echo "SKIP: $name is not installed in this environment." >&2
  fi
}

optional_python_check ruff python3 -m ruff format --check backend/app backend/tests
optional_python_check ruff python3 -m ruff check backend/app backend/tests
optional_python_check mypy env PYTHONPATH=backend python3 -m mypy backend/app
python3 scripts/generate_traceability.py --check
python3 -m compileall -q backend/app
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
elif [ "$STRICT" = "1" ]; then
  echo "Missing Playwright installation. Run 'cd frontend && npm install && npx playwright install chromium'." >&2
  exit 2
else
  echo "SKIP: Playwright package/browser is unavailable in this environment." >&2
fi

optional_command docker ./scripts/container-smoke.sh
