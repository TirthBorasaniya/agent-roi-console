"""Exact response cache (all step classes) and semantic cache (cacheable step classes only)."""

# ============= Standard Library =============
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Protocol

# ============= Local =============
from roiconsole.common.ids import canonical_json
from roiconsole.gateway.store import GatewayStore

# ============= Constants =============
logger = logging.getLogger(__name__)
SEMANTIC_INDEX_NAME = "roiconsole_semantic_cache"


# ============= Keys =============


def exact_key(model: str, request_dict: dict[str, Any]) -> str:
    """
    Key an exact-cache entry by model, messages, and every generation parameter.

    Parameters
    ----------
    model : str
        Gateway model id the request asked for (a model or "auto").
    request_dict : dict
        messages, tools, tool_choice, max_tokens, temperature, response_format.

    Returns
    -------
    key : str
        Namespaced SHA-256 key.
    """
    material = canonical_json({"model": model, **request_dict})
    return "cache:exact:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def prompt_text(message_list: list[dict[str, Any]]) -> str:
    """Flatten messages into the text embedded for semantic lookup."""
    return "\n".join(f"{m.get('role')}: {m.get('content') or ''}" for m in message_list)


# ============= Exact Cache =============


class ExactCache:
    """
    Exact-match cache over the gateway store (Redis in compose).

    Parameters
    ----------
    store : GatewayStore
        Backing store.
    ttl_s : int
        Entry lifetime (24 h by default).
    """

    def __init__(self, store: GatewayStore, ttl_s: int) -> None:
        self._store = store
        self._ttl_s = ttl_s

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return a cached response dict."""
        value = await self._store.get(key)
        return json.loads(value) if value else None

    async def put(self, key: str, response_dict: dict[str, Any]) -> None:
        """Store a response dict."""
        await self._store.set(key, json.dumps(response_dict), self._ttl_s)


# ============= Semantic Cache =============


class SemanticLookup(Protocol):
    """Semantic cache operations used by the gateway."""

    async def get(self, text: str, step_class: str, model: str) -> dict[str, Any] | None:
        """Return the response of a stored prompt within the distance threshold."""
        ...

    async def put(
        self, text: str, step_class: str, model: str, response_dict: dict[str, Any]
    ) -> None:
        """Store a prompt and its response."""
        ...


def fastembed_vectorizer(model_name: str, cache_dir: Path):
    """
    Build a RedisVL CustomVectorizer backed by fastembed (ONNX, no torch).

    Parameters
    ----------
    model_name : str
        fastembed model, e.g. BAAI/bge-small-en-v1.5.
    cache_dir : Path
        Model download directory.
    """
    from fastembed import TextEmbedding
    from redisvl.utils.vectorize import CustomVectorizer

    embedder = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))

    def embed(text: str) -> list[float]:
        return next(iter(embedder.embed([text]))).tolist()

    def embed_many(text_list: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in embedder.embed(text_list)]

    return CustomVectorizer(embed=embed, embed_many=embed_many)


class RedisSemanticCache:
    """
    RedisVL SemanticCache filterable on step_class and model.

    Parameters
    ----------
    redis_url : str
        Redis 8 URL (the query engine is required).
    distance_threshold : float
        Cosine distance under which a stored prompt counts as a hit (0.1).
    ttl_s : int
        Entry lifetime.
    vectorizer : Any
        RedisVL vectorizer.
    """

    def __init__(
        self, redis_url: str, distance_threshold: float, ttl_s: int, vectorizer: Any
    ) -> None:
        from redisvl.extensions.cache.llm import SemanticCache

        self._cache = SemanticCache(
            name=SEMANTIC_INDEX_NAME,
            redis_url=redis_url,
            distance_threshold=distance_threshold,
            ttl=ttl_s,
            vectorizer=vectorizer,
            filterable_fields=[
                {"name": "step_class", "type": "tag"},
                {"name": "model", "type": "tag"},
            ],
        )

    def _filter(self, step_class: str, model: str):
        """Build the tag filter."""
        from redisvl.query.filter import Tag

        return (Tag("step_class") == step_class) & (Tag("model") == model)

    async def get(self, text: str, step_class: str, model: str) -> dict[str, Any] | None:
        """Return the closest stored response within the threshold."""
        hit_list = await asyncio.to_thread(
            self._cache.check,
            prompt=text,
            num_results=1,
            filter_expression=self._filter(step_class, model),
        )
        if not hit_list:
            return None
        return json.loads(hit_list[0]["response"])

    async def put(
        self, text: str, step_class: str, model: str, response_dict: dict[str, Any]
    ) -> None:
        """Store a prompt and response with filter tags."""
        await asyncio.to_thread(
            self._cache.store,
            prompt=text,
            response=json.dumps(response_dict),
            filters={"step_class": step_class, "model": model},
        )
