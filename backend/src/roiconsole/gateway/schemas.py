"""Request and response shapes of the OpenAI-compatible gateway API."""

# ============= Standard Library =============
import time
import uuid
from typing import Any, Literal

# ============= Third-Party =============
from pydantic import BaseModel, ConfigDict, Field

# ============= Constants =============
DEFAULT_MAX_TOKENS = 1024
METADATA_FIELDS = ("workflow_id", "run_id", "step_id", "step_class")


# ============= Request =============


class RequestMetadata(BaseModel):
    """Attribution metadata every request carries (SPEC 5.1)."""

    model_config = ConfigDict(extra="allow")

    workflow_id: str
    run_id: str
    step_id: str
    step_class: str
    # evaluations turn caching off so repeated prompts are measured, not served from cache
    cache: Literal["on", "off"] = "on"


class ChatCompletionRequest(BaseModel):
    """POST /v1/chat/completions body; unknown OpenAI fields are accepted and ignored."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = None
    response_format: dict[str, Any] | None = None
    stream: bool = False
    metadata: dict[str, Any] | None = None

    @property
    def resolved_max_tokens(self) -> int:
        """Return max_tokens, max_completion_tokens, or the default."""
        return self.max_tokens or self.max_completion_tokens or DEFAULT_MAX_TOKENS

    def generation_params(self) -> dict[str, Any]:
        """Return everything that determines the output, for exact cache keys."""
        return {
            "messages": self.messages,
            "tools": self.tools,
            "tool_choice": self.tool_choice,
            "max_tokens": self.resolved_max_tokens,
            "temperature": self.temperature,
            "response_format": self.response_format,
        }


# ============= Response =============


def completion_response(
    model: str,
    content: str | None,
    tool_calls: list[dict[str, Any]],
    finish_reason: str,
    input_tokens: int,
    output_tokens: int,
    roi_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Build an OpenAI chat.completion object with gateway details under `x_roi`.

    Returns
    -------
    response : dict
        choices[0].message carries content and tool_calls; usage carries tokens.
    """
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "x_roi": roi_dict,
    }


def stream_chunks(response_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Split a completed response into OpenAI chat.completion.chunk objects.

    The gateway calls providers without streaming so that budgets, caching, and
    cost attribution see final usage; clients that ask for a stream receive the
    result as a role chunk, a content or tool-call chunk, and a final chunk.
    """
    base = {k: response_dict[k] for k in ("id", "created", "model")}
    choice = response_dict["choices"][0]
    message = choice["message"]
    delta_dict: dict[str, Any] = {}
    if message.get("content"):
        delta_dict["content"] = message["content"]
    if message.get("tool_calls"):
        delta_dict["tool_calls"] = [
            {**call, "index": i} for i, call in enumerate(message["tool_calls"])
        ]
    chunk_list = [
        {
            **base,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        },
        {
            **base,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta_dict, "finish_reason": None}],
        },
        {
            **base,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
            "usage": response_dict["usage"],
            "x_roi": response_dict["x_roi"],
        },
    ]
    return chunk_list
