"""Shared API dependencies: the task runner holder and request models."""

# ============= Standard Library =============
import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

# ============= Third-Party =============
from fastapi import HTTPException, Request
from pydantic import BaseModel

# ============= Local =============
from roiconsole.runner.runner import TaskRunner

# ============= Types =============

RunnerFactory = Callable[[], AbstractAsyncContextManager[TaskRunner]]


class RunRequest(BaseModel):
    """POST /api/workflows/{id}/run body; all fields optional."""

    task_id: str | None = None
    path: str | None = None
    model: str = "auto"


class RunnerHolder:
    """
    Owns one TaskRunner for the API process and serializes runs.

    Runs are serialized because a run resets the shared workspace.

    Parameters
    ----------
    factory : callable
        Returns an async context manager yielding a TaskRunner.
    """

    def __init__(self, factory: RunnerFactory) -> None:
        self._factory = factory
        self._context: AbstractAsyncContextManager[TaskRunner] | None = None
        self.runner: TaskRunner | None = None
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        """Open the runner session."""
        self._context = self._factory()
        self.runner = await self._context.__aenter__()

    async def stop(self) -> None:
        """Close the runner session."""
        if self._context is not None:
            await self._context.__aexit__(None, None, None)


def get_holder(request: Request) -> RunnerHolder:
    """Return the runner holder or 503 when runs are unavailable."""
    holder: RunnerHolder | None = getattr(request.app.state, "runner_holder", None)
    if holder is None or holder.runner is None:
        raise HTTPException(status_code=503, detail="task runner is not available")
    return holder
