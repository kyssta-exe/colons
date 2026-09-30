"""
Persistent session storage (SQLite).

Keeps conversation sessions and their messages across restarts so the web UI,
CLI and messaging integrations all resume where they left off.
"""
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..providers.base import Message

logger = logging.getLogger(__name__)


@dataclass
class SessionMeta:
    id: str
    agent_id: str = "default"
    user_id: str = "default"
    title: str = "New chat"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "agent_id": self.agent_id, "user_id": self.user_id,
            "title": self.title, "created_at": self.created_at,
            "updated_at": self.updated_at, "metadata": self.metadata,
        }


class SessionStore:
    """SQLite-backed session persistence."""

    def __init__(self, path: str, max_messages_per_session: int = 500):
        self.path = path
        self.max_messages_per_session = max_messages_per_session
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    user_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL DEFAULT 'New chat',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, agent_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);

                CREATE TABLE IF NOT EXISTS session_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    name TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_session_messages ON session_messages(session_id, id);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(session_messages)")}
            if "attachments" not in columns:
                conn.execute("ALTER TABLE session_messages ADD COLUMN attachments TEXT")
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    def save(self, session: SessionMeta):
        with self._lock:
            conn = self._conn()
            conn.execute("""
                INSERT INTO sessions (id, agent_id, user_id, title, created_at, updated_at, metadata)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, updated_at=excluded.updated_at,
                    metadata=excluded.metadata, agent_id=excluded.agent_id,
                    user_id=excluded.user_id
            """, (session.id, session.agent_id, session.user_id, session.title,
                  session.created_at, session.updated_at, json.dumps(session.metadata or {})))
            conn.commit()
            conn.close()

    def get_meta(self, session_id: str) -> Optional[SessionMeta]:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            conn.close()
        return self._row_to_meta(row) if row else None

    def list(self, user_id: Optional[str] = None, agent_id: Optional[str] = None,
             limit: int = 100) -> List[SessionMeta]:
        sql = "SELECT * FROM sessions"
        clauses, params = [], []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            conn = self._conn()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        return [self._row_to_meta(r) for r in rows]

    def delete(self, session_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
        return cur.rowcount > 0

    def touch(self, session_id: str, title: Optional[str] = None):
        with self._lock:
            conn = self._conn()
            if title is not None:
                conn.execute("UPDATE sessions SET updated_at = ?, title = ? WHERE id = ?",
                             (time.time(), title, session_id))
            else:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?",
                             (time.time(), session_id))
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    def append_message(self, session_id: str, message: Message):
        with self._lock:
            conn = self._conn()
            conn.execute("""
                INSERT INTO session_messages
                    (session_id, role, content, name, tool_calls, tool_call_id, created_at, attachments)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                session_id, message.role, message.content or "", message.name,
                json.dumps(message.tool_calls) if message.tool_calls else None,
                message.tool_call_id, time.time(),
                json.dumps(message.attachments) if message.attachments else None,
            ))
            # Prune oldest messages beyond the cap
            conn.execute("""
                DELETE FROM session_messages WHERE session_id = ? AND id NOT IN (
                    SELECT id FROM session_messages WHERE session_id = ?
                    ORDER BY id DESC LIMIT ?
                )
            """, (session_id, session_id, self.max_messages_per_session))
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?",
                         (time.time(), session_id))
            conn.commit()
            conn.close()

    def messages(self, session_id: str, limit: int = 1000) -> List[Message]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute("""
                SELECT role, content, name, tool_calls, tool_call_id, attachments
                FROM session_messages WHERE session_id = ?
                ORDER BY id ASC LIMIT ?
            """, (session_id, limit)).fetchall()
            conn.close()
        out: List[Message] = []
        for row in rows:
            out.append(Message(
                role=row["role"],
                content=row["content"] or "",
                name=row["name"],
                tool_calls=json.loads(row["tool_calls"]) if row["tool_calls"] else None,
                tool_call_id=row["tool_call_id"],
                attachments=json.loads(row["attachments"]) if row["attachments"] else None,
            ))
        return out

    def count(self, user_id: Optional[str] = None) -> int:
        with self._lock:
            conn = self._conn()
            if user_id:
                row = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?",
                                   (user_id,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            conn.close()
        return int(row[0]) if row else 0

    def count_messages(self, session_id: str) -> int:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
                               (session_id,)).fetchone()
            conn.close()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_meta(row: sqlite3.Row) -> SessionMeta:
        return SessionMeta(
            id=row["id"], agent_id=row["agent_id"], user_id=row["user_id"],
            title=row["title"], created_at=row["created_at"], updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
