# Task entry points (brief 6.2). Windows uses PowerShell; recipes are plain `uv run ...` lines.
# Install just:  uv tool install rust-just
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
set dotenv-load := false

default:
    @just --list

# --- setup -----------------------------------------------------------------
bootstrap:
    uv sync --all-packages

hooks:
    uv run pre-commit install

# --- database (private PostgreSQL on port 54329; see scripts/dev_db.py) -----
db-up:
    uv run python scripts/dev_db.py up

db-down:
    uv run python scripts/dev_db.py down

db-reset:
    uv run python scripts/dev_db.py reset

migrate:
    uv run alembic -c packages/persistence/alembic.ini upgrade head

# --- run ------------------------------------------------------------------
# Windows needs a selector event loop for async Postgres: always start through the module entry point.
dev:
    uv run python -m mde_context_api --port 8100

worker:
    uv run python -m mde_context_api.worker

gateway-dev:
    uv run uvicorn mde_llm_gateway_api.main:app --port 8101

# Compass (the first agent): standalone app and test UI at http://127.0.0.1:8110/ui/
compass-dev:
    uv run python -m mde_compass --port 8110

compass-migrate:
    uv run python -m mde_compass.store.migrate

compass-test:
    uv run pytest agents/compass -m "not integration and not live" -q

compass-test-int: db-up
    uv run pytest agents/compass -m integration -q

# Research (the second agent): the standalone app (API and front end on 127.0.0.1:8120, live only)
research-dev:
    uv run python -m mde_research_agent --port 8120

research-app-start:
    uv run python scripts/research_app.py start

research-app-stop:
    uv run python scripts/research_app.py stop

research-app-restart:
    uv run python scripts/research_app.py restart

research-app-status:
    uv run python scripts/research_app.py status

research-app-logs:
    uv run python scripts/research_app.py logs

research-migrate:
    uv run python -m mde_research_agent.store.migrate

research-test:
    uv run pytest agents/research -m "not integration and not live" -q

research-test-int: db-up
    uv run pytest agents/research -m integration -q

# --- quality --------------------------------------------------------------
lint:
    uv run ruff check .
    uv run ruff format --check .

format:
    uv run ruff check --fix .
    uv run ruff format .

typecheck:
    uv run pyright

layers:
    uv run lint-imports

# --- tests ----------------------------------------------------------------
test:
    uv run pytest -m "not integration and not e2e and not live" -q

test-int: db-up
    uv run pytest -m integration -q

test-e2e: db-up
    uv run pytest -m e2e -q

scan-secrets:
    uv run pytest tests/security -q

# Live batches: the lead / live-runner only. Needs ALLOW_LIVE_LLM=1 in the OUTER environment,
# otherwise every live test skips itself (see root conftest.py).
live-smoke:
    uv run pytest tests/live -m live -k smoke -q

# BATCH is L1, L2, L3 or L4 (see docs/evals/eval-plan.md); lead only, needs the live switches.
live-eval BATCH:
    uv run python -m evals.runners.live_batch --batch {{BATCH}}

# Offline eval run (mock mode, writes a report); free.
eval-mock:
    uv run python -m evals.runners.mock_batch

budget:
    uv run python -m mde_llm_gateway.budget_cli

# --- docs / schemas ---------------------------------------------------------
export-schemas:
    uv run python scripts/export_schemas.py

gen-docs:
    uv run python scripts/gen_prompt_catalog.py
    uv run python scripts/export_schemas.py

docs-check:
    uv run python scripts/docs_check.py

# --- aggregates -----------------------------------------------------------
ci-fast: lint typecheck layers test

ci: ci-fast test-int test-e2e docs-check scan-secrets
