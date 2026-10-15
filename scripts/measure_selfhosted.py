"""Measure llama.cpp throughput and write results/selfhosted_throughput.json with its price."""

# ============= Standard Library =============
import argparse
import asyncio
import platform
import subprocess

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.results import write_results
from roiconsole.common.settings import get_settings
from roiconsole.gateway.pricing import price_per_mtok
from roiconsole.selfhosted.throughput import measure

# ============= Constants =============
SELFHOSTED_FILE = "selfhosted.yaml"
PRICING_FILE = "pricing.yaml"


# ============= Helpers =============


def host_description() -> str:
    """Describe the measurement host, including the CPU brand on macOS."""
    brand = ""
    if platform.system() == "Darwin":
        try:
            brand = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            brand = ""
    return ", ".join(p for p in (brand, platform.machine(), platform.platform()) if p)


# ============= Entry Point =============


def main() -> None:
    """Run the measurement against SELFHOSTED_BASE_URL and write the results file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=None)
    parser.add_argument("--host-description", default=None)
    args = parser.parse_args()
    settings = get_settings()
    selfhosted_path = config_path(SELFHOSTED_FILE)
    selfhosted_dict = load_yaml(selfhosted_path)
    pricing_dict = load_yaml(config_path(PRICING_FILE))["selfhosted"]
    measurement_dict = selfhosted_dict["measurement"]
    metrics_dict = asyncio.run(
        measure(
            base_url=settings.selfhosted_base_url,
            model=selfhosted_dict["model"]["alias"],
            prompt_list=measurement_dict["prompts"],
            max_tokens=measurement_dict["max_tokens"],
            temperature=measurement_dict["temperature"],
            parallel=selfhosted_dict["server"]["parallel"],
            duration_s=args.duration_s or measurement_dict["duration_s"],
            warmup_requests=measurement_dict["warmup_requests"],
        )
    )
    host_hour_usd = float(pricing_dict["host_hour_usd"])
    metrics_dict["host_hour_usd"] = host_hour_usd
    metrics_dict["output_price_per_mtok"] = price_per_mtok(
        host_hour_usd, metrics_dict["output_tokens_per_second"]
    )
    metrics_dict["input_price_per_mtok"] = price_per_mtok(
        host_hour_usd, metrics_dict["prompt_tokens_per_second"]
    )
    metrics_dict["host"] = args.host_description or host_description()
    metrics_dict["served_model"] = selfhosted_dict["model"]["served_model"]
    metrics_dict["model_file"] = selfhosted_dict["model"]["filename"]
    metrics_dict["model_sha256"] = selfhosted_dict["model"]["sha256"]
    metrics_dict["server_image"] = selfhosted_dict["server"]["image"]
    path = write_results(
        "selfhosted_throughput",
        "selfhosted_throughput",
        selfhosted_path,
        metrics_dict,
        notes=selfhosted_dict["model"]["description"],
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
