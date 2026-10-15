"""Replay adapter for demo mode: serves responses recorded from real runs, keyed by request hash."""

# ============= Standard Library =============
import hashlib
import json
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.ids import canonical_json
from roiconsole.gateway.adapters.base import ProviderError, ProviderRequest, ProviderResponse

# ============= Constants =============
RECORDINGS_FILE = "llm_recordings.jsonl"


# ============= Helpers =============


def request_key(request: ProviderRequest) -> str:
    """Hash the parts of a request that determine its answer, including the provider model."""
    material = canonical_json(
        {
            "model": request.provider_model,
            "messages": request.messages,
            "tools": request.tools,
            "response_format": request.response_format,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def response_to_dict(response: ProviderResponse) -> dict[str, Any]:
    """Serialize a ProviderResponse for a recording line."""
    return {
        "content": response.content,
        "tool_calls": response.tool_calls,
        "finish_reason": response.finish_reason,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "response_model": response.response_model,
    }


# ============= Recorder and Replayer =============


class Recorder:
    """
    Append real responses to a JSONL file so demo mode can replay them.

    Parameters
    ----------
    path : Path
        Recordings file, e.g. eval/fixtures/recordings/llm_recordings.jsonl.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, model: str, request: ProviderRequest, response: ProviderResponse) -> None:
        """Append one recording line."""
        line = {"key": request_key(request), "model": model, **response_to_dict(response)}
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")


class ReplayAdapter:
    """
    Serve recorded responses; an unrecorded request fails rather than inventing an answer.

    Parameters
    ----------
    path : Path
        Recordings file.
    provider : str
        Provider label this replayer stands in for.
    """

    def __init__(self, path: Path, provider: str = "replay") -> None:
        self.provider = provider
        self._responses_dict: dict[str, dict[str, Any]] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._responses_dict[entry["key"]] = entry

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return the recorded response for this exact request."""
        entry = self._responses_dict.get(request_key(request))
        if entry is None:
            raise ProviderError("no recording for this request", 404, False, "not_recorded")
        return ProviderResponse(
            content=entry["content"],
            tool_calls=entry["tool_calls"],
            finish_reason=entry["finish_reason"],
            input_tokens=entry["input_tokens"],
            output_tokens=entry["output_tokens"],
            response_model=entry["response_model"],
        )
