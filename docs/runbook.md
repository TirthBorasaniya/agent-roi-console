# Runbook

## Environment

Copy `.env.example` to `.env`. Every key is optional; without connector keys the console runs on the sandbox in demo mode.

| Need | Variables |
|---|---|
| Hosted models | `GROQ_API_KEY`, `ANTHROPIC_API_KEY` |
| Live Slack | `SLACK_BOT_TOKEN` (scopes chat:write, chat:write.customize, channels:read, channels:history, groups:read, groups:history) and the five `roi-sandbox-*` channels with the bot invited |
| Live Notion | `NOTION_API_KEY`, `NOTION_PARENT_PAGE_ID`, then `make seed-notion` once (writes `data/live_refs.json`) |
| Self-hosted Twenty | `TWENTY_ENCRYPTION_KEY` and `TWENTY_PG_PASSWORD`, `make up-crm`, create the workspace at http://localhost:3000, create an API key, set `TWENTY_API_KEY`, then `make check-twenty` and `make seed-twenty` |
| Self-hosted model | `make up-selfhosted` (downloads and verifies the GGUF), then `make measure-selfhosted` |

## Order of the evaluation

1. `make up-full` and confirm every service is healthy (`docker compose ps`).
2. `make measure-selfhosted` writes `results/selfhosted_throughput.json`, which gives the self-hosted model its price.
3. `make eval-steps` runs E1 on the development split and writes `configs/routing_table.yaml`. Routed requests (`model: auto`) return 503 until this exists.
4. `make eval-tasks` runs E2 on the held-out split; it checkpoints to `eval/checkpoints/e2.jsonl` and resumes when rerun.
5. `make eval-roi`, `make eval-gateway`, `make eval-guard`, `make inventory`, `make live-tasks`.
6. `make results` renders the README tables; commit the results files with the code and configs that produced them.
7. `make demo` builds the static demo locally; pushing to `main` publishes it through `pages.yml` once the repository variable `PAGES_ENABLED` is `true`, GitHub Pages uses the GitHub Actions source, and `results/e2_tasks.json` is committed; until then the workflow skips.

## Operations

| Task | Command |
|---|---|
| Reset the sandbox to a task | `make reset-sandbox TASK=<task_id>` |
| Reset Twenty to a task | `make reset-twenty TASK=<task_id>` |
| Resume a workflow paused by the guard | `curl -X POST http://127.0.0.1:8000/api/workflows/<id>/resume` |
| Run the guard now | `curl -X POST http://127.0.0.1:8000/api/guard/evaluate` |
| Record model responses for demo replay | run evaluations in dry-run mode with `ROI_RECORD_LLM=1` |
| Integration tests on compose services | `make test-integration-compose` |
