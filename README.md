# Agent ROI Console

**Agent ROI Console** is an open-source, full-stack platform that converts LangGraph multi-agent workflow runs into quantified business-value estimates. Most agent-automation platforms tell you *what* the agent did — logs, traces, tool calls — but none of them answer the question a BizOps or Applied AI lead actually cares about: *was this automation worth the money?* Agent ROI Console fills that gap by mapping every completed workflow run to a net ROI figure (human cost saved minus LLM token cost), aggregating results by value category, and presenting them in a clean React dashboard designed for engineers who need to justify automation investment to non-technical stakeholders.

---

## Architecture

```mermaid
graph TD
    User["User / Scheduler"]
    API["FastAPI Backend\n(port 8000)"]
    DB[(SQLite\nroi_console.db)]
    LG["LangGraph\nOrchestrator"]
    Slack["Slack SDK\n(mock if no token)"]
    Notion["Notion SDK\n(mock if no token)"]
    CRM["Twenty CRM GraphQL\n(mock if no key)"]
    LLM["Claude Haiku\n(Anthropic API)"]
    FE["React Dashboard\n(port 5173)"]

    User -->|POST /api/workflows/{id}/run| API
    API --> LG
    LG -->|route_query node| LLM
    LG -->|slack_node| Slack
    LG -->|notion_node| Notion
    LG -->|crm_node| CRM
    LG -->|synthesize_node| LLM
    LG -->|write RunRecord + ROI| DB
    API --> DB
    FE -->|REST polls| API
```

---

## How ROI Is Calculated

For each completed workflow run, the console computes:

```
human_cost_usd = (baseline_minutes / 60) × loaded_cost_per_hour

token_cost_usd = (input_tokens  × input_price_per_1M  / 1,000,000)
               + (output_tokens × output_price_per_1M / 1,000,000)

net_roi_usd = human_cost_usd − token_cost_usd
```

**Default values** (override in `.env`):

| Variable | Default | Meaning |
|---|---|---|
| `LOADED_COST_PER_HOUR` | `75.0` | Fully loaded hourly cost (salary + benefits) |
| `LLM_INPUT_PRICE_PER_1M` | `3.0` | Price per 1M input tokens (USD) |
| `LLM_OUTPUT_PRICE_PER_1M` | `15.0` | Price per 1M output tokens (USD) |

A negative ROI (token cost exceeds human cost) is surfaced in red on the dashboard — the formula never crashes, it just shows you when automation isn't worth it.

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/TirthBorasaniya/agent-roi-console.git
cd agent-roi-console

# 2. Copy and edit environment variables
cp .env.example .env
# Add ANTHROPIC_API_KEY at minimum; Slack/Notion keys are optional (demo mode without them)

# 3. Start everything
docker compose up --build
```

- Dashboard: http://localhost:5173
- API docs: http://localhost:8000/docs

The four example workflows are seeded automatically on first startup.

---

## Example Workflows

Four workflows are pre-seeded into the database so the dashboard is populated immediately:

### 1. Slack Channel Digest
> Category: **SUMMARIZATION** · Baseline: 15 min

Reads recent messages from a Slack channel and posts a structured digest summary back to the channel. Replaces the manual work of reviewing a busy channel and writing a summary for async teammates.

### 2. Notion Knowledge Search
> Category: **RESEARCH** · Baseline: 10 min

Searches your Notion workspace for pages relevant to a query and returns summarized content. Replaces the time spent navigating Notion manually to answer a quick research question.

### 3. Meeting Notes to Notion
> Category: **DATA_ENTRY** · Baseline: 20 min

Takes raw meeting notes as input and creates a structured Notion page with sections, action items, and key decisions. Replaces the post-meeting cleanup task that often falls through the cracks.

### 4. CRM Pipeline Summary
> Category: **COORDINATION** · Baseline: 12 min

Lists open opportunities from the Twenty CRM pipeline and posts a summary to Slack. Replaces the manual work of checking the CRM and relaying pipeline status to the team.

---

## Value Categories

| Category | Description |
|---|---|
| `RESEARCH` | Information gathering and synthesis |
| `COMMUNICATION` | Drafting and sending messages |
| `DATA_ENTRY` | Creating or updating records in external systems |
| `SUMMARIZATION` | Summarizing content across tools or channels |
| `COORDINATION` | Coordinating actions between tools or people |

---

## API Reference

Interactive Swagger docs are available at **http://localhost:8000/docs** after starting the backend.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/workflows` | List all workflows |
| `POST` | `/api/workflows` | Create a new workflow |
| `GET` | `/api/workflows/{id}` | Get workflow details |
| `POST` | `/api/workflows/{id}/run` | Trigger a workflow run |
| `GET` | `/api/runs` | Paginated run history |
| `GET` | `/api/runs/{id}` | Run details with tool usage |
| `GET` | `/api/metrics/summary` | Total ROI, run count, avg cost |
| `GET` | `/api/metrics/roi-by-category` | ROI by value category |
| `GET` | `/api/metrics/cost-by-tool` | Token cost by tool |
| `GET` | `/api/metrics/timeline` | Daily run counts and ROI |
| `GET` | `/health` | Health check |

---

## Demo Mode

The console runs fully without any external API keys. When `SLACK_BOT_TOKEN`, `NOTION_API_KEY`, or `TWENTY_API_KEY` are absent, the connectors return realistic mock data and log a warning at startup — the Twenty CRM connector returns mock contacts and opportunities without a key. Set `ANTHROPIC_API_KEY` to enable real LLM calls; without it, the orchestrator falls back to a canned response so the run record and ROI calculation still complete.

---

## Development

```bash
# Backend (Python 3.11+)
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev

# Tests
pytest backend/tests/ -v
```

---

## License

MIT
