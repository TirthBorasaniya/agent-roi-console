# Application image for the console API, gateway, MCP servers, and scripts.
# One image, several compose services: each service picks its command.

# ============= Stage 1: Python environment =============
FROM python:3.11.13-slim AS app

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app/backend/src:/app \
    PATH=/opt/venv/bin:$PATH \
    ROI_ENV_FILE=

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/src ./backend/src
COPY configs ./configs
COPY eval ./eval
COPY scripts ./scripts
COPY results ./results
COPY tests ./tests

RUN mkdir -p /app/data /app/models

EXPOSE 8000
CMD ["python", "-m", "roiconsole.mcp_servers.slack_server", "--transport", "streamable-http", "--host", "0.0.0.0"]
