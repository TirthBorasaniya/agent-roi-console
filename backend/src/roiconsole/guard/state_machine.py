"""Pure ROI guard state machine: normal, downgraded, and paused phases per workflow."""

# ============= Standard Library =============
from dataclasses import dataclass, replace
from typing import Any

# ============= Constants =============
PHASE_NORMAL = "normal"
PHASE_DOWNGRADED = "downgraded"
PHASE_PAUSED = "paused"
ACTION_NONE = "none"
ACTION_DOWNGRADE = "downgrade"
ACTION_PAUSE = "pause"
ACTION_RECOVER = "recover"


# ============= Types =============


@dataclass(frozen=True)
class GuardConfig:
    """Guard thresholds."""

    min_runs: int = 20
    path_success_bar: float = 0.9


@dataclass(frozen=True)
class GuardState:
    """
    A workflow's guard state.

    Attributes
    ----------
    workflow_id : str
        Workflow.
    active_path : str
        Path new runs use.
    phase : str
        normal, downgraded, or paused.
    phase_run_offset : int
        Number of runs that existed when the phase started; runs after it count
        toward the post-downgrade evaluation.
    """

    workflow_id: str
    active_path: str
    phase: str = PHASE_NORMAL
    phase_run_offset: int = 0


@dataclass(frozen=True)
class PathStats:
    """Measured success and mean cost per task of one path."""

    success_rate: float
    mean_cost_usd: float


@dataclass(frozen=True)
class GuardDecision:
    """The guard's decision for one evaluation."""

    action: str
    new_state: GuardState
    window_runs: int
    net_roi_usd: float | None
    detail: str


# ============= Policy =============


def cheapest_qualifying_path(stats_dict: dict[str, PathStats], bar: float) -> str | None:
    """Return the lowest-cost path whose success rate meets the bar, or None."""
    qualifying_list = [(s.mean_cost_usd, p) for p, s in stats_dict.items() if s.success_rate >= bar]
    return min(qualifying_list)[1] if qualifying_list else None


def evaluate(
    state: GuardState,
    net_by_run: list[float],
    total_runs: int,
    path_stats: dict[str, PathStats],
    config: GuardConfig,
) -> GuardDecision:
    """
    Decide the guard action for one workflow.

    Parameters
    ----------
    state : GuardState
        Current state.
    net_by_run : list of float
        Net ROI of each run in the trailing window, oldest first. In the
        downgraded phase only runs after the downgrade are passed.
    total_runs : int
        Total runs of the workflow so far, stored as the next phase offset.
    path_stats : dict
        Path name to PathStats, for picking the downgrade target.
    config : GuardConfig
        Thresholds.

    Returns
    -------
    decision : GuardDecision
        The action (none, downgrade, pause, recover) and the next state.
    """
    window = len(net_by_run)
    net = sum(net_by_run) if net_by_run else None
    if state.phase == PHASE_PAUSED:
        return GuardDecision(ACTION_NONE, state, window, net, "paused until resumed")
    if window < config.min_runs:
        return GuardDecision(ACTION_NONE, state, window, net, f"{window} of {config.min_runs} runs")
    if state.phase == PHASE_NORMAL:
        if net >= 0:
            return GuardDecision(ACTION_NONE, state, window, net, "net ROI non-negative")
        target = cheapest_qualifying_path(path_stats, config.path_success_bar) or state.active_path
        new_state = replace(
            state, active_path=target, phase=PHASE_DOWNGRADED, phase_run_offset=total_runs
        )
        detail = (
            f"net ROI {net:.4f} USD over {window} runs; switching {state.active_path} -> {target}"
        )
        return GuardDecision(ACTION_DOWNGRADE, new_state, window, net, detail)
    if net < 0:
        new_state = replace(state, phase=PHASE_PAUSED, phase_run_offset=total_runs)
        detail = f"net ROI still {net:.4f} USD over {window} runs after downgrade; pausing"
        return GuardDecision(ACTION_PAUSE, new_state, window, net, detail)
    new_state = replace(state, phase=PHASE_NORMAL, phase_run_offset=total_runs)
    return GuardDecision(
        ACTION_RECOVER, new_state, window, net, "net ROI recovered after downgrade"
    )


def window_for(
    state: GuardState, run_list: list[dict[str, Any]], min_runs: int
) -> list[dict[str, Any]]:
    """
    Select the runs a decision looks at.

    Normal phase: the most recent min_runs runs in the trailing window.
    Downgraded phase: the first min_runs runs after the downgrade, so the pause
    decision happens exactly at the next min_runs runs.

    Parameters
    ----------
    state : GuardState
        Current state.
    run_list : list of dict
        All runs of the workflow in the trailing window, oldest first, each with
        a sequence number `seq` (count of runs before it).
    min_runs : int
        Window size.
    """
    if state.phase == PHASE_DOWNGRADED:
        return [r for r in run_list if r["seq"] >= state.phase_run_offset][:min_runs]
    return run_list[-min_runs:] if len(run_list) >= min_runs else run_list
