"""
Storage backends for Colons memory.
- SQLiteStore: zero-config default, in-process vector search (very light)
- PostgresStore: optional, pgvector-backed for scale
Both expose the same interface.
"""
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from .embeddings import cosine_similarity
except ImportError:  # pragma: no cover
    def cosine_similarity(a, b):
        return 0.0


@dataclass
class MemoryEntry:
    id: Optional[int] = None
    user_id: str = "default"
    agent_id: str = "default"
    kind: str = "message"  # message | note | fact | summary | task
    role: str = ""
    content: str = ""
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


class BaseStore:
    def store(self, entry: MemoryEntry) -> MemoryEntry:
        raise NotImplementedError

    def search(self, user_id: str, query_embedding: List[float], limit: int = 5,
               threshold: float = 0.0, agent_id: Optional[str] = None) -> List[Tuple[MemoryEntry, float]]:
        raise NotImplementedError

    def history(self, user_id: str, limit: int = 20, offset: int = 0,
                agent_id: Optional[str] = None) -> List[MemoryEntry]:
        raise NotImplementedError

    def delete(self, user_id: str, memory_id: Optional[int] = None,
               agent_id: Optional[str] = None) -> int:
        raise NotImplementedError

    def count(self, user_id: Optional[str] = None) -> int:
        raise NotImplementedError


class SQLiteStore(BaseStore):
    """Lightweight SQLite store with pure-Python vector similarity."""

    def __init__(self, path: str = "colons.db", vector_limit: int = 5000):
        self.path = path
        self.vector_limit = vector_limit
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    kind TEXT NOT NULL DEFAULT 'message',
                    role TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    embedding TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, agent_id);
                CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
                CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
            """)
            conn.commit()
            conn.close()

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        with self._lock:
            conn = self._conn()
            cur = conn.execute(
                """INSERT INTO memories (user_id, agent_id, kind, role, content, embedding, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.user_id,
                    entry.agent_id,
                    entry.kind,
                    entry.role,
                    entry.content,
                    json.dumps(entry.embedding) if entry.embedding else None,
                    json.dumps(entry.metadata or {}),
                    entry.created_at,
                ),
            )
            entry.id = cur.lastrowid
            conn.commit()
            conn.close()
        return entry

    def search(self, user_id, query_embedding, limit=5, threshold=0.0, agent_id=None,
               kind: Optional[str] = None) -> List[Tuple[MemoryEntry, float]]:
        with self._lock:
            conn = self._conn()
            sql = "SELECT * FROM memories WHERE user_id = ? AND embedding IS NOT NULL"
            params: List[Any] = [user_id]
            if agent_id:
                sql += " AND agent_id = ?"
                params.append(agent_id)
            if kind:
                sql += " AND kind = ?"
                params.append(kind)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(self.vector_limit)
            rows = conn.execute(sql, params).fetchall()
            conn.close()

        scored: List[Tuple[MemoryEntry, float]] = []
        for row in rows:
            entry = self._row_to_entry(row)
            score = cosine_similarity(query_embedding, entry.embedding or [])
            if score >= threshold:
                scored.append((entry, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def history(self, user_id, limit=20, offset=0, agent_id=None) -> List[MemoryEntry]:
        with self._lock:
            conn = self._conn()
            sql = "SELECT * FROM memories WHERE user_id = ?"
            params: List[Any] = [user_id]
            if agent_id:
                sql += " AND agent_id = ?"
                params.append(agent_id)
            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        return [self._row_to_entry(r) for r in rows]

    def delete(self, user_id, memory_id=None, agent_id=None) -> int:
        with self._lock:
            conn = self._conn()
            if memory_id is not None:
                cur = conn.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))
            else:
                sql = "DELETE FROM memories WHERE user_id = ?"
                params: List[Any] = [user_id]
                if agent_id:
                    sql += " AND agent_id = ?"
                    params.append(agent_id)
                cur = conn.execute(sql, params)
            conn.commit()
            count = cur.rowcount
            conn.close()
        return count

    def count(self, user_id: Optional[str] = None) -> int:
        with self._lock:
            conn = self._conn()
            if user_id:
                row = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            conn.close()
        return int(row[0]) if row else 0

    def prune(self, user_id: str, keep: int = 1000):
        """Keep only the newest `keep` entries per user (housekeeping)."""
        with self._lock:
            conn = self._conn()
            conn.execute(
                """DELETE FROM memories WHERE id IN (
                    SELECT id FROM memories WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT -1 OFFSET ?
                )""",
                (user_id, keep),
            )
            conn.commit()
            conn.close()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            kind=row["kind"],
            role=row["role"],
            content=row["content"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
        )


class PostgresStore(BaseStore):
    """Postgres + pgvector store for larger deployments."""

    def __init__(self, dsn: str, dim: int = 1536):
        self.dsn = dsn
        self.dim = dim
        self._init_db()

    def _conn(self):
        import psycopg2
        return psycopg2.connect(self.dsn)

    def _init_db(self):
        conn = self._conn()
        cur = conn.cursor()
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as e:
            logger.warning(f"pgvector extension unavailable: {e}")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memories (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT 'default',
                kind TEXT NOT NULL DEFAULT 'message',
                role TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                embedding vector({self.dim}),
                metadata JSONB NOT NULL DEFAULT '{{}}',
                created_at DOUBLE PRECISION NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, agent_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
        conn.commit()
        cur.close()
        conn.close()

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO memories (user_id, agent_id, kind, role, content, embedding, metadata, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (entry.user_id, entry.agent_id, entry.kind, entry.role, entry.content,
             entry.embedding, json.dumps(entry.metadata or {}), entry.created_at),
        )
        entry.id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return entry

    def search(self, user_id, query_embedding, limit=5, threshold=0.0, agent_id=None):
        conn = self._conn()
        cur = conn.cursor()
        sql = """SELECT id, user_id, agent_id, kind, role, content, embedding, metadata, created_at,
                        1 - (embedding <=> %s::vector) AS similarity
                 FROM memories
                 WHERE user_id = %s AND embedding IS NOT NULL"""
        params: List[Any] = [query_embedding, user_id]
        if agent_id:
            sql += " AND agent_id = %s"
            params.append(agent_id)
        sql += " AND 1 - (embedding <=> %s::vector) >= %s ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([query_embedding, threshold, query_embedding, limit])
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [(self._row_to_entry(r), r[9]) for r in rows]

    def history(self, user_id, limit=20, offset=0, agent_id=None):
        conn = self._conn()
        cur = conn.cursor()
        sql = "SELECT id, user_id, agent_id, kind, role, content, embedding, metadata, created_at FROM memories WHERE user_id = %s"
        params: List[Any] = [user_id]
        if agent_id:
            sql += " AND agent_id = %s"
            params.append(agent_id)
        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    def delete(self, user_id, memory_id=None, agent_id=None):
        conn = self._conn()
        cur = conn.cursor()
        if memory_id is not None:
            cur.execute("DELETE FROM memories WHERE id = %s AND user_id = %s", (memory_id, user_id))
        else:
            sql = "DELETE FROM memories WHERE user_id = %s"
            params: List[Any] = [user_id]
            if agent_id:
                sql += " AND agent_id = %s"
                params.append(agent_id)
            cur.execute(sql, params)
        count = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        return count

    def count(self, user_id=None):
        conn = self._conn()
        cur = conn.cursor()
        if user_id:
            cur.execute("SELECT COUNT(*) FROM memories WHERE user_id = %s", (user_id,))
        else:
            cur.execute("SELECT COUNT(*) FROM memories")
        n = cur.fetchone()[0]
        cur.close()
        conn.close()
        return int(n)

    def prune(self, user_id: str, keep: int = 10000):
        """Keep only the newest `keep` entries for a user."""
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM memories WHERE id IN (
                SELECT id FROM memories WHERE user_id = %s
                ORDER BY created_at DESC OFFSET %s
            )
        """, (user_id, keep))
        conn.commit()
        cur.close()
        conn.close()

    @staticmethod
    def _row_to_entry(row) -> MemoryEntry:
        return MemoryEntry(
            id=row[0], user_id=row[1], agent_id=row[2], kind=row[3], role=row[4],
            content=row[5], embedding=row[6],
            metadata=json.loads(row[7]) if isinstance(row[7], str) else (row[7] or {}),
            created_at=row[8],
        )


def create_store(url: Optional[str] = None, dim: int = 1536) -> BaseStore:
    """Create the right store based on a URL/path. Defaults to SQLite."""
    if not url:
        return SQLiteStore()
    if url.startswith("postgres"):
        return PostgresStore(url, dim=dim)
    return SQLiteStore(path=url)
