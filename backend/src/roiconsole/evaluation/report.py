"""Render README tables from results files, between <!-- results:<name>:start/end --> markers."""

# ============= Standard Library =============
import re
from collections.abc import Callable
from typing import Any

# ============= Local =============
from roiconsole.common.results import read_results

# ============= Constants =============
MARKER_RE = re.compile(
    r"(<!-- results:(?P<name>[a-z0-9_]+):start -->)(?P<body>.*?)(<!-- results:(?P=name):end -->)",
    re.DOTALL,
)
ROUTED = "auto"


# ============= Formatting =============


def fmt_pct(value: float | None) -> str:
    """Percent with no decimals, or n/a."""
    return "n/a" if value is None else f"{value * 100:.0f}%"


def fmt_usd(value: float | None, digits: int = 4) -> str:
    """Dollar amount, or n/a."""
    if value is None:
        return "n/a"
    return f"-${abs(value):.{digits}f}" if value < 0 else f"${value:.{digits}f}"


def fmt_rate(summary: dict[str, Any] | None) -> str:
    """Rate with its Wilson interval and trial count."""
    if not summary or summary.get("rate") is None:
        return "n/a"
    return (
        f"{fmt_pct(summary['rate'])} ({fmt_pct(summary['ci95_low'])} to "
        f"{fmt_pct(summary['ci95_high'])}, n={summary['trials']})"
    )


def table(header_list: list[str], row_list: list[list[str]]) -> str:
    """Render a Markdown table."""
    lines = ["| " + " | ".join(header_list) + " |", "|" + "---|" * len(header_list)]
    lines += ["| " + " | ".join(row) + " |" for row in row_list]
    return "\n".join(lines)


def source_line(results: dict[str, Any]) -> str:
    """One line naming the run that produced a table."""
    return f"_Source: `{results['run_id']}` at git `{results['git_sha']}`._"


# ============= Sections =============


def render_headline() -> str:
    """Headline numbers across experiments."""
    row_list: list[list[str]] = []
    e2 = read_results("e2_tasks")
    if e2:
        for path, configs in e2["metrics"]["by_path_and_config"].items():
            routed = configs.get(ROUTED)
            if routed:
                row_list.append(
                    [
                        f"Held-out task success, {path}, routed",
                        fmt_rate(routed["success"]),
                        "`results/e2_tasks.json`",
                    ]
                )
                row_list.append(
                    [
                        f"Cost per successful task, {path}, routed",
                        fmt_usd(routed["cost_per_successful_task_usd"]),
                        "`results/e2_tasks.json`",
                    ]
                )
    e4 = read_results("e4_gateway")
    if e4:
        metrics = e4["metrics"]
        row_list.append(
            [
                "Fallback success under injected faults",
                fmt_rate(metrics["fallback"]["fallback_success_under_faults"]),
                "`results/e4_gateway.json`",
            ]
        )
        row_list.append(
            [
                "Budget enforcement correct",
                fmt_rate(metrics["budget"]["enforcement_correct"]),
                "`results/e4_gateway.json`",
            ]
        )
        row_list.append(
            [
                "Semantic cache wrong-hit rate",
                fmt_rate(metrics["cache"]["semantic_wrong_hit_rate"]),
                "`results/e4_gateway.json`",
            ]
        )
    e5 = read_results("e5_guard")
    if e5:
        metrics = e5["metrics"]
        row_list.append(
            [
                "Guard streams acting at expected run counts",
                f"{metrics['streams_passed']} of {metrics['streams_total']}",
                "`results/e5_guard.json`",
            ]
        )
    selfhosted = read_results("selfhosted_throughput")
    if selfhosted:
        metrics = selfhosted["metrics"]
        row_list.append(
            [
                "Self-hosted output throughput and computed price",
                f"{metrics['output_tokens_per_second']:.1f} tok/s, "
                f"{fmt_usd(metrics['output_price_per_mtok'], 3)} per M output tokens",
                "`results/selfhosted_throughput.json`",
            ]
        )
    live = read_results("live_tasks")
    if live:
        row_list.append(
            [
                "Live tasks passing (Slack, Notion, Twenty)",
                fmt_rate(live["metrics"]["success"]),
                "`results/live_tasks.json`",
            ]
        )
    if not row_list:
        return ""
    return table(["Measure", "Value", "Artifact"], row_list)


def render_e1() -> str:
    """Step success per step class per model."""
    e1 = read_results("e1_step_success")
    if not e1:
        return ""
    model_list = e1["metrics"]["models"]
    row_list = [
        [step_class] + [fmt_rate(models.get(m, {}).get("success")) for m in model_list]
        for step_class, models in e1["metrics"]["step_classes"].items()
    ]
    return (
        table(["Step class"] + [f"`{m}`" for m in model_list], row_list) + "\n\n" + source_line(e1)
    )


def render_e2() -> str:
    """Held-out success, cost per success, and latency per path and configuration."""
    e2 = read_results("e2_tasks")
    if not e2:
        return ""
    row_list = []
    for path, configs in e2["metrics"]["by_path_and_config"].items():
        for config, summary in configs.items():
            row_list.append(
                [
                    path,
                    f"`{config}`",
                    fmt_rate(summary["success"]),
                    fmt_usd(summary["cost_per_successful_task_usd"]),
                    f"{summary['latency_p50_s']:.1f} s"
                    if summary["latency_p50_s"] is not None
                    else "n/a",
                ]
            )
    share = (
        ", ".join(f"`{m}` {fmt_pct(s)}" for m, s in e2["metrics"]["routed_model_share"].items())
        or "n/a"
    )
    body = table(
        ["Path", "Model configuration", "Success (95% CI)", "Cost per success", "Latency p50"],
        row_list,
    )
    return body + f"\n\nRouted calls by model: {share}.\n\n" + source_line(e2)


def render_e3() -> str:
    """ROI for the routed configuration on each workflow's paths."""
    e3 = read_results("e3_roi")
    if not e3:
        return ""
    row_list = []
    for workflow_id, entry in e3["metrics"]["workflows"].items():
        assumption = "timed" if entry["assumptions"]["o_timed"] else "assumed"
        for path, configs in entry["by_path_and_config"].items():
            summary = configs.get(ROUTED)
            if not summary or not summary.get("n_runs"):
                continue
            row_list.append(
                [
                    workflow_id,
                    path,
                    f"{entry['assumptions']['minutes_per_task']} min ({assumption})",
                    fmt_rate(summary["success"]),
                    fmt_pct(summary["break_even_success_rate"]),
                    fmt_usd(summary["net_roi_per_task_usd"], 2),
                    f"{summary['roi_ratio']:.2f}x" if summary["roi_ratio"] is not None else "n/a",
                    f"{summary['break_even_minutes_per_task']:.1f}"
                    if summary["break_even_minutes_per_task"] is not None
                    else "n/a",
                ]
            )
    header_list = [
        "Workflow",
        "Path",
        "Baseline",
        "Success",
        "Break-even success",
        "Net ROI per task",
        "ROI ratio",
        "Break-even minutes",
    ]
    return table(header_list, row_list) + "\n\n" + source_line(e3)


def render_e4() -> str:
    """Gateway fallback per fault scenario, budget enforcement, and cache rates."""
    e4 = read_results("e4_gateway")
    if not e4:
        return ""
    metrics = e4["metrics"]
    row_list = [
        [
            name,
            fmt_rate(s["success"]),
            f"{s['mean_fallback_depth']:.2f}" if s["mean_fallback_depth"] is not None else "n/a",
            f"{s['added_latency_p50_s']:+.2f} s" if s["added_latency_p50_s"] is not None else "n/a",
            str(s["circuit_open_skips"]),
        ]
        for name, s in metrics["fallback"]["scenarios"].items()
    ]
    cache = metrics["cache"]
    lines = [
        table(
            [
                "Fault scenario",
                "Success",
                "Mean fallback depth",
                "Added p50 latency",
                "Circuit-open skips",
            ],
            row_list,
        ),
        "",
        f"Budget enforcement correct: {fmt_rate(metrics['budget']['enforcement_correct'])}; "
        f"max overspend {fmt_usd(metrics['budget']['max_overspend_usd'], 6)}.",
        f"Exact hit rate {fmt_rate(cache['exact_hit_rate'])}; paraphrase semantic hit rate "
        f"{fmt_rate(cache['paraphrase_semantic_hit_rate'])}; semantic wrong-hit rate "
        f"{fmt_rate(cache['semantic_wrong_hit_rate'])} (threshold {cache['distance_threshold']}).",
        "",
        source_line(e4),
    ]
    return "\n".join(lines)


def render_e5() -> str:
    """Guard actions per simulated stream."""
    e5 = read_results("e5_guard")
    if not e5:
        return ""
    row_list = [
        [
            name,
            ", ".join(f"{e['action']}@{e['at_run']}" for e in s["expected"]) or "none",
            ", ".join(f"{o['action']}@{o['at_run']}" for o in s["observed"]) or "none",
            "yes" if s["o_passed"] else "no",
        ]
        for name, s in e5["metrics"]["streams"].items()
    ]
    return table(["Stream", "Expected", "Observed", "Match"], row_list) + "\n\n" + source_line(e5)


def render_inventory() -> str:
    """Counts derived from code."""
    inventory = read_results("inventory")
    if not inventory:
        return ""
    metrics = inventory["metrics"]
    return table(
        ["Item", "Count"],
        [
            ["Workflows", str(metrics["workflows"]["count"])],
            ["Execution paths", str(metrics["execution_paths"]["count"])],
            ["API endpoints", str(metrics["api_endpoints"]["count"])],
            ["MCP tools", str(metrics["mcp_tools"]["count"])],
            ["Evaluation tasks", str(metrics["tasks"]["count"])],
        ],
    )


RENDERERS_DICT: dict[str, Callable[[], str]] = {
    "headline": render_headline,
    "e1": render_e1,
    "e2": render_e2,
    "e3": render_e3,
    "e4": render_e4,
    "e5": render_e5,
    "inventory": render_inventory,
}


# ============= Rewrite =============


def render_document(text: str) -> str:
    """Fill each marked block from its results file; a block without results is left empty."""

    def substitute(match: re.Match) -> str:
        name = match.group("name")
        if name not in RENDERERS_DICT:
            return match.group(0)
        body = RENDERERS_DICT[name]()
        if not body:
            return f"{match.group(1)}{match.group(4)}"
        return f"{match.group(1)}\n{body}\n{match.group(4)}"

    return MARKER_RE.sub(substitute, text)
