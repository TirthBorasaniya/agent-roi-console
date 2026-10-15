"""Shared pytest configuration: isolate settings from local secrets and real state."""

# ============= Standard Library =============
import os
from pathlib import Path

# never read the developer's .env during tests
os.environ["ROI_ENV_FILE"] = ""

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common import settings as settings_module
from roiconsole.sandbox.store import SandboxStore
from roiconsole.workflows import registry

# ============= Constants =============
CREDENTIAL_ENV_VARS = (
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
    "SLACK_BOT_TOKEN",
    "NOTION_API_KEY",
    "TWENTY_API_KEY",
    "REDIS_URL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


# ============= Fixtures =============


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point data and results at a temp dir and remove credentials for every test."""
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROI_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("ROI_CONNECTOR_MODE", "dryrun")
    settings_module.get_settings.cache_clear()
    registry.clear_caches()
    yield
    settings_module.get_settings.cache_clear()
    registry.clear_caches()


@pytest.fixture
def sandbox_store(tmp_path: Path) -> SandboxStore:
    """Return an empty sandbox store in a temp file."""
    return SandboxStore(tmp_path / "sandbox.db")
