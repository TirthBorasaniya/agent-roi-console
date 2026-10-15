"""Unit tests for the generated task suite, split, and sandbox fixture loading."""

# ============= Standard Library =============
import re
from collections import Counter

# ============= Third-Party =============
import pytest
import yaml

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.task_suite import build_suite, split_task_ids
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.sandbox.fixtures import resolve_inputs
from roiconsole.validators.engine import check_task_validators
from roiconsole.workflows.registry import load_splits, load_tasks, load_workflows, tasks_for

# ============= Constants =============
REF_RE = re.compile(r"\{\{\s*refs\.(\w+)\s*\}\}")

pytestmark = pytest.mark.unit


# ============= Tests =============


def test_suite_has_sixty_tasks_fifteen_per_workflow():
    tasks_dict = load_tasks()
    assert len(tasks_dict) == 60
    assert set(Counter(t.workflow_id for t in tasks_dict.values()).values()) == {15}


def test_split_is_twenty_dev_forty_heldout_and_disjoint():
    splits_dict = load_splits()
    assert len(splits_dict["dev"]) == 20
    assert len(splits_dict["heldout"]) == 40
    assert not set(splits_dict["dev"]) & set(splits_dict["heldout"])
    assert set(splits_dict["dev"]) | set(splits_dict["heldout"]) == set(load_tasks())
    for workflow_id in load_workflows():
        assert len(tasks_for("dev", workflow_id)) == 5


def test_committed_suite_matches_generator_output():
    tasks_dir = get_settings().eval_dir / "tasks"
    for task in build_suite():
        committed = yaml.safe_load((tasks_dir / f"{task['task_id']}.yaml").read_text())
        assert committed == task, task["task_id"]
    assert split_task_ids([t["task_id"] for t in build_suite()]) == load_splits()


def test_every_task_configures_its_workflow_validators():
    workflows_dict = load_workflows()
    for task in load_tasks().values():
        check_task_validators(workflows_dict[task.workflow_id], task)


def test_step_checks_target_llm_steps():
    workflows_dict = load_workflows()
    for task in load_tasks().values():
        llm_step_set = {s.step_id for s in workflows_dict[task.workflow_id].llm_steps}
        assert set(task.expected.step_checks) == llm_step_set, task.task_id


def test_fixtures_load_and_every_ref_resolves():
    backends = build_backends()
    for task in load_tasks().values():
        refs_dict = backends.reset(task.fixtures)
        used_set = set(REF_RE.findall(yaml.safe_dump(task.model_dump())))
        assert used_set <= set(refs_dict), (task.task_id, used_set - set(refs_dict))
        inputs_dict = resolve_inputs(task, refs_dict)
        assert "{{" not in yaml.safe_dump(inputs_dict)


def test_crm_tasks_have_unique_largest_deal():
    for task in tasks_for("dev", "crm_pipeline") + tasks_for("heldout", "crm_pipeline"):
        stage = task.inputs["stage"]
        stage_set = {stage} if stage else {"NEW", "SCREENING", "MEETING", "PROPOSAL"}
        amount_list = [
            o["amount"] for o in task.fixtures["crm"]["opportunities"] if o["stage"] in stage_set
        ]
        assert amount_list.count(max(amount_list)) == 1


def test_reset_clears_previous_task_state():
    backends = build_backends()
    tasks_dict = load_tasks()
    backends.reset(tasks_dict["crm_pipeline_001"].fixtures)
    backends.reset(tasks_dict["slack_digest_001"].fixtures)
    assert backends.crm.list_opportunities(None) == {"opportunities": []}


def test_resets_are_deterministic_for_demo_replay():
    backends = build_backends()
    task = load_tasks()["crm_pipeline_003"]
    first = backends.reset(task.fixtures)
    second = backends.reset(task.fixtures)
    assert first == second
