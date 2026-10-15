# Decisions

## D-001: Workflows are declared in YAML and the pipeline path calls a model only at declared steps
Status: accepted (2026-10-14)
Context: version 1 ran every workflow through one tool-calling loop, so every step cost a model call and the seeded workflows lived only as database rows.
Decision: each workflow is a YAML file with tool, LLM, and transform steps, Jinja prompts, human-time assumptions, and validator names. The `pipeline` path executes those steps; `planner_executor` and `react` receive the same tools and an instruction rendered from the same inputs.
Alternatives: code-defined graphs per workflow (rejected: three paths would need three hand-written graphs per workflow); a single agent path (rejected: SPEC requires three paths and the cheapest one is the deterministic pipeline).
Consequences: the same task can be compared across paths on identical inputs, and the guard can switch paths without code changes.

## D-002: Validators read real end state through the connector backends
Status: accepted (2026-10-14)
Context: success measured from the agent's own report is meaningless (SPEC 12).
Decision: after each run, the runner snapshots everything created since the reset (bot messages, Notion pages with blocks, CRM notes) through the same backend classes the MCP servers use, live or sandbox, and pure validator functions check that snapshot. Live Slack output carries message metadata (`roi_console_output`) so console writes are distinguishable from seeded discussion.
Alternatives: validators calling the MCP tools (rejected: couples measurement to the tools under test); checking the model's final message (rejected: self-report).
Consequences: validators are unit-tested on hand-built states and run unchanged against live workspaces.

## D-003: Dry-run and demo connectors share one SQLite sandbox file
Status: accepted (2026-10-14)
Context: MCP servers run as separate processes (stdio or compose services) while the runner resets fixtures and validators read end state.
Decision: a WAL-mode SQLite file (`data/sandbox.db`) holds sandbox Slack, Notion, and CRM state; every process opens it. The simulated CRM exists only for CI and demo mode; live mode uses Twenty.
Alternatives: in-memory fakes per process (rejected: state would not be shared); Redis (rejected: relational fixtures fit SQLite better and need no service in CI).
Consequences: runs on the sandbox are sequential because a reset replaces the whole workspace.

## D-004: Write tools require an idempotency key and the client computes it
Status: accepted (2026-10-14)
Context: retries and fallbacks can repeat a tool call.
Decision: write tools take `idempotency_key`; the MCP client injects `sha256(run_id | step_id | arguments)` and hides the parameter from model tool schemas. Servers claim the key before writing, concurrent duplicates wait for the first result, and failed writes release the claim.
Alternatives: server-side deduplication by content (rejected: two legitimate identical posts would collapse).
Consequences: a repeated call returns the first result with `idempotent_replay: true` and makes no second write.

## D-005: The gateway is a separate OpenAI-compatible service
Status: accepted (2026-10-14)
Context: version 1 called Anthropic directly, so cost attribution was an estimate divided evenly across tools.
Decision: every model call goes to `/v1/chat/completions` with workflow, run, step, and step-class metadata; the gateway records actual cost per call. The runner can use it over HTTP or in process (same `GatewayService`), which evaluations use to avoid an HTTP hop.
Alternatives: a routing library inside the runner (rejected: budgets, caches, and breakers would not be shared across processes); LiteLLM proxy (rejected: the routing policy depends on this project's measured step success and budgets).
Consequences: one place enforces budgets and emits telemetry; strict mode rejects unattributed calls.

## D-006: The routing table is generated from E1, and a step class with no qualifying model routes by success
Status: accepted (2026-10-14)
Context: SPEC 5.3 routes to the cheapest model meeting the step success bar.
Decision: `scripts/build_routing_table.py` writes `configs/routing_table.yaml` from `results/e1_step_success.json`, with the evaluation date and source hash. When no candidate meets the bar, the plan orders candidates by measured success and flags the request `below_bar`, so the run still executes and its failure is measured.
Alternatives: refusing such requests (rejected: hides the failure from E2 and ROI); hand-written tables (rejected: unmeasured).
Consequences: routed requests return 503 until E1 has run; the table is regenerated whenever models or prices change.

## D-007: Budgets use a pessimistic pre-call estimate
Status: accepted (2026-10-14)
Context: SPEC 5.3 defines the estimate as prompt tokens times input price plus `max_tokens` times output price.
Decision: prompt tokens are estimated at four characters per token; when the remaining run budget cannot cover the chosen model's estimate, the plan is re-ordered by estimate among qualifying candidates that fit, and with none it halts with `budget_exhausted`, which the runner records as a failed task.
Alternatives: provider token counting endpoints (rejected: an extra call per request and not available for every provider).
Consequences: no run can overspend its budget (E4 checks this); budgets are conservative.

## D-008: Semantic caching only for cacheable step classes, with a fastembed vectorizer
Status: accepted (2026-10-14)
Context: semantic caching of personalized drafts can leak one customer's content into another's (SPEC 12). RedisVL's default vectorizer needs sentence-transformers and torch.
Decision: `configs/step_classes.yaml` marks extraction and classification cacheable; summarization, short generation, planning, and agent turns never use the semantic cache. Entries are filtered on step class and model at distance threshold 0.1. A RedisVL `CustomVectorizer` wraps fastembed (ONNX, `BAAI/bge-small-en-v1.5`). E4 measures the wrong-hit rate on near-miss queries.
Alternatives: no semantic cache (rejected: SPEC requires it); torch-based embeddings (rejected: image size).
Consequences: Redis 8 (with the query engine) is required for the semantic cache.

## D-009: The self-hosted price is computed from two measured rates
Status: accepted (2026-10-14)
Context: SPEC 5.4 prices the self-hosted model as `host_hour_usd / 3600 / tokens_per_second * 1e6`.
Decision: `scripts/measure_selfhosted.py` measures sustained output tokens per second at `--parallel 2` and, in a separate prefill phase with one-token completions, prompt tokens per second; the formula applies to each rate to give the output and input prices. `host_hour_usd` is the on-demand price of an AWS c7g.2xlarge (0.29 USD per hour, us-east-1, 2026-10-14).
Alternatives: one rate for both (rejected: prefill is far faster than decoding, so one rate would overprice input); a zero price (rejected: hardware is not free).
Consequences: the self-hosted model is unpriced, and excluded from routing, until the measurement exists.

## D-010: ROI includes review and fix time
Status: accepted (2026-10-14)
Context: time saved minus token cost produces ROI ratios in the hundreds and never goes negative.
Decision: SPEC 6 terms: value from measured success, human cost from review minutes on every task and fix minutes on failures, machine cost from measured model and tool cost. Break-even success rate and break-even minutes are reported with every summary.
Alternatives: version 1 formula (rejected: not credible and gives the guard nothing to act on).
Consequences: low-success paths show negative ROI, which is what the guard acts on.

## D-011: The guard acts on production runs and picks targets from E2
Status: accepted (2026-10-14)
Context: evaluation runs are deliberately spread across weak configurations and would trip the guard.
Decision: guard windows exclude runs whose trigger starts with `eval`. The downgrade target is the cheapest path whose routed success in E2 meets the bar; before E2 exists, trailing production runs per path are used when each has at least 5 runs; otherwise the path is kept and only the phase changes.
Alternatives: a time window only (rejected: SPEC counts runs, and the pause decision must come after exactly the next 20 runs).
Consequences: E5 shows downgrade at run 20 and pause at run 40 on a negative stream.

## D-012: Model candidates replace the retired Groq Llama models
Status: accepted (2026-10-14)
Context: Groq shut down `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` for free and developer tiers on 2026-08-16; neither has a published price.
Decision: candidates are the self-hosted 1.5B model, `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` (Groq's recommended successor for the 70B), and `claude-haiku-5-5`, since the repository already depended on Anthropic.
Alternatives: keeping the retired ids (rejected: calls would fail); dropping Anthropic (rejected: SPEC keeps it when the repo used it).
Consequences: E1 and E2 tables list the substituted models; the README states the substitution.

## D-013: The public demo is a static snapshot on GitHub Pages
Status: accepted (2026-10-14)
Context: version 1 deployed the full backend to a free Render instance in demo mode, which showed simulated numbers and slept when idle.
Decision: `scripts/export_demo_data.py` writes API-shaped JSON from committed results; the dashboard builds in static mode; `pages.yml` publishes it and skips until results exist. `render.yaml` and the keep-warm job are removed.
Alternatives: keeping Render (rejected: SPEC 8.1 requires a static, read-only demo of real results).
Consequences: the demo shows only measurements; it cannot trigger runs.

## D-014: crm-mcp adds list_opportunities
Status: accepted (2026-10-14)
Context: the existing CRM Pipeline Summary workflow lists open opportunities, which the SPEC tool table does not include.
Decision: `crm-mcp` exposes `list_opportunities(stage)` next to the four SPEC tools.
Alternatives: replacing the workflow with the SPEC example `lead_followup` (rejected: SPEC keeps the four existing workflows).
Consequences: the CRM server has five tools.

## D-015: Twenty access is scoped to sandbox-marked records
Status: accepted (2026-10-14)
Context: resets delete records; a shared Twenty workspace may hold other data.
Decision: sandbox companies use domains under `roi-sandbox.test` and sandbox people use matching emails. Reads list only those companies' opportunities and those people, writes refuse other contacts, and `reset_twenty.py` hard-deletes only sandbox records.
Alternatives: wiping the workspace on reset (rejected: destructive).
Consequences: Twenty's own sample data never appears in tasks.

## D-016: The gateway streams by replaying a completed response
Status: accepted (2026-10-14)
Context: SPEC 5.1 requires streaming support; budgets, caches, and attribution need final usage.
Decision: providers are called without streaming; a client that asks for `stream: true` receives the result as OpenAI chunk events.
Alternatives: proxying provider streams (rejected: cost and cache entries would need partial accounting).
Consequences: streaming clients see the first token only when the call completes.

## D-017: SQLite for console persistence
Status: accepted (2026-10-14)
Context: one operator, one host; the gateway, API, and runner share a volume.
Decision: SQLite in WAL mode with a busy timeout.
Alternatives: Postgres (rejected: another service for no current need; Twenty's Postgres stays private to Twenty).
Consequences: a multi-host deployment would need Postgres.

## D-018: Demo replay serves only exact recorded requests
Status: accepted (2026-10-14)
Context: demo mode with no keys must not invent model answers.
Decision: the replay adapter answers only requests whose hash matches a recording made from a real run in dry-run mode (`ROI_RECORD_LLM=1`); anything else fails. Sandbox ids and Slack timestamps come from per-reset sequences rather than random UUIDs, so the same task produces the same requests and replay hits its recordings.
Alternatives: scripted responses (rejected: version 1 did this and its numbers were simulated).
Consequences: demo-mode runs work for recorded tasks and fail visibly for unrecorded ones.

## D-019: Evaluations turn gateway caching off
Status: accepted (2026-10-14)
Context: the same prompt repeats across model configurations and repeats.
Decision: the runner sends `cache: off` for evaluation runs; API runs allow caching.
Alternatives: a fresh cache namespace per configuration (rejected: still serves repeats within a configuration).
Consequences: E2 costs and latencies are fully measured.
