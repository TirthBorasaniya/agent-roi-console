"""Counter and key-value storage for budgets, rate limits, and the exact cache: memory or Redis."""

# ============= Standard Library =============
import asyncio
import time
from typing import Protocol

# ============= Third-Party =============
from redis import asyncio as redis_asyncio

# ============= Constants =============
TOKEN_BUCKET_LUA = """
local tokens_key = KEYS[1]
local ts_key = KEYS[2]
local capacity = tonumber(ARGV[1])
local refill_per_s = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local tokens = tonumber(redis.call('GET', tokens_key) or capacity)
local last = tonumber(redis.call('GET', ts_key) or now)
tokens = math.min(capacity, tokens + (now - last) * refill_per_s)
local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end
local ttl = math.ceil(capacity / refill_per_s) + 60
redis.call('SET', tokens_key, tokens, 'EX', ttl)
redis.call('SET', ts_key, now, 'EX', ttl)
return {allowed, tostring(tokens)}
"""


# ============= Interface =============


class GatewayStore(Protocol):
    """Storage operations the gateway needs."""

    async def incr_float(self, key: str, amount: float, ttl_s: int) -> float:
        """Add to a float counter and return the new value."""
        ...

    async def get_float(self, key: str) -> float:
        """Return a float counter, 0 when absent."""
        ...

    async def get(self, key: str) -> str | None:
        """Return a string value or None."""
        ...

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        """Set a string value with a TTL."""
        ...

    async def take_tokens(
        self, key: str, capacity: float, refill_per_s: float, cost: float
    ) -> tuple[bool, float]:
        """Token bucket: try to take `cost` tokens; return (allowed, tokens_left)."""
        ...


# ============= Memory =============


class MemoryStore:
    """In-process store for tests, demo mode, and single-process runs."""

    def __init__(self) -> None:
        self._values_dict: dict[str, tuple[str, float]] = {}
        self._buckets_dict: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    def _live(self, key: str) -> str | None:
        """Return an unexpired value."""
        entry = self._values_dict.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at and time.time() > expires_at:
            del self._values_dict[key]
            return None
        return value

    async def incr_float(self, key: str, amount: float, ttl_s: int) -> float:
        """Add to a counter."""
        async with self._lock:
            current = float(self._live(key) or 0.0) + amount
            self._values_dict[key] = (repr(current), time.time() + ttl_s)
            return current

    async def get_float(self, key: str) -> float:
        """Read a counter."""
        return float(self._live(key) or 0.0)

    async def get(self, key: str) -> str | None:
        """Read a value."""
        return self._live(key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        """Write a value."""
        self._values_dict[key] = (value, time.time() + ttl_s)

    async def take_tokens(
        self, key: str, capacity: float, refill_per_s: float, cost: float
    ) -> tuple[bool, float]:
        """Token bucket in memory."""
        async with self._lock:
            now = time.monotonic()
            tokens, last = self._buckets_dict.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * refill_per_s)
            allowed = tokens >= cost
            if allowed:
                tokens -= cost
            self._buckets_dict[key] = (tokens, now)
            return allowed, tokens


# ============= Redis =============


class RedisStore:
    """
    Redis-backed store shared by every gateway replica.

    Parameters
    ----------
    client : redis.asyncio.Redis
        Connected client with decode_responses=True.
    prefix : str
        Key namespace.
    """

    def __init__(self, client: redis_asyncio.Redis, prefix: str = "roiconsole:") -> None:
        self._client = client
        self._prefix = prefix
        self._bucket_script = client.register_script(TOKEN_BUCKET_LUA)

    @classmethod
    def from_url(cls, url: str, prefix: str = "roiconsole:") -> "RedisStore":
        """Connect from a redis:// URL."""
        return cls(redis_asyncio.from_url(url, decode_responses=True), prefix)

    async def incr_float(self, key: str, amount: float, ttl_s: int) -> float:
        """INCRBYFLOAT with expiry."""
        full_key = self._prefix + key
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incrbyfloat(full_key, amount)
            pipe.expire(full_key, ttl_s)
            value, _ = await pipe.execute()
        return float(value)

    async def get_float(self, key: str) -> float:
        """GET as float."""
        value = await self._client.get(self._prefix + key)
        return float(value or 0.0)

    async def get(self, key: str) -> str | None:
        """GET."""
        return await self._client.get(self._prefix + key)

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        """SET with EX."""
        await self._client.set(self._prefix + key, value, ex=ttl_s)

    async def take_tokens(
        self, key: str, capacity: float, refill_per_s: float, cost: float
    ) -> tuple[bool, float]:
        """Atomic token bucket in Lua."""
        allowed, tokens = await self._bucket_script(
            keys=[self._prefix + key + ":tokens", self._prefix + key + ":ts"],
            args=[capacity, refill_per_s, cost, time.time()],
        )
        return bool(int(allowed)), float(tokens)
