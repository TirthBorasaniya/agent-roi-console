"""Typed process settings loaded from environment variables with pydantic-settings."""

# ============= Standard Library =============
import os
from functools import lru_cache
from pathlib import Path

# ============= Third-Party =============
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ============= Constants =============
REPO_ROOT = Path(__file__).resolve().parents[4]
CONNECTOR_MODES = ("live", "dryrun", "demo")
# ROI_ENV_FILE="" disables .env loading, which tests use so local secrets never leak in
ENV_FILE = os.environ.get("ROI_ENV_FILE", ".env") or None


# ============= Settings =============


class Settings(BaseSettings):
    """
    Environment-driven settings shared by the API, gateway, runner, and MCP servers.

    Secrets are read only from the environment. Everything that is not a secret
    lives in YAML under configs/ and is referenced here by path.
    """

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # paths
    configs_dir: Path = Field(default=REPO_ROOT / "configs", alias="ROI_CONFIGS_DIR")
    eval_dir: Path = Field(default=REPO_ROOT / "eval", alias="ROI_EVAL_DIR")
    results_dir: Path = Field(default=REPO_ROOT / "results", alias="ROI_RESULTS_DIR")
    data_dir: Path = Field(default=REPO_ROOT / "data", alias="ROI_DATA_DIR")
    db_path: Path | None = Field(default=None, alias="ROI_DB_PATH")
    sandbox_db_path: Path | None = Field(default=None, alias="ROI_SANDBOX_DB_PATH")

    # connector mode: live calls real APIs, dryrun and demo use the sandbox store
    connector_mode: str | None = Field(default=None, alias="ROI_CONNECTOR_MODE")

    # llm providers
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    selfhosted_base_url: str = Field(
        default="http://localhost:8081/v1", alias="SELFHOSTED_BASE_URL"
    )

    # gateway
    gateway_url: str = Field(default="http://localhost:8090/v1", alias="GATEWAY_URL")
    gateway_api_key: str = Field(default="", alias="GATEWAY_API_KEY")
    gateway_strict: bool = Field(default=True, alias="GATEWAY_STRICT")
    redis_url: str = Field(default="", alias="REDIS_URL")

    # connectors
    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    notion_api_key: str = Field(default="", alias="NOTION_API_KEY")
    notion_parent_page_id: str = Field(default="", alias="NOTION_PARENT_PAGE_ID")
    twenty_api_url: str = Field(default="http://localhost:3000", alias="TWENTY_API_URL")
    twenty_api_key: str = Field(default="", alias="TWENTY_API_KEY")
    slack_mcp_url: str = Field(default="", alias="SLACK_MCP_URL")
    notion_mcp_url: str = Field(default="", alias="NOTION_MCP_URL")
    crm_mcp_url: str = Field(default="", alias="CRM_MCP_URL")

    # telemetry
    otel_exporter_otlp_endpoint: str = Field(default="", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    service_name: str = Field(default="roiconsole", alias="ROI_SERVICE_NAME")

    # api
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    run_rate_limit: int = Field(default=10, alias="RUN_RATE_LIMIT")
    run_rate_window_seconds: float = Field(default=60.0, alias="RUN_RATE_WINDOW_SECONDS")
    guard_interval_seconds: float = Field(default=900.0, alias="GUARD_INTERVAL_SECONDS")
    o_guard_enabled: bool = Field(default=False, alias="GUARD_ENABLED")

    @property
    def resolved_db_path(self) -> Path:
        """Return the console database path, defaulting into the data directory."""
        return self.db_path or self.data_dir / "roi_console.db"

    @property
    def resolved_sandbox_db_path(self) -> Path:
        """Return the sandbox store path shared by MCP servers and validators."""
        return self.sandbox_db_path or self.data_dir / "sandbox.db"

    @property
    def resolved_connector_mode(self) -> str:
        """
        Return the effective connector mode.

        An explicit ROI_CONNECTOR_MODE wins. Otherwise the servers use live mode
        only when every connector credential is present, and demo mode when none
        is, so a fresh clone with no keys runs on fixtures.
        """
        if self.connector_mode:
            if self.connector_mode not in CONNECTOR_MODES:
                raise ValueError(f"ROI_CONNECTOR_MODE must be one of {CONNECTOR_MODES}")
            return self.connector_mode
        if self.slack_bot_token and self.notion_api_key and self.twenty_api_key:
            return "live"
        return "demo"

    @property
    def o_demo_mode(self) -> bool:
        """True when the console is running on recorded fixtures."""
        return self.resolved_connector_mode == "demo"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
