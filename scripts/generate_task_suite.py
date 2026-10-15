"""Generate the 60-task evaluation suite and the dev/held-out split under eval/."""

# ============= Standard Library =============
import argparse

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.task_suite import SUITE_SEED, write_suite

# ============= Entry Point =============


def main() -> None:
    """Write eval/tasks/*.yaml and eval/splits.yaml."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SUITE_SEED)
    args = parser.parse_args()
    n_tasks, splits_dict = write_suite(get_settings().eval_dir, seed=args.seed)
    n_dev, n_heldout = len(splits_dict["dev"]), len(splits_dict["heldout"])
    print(f"wrote {n_tasks} tasks: {n_dev} dev, {n_heldout} held-out")


if __name__ == "__main__":
    main()
