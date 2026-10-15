"""Measure sustained llama.cpp throughput at the configured parallelism."""

# ============= Standard Library =============
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

# ============= Third-Party =============
import httpx

# ============= Constants =============
logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_S = 300.0
# repeats of the base prompts that make the prefill phase prompts about 1,000 tokens long
PREFILL_REPEATS = 12
PREFILL_DURATION_FRACTION = 1 / 3


# ============= Types =============


@dataclass
class PhaseStats:
    """Accumulated counts for one measurement phase."""

    requests: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_list: list[float] = field(default_factory=list)


# ============= Measurement =============


async def _one_request(
    client: httpx.AsyncClient, model: str, prompt: str, max_tokens: int, temperature: float
) -> dict[str, Any]:
    """Send one chat completion and return the JSON body."""
    response = await client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
    )
    response.raise_for_status()
    return response.json()


async def _stream_worker(
    client: httpx.AsyncClient,
    model: str,
    prompt_list: list[str],
    max_tokens: int,
    temperature: float,
    deadline: float,
    stats: PhaseStats,
    worker_index: int,
) -> None:
    """Issue requests back to back until the deadline, accumulating usage."""
    index = worker_index
    while time.monotonic() < deadline:
        prompt = prompt_list[index % len(prompt_list)]
        index += 1
        started = time.monotonic()
        try:
            body = await _one_request(client, model, prompt, max_tokens, temperature)
        except httpx.HTTPError as exc:
            stats.errors += 1
            logger.warning(f"throughput request failed: {exc}")
            continue
        usage = body.get("usage") or {}
        stats.requests += 1
        stats.prompt_tokens += int(usage.get("prompt_tokens", 0))
        stats.completion_tokens += int(usage.get("completion_tokens", 0))
        stats.latency_list.append(time.monotonic() - started)


async def _run_phase(
    base_url: str,
    model: str,
    prompt_list: list[str],
    max_tokens: int,
    temperature: float,
    parallel: int,
    duration_s: float,
) -> tuple[PhaseStats, float]:
    """Run `parallel` streams for `duration_s` and return stats and elapsed seconds."""
    stats = PhaseStats()
    async with httpx.AsyncClient(base_url=base_url, timeout=REQUEST_TIMEOUT_S) as client:
        started = time.monotonic()
        deadline = started + duration_s
        await asyncio.gather(
            *[
                _stream_worker(
                    client, model, prompt_list, max_tokens, temperature, deadline, stats, i
                )
                for i in range(parallel)
            ]
        )
        elapsed = time.monotonic() - started
    return stats, elapsed


def _percentile(value_list: list[float], q: float) -> float | None:
    """Return the q-quantile by nearest rank, or None for an empty list."""
    if not value_list:
        return None
    ordered = sorted(value_list)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def measure(
    base_url: str,
    model: str,
    prompt_list: list[str],
    max_tokens: int,
    temperature: float,
    parallel: int,
    duration_s: float,
    warmup_requests: int,
) -> dict[str, Any]:
    """
    Measure sustained output and prompt throughput.

    The generation phase runs the configured prompts with max_tokens for
    `duration_s`; output tokens per second is total completion tokens over wall
    time. The prefill phase sends long prompts with max_tokens=1; prompt tokens
    per second is total prompt tokens over wall time.

    Returns
    -------
    metrics : dict
        output_tokens_per_second, prompt_tokens_per_second, request counts,
        error counts, and latency percentiles.
    """
    async with httpx.AsyncClient(base_url=base_url, timeout=REQUEST_TIMEOUT_S) as client:
        for i in range(warmup_requests):
            await _one_request(client, model, prompt_list[i % len(prompt_list)], 16, temperature)
    generation, generation_s = await _run_phase(
        base_url, model, prompt_list, max_tokens, temperature, parallel, duration_s
    )
    prefill_prompt_list = [" ".join([p] * PREFILL_REPEATS) for p in prompt_list]
    prefill, prefill_s = await _run_phase(
        base_url,
        model,
        prefill_prompt_list,
        1,
        temperature,
        parallel,
        duration_s * PREFILL_DURATION_FRACTION,
    )
    if generation.completion_tokens == 0 or prefill.prompt_tokens == 0:
        raise RuntimeError("no tokens measured; is the llama.cpp server up?")
    return {
        "output_tokens_per_second": generation.completion_tokens / generation_s,
        "prompt_tokens_per_second": prefill.prompt_tokens / prefill_s,
        "generation_phase": {
            "seconds": generation_s,
            "requests": generation.requests,
            "errors": generation.errors,
            "prompt_tokens": generation.prompt_tokens,
            "completion_tokens": generation.completion_tokens,
            "latency_p50_s": _percentile(generation.latency_list, 0.5),
            "latency_p95_s": _percentile(generation.latency_list, 0.95),
        },
        "prefill_phase": {
            "seconds": prefill_s,
            "requests": prefill.requests,
            "errors": prefill.errors,
            "prompt_tokens": prefill.prompt_tokens,
        },
        "parallel": parallel,
    }
