"""Introspect the running Twenty instance and report fields the CRM connector needs but lacks."""

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.twenty import TwentyClient
from roiconsole.mcp_servers.backends.twenty_schema import check_schema

# ============= Entry Point =============


def main() -> None:
    """Exit non-zero when any required type, field, or mutation is missing."""
    settings = get_settings()
    missing_list = check_schema(TwentyClient(settings.twenty_api_url, settings.twenty_api_key))
    if missing_list:
        raise SystemExit(f"missing: {missing_list}")
    print("Twenty schema has every field and mutation the connector uses")


if __name__ == "__main__":
    main()
