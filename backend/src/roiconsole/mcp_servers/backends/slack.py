"""Slack backends: the live Slack Web API and the SQLite sandbox."""

# ============= Standard Library =============
import logging
from typing import Any, Protocol

# ============= Third-Party =============
from mcp.server.fastmcp.exceptions import ToolError
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

# ============= Local =============
from roiconsole.mcp_servers.common import WriteThrottle, check_allowlisted
from roiconsole.sandbox.store import SANDBOX_BOT_USER, SandboxStore
from roiconsole.validators.state import SlackMessage

# ============= Constants =============
logger = logging.getLogger(__name__)

OUTPUT_EVENT_TYPE = "roi_console_output"
FIXTURE_EVENT_TYPE = "roi_console_fixture"
HISTORY_PAGE_LIMIT = 200
RATE_LIMIT_RETRIES = 3


# ============= Interface =============


class SlackBackend(Protocol):
    """Operations behind slack-mcp, plus readers for validators and resets."""

    def post_message(
        self, channel: str, text: str, thread_ts: str | None, run_id: str
    ) -> dict[str, Any]:
        """Post a message as the console bot and return {channel, ts}."""
        ...

    def list_channels(self) -> dict[str, Any]:
        """Return the allowlisted channels as {channels: [{id, name}]}."""
        ...

    def read_thread(self, channel: str, thread_ts: str) -> dict[str, Any]:
        """Return {channel, thread_ts, messages: [{ts, user, text}]}, parent first."""
        ...

    def post_fixture_thread(self, channel: str, message_list: list[dict[str, str]]) -> str:
        """Post a fixture thread (parent then replies) and return the parent ts."""
        ...

    def messages_since(self, since: float) -> list[SlackMessage]:
        """Return messages in allowlisted channels created at or after `since`."""
        ...


# ============= Sandbox Backend =============


class SandboxSlackBackend:
    """
    Slack backed by the SQLite sandbox store.

    Parameters
    ----------
    store : SandboxStore
        Shared sandbox state.
    allowlist : list of str
        Channel names the tools may touch.
    """

    def __init__(self, store: SandboxStore, allowlist: list[str]) -> None:
        self._store = store
        self._allowlist = list(allowlist)

    def _channel_id(self, channel: str) -> str:
        """Resolve an allowlisted channel name to its sandbox id."""
        name = self._store.channel_name(channel) or channel
        check_allowlisted("channel", name, self._allowlist)
        return self._store.ensure_channel(name)

    def post_message(
        self, channel: str, text: str, thread_ts: str | None, run_id: str
    ) -> dict[str, Any]:
        """Insert a bot message into the sandbox."""
        if not text.strip():
            raise ToolError("text must not be empty")
        channel_id = self._channel_id(channel)
        if thread_ts and not self._store.thread_messages(channel_id, thread_ts):
            raise ToolError(f"thread {thread_ts} not found in {channel}")
        ts = self._store.add_message(channel_id, text, thread_ts=thread_ts or None, run_id=run_id)
        return {"channel": channel, "ts": ts}

    def list_channels(self) -> dict[str, Any]:
        """List allowlisted channels present in the sandbox."""
        channel_list = [c for c in self._store.list_channels() if c["name"] in self._allowlist]
        return {"channels": channel_list}

    def read_thread(self, channel: str, thread_ts: str) -> dict[str, Any]:
        """Read a sandbox thread."""
        channel_id = self._channel_id(channel)
        row_list = self._store.thread_messages(channel_id, thread_ts)
        if not row_list:
            raise ToolError(f"thread {thread_ts} not found in {channel}")
        message_list = [{"ts": r["ts"], "user": r["user"], "text": r["text"]} for r in row_list]
        return {"channel": channel, "thread_ts": thread_ts, "messages": message_list}

    def post_fixture_thread(self, channel: str, message_list: list[dict[str, str]]) -> str:
        """Insert a fixture thread authored by the named users."""
        channel_id = self._channel_id(channel)
        parent_ts = self._store.add_message(
            channel_id, message_list[0]["text"], user=message_list[0]["user"], o_bot=False
        )
        for message in message_list[1:]:
            self._store.add_message(
                channel_id, message["text"], user=message["user"], thread_ts=parent_ts, o_bot=False
            )
        return parent_ts

    def messages_since(self, since: float) -> list[SlackMessage]:
        """Read sandbox messages created since a timestamp."""
        message_list = []
        for channel in self.list_channels()["channels"]:
            for row in self._store.channel_messages(channel["id"], since):
                message_list.append(
                    SlackMessage(
                        channel=channel["name"],
                        ts=row["ts"],
                        text=row["text"],
                        user=row["user"],
                        o_bot=bool(row["o_bot"]) and row["user"] == SANDBOX_BOT_USER,
                        thread_ts=row["thread_ts"],
                    )
                )
        return message_list


# ============= Live Backend =============


class LiveSlackBackend:
    """
    Slack Web API backend.

    Bot output carries message metadata with event type `roi_console_output` and
    the run id, and fixture messages carry `roi_console_fixture`, so validators
    can tell the console's writes from seeded discussion without trusting the
    agent's own report.

    Parameters
    ----------
    token : str
        Bot token with chat:write, chat:write.customize, channels:read,
        channels:history, groups:read, and groups:history scopes.
    allowlist : list of str
        Channel names the tools may touch.
    throttle : WriteThrottle
        Minimum interval between writes.
    client : WebClient or None
        Injected client for tests.
    """

    def __init__(
        self,
        token: str,
        allowlist: list[str],
        throttle: WriteThrottle,
        client: WebClient | None = None,
    ) -> None:
        self._client = client or WebClient(token=token)
        self._client.retry_handlers.append(
            RateLimitErrorRetryHandler(max_retry_count=RATE_LIMIT_RETRIES)
        )
        self._allowlist = list(allowlist)
        self._throttle = throttle
        self._channel_ids_dict: dict[str, str] = {}

    def _resolve_channels(self) -> dict[str, str]:
        """Map allowlisted channel names to ids, caching the lookup."""
        if self._channel_ids_dict:
            return self._channel_ids_dict
        cursor = None
        while True:
            response = self._client.conversations_list(
                types="public_channel,private_channel", limit=HISTORY_PAGE_LIMIT, cursor=cursor
            )
            for channel in response["channels"]:
                if channel["name"] in self._allowlist:
                    self._channel_ids_dict[channel["name"]] = channel["id"]
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        missing_list = [n for n in self._allowlist if n not in self._channel_ids_dict]
        if missing_list:
            logger.warning(f"allowlisted Slack channels not visible to the bot: {missing_list}")
        return self._channel_ids_dict

    def _channel_id(self, channel: str) -> str:
        """Resolve a channel name (or an allowlisted id) to an id."""
        ids_dict = self._resolve_channels()
        if channel in ids_dict.values():
            return channel
        check_allowlisted("channel", channel, self._allowlist)
        if channel not in ids_dict:
            raise ToolError(f"channel {channel} is allowlisted but the bot cannot see it")
        return ids_dict[channel]

    def _channel_name(self, channel_id: str) -> str:
        """Return the allowlisted name of a channel id."""
        for name, known_id in self._resolve_channels().items():
            if known_id == channel_id:
                return name
        return channel_id

    def post_message(
        self, channel: str, text: str, thread_ts: str | None, run_id: str
    ) -> dict[str, Any]:
        """Post through chat.postMessage with output metadata."""
        if not text.strip():
            raise ToolError("text must not be empty")
        channel_id = self._channel_id(channel)
        self._throttle.wait()
        try:
            response = self._client.chat_postMessage(
                channel=channel_id,
                text=text,
                thread_ts=thread_ts or None,
                metadata={"event_type": OUTPUT_EVENT_TYPE, "event_payload": {"run_id": run_id}},
            )
        except SlackApiError as exc:
            raise ToolError(f"slack post failed: {exc.response.get('error')}") from exc
        return {"channel": self._channel_name(channel_id), "ts": response["ts"]}

    def list_channels(self) -> dict[str, Any]:
        """List allowlisted channels visible to the bot."""
        ids_dict = self._resolve_channels()
        return {"channels": [{"id": i, "name": n} for n, i in sorted(ids_dict.items())]}

    def read_thread(self, channel: str, thread_ts: str) -> dict[str, Any]:
        """Read a thread through conversations.replies."""
        channel_id = self._channel_id(channel)
        try:
            response = self._client.conversations_replies(
                channel=channel_id, ts=thread_ts, limit=HISTORY_PAGE_LIMIT
            )
        except SlackApiError as exc:
            raise ToolError(f"slack read failed: {exc.response.get('error')}") from exc
        message_list = [
            {
                "ts": m["ts"],
                "user": m.get("username") or m.get("user") or "unknown",
                "text": m.get("text", ""),
            }
            for m in response["messages"]
        ]
        return {
            "channel": self._channel_name(channel_id),
            "thread_ts": thread_ts,
            "messages": message_list,
        }

    def post_fixture_thread(self, channel: str, message_list: list[dict[str, str]]) -> str:
        """Post a fixture thread with per-message display names."""
        channel_id = self._channel_id(channel)
        metadata = {"event_type": FIXTURE_EVENT_TYPE, "event_payload": {}}
        parent_ts = None
        for message in message_list:
            self._throttle.wait()
            response = self._client.chat_postMessage(
                channel=channel_id,
                text=message["text"],
                username=message["user"],
                thread_ts=parent_ts,
                metadata=metadata,
            )
            parent_ts = parent_ts or response["ts"]
        return str(parent_ts)

    def _history(self, channel_id: str, since: float) -> list[dict[str, Any]]:
        """Return top-level messages and every thread reply since a timestamp."""
        message_list: list[dict[str, Any]] = []
        cursor = None
        while True:
            response = self._client.conversations_history(
                channel=channel_id,
                oldest=f"{since:.6f}",
                include_all_metadata=True,
                limit=HISTORY_PAGE_LIMIT,
                cursor=cursor,
            )
            message_list += response["messages"]
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        reply_list = []
        for message in message_list:
            if message.get("reply_count"):
                replies = self._client.conversations_replies(
                    channel=channel_id,
                    ts=message["ts"],
                    oldest=f"{since:.6f}",
                    include_all_metadata=True,
                    limit=HISTORY_PAGE_LIMIT,
                )
                reply_list += [r for r in replies["messages"] if r["ts"] != message["ts"]]
        # replies to parents older than `since` are invisible here, so callers pass the
        # reset start time: fixture thread parents are posted after it
        return message_list + reply_list

    def messages_since(self, since: float) -> list[SlackMessage]:
        """Read messages in allowlisted channels since a timestamp."""
        result_list = []
        for name, channel_id in self._resolve_channels().items():
            for message in self._history(channel_id, since):
                event_type = (message.get("metadata") or {}).get("event_type")
                thread_ts = message.get("thread_ts")
                result_list.append(
                    SlackMessage(
                        channel=name,
                        ts=message["ts"],
                        text=message.get("text", ""),
                        user=message.get("username") or message.get("user") or "unknown",
                        o_bot=event_type == OUTPUT_EVENT_TYPE,
                        thread_ts=thread_ts if thread_ts and thread_ts != message["ts"] else None,
                    )
                )
        return result_list
