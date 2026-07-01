"""Unit tests for connector graceful-degradation (mock mode) behavior."""

# ============= Standard Library =============
import os
import sys
from pathlib import Path

# ============= Local =============
sys.path.insert(0, str(Path(__file__).parent.parent))
from connectors.twenty_connector import search_crm_contacts


def test_crm_connector_mock_mode():
    """Assert that search_crm_contacts returns mock data when TWENTY_API_KEY is not set."""
    os.environ.pop("TWENTY_API_KEY", None)

    contacts = search_crm_contacts(query="Sarah")

    assert isinstance(contacts, list)
    assert len(contacts) > 0
    for contact in contacts:
        assert {"id", "name", "email", "company"} <= contact.keys()
