"""Unit tests for workflow definitions, step classes, and templating."""

# ============= Third-Party =============
import pytest
from pydantic import ValidationError

# ============= Local =============
from roiconsole.validators.library import VALIDATORS_DICT
from roiconsole.workflows.registry import load_step_classes, load_workflows
from roiconsole.workflows.schema import PATH_NAMES, StepDefinition
from roiconsole.workflows.templating import TemplateRenderError, render_value
from roiconsole.workflows.transforms import meeting_blocks

# ============= Constants =============
EXPECTED_WORKFLOWS = {"slack_digest", "notion_search", "meeting_notes", "crm_pipeline"}

pytestmark = pytest.mark.unit


# ============= Tests =============


def test_four_existing_workflows_load():
    workflows_dict = load_workflows()
    assert set(workflows_dict) == EXPECTED_WORKFLOWS


def test_every_workflow_uses_registered_validators_and_known_step_classes():
    step_class_set = set(load_step_classes().step_classes)
    for workflow in load_workflows().values():
        assert workflow.validators
        assert all(name in VALIDATORS_DICT for name in workflow.validators)
        assert set(workflow.step_classes()) <= step_class_set
        assert workflow.default_path in PATH_NAMES


def test_baseline_assumptions_are_labeled():
    for workflow in load_workflows().values():
        if not workflow.human_baseline.o_timed:
            assert "assumption" in workflow.human_baseline.source.lower()
        assert workflow.review_source and workflow.fix_source


def test_tool_step_requires_known_server():
    with pytest.raises(ValidationError):
        StepDefinition(step_id="x", type="tool", tool="jira.create_issue")


def test_llm_step_requires_step_class():
    with pytest.raises(ValidationError):
        StepDefinition(step_id="x", type="llm", user_prompt="hi")


def test_cacheable_flags_exclude_personal_drafting():
    catalog = load_step_classes().step_classes
    assert catalog["extraction"].cacheable
    assert not catalog["summarization"].cacheable
    assert not catalog["generation_short"].cacheable


def test_render_value_returns_native_values_for_single_expressions():
    context = {"inputs": {"stage": None, "tags": ["a", "b"]}, "steps": {"s": {"output": {"x": 1}}}}
    assert render_value("{{ inputs.stage }}", context) is None
    assert render_value("{{ inputs.tags }}", context) == ["a", "b"]
    assert render_value({"f": "{{ {'k': steps.s.output.x} }}"}, context) == {"f": {"k": 1}}
    assert render_value("tags: {{ inputs.tags | join(', ') }}", context) == "tags: a, b"


def test_render_value_raises_on_missing_values():
    with pytest.raises(TemplateRenderError):
        render_value("{{ steps.missing.output }}", {"steps": {}})


def test_meeting_blocks_builds_todos():
    block_list = meeting_blocks(
        ["Ship it"], [{"owner": "Maya", "task": "send recap", "due": "Oct 3"}]
    )
    todo_list = [b for b in block_list if b["type"] == "to_do"]
    assert todo_list == [
        {"type": "to_do", "text": "Maya: send recap (due Oct 3)", "checked": False}
    ]


def test_meeting_blocks_rejects_bad_shapes():
    with pytest.raises(ValueError):
        meeting_blocks("not a list", [])
