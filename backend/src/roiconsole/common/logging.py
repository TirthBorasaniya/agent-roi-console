"""Structured JSON logging with the standard library, shared by every service."""

# ============= Standard Library =============
import json
import logging
import sys
from datetime import UTC, datetime

# ============= Constants =============
CONTEXT_FIELDS = ("run_id", "step_id", "workflow_id", "request_id", "event_id", "task_id")
RESERVED_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


# ============= Formatter =============


class JsonFormatter(logging.Formatter):
    """
    Render log records as one JSON object per line.

    Parameters
    ----------
    service : str
        Service name written into every record.
    """

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record with ts, level, service, event, and any extra context ids."""
        payload_dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "service": self._service,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in RESERVED_ATTRS or key.startswith("_"):
                continue
            payload_dict[key] = value
        if record.exc_info:
            payload_dict["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload_dict, default=str)


# ============= Setup =============


def configure_logging(service: str, level: int = logging.INFO, o_stderr: bool = False) -> None:
    """
    Install the JSON formatter on the root logger.

    Parameters
    ----------
    service : str
        Service name recorded on every line.
    level : int
        Root log level.
    o_stderr : bool
        Write to stderr instead of stdout. MCP stdio servers must use stderr,
        because stdout carries the protocol stream.
    """
    handler = logging.StreamHandler(sys.stderr if o_stderr else sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
