"""E1: step success per step class per model on development tasks."""

# ============= Standard Library =============
import logging
import time
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.settings import REPO_ROOT
from roiconsole.evaluation.checkpoint import Checkpoint
from roiconsole.evaluation.stats import mean, rate_summary
from roiconsole.evaluation.step_checks import run_check
from roiconsole.gateway.pricing import PriceTable
from roiconsole.paths.base import PathContext, PathError
from roiconsole.paths.pipeline import execute_step, template_context
from roiconsole.paths.planner_executor import PlannerExecutorPath, required_tool_order_ok
from roiconsole.runner.llm import CallMeta, LlmCallError, parse_json_output
from roiconsole.runner.runner import TaskRunner
from roiconsole.sandbox.fixtures import resolve_inputs
from roiconsole.workflows.registry import get_workflow, tasks_for
from roiconsole.workflows.schema import StepDefinition, TaskDefinition, WorkflowDefinition
from roiconsole.workflows.templating import render_string, render_value

# ============= Constants =============
logger = logging.getLogger(__name__)
E1_CONFIG = "eval/e1.yaml"
KEY_FIELDS = ("kind", "task_id", "step_id", "model", "repeat")


# ============= Step Inputs =============


async def prepare_step_context(
    runner: TaskRunner,
    workflow: WorkflowDefinition,
    task: TaskDefinition,
    target: StepDefinition,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """
    Reset the workspace and build the inputs an LLM step would see on a correct run.

    Tool and transform steps before the target run for real; earlier LLM steps
    take their gold output from the task, so a step is measured on its own and
    not on upstream mistakes.

    Returns
    -------
    steps_dict : dict
        step_id to {"output": ...} for the steps before the target.
    refs_dict : dict
        Fixture refs from the reset.
    inputs_dict : dict
        Resolved task inputs.
    """
    refs_dict = runner.backends.reset(task.fixtures)
    inputs_dict = resolve_inputs(task, refs_dict)
    ctx = PathContext(workflow, inputs_dict, "", run_id, "auto", runner.llm, runner.toolbox, False)
    steps_dict: dict[str, Any] = {}
    for step in workflow.steps:
        if step.step_id == target.step_id:
            break
        if step.type == "llm":
            if step.step_id in task.expected.gold_outputs:
                steps_dict[step.step_id] = {"output": task.expected.gold_outputs[step.step_id]}
            continue
        steps_dict[step.step_id] = {"output": await execute_step(ctx, step, steps_dict)}
    return steps_dict, refs_dict, inputs_dict


async def measure_llm_step(
    runner: TaskRunner, task: TaskDefinition, step: StepDefinition, model: str, run_id: str
) -> dict[str, Any]:
    """Call one model on one prepared step and apply the task's step checks."""
    workflow = get_workflow(task.workflow_id)
    steps_dict, refs_dict, inputs_dict = await prepare_step_context(
        runner, workflow, task, step, run_id
    )
    context_dict = template_context(
        PathContext(workflow, inputs_dict, "", run_id, model, runner.llm, runner.toolbox),
        steps_dict,
    )
    messages = []
    if step.system_prompt:
        messages.append(
            {"role": "system", "content": render_string(step.system_prompt, context_dict)}
        )
    messages.append(
        {"role": "user", "content": render_string(step.user_prompt or "", context_dict)}
    )
    meta = CallMeta(
        workflow.workflow_id, run_id, step.step_id, step.step_class or "", o_cache=False
    )
    started = time.monotonic()
    record = {"kind": "llm_step", "step_class": step.step_class, "cost_usd": 0.0, "latency_s": None}
    try:
        result = await runner.llm.chat(
            model,
            messages,
            meta,
            max_tokens=step.max_tokens,
            response_format={"type": "json_object"} if step.output == "json" else None,
        )
    except LlmCallError as exc:
        return {
            **record,
            "success": False,
            "detail": f"call failed: {exc.code}",
            "latency_s": time.monotonic() - started,
        }
    record.update(
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    try:
        output = (
            parse_json_output(result.content)
            if step.output == "json"
            else (result.content or "").strip()
        )
    except ValueError as exc:
        return {**record, "success": False, "detail": f"invalid json: {exc}"}
    detail_list, o_success = [], True
    for check in task.expected.step_checks.get(step.step_id, []):
        args_dict = render_value(check.args, {"refs": refs_dict, "inputs": inputs_dict})
        o_passed, detail = run_check(check.check, output, args_dict)
        o_success = o_success and o_passed
        detail_list.append(f"{check.check}: {'pass' if o_passed else 'fail'} ({detail})")
    return {**record, "success": o_success, "detail": "; ".join(detail_list)}


async def measure_planning(
    runner: TaskRunner, task: TaskDefinition, model: str, run_id: str
) -> dict[str, Any]:
    """
    Score one plan: valid structure, allowed tools, the workflow's tool sequence in order,
    and every required tool parameter present.
    """
    workflow = get_workflow(task.workflow_id)
    refs_dict = runner.backends.reset(task.fixtures)
    inputs_dict = resolve_inputs(task, refs_dict)
    instruction = render_value(workflow.agent.instruction_template, {"inputs": inputs_dict})
    ctx = PathContext(
        workflow, inputs_dict, instruction, run_id, model, runner.llm, runner.toolbox, False
    )
    record = {"kind": "planning", "step_class": workflow.agent.planner_step_class}
    try:
        step_list = await PlannerExecutorPath().plan(ctx)
    except PathError as exc:
        o_success, detail = False, f"{exc.reason}: {exc}"
    else:
        specs_dict = await runner.toolbox.list_tools()
        required_list = [s.tool for s in workflow.steps if s.type == "tool"]
        o_order = required_tool_order_ok(step_list, required_list)
        missing_list = [
            f"{s['id']}.{p}"
            for s in step_list
            if s["action"] == "tool"
            for p in specs_dict[s["tool"]]
            .openai_schema()["function"]["parameters"]
            .get("required", [])
            if s.get("args", {}).get(p) in (None, "")
        ]
        o_success = o_order and not missing_list
        detail = f"tool order ok={o_order}; missing required args {missing_list}"
    result = ctx.llm_results[-1] if ctx.llm_results else None
    return {
        **record,
        "success": o_success,
        "detail": detail,
        "cost_usd": result.cost_usd if result else 0.0,
        "latency_s": result.latency_s if result else None,
    }


async def measure_agent(
    runner: TaskRunner, task: TaskDefinition, model: str, step_class: str
) -> dict[str, Any]:
    """Score agent_tool_use by a full ReAct run's task success."""
    outcome = await runner.run(task, "react", model, trigger="eval_e1")
    return {
        "kind": "agent",
        "step_class": step_class,
        "success": outcome.success,
        "detail": outcome.failure_reason or "",
        "cost_usd": outcome.llm_cost_usd,
        "latency_s": outcome.latency_s,
        "run_id": outcome.run_id,
    }


# ============= Experiment =============


def summarize(record_list: list[dict[str, Any]], model_list: list[str]) -> dict[str, Any]:
    """
    Aggregate records into per step class, per model success and cost.

    Returns
    -------
    summary : dict
        step_class to model to {success (rate with Wilson interval), mean_cost_usd, mean_latency_s}.
    """
    summary_dict: dict[str, dict[str, Any]] = {}
    for step_class in sorted({r["step_class"] for r in record_list}):
        summary_dict[step_class] = {}
        for model in model_list:
            subset = [
                r for r in record_list if r["step_class"] == step_class and r["model"] == model
            ]
            if not subset:
                continue
            summary_dict[step_class][model] = {
                "success": rate_summary(sum(bool(r["success"]) for r in subset), len(subset)),
                "mean_cost_usd": mean([r["cost_usd"] for r in subset]),
                "mean_latency_s": mean(
                    [r["latency_s"] for r in subset if r.get("latency_s") is not None]
                ),
            }
    return summary_dict


async def run_e1(
    runner: TaskRunner, config_file: Path | None = None, o_run_from_scratch: bool = False
):
    """
    Run E1 over every dev task, every LLM step, and every priced candidate model.

    Returns
    -------
    metrics : dict
        step_classes summary, models measured, models skipped for lack of a price.
    records : list of dict
        Every measurement.
    config_file : Path
        Config used.
    """
    config_file = config_file or config_path(E1_CONFIG)
    config_dict = load_yaml(config_file)
    prices = PriceTable.load()
    model_list = [m for m in config_dict["models"] if prices.has(m)]
    skipped_dict = {
        m: prices.unpriced_dict.get(m, "no price")
        for m in config_dict["models"]
        if not prices.has(m)
    }
    checkpoint = Checkpoint(REPO_ROOT / config_dict["checkpoint"], KEY_FIELDS, o_run_from_scratch)
    task_list = tasks_for(config_dict["split"])
    for repeat in range(config_dict["repeats"]):
        for task in task_list:
            workflow = get_workflow(task.workflow_id)
            for model in model_list:
                work_list = [("llm_step", step.step_id) for step in workflow.llm_steps]
                work_list += [("planning", "plan"), ("agent", "react")]
                for kind, step_id in work_list:
                    key = (kind, task.task_id, step_id, model, str(repeat))
                    if checkpoint.done(key):
                        continue
                    run_id = f"e1-{task.task_id}-{step_id}-{model.replace('/', '_')}-{repeat}"
                    if kind == "llm_step":
                        step = next(s for s in workflow.llm_steps if s.step_id == step_id)
                        record = await measure_llm_step(runner, task, step, model, run_id)
                    elif kind == "planning":
                        record = await measure_planning(runner, task, model, run_id)
                    else:
                        record = await measure_agent(
                            runner, task, model, config_dict["agent_step_class"]
                        )
                    record.update(
                        kind=kind, task_id=task.task_id, step_id=step_id, model=model, repeat=repeat
                    )
                    checkpoint.add(record)
                    logger.info(
                        "e1 record", extra={"key": "/".join(key), "success": record["success"]}
                    )
    record_list = checkpoint.records()
    metrics_dict = {
        "split": config_dict["split"],
        "n_tasks": len(task_list),
        "models": model_list,
        "models_skipped": skipped_dict,
        "step_classes": summarize(record_list, model_list),
    }
    return metrics_dict, record_list, config_file
