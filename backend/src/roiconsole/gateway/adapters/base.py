"""Provider adapter interface: complete(request) -> response with usage."""

# ============= Standard Library =============
from dataclasses import dataclass, field
from typing import Any, Protocol

# ============= Constants =============
RETRYABLE_STATUS_SET = {408, 409, 425, 429, 500, 502, 503, 504, 529}


# ============= Types =============


@dataclass(frozen=True)
class ProviderRequest:
    """
    A chat request addressed to one provider model, in OpenAI message format.

    Attributes
    ----------
    provider_model : str
        Model id as the provider names it, e.g. "openai/gpt-oss-20b".
    messages : list of dict
        OpenAI chat messages (system, user, assistant with tool_calls, tool).
    tools : list of dict or None
        OpenAI function tool definitions.
    tool_choice : str or dict or None
        OpenAI tool_choice.
    max_tokens : int
        Output token cap.
    temperature : float or None
        Sampling temperature; adapters drop it where the provider rejects it.
    response_format : dict or None
        OpenAI response_format, e.g. {"type": "json_object"}.
    """

    provider_model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    max_tokens: int = 512
    temperature: float | None = None
    response_format: dict[str, Any] | None = None


@dataclass
class ProviderResponse:
    """A provider's answer normalized to OpenAI semantics."""

    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    response_model: str = ""


class ProviderError(RuntimeError):
    """
    A failed provider call.

    Parameters
    ----------
    message : str
        Description.
    status : int or None
        HTTP status when known; None for timeouts and connection errors.
    o_retryable : bool
        True for 429, 5xx, timeouts, and connection errors.
    kind : str
        Short label: http_<status>, timeout, connection, or invalid_response.
    """

    def __init__(self, message: str, status: int | None, o_retryable: bool, kind: str) -> None:
        super().__init__(message)
        self.status = status
        self.o_retryable = o_retryable
        self.kind = kind


def status_error(status: int, body: str) -> ProviderError:
    """Build a ProviderError for an HTTP status."""
    return ProviderError(
        f"provider returned HTTP {status}: {body[:300]}",
        status,
        status in RETRYABLE_STATUS_SET,
        f"http_{status}",
    )


class Adapter(Protocol):
    """A provider adapter."""

    provider: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Run one non-streaming completion."""
        ...
