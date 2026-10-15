"""Download the self-hosted GGUF named in configs/selfhosted.yaml and verify its SHA-256."""

# ============= Standard Library =============
import argparse
from pathlib import Path

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.settings import REPO_ROOT
from roiconsole.selfhosted.download import download_model

# ============= Constants =============
DEFAULT_DEST_DIR = REPO_ROOT / "models"


# ============= Entry Point =============


def main() -> None:
    """Download once into --dest-dir (default ./models)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest-dir", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--o-run-from-scratch", action="store_true")
    args = parser.parse_args()
    model_dict = load_yaml(config_path("selfhosted.yaml"))["model"]
    path = download_model(
        model_dict["url"],
        args.dest_dir / model_dict["filename"],
        model_dict["sha256"],
        o_run_from_scratch=args.o_run_from_scratch,
    )
    print(f"model ready: {path}")


if __name__ == "__main__":
    main()
