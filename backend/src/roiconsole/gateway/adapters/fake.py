"""Deterministic fake provider for tests, CI integration runs, and gateway fault experiments."""

# ============= Standard Library =============
import asyncio
import hashlib
import json
from collections.abc import Callable

# ============= Local =============
from roiconsole.gateway.adapters.base import ProviderRequest, ProviderResponse

# ============= Constants =============
CHARS_PER_TOKEN = 4


# ============= Helpers =============


def approx_tokens(text: str) -> int:
    """Approximate a token count as one token per four characters."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def echo_responder(request: ProviderRequest) -> ProviderResponse:
    """Answer with a digest of the last user message, so answers differ per prompt."""
    last_user = next(
        (m.get("content") or "" for m in reversed(request.messages) if m.get("role") == "user"), ""
    )
    digest = hashlib.sha256(str(last_user).encode("utf-8")).hexdigest()[:12]
    if request.response_format and request.response_format.get("type") == "json_object":
        content = json.dumps({"answer": digest})
    else:
        content = f"answer {digest}"
    prompt_text = json.dumps(request.messages)
    return ProviderResponse(
        content=content,
        input_tokens=approx_tokens(prompt_text),
        output_tokens=approx_tokens(content),
        response_model=request.provider_model,
    )


# ============= Adapter =============


class FakeAdapter:
    """
    Provider that answers with a responder function after a fixed latency.

    Parameters
    ----------
    provider : str
        Provider label.
    responder : callable
        Maps a ProviderRequest to a ProviderResponse.
    latency_s : float
        Simulated provider latency.
    """

    def __init__(
        self,
        provider: str = "fake",
        responder: Callable[[ProviderRequest], ProviderResponse] = echo_responder,
        latency_s: float = 0.0,
    ) -> None:
        self.provider = provider
        self._responder = responder
        self._latency_s = latency_s
        self.calls = 0

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Sleep for the latency and return the responder's answer."""
        self.calls += 1
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        return self._responder(request)
