"""Adapter for Anthropic's Messages API, translating from and to the OpenAI chat format."""

# ============= Standard Library =============
import json
from typing import Any

# ============= Third-Party =============
import anthropic

# ============= Local =============
from roiconsole.gateway.adapters.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    status_error,
)

# ============= Constants =============
STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
    "pause_turn": "stop",
}
JSON_INSTRUCTION = "Respond with a single JSON object only, with no surrounding text."


# ============= Translation =============


def _text_of(content: Any) -> str:
    """Flatten OpenAI message content (string or parts) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def to_anthropic_messages(
    message_list: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Convert OpenAI chat messages to an Anthropic system string and message list.

    System messages are joined into the top-level system prompt. Assistant tool
    calls become tool_use blocks, and consecutive tool messages become one user
    message of tool_result blocks, as the Messages API requires.

    Returns
    -------
    system : str
        Joined system prompt.
    messages : list of dict
        Anthropic messages.
    """
    system_list: list[str] = []
    converted_list: list[dict[str, Any]] = []
    for message in message_list:
        role = message.get("role")
        if role == "system":
            system_list.append(_text_of(message.get("content")))
        elif role == "user":
            converted_list.append({"role": "user", "content": _text_of(message.get("content"))})
        elif role == "assistant":
            block_list: list[dict[str, Any]] = []
            text = _text_of(message.get("content"))
            if text:
                block_list.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                block_list.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": function.get("name", ""),
                        "input": arguments,
                    }
                )
            converted_list.append({"role": "assistant", "content": block_list or ""})
        elif role == "tool":
            result_block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id", ""),
                "content": _text_of(message.get("content")),
            }
            previous = converted_list[-1] if converted_list else None
            if (
                previous
                and previous["role"] == "user"
                and isinstance(previous["content"], list)
                and all(b.get("type") == "tool_result" for b in previous["content"])
            ):
                previous["content"].append(result_block)
            else:
                converted_list.append({"role": "user", "content": [result_block]})
    return "\n\n".join(s for s in system_list if s), converted_list


def to_anthropic_tools(tool_list: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert OpenAI function tools to Anthropic tool definitions."""
    return [
        {
            "name": tool["function"]["name"],
            "description": tool["function"].get("description", ""),
            "input_schema": tool["function"].get("parameters") or {"type": "object"},
        }
        for tool in tool_list or []
    ]


def from_anthropic_message(message: Any) -> ProviderResponse:
    """Normalize an Anthropic Message into a ProviderResponse."""
    text_list: list[str] = []
    tool_call_list: list[dict[str, Any]] = []
    for block in message.content:
        if block.type == "text":
            text_list.append(block.text)
        elif block.type == "tool_use":
            tool_call_list.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": json.dumps(block.input)},
                }
            )
    return ProviderResponse(
        content="".join(text_list) or None,
        tool_calls=tool_call_list,
        finish_reason=STOP_REASON_MAP.get(message.stop_reason or "end_turn", "stop"),
        input_tokens=int(message.usage.input_tokens),
        output_tokens=int(message.usage.output_tokens),
        response_model=message.model,
    )


# ============= Adapter =============


class AnthropicAdapter:
    """
    Anthropic Messages API adapter.

    The SDK's own retries are disabled (max_retries=0) because the gateway owns
    retries, timeouts, and fallback. Sampling parameters are not forwarded:
    current Haiku models reject non-default temperature.

    Parameters
    ----------
    api_key : str
        ANTHROPIC_API_KEY.
    client : anthropic.AsyncAnthropic or None
        Injected client for tests.
    """

    provider = "anthropic"

    def __init__(self, api_key: str, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Translate, call messages.create, and normalize."""
        system, message_list = to_anthropic_messages(request.messages)
        if request.response_format and request.response_format.get("type") == "json_object":
            system = f"{system}\n\n{JSON_INSTRUCTION}".strip()
        kwargs: dict[str, Any] = {
            "model": request.provider_model,
            "max_tokens": request.max_tokens,
            "messages": message_list,
        }
        if system:
            kwargs["system"] = system
        tool_list = to_anthropic_tools(request.tools)
        if tool_list:
            kwargs["tools"] = tool_list
            kwargs["tool_choice"] = {"type": "auto"}
        try:
            message = await self._client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            raise ProviderError(f"timeout: {exc}", None, True, "timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}", None, True, "connection") from exc
        except anthropic.APIStatusError as exc:
            raise status_error(exc.status_code, str(exc.message)) from exc
        return from_anthropic_message(message)
