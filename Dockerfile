# Application image for the console API (with the built dashboard), gateway, MCP
# servers, and scripts. One image, several compose services: each picks its command.

# ============= Stage 1: dashboard bundle =============
FROM node:20.19.4-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ============= Stage 2: Python environment =============
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
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p /app/data /app/models

EXPOSE 8000
CMD ["python", "-m", "roiconsole.mcp_servers.slack_server", "--transport", "streamable-http", "--host", "0.0.0.0"]
