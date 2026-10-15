"""Append-only JSONL checkpoints so long evaluations resume after an interruption."""

# ============= Standard Library =============
import json
import logging
from pathlib import Path
from typing import Any

# ============= Constants =============
logger = logging.getLogger(__name__)


# ============= Checkpoint =============


class Checkpoint:
    """
    Records keyed by a tuple of strings, one JSON object per line.

    A truncated final line (from a crash mid-write) is skipped on load, so the
    corresponding work item simply runs again.

    Parameters
    ----------
    path : Path
        JSONL file under eval/checkpoints (gitignored).
    key_fields : tuple of str
        Record fields that identify a work item.
    o_run_from_scratch : bool
        Discard an existing checkpoint.
    """

    def __init__(
        self, path: Path, key_fields: tuple[str, ...], o_run_from_scratch: bool = False
    ) -> None:
        self.path = path
        self.key_fields = key_fields
        path.parent.mkdir(parents=True, exist_ok=True)
        if o_run_from_scratch and path.exists():
            path.unlink()
        self._records_dict: dict[tuple[str, ...], dict[str, Any]] = {}
        if path.exists():
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"skipping unreadable checkpoint line {line_no} in {path}")
                    continue
                self._records_dict[self.key(record)] = record

    def key(self, record: dict[str, Any]) -> tuple[str, ...]:
        """Return a record's key."""
        return tuple(str(record[f]) for f in self.key_fields)

    def done(self, key: tuple[str, ...]) -> bool:
        """True when a work item already has a record."""
        return key in self._records_dict

    def add(self, record: dict[str, Any]) -> None:
        """Append one record and flush it to disk."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
            handle.flush()
        self._records_dict[self.key(record)] = record

    def records(self) -> list[dict[str, Any]]:
        """Return all records."""
        return list(self._records_dict.values())
