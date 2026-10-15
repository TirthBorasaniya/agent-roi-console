"""Unit tests for idempotency keys and the first-result-wins store."""

# ============= Standard Library =============
import threading

# ============= Third-Party =============
import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ============= Local =============
from roiconsole.common.ids import idempotency_key
from roiconsole.mcp_servers.common import CallContext, IdempotencyStore, idempotent_write

pytestmark = pytest.mark.unit


# ============= Keys =============


def test_key_is_stable_across_argument_order():
    a = idempotency_key("run", "step", {"x": 1, "y": [1, 2]})
    b = idempotency_key("run", "step", {"y": [1, 2], "x": 1})
    assert a == b and len(a) == 32


def test_key_changes_with_run_step_or_arguments():
    base = idempotency_key("run", "step", {"x": 1})
    assert base != idempotency_key("run2", "step", {"x": 1})
    assert base != idempotency_key("run", "step2", {"x": 1})
    assert base != idempotency_key("run", "step", {"x": 2})


def test_key_ignores_an_embedded_idempotency_key():
    assert idempotency_key("r", "s", {"x": 1}) == idempotency_key(
        "r", "s", {"x": 1, "idempotency_key": "abc"}
    )


# ============= Store =============


def test_repeated_write_returns_first_result_without_second_write(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.db")
    context = CallContext("run", "step")
    calls_list = []

    def write():
        calls_list.append(1)
        return {"ts": str(len(calls_list))}

    first = idempotent_write(store, "slack-mcp", "post", context, {}, "k", write)
    second = idempotent_write(store, "slack-mcp", "post", context, {}, "k", write)
    assert calls_list == [1]
    assert first["ts"] == second["ts"] == "1"
    assert not first["idempotent_replay"] and second["idempotent_replay"]


def test_failed_write_releases_key_so_retry_writes(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.db")
    context = CallContext("run", "step")

    def failing():
        raise ToolError("boom")

    with pytest.raises(ToolError):
        idempotent_write(store, "s", "t", context, {}, "k", failing)
    result = idempotent_write(store, "s", "t", context, {}, "k", lambda: {"ok": True})
    assert result["ok"] and not result["idempotent_replay"]


def test_empty_key_is_rejected(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.db")
    with pytest.raises(ToolError):
        idempotent_write(store, "s", "t", CallContext("r", "s"), {}, "", lambda: {})


def test_concurrent_duplicates_write_once(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.db")
    context = CallContext("run", "step")
    lock = threading.Lock()
    writes_list: list[int] = []
    results_list: list[dict] = []

    def write():
        with lock:
            writes_list.append(1)
        return {"n": 1}

    def worker():
        results_list.append(idempotent_write(store, "s", "t", context, {}, "same", write))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(writes_list) == 1
    assert len(results_list) == 8
    assert sum(not r["idempotent_replay"] for r in results_list) == 1
