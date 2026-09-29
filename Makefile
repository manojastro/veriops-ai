# VeriOps AI — developer commands
PY ?= .venv/bin/python
WEB := apps/web

.PHONY: help install up down logs demo demo-offline test test-unit test-integration test-critical eval lint format typecheck security web-build web-test e2e migrate clean

help:            ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install:         ## Create venv and install backend + frontend dependencies
	uv venv --python 3.12 .venv && uv pip install --python $(PY) -e ".[llm,dev]"
	cd $(WEB) && npm ci

up:              ## Build and start the full stack (http://localhost:3000)
	docker compose up -d --build

down:            ## Stop the stack
	docker compose down

logs:            ## Tail API and worker logs
	docker compose logs -f api worker

demo:            ## Trigger the flagship incident against the running stack
	$(PY) scripts/demo_incident.py

demo-offline:    ## Run the flagship end to end in-process (no infrastructure)
	$(PY) scripts/demo_incident.py --offline --approve

migrate:         ## Apply database migrations
	$(PY) -m alembic upgrade head

test: test-unit test-critical eval  ## Unit tests + safety-critical tests + evaluation gates

test-unit:       ## Unit + evaluation tests (no infrastructure)
	$(PY) -m pytest tests/unit tests/evals

test-integration: ## API tests against PostgreSQL (DATABASE_URL)
	$(PY) -m pytest tests/integration

test-critical:   ## Safety-critical tests only
	$(PY) -m pytest -m critical

eval:            ## Run the agent evaluation suite (fails when a gate fails)
	$(PY) -m veriops.evaluation.cli --output reports/evaluation.json

lint:            ## Ruff lint + format check
	$(PY) -m ruff check src tests scripts && $(PY) -m ruff format --check src tests scripts

format:          ## Auto-format
	$(PY) -m ruff format src tests scripts && $(PY) -m ruff check --fix src tests scripts

typecheck:       ## mypy + tsc
	$(PY) -m mypy
	cd $(WEB) && npx tsc --noEmit

security:        ## Bandit + pip-audit + npm audit
	$(PY) -m bandit -q -r src -c pyproject.toml
	$(PY) -m pip_audit --skip-editable || true
	cd $(WEB) && npm audit --omit=dev --audit-level=high

web-build:       ## Production build of the web console
	cd $(WEB) && npm run build

web-test:        ## Frontend unit tests
	cd $(WEB) && npm test

e2e:             ## Browser end-to-end demo (requires `make up`)
	cd $(WEB) && npx playwright test

clean:
	rm -rf reports .pytest_cache .ruff_cache .mypy_cache $(WEB)/.next $(WEB)/test-results
