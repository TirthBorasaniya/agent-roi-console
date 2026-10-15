"""Model price table from configs/pricing.yaml; the self-hosted price comes from throughput."""

# ============= Standard Library =============
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.settings import REPO_ROOT

# ============= Constants =============
logger = logging.getLogger(__name__)

PRICING_FILE = "pricing.yaml"
TOKENS_PER_MTOK = 1_000_000
SECONDS_PER_HOUR = 3600


# ============= Types =============


class MissingPriceError(KeyError):
    """Raised when a model has no usable price."""


@dataclass(frozen=True)
class ModelPrice:
    """Price of one model in USD per million tokens."""

    model: str
    provider: str
    provider_model: str
    input_per_mtok: float
    output_per_mtok: float
    source: str
    as_of: str
    o_computed: bool = False


# ============= Self-Hosted Price =============


def price_per_mtok(host_hour_usd: float, tokens_per_second: float) -> float:
    """
    Convert a host hourly price and a sustained token rate into USD per million tokens.

    Implements SPEC 5.4: host_hour_usd / 3600 / tokens_per_second * 1e6.

    Parameters
    ----------
    host_hour_usd : float
        On-demand hourly price of the comparable cloud instance.
    tokens_per_second : float
        Sustained tokens per second measured at the configured parallelism.

    Returns
    -------
    price : float
        USD per million tokens.
    """
    if tokens_per_second <= 0:
        raise ValueError("tokens_per_second must be positive")
    if host_hour_usd < 0:
        raise ValueError("host_hour_usd must be non-negative")
    return host_hour_usd / SECONDS_PER_HOUR / tokens_per_second * TOKENS_PER_MTOK


def computed_selfhosted_price(
    model: str, entry_dict: dict[str, Any], selfhosted_dict: dict[str, Any], results_path: Path
) -> ModelPrice:
    """
    Build the self-hosted model's price from the throughput results file.

    Parameters
    ----------
    model : str
        Gateway model id.
    entry_dict : dict
        The model's pricing.yaml entry.
    selfhosted_dict : dict
        The `selfhosted` section with host_hour_usd and its source.
    results_path : Path
        results/selfhosted_throughput.json.

    Returns
    -------
    price : ModelPrice
        Computed input and output prices.

    Raises
    ------
    MissingPriceError
        When the throughput has not been measured yet.
    """
    if not results_path.is_file():
        raise MissingPriceError(f"{model}: {results_path} not measured yet")
    metrics_dict = json.loads(results_path.read_text(encoding="utf-8"))["metrics"]
    host_hour_usd = float(selfhosted_dict["host_hour_usd"])
    return ModelPrice(
        model=model,
        provider=entry_dict["provider"],
        provider_model=entry_dict["provider_model"],
        input_per_mtok=price_per_mtok(host_hour_usd, metrics_dict["prompt_tokens_per_second"]),
        output_per_mtok=price_per_mtok(host_hour_usd, metrics_dict["output_tokens_per_second"]),
        source=f"computed from {results_path.name} and {selfhosted_dict['host_hour_source']}",
        as_of=str(selfhosted_dict["as_of"]),
        o_computed=True,
    )


# ============= Table =============


class PriceTable:
    """
    Lookup of model prices and cost arithmetic.

    Parameters
    ----------
    prices_dict : dict
        Model id to ModelPrice.
    unpriced_dict : dict
        Model id to the reason it has no price yet.
    """

    def __init__(self, prices_dict: dict[str, ModelPrice], unpriced_dict: dict[str, str]) -> None:
        self._prices_dict = prices_dict
        self.unpriced_dict = unpriced_dict

    @classmethod
    def load(cls, path: Path | None = None, repo_root: Path = REPO_ROOT) -> "PriceTable":
        """Load configs/pricing.yaml and compute the self-hosted price when measured."""
        data = load_yaml(path or config_path(PRICING_FILE))
        prices_dict: dict[str, ModelPrice] = {}
        unpriced_dict: dict[str, str] = {}
        for model, entry_dict in data["models"].items():
            if entry_dict.get("pricing") == "computed":
                results_path = repo_root / data["selfhosted"]["throughput_results"]
                try:
                    prices_dict[model] = computed_selfhosted_price(
                        model, entry_dict, data["selfhosted"], results_path
                    )
                except MissingPriceError as exc:
                    logger.warning(f"no price for {model}: {exc}")
                    unpriced_dict[model] = str(exc)
                continue
            prices_dict[model] = ModelPrice(
                model=model,
                provider=entry_dict["provider"],
                provider_model=entry_dict["provider_model"],
                input_per_mtok=float(entry_dict["input_per_mtok"]),
                output_per_mtok=float(entry_dict["output_per_mtok"]),
                source=entry_dict["source"],
                as_of=str(entry_dict["as_of"]),
            )
        return cls(prices_dict, unpriced_dict)

    def models(self) -> list[str]:
        """Return priced model ids."""
        return sorted(self._prices_dict)

    def has(self, model: str) -> bool:
        """True when the model has a price."""
        return model in self._prices_dict

    def get(self, model: str) -> ModelPrice:
        """Return a model's price or raise MissingPriceError."""
        if model not in self._prices_dict:
            reason = self.unpriced_dict.get(model, "not in configs/pricing.yaml")
            raise MissingPriceError(f"{model}: {reason}")
        return self._prices_dict[model]

    def estimate(self, model: str, prompt_tokens: int, max_tokens: int) -> float:
        """Pre-call estimate: prompt tokens times input price plus max_tokens times output price."""
        price = self.get(model)
        return (
            prompt_tokens * price.input_per_mtok + max_tokens * price.output_per_mtok
        ) / TOKENS_PER_MTOK

    def actual(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Post-call cost from reported usage."""
        price = self.get(model)
        return (
            input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
        ) / TOKENS_PER_MTOK
