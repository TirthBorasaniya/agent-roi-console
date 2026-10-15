# Architecture

## Processes

| Process | Command | Port | State |
|---|---|---|---|
| Console API and dashboard | `python -m roiconsole.api.app` | 8000 | SQLite console database |
| Gateway | `python -m roiconsole.gateway.app` | 8090 | Redis (budgets, caches, rate limits), SQLite `llm_calls` |
| slack-mcp, notion-mcp, crm-mcp | `python -m roiconsole.mcp_servers.<name>_server` | 8101 to 8103 | Live APIs, or the SQLite sandbox in dry-run and demo |
| Twenty (profile `crm`) | `twentycrm/twenty:v2.43.0` server and worker | 3000 | Postgres 16, Redis |
| llama.cpp (profile `selfhosted`) | `ghcr.io/ggml-org/llama.cpp:server-b11243` | 8081 | GGUF in the `models` volume |
| OTLP collector | `otel/opentelemetry-collector-contrib:0.130.0` | 4318 | none |

## One run

1. The API (or an evaluation script) calls `TaskRunner.run(task, path, model)`.
2. The runner resets the workspace through `ConnectorBackends.reset`, which posts fixture threads, recreates Notion pages, and recreates CRM records, returning concrete ids for the task's logical refs.
3. The path executes. Model calls go to the gateway with `workflow_id`, `run_id`, `step_id`, and `step_class`; tool calls go to the MCP servers with `run_id` and `step_id` in request `_meta` and an idempotency key for writes.
4. The runner snapshots everything created since the reset and runs the task's validators.
5. The run, its step traces, and its tool calls are written to SQLite; the gateway wrote one `llm_calls` row per model call.

## Gateway request

1. Validate metadata (strict mode rejects requests without it).
2. Plan: an explicit model is a single-model call; `auto` asks the router for the cheapest candidates meeting the step class bar.
3. Rate limit per workflow (requests and tokens per minute).
4. Exact cache, then the semantic cache for cacheable step classes.
5. Budget: if the remaining run or daily budget cannot cover the first choice's estimate, downgrade among qualifying candidates that fit, or halt with `budget_exhausted`.
6. Call candidates in order, each with a timeout, two jittered retries, and its circuit breaker.
7. Record cost, charge the budget, store cache entries, emit the span and metrics, and return the completion with an `x_roi` block.

## Data

| Table | Written by | Read by |
|---|---|---|
| `workflows`, `workflow_state` | API startup, guard | API, guard |
| `runs`, `run_steps`, `tool_calls` | runner | API, guard, demo export |
| `llm_calls` | gateway | API (gateway stats, model mix, budget burn) |
| `guard_actions` | guard | API |
