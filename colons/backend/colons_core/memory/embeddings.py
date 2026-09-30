"""
Embedding service with caching and provider fallback.
"""
import hashlib
import logging
from typing import Dict, List, Optional

from ..providers.base import ProviderAdapter

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Generates embeddings via the active provider, with an in-memory + optional
    cache layer and a deterministic fallback for offline/local usage.
    """

    def __init__(
        self,
        provider: Optional[ProviderAdapter] = None,
        model: Optional[str] = None,
        cache=None,
        dim: int = 384,
    ):
        self.provider = provider
        self.model = model
        self.cache = cache
        self.dim = dim
        self._memory: Dict[str, List[float]] = {}

    def set_provider(self, provider: ProviderAdapter, model: Optional[str] = None):
        self.provider = provider
        if model:
            self.model = model

    async def embed(self, text: str) -> List[float]:
        key = self._key(text)

        if key in self._memory:
            return self._memory[key]
        cache_key = f"embedding:{key}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached:
                self._memory[key] = cached
                return cached

        vector: Optional[List[float]] = None
        if self.provider is not None:
            try:
                vector = await self.provider.embed(text, self.model or self._default_model())
            except NotImplementedError:
                vector = None
            except Exception as e:
                logger.warning(f"Embedding provider failed, using fallback: {e}")
                vector = None

        if not vector:
            vector = self._fallback(text)

        if len(vector) != self.dim:
            self.dim = len(vector)

        self._memory[key] = vector
        if self.cache:
            try:
                self.cache.set(cache_key, vector, ttl=0)  # embeddings don't expire
            except Exception:
                pass
        return vector

    async def embed_many(self, texts: List[str]) -> List[List[float]]:
        return [await self.embed(t) for t in texts]

    def _default_model(self) -> str:
        name = getattr(self.provider, "name", "")
        return {
            "openai": "text-embedding-3-small",
            "google": "text-embedding-004",
            "ollama": "nomic-embed-text",
        }.get(name, "text-embedding-3-small")

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _fallback(self, text: str) -> List[float]:
        """
        Deterministic hashing-based embedding (no dependencies).
        Good enough for local dedup/similarity, replaceable by real embeddings.
        """
        digest = hashlib.sha512(text.encode("utf-8")).digest()
        extended = (digest * ((self.dim * 2) // len(digest) + 1))[: self.dim]
        return [(b / 127.5) - 1.0 for b in extended]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors (pure Python, no numpy)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))
