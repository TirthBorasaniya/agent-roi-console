"""YAML configuration loading with version checks and content hashing."""

# ============= Standard Library =============
import hashlib
from pathlib import Path
from typing import Any

# ============= Third-Party =============
import yaml

# ============= Local =============
from roiconsole.common.settings import REPO_ROOT, get_settings

# ============= Constants =============
SUPPORTED_CONFIG_VERSION = 1


# ============= Loading =============


class ConfigError(ValueError):
    """Raised when a configuration file is missing, malformed, or the wrong version."""


def config_path(name: str) -> Path:
    """
    Resolve a path under the configs directory.

    Parameters
    ----------
    name : str
        Relative path such as "pricing.yaml" or "workflows/slack_digest.yaml".

    Returns
    -------
    path : Path
        Absolute path to the config file.
    """
    return get_settings().configs_dir / name


def load_yaml(path: Path, o_require_version: bool = True) -> dict[str, Any]:
    """
    Load a YAML mapping and check its version field.

    Parameters
    ----------
    path : Path
        File to read.
    o_require_version : bool
        Enforce the `version: 1` field required by CONVENTIONS section 5.

    Returns
    -------
    data : dict
        Parsed mapping.
    """
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"config file is not a mapping: {path}")
    if o_require_version and data.get("version") != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(f"{path} must declare version: {SUPPORTED_CONFIG_VERSION}")
    return data


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_to_repo(path: Path) -> str:
    """Return a repo-relative POSIX path when possible, for results files."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()
