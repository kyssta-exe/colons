"""Tests for memory subsystem."""
import time

import pytest

from colons_core.memory import (
    InMemoryCache, MemoryEntry, SemanticCache, SQLiteStore, create_cache, create_store,
    cosine_similarity, EmbeddingService,
)


def test_sqlite_store_roundtrip(store):
    entry = store.store(MemoryEntry(user_id="u1", content="hello world", embedding=[1.0, 0.0, 0.0]))
    assert entry.id is not None
    history = store.history("u1")
    assert len(history) == 1
    assert history[0].content == "hello world"


def test_sqlite_search_ordering(store):
    store.store(MemoryEntry(user_id="u1", content="cats are great", embedding=[1.0, 0.0]))
    store.store(MemoryEntry(user_id="u1", content="dogs are fine", embedding=[0.0, 1.0]))
    results = store.search("u1", query_embedding=[1.0, 0.0], limit=2)
    assert results[0][0].content == "cats are great"
    assert results[0][1] > results[1][1]


def test_store_isolation_between_users(store):
    store.store(MemoryEntry(user_id="alice", content="alice data", embedding=[1.0]))
    store.store(MemoryEntry(user_id="bob", content="bob data", embedding=[1.0]))
    assert len(store.history("alice")) == 1
    assert store.history("alice")[0].content == "alice data"
    assert store.count("bob") == 1


def test_store_delete(store):
    e = store.store(MemoryEntry(user_id="u1", content="temp", embedding=[1.0]))
    assert store.delete("u1", memory_id=e.id) == 1
    assert store.count("u1") == 0


def test_create_store_defaults_to_sqlite(tmp_path):
    s = create_store(str(tmp_path / "x.db"))
    assert isinstance(s, SQLiteStore)


def test_inmemory_cache_ttl():
    cache = InMemoryCache(default_ttl=1)
    cache.set("a", {"x": 1})
    assert cache.get("a") == {"x": 1}
    cache.set("b", "value", ttl=0)  # never expires
    assert cache.get("b") == "value"


def test_inmemory_cache_lru_eviction():
    cache = InMemoryCache(max_entries=3, default_ttl=60)
    for i in range(5):
        cache.set(f"k{i}", i)
    assert cache.get("k0") is None
    assert cache.get("k4") == 4


def test_inmemory_cache_incr():
    cache = InMemoryCache()
    assert cache.incr("counter") == 1
    assert cache.incr("counter", 4) == 5


def test_cache_clear_pattern():
    cache = InMemoryCache()
    cache.set("llm:1", "a")
    cache.set("llm:2", "b")
    cache.set("other", "c")
    cache.clear_pattern("llm:*")
    assert cache.get("llm:1") is None
    assert cache.get("other") == "c"


def test_semantic_cache_only_caches_deterministic():
    cache = InMemoryCache()
    sc = SemanticCache(cache)
    messages = [{"role": "user", "content": "hi"}]
    sc.put("m", messages, {"content": "hello"}, temperature=0.0)
    assert sc.get("m", messages, temperature=0.0) == {"content": "hello"}
    # High temperature should bypass caching
    sc.put("m", messages, {"content": "random"}, temperature=0.9)
    assert sc.get("m", messages, temperature=0.9) is None


def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine_similarity([], [1]) == 0.0


@pytest.mark.asyncio
async def test_embedding_service_fallback_deterministic():
    service = EmbeddingService(provider=None, dim=32)
    v1 = await service.embed("hello")
    v2 = await service.embed("hello")
    v3 = await service.embed("different")
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == 32


@pytest.mark.asyncio
async def test_embedding_service_uses_provider(provider):
    service = EmbeddingService(provider=provider, dim=8)
    await service.embed("hello")
    assert provider.embed_calls == 1
    # Second call hits the in-memory cache
    await service.embed("hello")
    assert provider.embed_calls == 1
