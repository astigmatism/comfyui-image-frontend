SHELL := /bin/sh
PYTHON ?= python3
NODE ?= node

.PHONY: install-dev format-check lint typecheck test test-backend test-frontend build traceability container-smoke e2e validate validate-available clean

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'
	cd frontend && npm install

format-check:
	$(PYTHON) -m ruff format --check backend/app backend/tests
	cd frontend && $(NODE) scripts/format-check.mjs

lint:
	$(PYTHON) -m ruff check backend/app backend/tests
	cd frontend && $(NODE) scripts/lint.mjs

typecheck:
	PYTHONPATH=backend $(PYTHON) -m mypy backend/app

test-backend:
	PYTHONPATH=backend $(PYTHON) -m pytest -q

test-frontend:
	cd frontend && $(NODE) --test test/*.test.mjs

test: test-backend test-frontend

build:
	$(PYTHON) -m compileall -q backend/app
	cd frontend && $(NODE) scripts/build.mjs
	$(PYTHON) -m build --wheel --no-isolation

traceability:
	$(PYTHON) scripts/generate_traceability.py --check

container-smoke:
	./scripts/container-smoke.sh

e2e:
	cd frontend && npx playwright test

validate: format-check lint typecheck traceability test build e2e container-smoke

validate-available:
	VALIDATE_STRICT=0 ./scripts/validate.sh

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	rm -rf frontend/dist frontend/playwright-report frontend/test-results
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
