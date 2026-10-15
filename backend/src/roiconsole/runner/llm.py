"""Gateway client for the runner: OpenAI-compatible calls with attribution metadata."""

# ============= Standard Library =============
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# ============= Third-Party =============
import httpx

# ============= Local =============
from roiconsole.gateway.schemas import ChatCompletionRequest
from roiconsole.gateway.service import GatewayError, GatewayService

# ============= Constants =============
GATEWAY_TIMEOUT_S = 300.0
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# ============= Types =============


class LlmCallError(RuntimeError):
    """
    A gateway call that did not return a completion.

    Parameters
    ----------
    code : str
        Gateway error type, e.g. budget_exhausted or all_providers_failed.
    message : str
        Description.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class LlmResult:
    """One completion with its attribution."""

    content: str | None
    tool_calls: list[dict[str, Any]]
    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache: str
    fallback_depth: int
    latency_s: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallMeta:
    """Attribution metadata for one call."""

    workflow_id: str
    run_id: str
    step_id: str
    step_class: str
    o_cache: bool = True


class LlmClient(Protocol):
    """Anything that can serve a chat completion for the runner."""

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        meta: CallMeta,
        max_tokens: int = 512,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LlmResult:
        """Return a completion or raise LlmCallError."""
        ...


# ============= Helpers =============


def build_body(
    model: str,
    messages: list[dict[str, Any]],
    meta: CallMeta,
    max_tokens: int,
    tools: list[dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a /v1/chat/completions body with metadata."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "metadata": {
            "workflow_id": meta.workflow_id,
            "run_id": meta.run_id,
            "step_id": meta.step_id,
            "step_class": meta.step_class,
            "cache": "on" if meta.o_cache else "off",
        },
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
    return body


def parse_result(payload: dict[str, Any], latency_s: float) -> LlmResult:
    """Convert a chat.completion with x_roi into an LlmResult."""
    message = payload["choices"][0]["message"]
    roi = payload.get("x_roi", {})
    usage = payload.get("usage", {})
    return LlmResult(
        content=message.get("content"),
        tool_calls=message.get("tool_calls") or [],
        model=roi.get("model", payload.get("model", "")),
        cost_usd=float(roi.get("cost_usd", 0.0)),
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cache=roi.get("cache", "miss"),
        fallback_depth=int(roi.get("fallback_depth", 0)),
        latency_s=latency_s,
        raw=payload,
    )


def parse_json_output(text: str | None) -> Any:
    """
    Parse a model's JSON answer, tolerating code fences and leading prose.

    Raises
    ------
    ValueError
        When no JSON object can be decoded.
    """
    if not text:
        raise ValueError("empty model output")
    cleaned = CODE_FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in output: {text[:200]!r}") from None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in output: {exc}") from exc


# ============= Clients =============


class HttpGatewayClient:
    """
    Calls the gateway over HTTP.

    Parameters
    ----------
    base_url : str
        Gateway base including /v1, e.g. http://localhost:8090/v1.
    api_key : str
        GATEWAY_API_KEY, empty when the gateway is unauthenticated.
    http_client : httpx.AsyncClient or None
        Injected client for tests.
    """

    def __init__(
        self, base_url: str, api_key: str = "", http_client: httpx.AsyncClient | None = None
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=GATEWAY_TIMEOUT_S
        )

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        meta: CallMeta,
        max_tokens: int = 512,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LlmResult:
        """POST /chat/completions."""
        body = build_body(model, messages, meta, max_tokens, tools, response_format)
        started = time.monotonic()
        try:
            response = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LlmCallError("gateway_unreachable", str(exc)) from exc
        payload = response.json()
        if response.status_code >= 400:
            error = payload.get("error", {})
            raise LlmCallError(
                error.get("type", f"http_{response.status_code}"), error.get("message", "")
            )
        return parse_result(payload, time.monotonic() - started)

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class InProcessGatewayClient:
    """
    Calls a GatewayService in the same process (same code path, no HTTP hop).

    Parameters
    ----------
    service : GatewayService
        Built with roiconsole.gateway.app.build_service.
    """

    def __init__(self, service: GatewayService) -> None:
        self.service = service

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        meta: CallMeta,
        max_tokens: int = 512,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LlmResult:
        """Call service.complete directly."""
        body = build_body(model, messages, meta, max_tokens, tools, response_format)
        started = time.monotonic()
        try:
            payload = await self.service.complete(ChatCompletionRequest.model_validate(body))
        except GatewayError as exc:
            raise LlmCallError(exc.code, str(exc)) from exc
        return parse_result(payload, time.monotonic() - started)
