# Single-service deployment image: builds the React bundle, then serves it from
# FastAPI so the whole app is one container, one port and one origin.
# Local development still uses the two-service docker-compose.yml.

# ============= Stage 1: build the frontend =============
FROM node:20-slim AS frontend

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ============= Stage 2: backend + bundled frontend =============
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# StaticFiles picks this up automatically via STATIC_DIR
COPY --from=frontend /build/dist ./static

RUN mkdir -p /app/data

# PORT is injected by most PaaS platforms; default keeps local runs unchanged
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
