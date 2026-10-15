"""Adapter for OpenAI-compatible chat completion APIs: Groq and the self-hosted llama.cpp server."""

# ============= Standard Library =============
from typing import Any

# ============= Third-Party =============
import httpx

# ============= Local =============
from roiconsole.gateway.adapters.base import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    status_error,
)

# ============= Constants =============
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


# ============= Adapter =============


class OpenAICompatibleAdapter:
    """
    POST /chat/completions against any OpenAI-compatible server.

    Parameters
    ----------
    provider : str
        Provider label, e.g. "groq" or "selfhosted".
    base_url : str
        API base including /v1.
    api_key : str
        Bearer token; empty for an unauthenticated local server.
    http_client : httpx.AsyncClient or None
        Injected client for tests. Timeouts are enforced by the gateway.
    """

    def __init__(
        self,
        provider: str,
        base_url: str,
        api_key: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=None
        )

    def _body(self, request: ProviderRequest) -> dict[str, Any]:
        """Build the request body, omitting unset optional fields."""
        body: dict[str, Any] = {
            "model": request.provider_model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            body["tools"] = request.tools
            body["tool_choice"] = request.tool_choice or "auto"
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.response_format:
            body["response_format"] = request.response_format
        return body

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Send the request and normalize the first choice."""
        try:
            response = await self._client.post("/chat/completions", json=self._body(request))
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timeout: {exc}", None, True, "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"connection error: {exc}", None, True, "connection") from exc
        if response.status_code >= 400:
            raise status_error(response.status_code, response.text)
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderError(
                f"malformed response: {exc}", 200, False, "invalid_response"
            ) from exc
        usage = payload.get("usage") or {}
        return ProviderResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls") or [],
            finish_reason=choice.get("finish_reason") or "stop",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            response_model=payload.get("model", request.provider_model),
        )

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
