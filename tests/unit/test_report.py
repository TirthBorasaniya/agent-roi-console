"""Unit tests for README rendering from results files."""

# ============= Standard Library =============
import json

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.report import render_document

pytestmark = pytest.mark.unit
DOC = (
    "intro\n<!-- results:e5:start -->\nold\n<!-- results:e5:end -->\n"
    "<!-- results:unknown:start -->keep<!-- results:unknown:end -->\n"
)


def test_block_stays_empty_without_results():
    rendered = render_document(DOC)
    assert "<!-- results:e5:start --><!-- results:e5:end -->" in rendered
    assert "keep" in rendered
    assert render_document(rendered) == rendered


def test_renders_table_from_results_and_is_idempotent():
    results_dir = get_settings().results_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    stream = {
        "expected": [{"action": "downgrade", "at_run": 20}],
        "observed": [{"action": "downgrade", "at_run": 20}],
        "o_passed": True,
    }
    payload = {"run_id": "r1", "git_sha": "abc", "metrics": {"streams": {"negative": stream}}}
    (results_dir / "e5_guard.json").write_text(json.dumps(payload))
    rendered = render_document(DOC)
    assert "| negative | downgrade@20 | downgrade@20 | yes |" in rendered
    assert render_document(rendered) == rendered
