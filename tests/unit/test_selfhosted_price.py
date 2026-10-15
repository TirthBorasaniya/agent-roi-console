"""Unit tests for the computed self-hosted price, the price table, and throughput measurement."""

# ============= Standard Library =============
import json

# ============= Third-Party =============
import httpx
import pytest
import respx

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.gateway.pricing import MissingPriceError, PriceTable, price_per_mtok
from roiconsole.selfhosted.throughput import measure

pytestmark = pytest.mark.unit

SELFHOSTED_MODEL = "selfhosted/qwen2.5-1.5b-instruct-q4_k_m"


# ============= Helpers =============


def write_throughput(root, output_tps: float, prompt_tps: float) -> None:
    """Write a throughput results file under a fake repo root."""
    path = root / "results" / "selfhosted_throughput.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = {"output_tokens_per_second": output_tps, "prompt_tokens_per_second": prompt_tps}
    path.write_text(json.dumps({"metrics": metrics}))


# ============= Tests =============


def test_price_formula_matches_spec():
    # 0.29 USD/h at 20 tok/s: 0.29 / 3600 / 20 * 1e6
    assert price_per_mtok(0.29, 20.0) == pytest.approx(4.027777, rel=1e-5)
    with pytest.raises(ValueError):
        price_per_mtok(0.29, 0.0)


def test_selfhosted_price_is_computed_from_results_file(tmp_path):
    write_throughput(tmp_path, output_tps=40.0, prompt_tps=400.0)
    table = PriceTable.load(repo_root=tmp_path)
    price = table.get(SELFHOSTED_MODEL)
    host_hour = load_yaml(config_path("pricing.yaml"))["selfhosted"]["host_hour_usd"]
    assert price.o_computed
    assert price.output_per_mtok == pytest.approx(price_per_mtok(host_hour, 40.0))
    assert price.input_per_mtok == pytest.approx(price_per_mtok(host_hour, 400.0))


def test_selfhosted_model_is_unpriced_until_measured(tmp_path):
    table = PriceTable.load(repo_root=tmp_path)
    assert SELFHOSTED_MODEL in table.unpriced_dict
    with pytest.raises(MissingPriceError):
        table.get(SELFHOSTED_MODEL)


def test_every_hosted_price_has_source_and_date():
    data = load_yaml(config_path("pricing.yaml"))
    for model, entry in data["models"].items():
        if entry.get("pricing") == "computed":
            continue
        assert entry["source"].startswith("https://"), model
        assert entry["as_of"], model
        assert entry["input_per_mtok"] > 0 and entry["output_per_mtok"] > 0


def test_estimate_and_actual_cost(tmp_path):
    table = PriceTable.load(repo_root=tmp_path)
    model = "groq/openai/gpt-oss-20b"
    # 1,000 prompt tokens at 0.075 plus 500 max tokens at 0.30 per million
    assert table.estimate(model, 1000, 500) == pytest.approx((1000 * 0.075 + 500 * 0.30) / 1e6)
    assert table.actual(model, 1000, 100) == pytest.approx((1000 * 0.075 + 100 * 0.30) / 1e6)


@respx.mock
async def test_measure_counts_completion_and_prompt_tokens():
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        completion = 1 if body["max_tokens"] == 1 else 50
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 100, "completion_tokens": completion}}
        )

    respx.post("http://llama:8080/v1/chat/completions").mock(side_effect=respond)
    metrics = await measure(
        "http://llama:8080/v1",
        "m",
        ["hello"],
        50,
        0.0,
        parallel=2,
        duration_s=0.2,
        warmup_requests=1,
    )
    phase = metrics["generation_phase"]
    assert phase["requests"] > 0 and phase["errors"] == 0
    assert phase["completion_tokens"] == 50 * phase["requests"]
    assert metrics["output_tokens_per_second"] > 0 and metrics["prompt_tokens_per_second"] > 0
    assert metrics["parallel"] == 2
