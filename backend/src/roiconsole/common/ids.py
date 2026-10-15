"""Identifier helpers: run ids and idempotency keys for connector writes."""

# ============= Standard Library =============
import hashlib
import json
import uuid
from typing import Any

# ============= Constants =============
IDEMPOTENCY_KEY_LENGTH = 32


# ============= Helpers =============


def new_run_id() -> str:
    """Return a fresh run id."""
    return f"run_{uuid.uuid4().hex[:16]}"


def canonical_json(value: Any) -> str:
    """Serialize a value deterministically: sorted keys, no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def idempotency_key(run_id: str, step_id: str, arguments: dict[str, Any]) -> str:
    """
    Hash run_id, step_id, and tool arguments into an idempotency key.

    Parameters
    ----------
    run_id : str
        Workflow run id.
    step_id : str
        Step id within the run.
    arguments : dict
        Tool arguments, excluding the idempotency_key itself.

    Returns
    -------
    key : str
        Hex digest prefix, stable for identical inputs.
    """
    clean_dict = {k: v for k, v in arguments.items() if k != "idempotency_key"}
    material = f"{run_id}|{step_id}|{canonical_json(clean_dict)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:IDEMPOTENCY_KEY_LENGTH]
