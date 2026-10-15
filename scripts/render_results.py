"""Rewrite the README tables from results/*.json; with --check, fail when the README is stale."""

# ============= Standard Library =============
import argparse
import sys

# ============= Local =============
from roiconsole.common.settings import REPO_ROOT
from roiconsole.evaluation.report import render_document

# ============= Constants =============
README = REPO_ROOT / "README.md"


# ============= Entry Point =============


def main() -> None:
    """Render, or check that the committed README matches the rendering."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = README.read_text(encoding="utf-8")
    rendered = render_document(current)
    if args.check:
        if rendered != current:
            print("README.md tables are stale; run make results", file=sys.stderr)
            sys.exit(1)
        print("README.md tables are current")
        return
    README.write_text(rendered, encoding="utf-8")
    print("README.md tables rendered")


if __name__ == "__main__":
    main()
