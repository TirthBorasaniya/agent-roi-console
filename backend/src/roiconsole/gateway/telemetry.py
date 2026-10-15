"""OpenTelemetry GenAI spans for model calls, exported over OTLP when configured."""

# ============= Standard Library =============
import logging
from typing import Any

# ============= Third-Party =============
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

# ============= Constants =============
logger = logging.getLogger(__name__)
TRACER_NAME = "roiconsole.gateway"
OTLP_TRACES_PATH = "/v1/traces"
_PROVIDER: TracerProvider | None = None


# ============= Setup =============


def setup_tracing(
    service_name: str, otlp_endpoint: str, exporter: SpanExporter | None = None
) -> TracerProvider:
    """
    Install a tracer provider once per process.

    Parameters
    ----------
    service_name : str
        service.name resource attribute.
    otlp_endpoint : str
        Collector base URL, e.g. http://otel-collector:4318; empty disables export.
    exporter : SpanExporter or None
        Explicit exporter, used by tests (in-memory).

    Returns
    -------
    provider : TracerProvider
        The active provider.
    """
    global _PROVIDER
    if _PROVIDER is not None and exporter is None:
        return _PROVIDER
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is None and otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint.rstrip("/") + OTLP_TRACES_PATH)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    _PROVIDER = provider
    return provider


def tracer(provider: TracerProvider | None = None) -> trace.Tracer:
    """Return the gateway tracer."""
    return (provider or _PROVIDER or trace.get_tracer_provider()).get_tracer(TRACER_NAME)


# ============= Attributes =============


def chat_span_attributes(
    model: str,
    provider: str,
    response_model: str,
    input_tokens: int,
    output_tokens: int,
    metadata_dict: dict[str, Any],
    cost_usd: float,
    cache_kind: str,
    fallback_depth: int,
) -> dict[str, Any]:
    """
    Build the SPEC 5.6 attribute set for a `chat {model}` span.

    Returns
    -------
    attributes : dict
        gen_ai.* semantic convention attributes plus roi.* attributes.
    """
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "gen_ai.response.model": response_model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "roi.workflow_id": metadata_dict.get("workflow_id", ""),
        "roi.run_id": metadata_dict.get("run_id", ""),
        "roi.step_id": metadata_dict.get("step_id", ""),
        "roi.cost_usd": cost_usd,
        "roi.cache": cache_kind,
        "roi.fallback_depth": fallback_depth,
    }
