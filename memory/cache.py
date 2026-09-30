"""
Redis cache layer for Colons
Optimized for AI response caching
"""
import json
import hashlib
from typing import Optional, Any
import redis

class CacheStore:
    """Redis cache with semantic key hashing"""

    def __init__(self, redis_url: str, default_ttl: int = 3600):
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.default_ttl = default_ttl

    def _make_key(self, prefix: str, *args) -> str:
        """Create cache key from prefix and arguments"""
        key_data = json.dumps(args, sort_keys=True)
        hash_val = hashlib.md5(key_data.encode()).hexdigest()[:12]
        return f"{prefix}:{hash_val}"

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        value = self.redis.get(key)
        if value:
            return json.loads(value)
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in cache"""
        ttl = ttl or self.default_ttl
        self.redis.setex(key, ttl, json.dumps(value))

    def delete(self, key: str):
        """Delete from cache"""
        self.redis.delete(key)

    def exists(self, key: str) -> bool:
        """Check if key exists"""
        return self.redis.exists(key)

    def clear_pattern(self, pattern: str):
        """Clear all keys matching pattern"""
        for key in self.redis.scan_iter(match=pattern):
            self.redis.delete(key)

    # Semantic cache methods

    def cache_response(self, prompt: str, response: Any, ttl: Optional[int] = None):
        """Cache a response based on prompt hash"""
        key = self._make_key("resp", prompt)
        self.set(key, response, ttl)

    def get_cached_response(self, prompt: str) -> Optional[Any]:
        """Get cached response for prompt"""
        key = self._make_key("resp", prompt)
        return self.get(key)

    def cache_embedding(self, text: str, embedding: list):
        """Cache an embedding"""
        key = self._make_key("emb", text)
        self.set(key, embedding, ttl=None)  # No TTL for embeddings

    def get_cached_embedding(self, text: str) -> Optional[list]:
        """Get cached embedding"""
        key = self._make_key("emb", text)
        return self.get(key)