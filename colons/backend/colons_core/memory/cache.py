"""
Cache layer for Colons.
- InMemoryCache: zero-dependency default (TTL + LRU)
- RedisCache: optional, when REDIS_URL is set, with semantic caching
Both expose the same interface.
"""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BaseCache:
    def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        raise NotImplementedError

    def delete(self, key: str):
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def clear_pattern(self, pattern: str = "*"):
        raise NotImplementedError

    def incr(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> int:
        raise NotImplementedError

    # Semantic helpers ------------------------------------------------ #

    @staticmethod
    def hash_key(prefix: str, *parts: Any) -> str:
        basis = json.dumps(parts, sort_keys=True, default=str)
        return f"{prefix}:{hashlib.sha256(basis.encode()).hexdigest()[:20]}"


class InMemoryCache(BaseCache):
    """Thread-safe in-process TTL + LRU cache. Zero dependencies."""

    def __init__(self, max_entries: int = 10000, default_ttl: int = 3600):
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.time()

    def _expired(self, entry: Tuple[float, Any]) -> bool:
        expires, _ = entry
        return expires != 0 and expires < self._now()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if self._expired(entry):
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return entry[1]

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        ttl = self.default_ttl if ttl is None else ttl
        expires = 0 if ttl == 0 else self._now() + ttl
        with self._lock:
            self._data[key] = (expires, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def clear_pattern(self, pattern: str = "*"):
        prefix = pattern.rstrip("*")
        with self._lock:
            if not prefix:
                self._data.clear()
            else:
                for k in [k for k in self._data if k.startswith(prefix)]:
                    del self._data[k]

    def incr(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> int:
        with self._lock:
            entry = self._data.get(key)
            current = 0
            expires = self._now() + (ttl or self.default_ttl)
            if entry and not self._expired(entry):
                current = int(entry[1])
                expires = entry[0] or expires
            value = current + amount
            self._data[key] = (expires, value)
            return value

    def stats(self) -> Dict:
        with self._lock:
            return {"entries": len(self._data), "max_entries": self.max_entries, "backend": "memory"}


class RedisCache(BaseCache):
    """Redis-backed cache with the same interface (optional)."""

    def __init__(self, url: str, default_ttl: int = 3600):
        import redis
        self.client = redis.from_url(url, decode_responses=True)
        self.default_ttl = default_ttl
        self.client.ping()

    def get(self, key: str) -> Optional[Any]:
        val = self.client.get(key)
        if val is None:
            return None
        try:
            return json.loads(val)
        except Exception:
            return val

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        ttl = self.default_ttl if ttl is None else ttl
        payload = json.dumps(value, default=str)
        if ttl and ttl > 0:
            self.client.setex(key, ttl, payload)
        else:
            self.client.set(key, payload)

    def delete(self, key: str):
        self.client.delete(key)

    def exists(self, key: str) -> bool:
        return bool(self.client.exists(key))

    def clear_pattern(self, pattern: str = "*"):
        for key in self.client.scan_iter(match=pattern, count=500):
            self.client.delete(key)

    def incr(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> int:
        value = self.client.incrby(key, amount)
        if ttl:
            self.client.expire(key, ttl)
        return int(value)

    def stats(self) -> Dict:
        try:
            info = self.client.info()
            return {
                "entries": info.get("db0", {}).get("keys", 0),
                "backend": "redis",
                "used_memory_human": info.get("used_memory_human", ""),
            }
        except Exception:
            return {"backend": "redis"}


def create_cache(url: Optional[str] = None, default_ttl: int = 3600) -> BaseCache:
    """Create a cache backend. Falls back to in-memory if Redis is unavailable."""
    if url:
        try:
            return RedisCache(url, default_ttl=default_ttl)
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}); using in-memory cache")
    return InMemoryCache(default_ttl=default_ttl)


class SemanticCache:
    """
    Higher-level cache for LLM responses.
    Exact-match on normalized prompt by default; can be extended with embeddings.
    """

    def __init__(self, cache: BaseCache, default_ttl: int = 3600):
        self.cache = cache
        self.default_ttl = default_ttl

    def _normalize(self, model: str, messages: List[Dict], temperature: float) -> str:
        # Only cache deterministic-ish responses
        key_parts = {
            "model": model,
            "temperature": round(temperature, 2),
            "messages": [
                {"role": m.get("role"), "content": m.get("content")}
                for m in messages
            ],
        }
        return BaseCache.hash_key("llm", key_parts)

    def get(self, model: str, messages: List[Dict], temperature: float = 0.7) -> Optional[Dict]:
        if temperature > 0.2:
            return None  # too random to cache safely
        return self.cache.get(self._normalize(model, messages, temperature))

    def put(self, model: str, messages: List[Dict], response: Dict, temperature: float = 0.7,
            ttl: Optional[int] = None):
        if temperature > 0.2:
            return
        self.cache.set(self._normalize(model, messages, temperature), response, ttl or self.default_ttl)

    def invalidate(self):
        self.cache.clear_pattern("llm:*")
