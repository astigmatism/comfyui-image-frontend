# Agent instructions — ComfyUI Image Frontend

These rules apply to every person or AI agent changing this repository.

## LLM Router contract

- Router integration (prompt assistant and vision requests, model selection, discovery,
  retries) must uphold [`docs/llm-router-contract.md`](docs/llm-router-contract.md). Update its
  conformance map ("How ComfyUI Image Frontend upholds this contract", at the end of that file)
  with any such change.
- Never edit the vendored contract text; replace it only when the LLM Router maintainer
  announces a new version. The copy starts with a "Vendored copy — do not edit" header; the
  conformance map after the contract text is this project's own and must stay current.
- The router-facing code is `backend/app/services/llm_router.py` (discovery, selection, error
  classification, the event-stream subscriber) and `backend/app/services/ollama.py` (requests).
  Its tests are `backend/tests/unit/test_llm_router.py`, `backend/tests/unit/test_ollama_router.py`,
  and `backend/tests/integration/test_llm_router_contract.py`; the fake router lives in
  `backend/tests/fake_services.py` and `backend/tests/router_fixtures.py`.
- Send service IDs (`daytime`, `nighttime`) only; never configure, persist, or compare canonical
  model IDs. Do not modify the LLM Router or AI Runtime from this repository, and never switch
  AI Runtime configurations to test.

## Validation

- [`docs/testing.md`](docs/testing.md) describes every suite, the fake services, and the opt-in
  live suites (the live LLM Router suite may be run read-only against `http://192.168.1.4:11434`).
- Run `PYTHONPATH=backend python3 -m pytest -q` for the backend and `make validate` for the full
  release gate (formatting, lint, mypy, traceability, all tests, build, Playwright, container
  smoke). `make validate-available` runs what the environment supports and prints skips.
- After changing router prose in `scripts/generate_traceability.py`, regenerate
  `docs/traceability.md` with `python3 scripts/generate_traceability.py`, and confirm with
  `python3 scripts/generate_traceability.py --check`.

## Deployment

- Develop in this checkout, test, and push `main`. Production runs on Samus (192.168.1.5) and is
  updated only by its documented process: [`docs/production-deployment-agent.md`](docs/production-deployment-agent.md),
  `update_production`, or the Samus Service Portal "Update and restart". Deploy only when the
  owner asks.
- Do not commit generated or stale local artifacts such as `frontend/.dist-stale-root*`,
  `frontend/.playwright-report-stale-root*`, or `frontend/.test-results-stale-root*`.
