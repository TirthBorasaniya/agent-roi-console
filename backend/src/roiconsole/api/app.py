"""Console API application: routers, guard scheduler, task runner, and the built dashboard."""

# ============= Standard Library =============
import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

# ============= Third-Party =============
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# ============= Local =============
from roiconsole import __version__
from roiconsole.api.deps import RunnerFactory, RunnerHolder
from roiconsole.api.queries import sync_workflows
from roiconsole.api.routers import insights, runs, workflows
from roiconsole.common.db import init_db
from roiconsole.common.logging import configure_logging
from roiconsole.common.metrics import REGISTRY
from roiconsole.common.settings import REPO_ROOT, get_settings
from roiconsole.guard.service import GuardService

# ============= Constants =============
logger = logging.getLogger(__name__)
STATIC_DIR = Path(os.environ.get("ROI_STATIC_DIR", REPO_ROOT / "frontend" / "dist"))
WILDCARD_ORIGIN = "*"
ALERT_STEP_ID = "guard_alert"


# ============= Configuration Guards =============


def assert_cors_config_safe(allowed_origin_list: list[str], o_allow_credentials: bool) -> None:
    """Refuse a wildcard CORS origin combined with credentials (carried over from v1)."""
    if o_allow_credentials and WILDCARD_ORIGIN in allowed_origin_list:
        raise RuntimeError(
            "refusing to start: FRONTEND_ORIGIN is '*' while CORS credentials are enabled"
        )


def default_runner_factory() -> RunnerFactory:
    """Open runs through the gateway and MCP servers chosen by the environment."""
    from roiconsole.runner.session import runner_session

    settings = get_settings()
    gateway_mode = os.environ.get(
        "ROI_API_GATEWAY_MODE", "http" if os.environ.get("GATEWAY_URL") else "inprocess"
    )
    mcp_mode = (
        "http"
        if settings.slack_mcp_url and settings.notion_mcp_url and settings.crm_mcp_url
        else "stdio"
    )
    return lambda: runner_session(gateway_mode, mcp_mode)


# ============= App =============


def create_app(runner_factory: RunnerFactory | None = None, o_start_runner: bool = True) -> FastAPI:
    """
    Create the console API.

    Parameters
    ----------
    runner_factory : callable or None
        Builds the task runner session; defaults to the environment's gateway and MCP modes.
    o_start_runner : bool
        Open the runner at startup; tests that only read can skip it.
    """
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db()
        sync_workflows()
        holder = None
        if o_start_runner:
            holder = RunnerHolder(runner_factory or default_runner_factory())
            try:
                await holder.start()
            except Exception:
                # the read-only dashboard still works without a runner; runs return 503
                logger.exception("task runner failed to start")
                holder = None
        app.state.runner_holder = holder

        async def notifier(workflow_id: str, text: str) -> None:
            if holder is None or holder.runner is None:
                logger.warning("no runner for guard alert", extra={"workflow_id": workflow_id})
                return
            from roiconsole.sandbox.fixtures import load_connectors_config

            channel = load_connectors_config()["slack"]["alerts_channel"]
            await holder.runner.toolbox.call(
                "slack.post_message",
                {"channel": channel, "text": text},
                f"guard_{workflow_id}",
                ALERT_STEP_ID,
            )

        app.state.guard = GuardService(notifier)
        guard_task = None
        if settings.o_guard_enabled:
            guard_task = asyncio.create_task(app.state.guard.run_forever())
        yield
        if guard_task is not None:
            guard_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await guard_task
        if holder is not None:
            await holder.stop()

    app = FastAPI(title="Agent ROI Console", version=__version__, lifespan=lifespan)
    origin_list = [settings.frontend_origin, "http://localhost:5173"]
    assert_cors_config_safe(origin_list, True)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(workflows.router)
    app.include_router(runs.router)
    app.include_router(insights.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness for container health checks."""
        return {"status": "ok"}

    @app.get("/metrics", tags=["health"])
    def prometheus_metrics() -> PlainTextResponse:
        """Prometheus exposition."""
        return PlainTextResponse(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    if STATIC_DIR.is_dir():
        # mounted last so API routes take precedence
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
    return app


def main() -> None:
    """Run the API with uvicorn."""
    import uvicorn

    configure_logging("api")
    uvicorn.run(
        create_app(),
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
