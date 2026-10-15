"""End-state snapshot types that validators inspect, independent of the backing system."""

# ============= Standard Library =============
from dataclasses import dataclass, field
from typing import Any

# ============= Snapshot Types =============


@dataclass(frozen=True)
class SlackMessage:
    """A Slack message observed after a run started."""

    channel: str
    ts: str
    text: str
    user: str
    o_bot: bool
    thread_ts: str | None = None


@dataclass(frozen=True)
class NotionBlock:
    """A simplified Notion block."""

    type: str
    text: str
    checked: bool | None = None


@dataclass
class NotionPage:
    """A Notion database page created after a run started, with its blocks."""

    database: str
    page_id: str
    title: str
    properties: dict[str, Any]
    blocks: list[NotionBlock] = field(default_factory=list)


@dataclass(frozen=True)
class CrmActivity:
    """A CRM activity note created after a run started."""

    contact_id: str
    note: str


@dataclass
class EndState:
    """
    Everything validators may inspect for one run.

    Messages, pages, and activities are restricted to objects created after the
    run started, so state left by earlier runs cannot satisfy a validator.
    """

    slack_messages: list[SlackMessage] = field(default_factory=list)
    notion_pages: list[NotionPage] = field(default_factory=list)
    crm_activities: list[CrmActivity] = field(default_factory=list)


@dataclass(frozen=True)
class ValidatorResult:
    """Outcome of one validator."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {"name": self.name, "passed": self.passed, "detail": self.detail}
