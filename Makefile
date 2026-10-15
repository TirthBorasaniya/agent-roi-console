# Agent ROI Console. Caches and temp files stay inside the repo (.cache, .tmp).
SHELL := /bin/bash
export UV_CACHE_DIR := $(CURDIR)/.cache/uv
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.cache/python
export PYTHONPATH := $(CURDIR)/backend/src:$(CURDIR)
PY := uv run --frozen python
COMPOSE := docker compose

.PHONY: setup lint format test test-integration test-live up down logs demo eval benchmark \
	loadtest results clean tasks reset-sandbox seed-notion mcp-slack mcp-notion mcp-crm \
	up-crm up-selfhosted up-full seed-twenty reset-twenty check-twenty download-model \
	measure-selfhosted

# ============= Environment =============
setup:
	uv sync --frozen --python 3.11
	uv run --frozen pre-commit install
	cd frontend && npm ci

lint:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .

format:
	uv run --frozen ruff format .
	uv run --frozen ruff check --fix .

# ============= Tests =============
test:
	ROI_ENV_FILE= uv run --frozen pytest

test-integration:
	ROI_ENV_FILE= uv run --frozen pytest -m integration

test-live:
	uv run --frozen pytest -m live

# ============= Task Suite =============
tasks:
	$(PY) scripts/generate_task_suite.py

reset-sandbox:
	$(PY) scripts/reset_sandbox.py --task $(TASK)

# ============= Stack =============
up:
	$(COMPOSE) up -d --build

up-crm:
	$(COMPOSE) --profile crm up -d --build

up-selfhosted:
	$(COMPOSE) --profile selfhosted up -d --build

up-full:
	$(COMPOSE) --profile crm --profile selfhosted up -d --build

down:
	$(COMPOSE) --profile crm --profile selfhosted down

logs:
	$(COMPOSE) logs -f --tail=200

# ============= Twenty and Self-Hosted Model =============
seed-twenty:
	$(PY) scripts/seed_twenty.py

reset-twenty:
	$(PY) scripts/reset_twenty.py --task $(TASK)

check-twenty:
	$(PY) scripts/check_twenty_schema.py

download-model:
	$(PY) scripts/download_model.py

measure-selfhosted:
	$(PY) scripts/measure_selfhosted.py

# ============= Connectors =============
seed-notion:
	$(PY) scripts/seed_notion.py

mcp-slack:
	$(PY) -m roiconsole.mcp_servers.slack_server --transport streamable-http --port 8101

mcp-notion:
	$(PY) -m roiconsole.mcp_servers.notion_server --transport streamable-http --port 8102

mcp-crm:
	$(PY) -m roiconsole.mcp_servers.crm_server --transport streamable-http --port 8103

# ============= Housekeeping =============
clean:
	rm -rf .pytest_cache .ruff_cache .tmp/* frontend/dist
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
