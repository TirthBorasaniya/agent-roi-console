"""Slack SDK wrapper with graceful degradation when credentials are absent."""

# ============= Standard Library =============
import logging
import os

# ============= Third-Party =============
try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    _SLACK_AVAILABLE = True
except ImportError:
    _SLACK_AVAILABLE = False

# ============= Constants =============
logger = logging.getLogger(__name__)

MOCK_MESSAGES = [
    {"user": "U001", "text": "Weekly sync is at 3 PM today", "ts": "1700000001.000100"},
    {"user": "U002", "text": "Deployment to prod completed successfully", "ts": "1700000002.000200"},
    {"user": "U003", "text": "Q4 planning doc is in Notion — please review by EOD", "ts": "1700000003.000300"},
    {"user": "U001", "text": "Reminder: retro tomorrow 10 AM", "ts": "1700000004.000400"},
    {"user": "U004", "text": "Hotfix merged and deployed", "ts": "1700000005.000500"},
]


# ============= Client Factory =============

def _get_client() -> "WebClient | None":
    """
    Return a Slack WebClient if credentials are configured, else None.

    Returns
    -------
    WebClient | None
        Authenticated client or None when token is absent.
    """
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        logger.warning("SLACK_BOT_TOKEN not set, running in mock mode")
        return None
    if not _SLACK_AVAILABLE:
        logger.warning("slack_sdk not installed, running in mock mode")
        return None
    return WebClient(token=token)


# ============= Public Functions =============

def post_slack_message(channel_id: str, text: str) -> str:
    """
    Post a message to a Slack channel and return the message timestamp.

    Parameters
    ----------
    channel_id : str
        The Slack channel ID to post to.
    text : str
        The message text to post.

    Returns
    -------
    str
        The message timestamp (ts) from Slack, or a mock value.
    """
    client = _get_client()
    if client is None:
        mock_ts = "1700000099.000000"
        logger.info(f"mock: would post to {channel_id}: {text!r}")
        return mock_ts

    try:
        response = client.chat_postMessage(channel=channel_id, text=text)
        return response["ts"]
    except SlackApiError as exc:
        logger.error(f"slack post failed: {exc.response['error']}")
        return "0.000000"


def read_slack_channel(channel_id: str, limit: int = 10) -> list[dict]:
    """
    Read the most recent messages from a Slack channel.

    Parameters
    ----------
    channel_id : str
        The Slack channel ID to read from.
    limit : int, optional
        Maximum number of messages to return (default 10).

    Returns
    -------
    list[dict]
        List of message dicts with keys: user, text, ts.
    """
    client = _get_client()
    if client is None:
        logger.info(f"mock: reading {limit} messages from {channel_id}")
        return MOCK_MESSAGES[:limit]

    try:
        response = client.conversations_history(channel=channel_id, limit=limit)
        messages_list = []
        for msg in response.get("messages", []):
            messages_list.append({
                "user": msg.get("user", "unknown"),
                "text": msg.get("text", ""),
                "ts": msg.get("ts", ""),
            })
        return messages_list
    except SlackApiError as exc:
        logger.error(f"slack read failed: {exc.response['error']}")
        return MOCK_MESSAGES[:limit]
