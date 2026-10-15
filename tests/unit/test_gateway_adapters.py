"""Unit tests for the OpenAI-compatible and Anthropic adapters' request and response translation."""

# ============= Standard Library =============
import json
from types import SimpleNamespace

# ============= Third-Party =============
import httpx
import pytest
import respx

# ============= Local =============
from roiconsole.gateway.adapters.anthropic import (
    AnthropicAdapter,
    from_anthropic_message,
    to_anthropic_messages,
    to_anthropic_tools,
)
from roiconsole.gateway.adapters.base import ProviderError, ProviderRequest
from roiconsole.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from roiconsole.gateway.adapters.replay import Recorder, ReplayAdapter

pytestmark = pytest.mark.unit

TOOL = {
    "type": "function",
    "function": {
        "name": "slack__post_message",
        "description": "Post",
        "parameters": {"type": "object"},
    },
}


# ============= OpenAI-Compatible =============


@respx.mock
async def test_openai_compatible_parses_tool_calls_and_usage():
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-20b",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "x", "arguments": "{}"},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )
    )
    adapter = OpenAICompatibleAdapter("groq", "https://api.groq.com/openai/v1", "key")
    response = await adapter.complete(
        ProviderRequest("openai/gpt-oss-20b", [{"role": "user", "content": "hi"}], tools=[TOOL])
    )
    assert response.finish_reason == "tool_calls" and response.tool_calls[0]["id"] == "c1"
    assert (response.input_tokens, response.output_tokens) == (12, 3)
    sent = json.loads(respx.calls.last.request.content)
    assert sent["tool_choice"] == "auto" and "temperature" not in sent


@respx.mock
@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False), (401, False)])
async def test_openai_compatible_classifies_errors(status, retryable):
    respx.post("http://llama:8080/v1/chat/completions").mock(
        return_value=httpx.Response(status, text="err")
    )
    adapter = OpenAICompatibleAdapter("selfhosted", "http://llama:8080/v1")
    with pytest.raises(ProviderError) as excinfo:
        await adapter.complete(ProviderRequest("m", [{"role": "user", "content": "hi"}]))
    assert excinfo.value.o_retryable is retryable and excinfo.value.status == status


# ============= Anthropic =============


def test_anthropic_message_translation_groups_tool_results():
    message_list = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "post it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "a", "arguments": '{"x": 1}'},
                },
                {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "ok1"},
        {"role": "tool", "tool_call_id": "t2", "content": "ok2"},
    ]
    system, converted = to_anthropic_messages(message_list)
    assert system == "be brief"
    assert converted[1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "a",
        "input": {"x": 1},
    }
    assert [b["tool_use_id"] for b in converted[2]["content"]] == ["t1", "t2"]
    assert to_anthropic_tools([TOOL])[0]["input_schema"] == {"type": "object"}


def test_anthropic_response_normalization():
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Posting."),
            SimpleNamespace(
                type="tool_use", id="tu1", name="slack__post_message", input={"text": "hi"}
            ),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=40, output_tokens=9),
        model="claude-haiku-5-5",
    )
    response = from_anthropic_message(message)
    assert response.finish_reason == "tool_calls"
    assert json.loads(response.tool_calls[0]["function"]["arguments"]) == {"text": "hi"}
    assert response.content == "Posting." and response.input_tokens == 40


async def test_anthropic_adapter_sends_no_sampling_params_and_adds_json_instruction():
    captured = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="{}")],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                model="claude-haiku-5-5",
            )

    adapter = AnthropicAdapter("key", client=SimpleNamespace(messages=FakeMessages()))
    await adapter.complete(
        ProviderRequest(
            "claude-haiku-5-5",
            [{"role": "user", "content": "x"}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    )
    assert "temperature" not in captured and "JSON" in captured["system"]


# ============= Replay =============


async def test_replay_serves_recorded_and_refuses_unrecorded(tmp_path):
    from roiconsole.gateway.adapters.base import ProviderResponse

    path = tmp_path / "rec.jsonl"
    request = ProviderRequest("openai/gpt-oss-20b", [{"role": "user", "content": "hi"}])
    Recorder(path).record(
        "groq/openai/gpt-oss-20b",
        request,
        ProviderResponse("hello", input_tokens=3, output_tokens=1),
    )
    replay = ReplayAdapter(path, "groq")
    assert (await replay.complete(request)).content == "hello"
    with pytest.raises(ProviderError):
        await replay.complete(
            ProviderRequest("openai/gpt-oss-20b", [{"role": "user", "content": "other"}])
        )
