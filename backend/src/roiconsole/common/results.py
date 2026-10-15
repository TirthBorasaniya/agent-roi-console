"""Results artifact writer following CONVENTIONS section 7."""

# ============= Standard Library =============
import json
import platform
import subprocess
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import relative_to_repo, sha256_file
from roiconsole.common.settings import REPO_ROOT, get_settings

# ============= Constants =============
SCHEMA_VERSION = 1
DEFAULT_PACKAGES = ("fastapi", "langgraph", "mcp", "openai", "anthropic", "redisvl", "httpx")


# ============= Environment =============


def git_sha() -> str:
    """Return the short git SHA of HEAD, or "unknown" outside a repository."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip()


def describe_hardware() -> str:
    """Return a short host description, e.g. "arm64, Darwin 25.0.0"."""
    return f"{platform.machine()}, {platform.system()} {platform.release()}"


def environment_dict(package_list: tuple[str, ...] = DEFAULT_PACKAGES) -> dict[str, Any]:
    """
    Collect the python version, package versions, and hardware string.

    Parameters
    ----------
    package_list : tuple of str
        Distribution names whose installed versions are recorded.

    Returns
    -------
    environment : dict
        Keys python, packages (name to version), hardware.
    """
    packages_dict = {}
    for name in package_list:
        try:
            packages_dict[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages_dict[name] = "not-installed"
    return {
        "python": platform.python_version(),
        "packages": packages_dict,
        "hardware": describe_hardware(),
    }


# ============= Writer =============


def build_run_id(experiment: str, now: datetime | None = None) -> str:
    """Return a run id such as 2026-10-15T14-02-11Z_e1_step_success."""
    now = now or datetime.now(UTC)
    return f"{now.strftime('%Y-%m-%dT%H-%M-%SZ')}_{experiment}"


def write_results(
    name: str,
    experiment: str,
    config_file: Path | None,
    metrics: dict[str, Any],
    notes: str = "",
    extra_dict: dict[str, Any] | None = None,
) -> Path:
    """
    Write one results JSON file to results/<name>.json.

    Parameters
    ----------
    name : str
        File stem, e.g. "e1_step_success".
    experiment : str
        Experiment label included in run_id.
    config_file : Path or None
        Config that produced the results; its path and SHA-256 are recorded.
    metrics : dict
        Experiment metrics.
    notes : str
        Free-text notes, such as which model was served for the self-hosted provider.
    extra_dict : dict or None
        Additional top-level keys, such as per-task records.

    Returns
    -------
    path : Path
        Written file.
    """
    now = datetime.now(UTC)
    payload_dict: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": build_run_id(experiment, now),
        "git_sha": git_sha(),
        "timestamp_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config_path": relative_to_repo(config_file) if config_file else None,
        "config_sha256": sha256_file(config_file) if config_file else None,
        "environment": environment_dict(),
        "metrics": metrics,
        "notes": notes,
    }
    if extra_dict:
        payload_dict.update(extra_dict)
    results_dir = get_settings().results_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{name}.json"
    out_path.write_text(
        json.dumps(payload_dict, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return out_path


def read_results(name: str) -> dict[str, Any] | None:
    """Return a parsed results file, or None when it has not been produced yet."""
    path = get_settings().results_dir / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
