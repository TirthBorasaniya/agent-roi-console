# Agent ROI Console. Caches and temp files stay inside the repo (.cache, .tmp).
SHELL := /bin/bash
export UV_CACHE_DIR := $(CURDIR)/.cache/uv
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.cache/python
export PYTHONPATH := $(CURDIR)/backend/src:$(CURDIR)
PY := uv run --frozen python
COMPOSE := docker compose

.PHONY: setup lint format test test-integration test-live up down logs demo eval benchmark \
	loadtest results clean tasks reset-sandbox

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

# ============= Housekeeping =============
clean:
	rm -rf .pytest_cache .ruff_cache .tmp/* frontend/dist
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
