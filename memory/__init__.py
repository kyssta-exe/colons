"""
Memory system for Colons
Uses PostgreSQL + pgvector for semantic memory
Redis for caching
"""
import json
import time
from typing import List, Dict, Optional, Any, Tuple
import psycopg2
import redis
from dataclasses import dataclass, asdict
from datetime import datetime

@dataclass
class MemoryEntry:
    """Single memory entry"""
    id: Optional[int] = None
    user_id: str = ""
    content: str = ""
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = None
    created_at: float = 0.0

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_at == 0.0:
            self.created_at = time.time()

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "MemoryEntry":
        return cls(**data)

class MemoryStore:
    """PostgreSQL + pgvector memory store"""

    def __init__(self, db_url: str, redis_url: Optional[str] = None):
        self.db_url = db_url
        self.redis_url = redis_url

        # Redis cache (optional)
        self.redis_client = None
        if redis_url:
            self.redis_client = redis.from_url(redis_url, decode_responses=True)

        self._init_db()

    def _get_conn(self):
        """Get database connection"""
        return psycopg2.connect(self.db_url)

    def _init_db(self):
        """Initialize database tables"""
        conn = self._get_conn()
        cur = conn.cursor()

        # Create memories table with pgvector support
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                embedding vector(1536),
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create index for vector similarity search
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_embedding
            ON memories USING vector_cosine_ops (embedding)
        """)

        # Create index for user_id lookups
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user_id
            ON memories (user_id)
        """)

        conn.commit()
        cur.close()
        conn.close()

    async def store(
        self,
        user_id: str,
        content: str,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict] = None
    ) -> MemoryEntry:
        """Store a new memory entry"""
        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO memories (user_id, content, embedding, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, user_id, content, embedding, metadata, created_at
        """, (
            user_id,
            content,
            embedding,
            json.dumps(metadata or {}),
            datetime.fromtimestamp(time.time())
        ))

        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        # Cache in Redis if available
        if self.redis_client:
            cache_key = f"memory:{user_id}:{row[0]}"
            self.redis_client.setex(
                cache_key,
                3600,  # 1 hour TTL
                json.dumps(row)
            )

        return self._row_to_entry(row)

    async def search(
        self,
        user_id: str,
        query_embedding: List[float],
        limit: int = 5,
        similarity_threshold: float = 0.7
    ) -> List[Tuple[MemoryEntry, float]]:
        """Search memories by semantic similarity"""
        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, user_id, content, embedding, metadata, created_at,
                   1 - (embedding <=> %s::vector) as similarity
            FROM memories
            WHERE user_id = %s
              AND 1 - (embedding <=> %s::vector) > %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (query_embedding, user_id, query_embedding, similarity_threshold, query_embedding, limit))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        return [(self._row_to_entry(row), row[-1]) for row in rows]

    async def get_history(
        self,
        user_id: str,
        limit: int = 20,
        offset: int = 0
    ) -> List[MemoryEntry]:
        """Get recent conversation history"""
        # Check cache first
        cache_key = f"history:{user_id}:{limit}:{offset}"
        if self.redis_client:
            cached = self.redis_client.get(cache_key)
            if cached:
                return [MemoryEntry.from_dict(json.loads(item)) for item in json.loads(cached)]

        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, user_id, content, embedding, metadata, created_at
            FROM memories
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, limit, offset))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        entries = [self._row_to_entry(row) for row in rows]

        # Cache results
        if self.redis_client:
            self.redis_client.setex(
                cache_key,
                300,  # 5 min TTL
                json.dumps([e.to_dict() for e in entries])
            )

        return entries

    async def delete(self, user_id: str, memory_id: Optional[int] = None):
        """Delete memories"""
        conn = self._get_conn()
        cur = conn.cursor()

        if memory_id is not None:
            cur.execute("DELETE FROM memories WHERE id = %s AND user_id = %s", (memory_id, user_id))
        else:
            cur.execute("DELETE FROM memories WHERE user_id = %s", (user_id,))

        conn.commit()
        cur.close()
        conn.close()

        # Clear cache
        if self.redis_client:
            pattern = f"memory:{user_id}:*"
            for key in self.redis_client.scan_iter(match=pattern):
                self.redis_client.delete(key)

    def _row_to_entry(self, row) -> MemoryEntry:
        """Convert database row to MemoryEntry"""
        return MemoryEntry(
            id=row[0],
            user_id=row[1],
            content=row[2],
            embedding=row[3],
            metadata=json.loads(row[4]) if isinstance(row[4], str) else row[4],
            created_at=row[5].timestamp() if hasattr(row[5], 'timestamp') else row[5]
        )