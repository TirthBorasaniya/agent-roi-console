"""E5: run the ROI guard on simulated run streams and write results/e5_guard.json."""

# ============= Standard Library =============
import asyncio
import os

# ============= Local =============
from roiconsole.common.results import write_results
from roiconsole.common.settings import REPO_ROOT, get_settings
from roiconsole.evaluation.e5_guard import run_e5

# ============= Constants =============
SCRATCH_DB = REPO_ROOT / ".tmp" / "e5_guard.db"


# ============= Entry Point =============


def main() -> None:
    """Run E5 on a scratch database."""
    SCRATCH_DB.parent.mkdir(parents=True, exist_ok=True)
    SCRATCH_DB.unlink(missing_ok=True)
    os.environ["ROI_DB_PATH"] = str(SCRATCH_DB)
    get_settings.cache_clear()
    metrics_dict, config_file = asyncio.run(run_e5())
    path = write_results(
        "e5_guard", "e5_guard", config_file, metrics_dict, "simulated streams, scratch database"
    )
    passed, total = metrics_dict["streams_passed"], metrics_dict["streams_total"]
    print(f"wrote {path}: {passed}/{total} streams as expected")


if __name__ == "__main__":
    main()
