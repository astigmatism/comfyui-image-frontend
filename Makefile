SHELL := /bin/sh
PYTHON ?= python3
NODE ?= node

.PHONY: install-dev format-check lint typecheck test test-backend test-frontend test-deployment build traceability container-smoke e2e e2e-tls validate validate-available clean

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'
	cd frontend && npm install

format-check:
	$(PYTHON) -m ruff format --check backend/app backend/tests comfyui_extension
	cd frontend && $(NODE) scripts/format-check.mjs

lint:
	$(PYTHON) -m ruff check backend/app backend/tests comfyui_extension
	cd frontend && $(NODE) scripts/lint.mjs

typecheck:
	PYTHONPATH=backend $(PYTHON) -m mypy backend/app

test-backend:
	PYTHONPATH=backend $(PYTHON) -m pytest -q

test-frontend:
	cd frontend && $(NODE) --test test/*.test.mjs

test-deployment:
	$(PYTHON) -m unittest discover -s scripts/tests -q

test: test-backend test-frontend test-deployment

build:
	$(PYTHON) -m compileall -q backend/app comfyui_extension
	cd frontend && $(NODE) scripts/build.mjs
	$(PYTHON) -m build --wheel --no-isolation

traceability:
	$(PYTHON) scripts/generate_traceability.py --check

container-smoke:
	./scripts/container-smoke.sh

e2e:
	cd frontend && npx playwright test

# TLS-edge e2e (second Playwright project). The runner brings up the real
# Compose stack when a Docker daemon is reachable, otherwise a local Caddy in
# front of the in-process app, then runs tls-edge.spec.mjs over https://.
e2e-tls:
	cd frontend && npx playwright test -c playwright.tls.config.mjs

validate: format-check lint typecheck traceability test build e2e e2e-tls container-smoke

validate-available:
	VALIDATE_STRICT=0 ./scripts/validate.sh

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	rm -rf frontend/dist frontend/playwright-report frontend/test-results
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
