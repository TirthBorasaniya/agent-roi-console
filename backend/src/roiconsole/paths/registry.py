"""Lookup of the three execution paths by name."""

# ============= Local =============
from roiconsole.paths.base import ExecutionPath
from roiconsole.paths.pipeline import PipelinePath
from roiconsole.paths.planner_executor import PlannerExecutorPath
from roiconsole.paths.react import ReactPath

# ============= Registry =============

PATHS_DICT: dict[str, ExecutionPath] = {
    "pipeline": PipelinePath(),
    "planner_executor": PlannerExecutorPath(),
    "react": ReactPath(),
}


def get_path(name: str) -> ExecutionPath:
    """Return a path by name or raise KeyError."""
    if name not in PATHS_DICT:
        raise KeyError(f"unknown path {name!r}; paths: {sorted(PATHS_DICT)}")
    return PATHS_DICT[name]
