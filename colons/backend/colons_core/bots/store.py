"""
Bot model and SQLite persistence.
"""
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(value: str, fallback: Optional[str] = None) -> str:
    value = (value or "").strip().lower()
    value = _SLUG_RE.sub("-", value).strip("-")
    return value or (fallback or uuid.uuid4().hex[:6])


@dataclass
class Bot:
    id: str = ""
    name: str = ""
    title: str = ""
    description: str = ""
    persona: str = ""              # extra system-prompt text
    avatar: str = ""               # avatar catalog id
    model: str = ""                # optional per-bot model override
    owner: str = "default"         # user namespace
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = slugify(self.name, fallback=uuid.uuid4().hex[:8])
        if not self.name:
            self.name = self.id

    @property
    def handle(self) -> str:
        return f"@{self.id}"

    def aliases(self) -> List[str]:
        """All names this bot answers to (lowercased)."""
        out = {self.id.lower(), self.name.lower(), self.handle.lower()}
        if self.title:
            out.add(slugify(self.title, fallback=""))
            out.add(self.title.lower())
        return [a for a in out if a]

    def to_dict(self) -> Dict:
        return {**asdict(self), "handle": self.handle}


class BotStore:
    """SQLite-backed bot roster."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS bots (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    persona TEXT NOT NULL DEFAULT '',
                    avatar TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT 'default',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_bots_owner ON bots(owner);
            """)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def save(self, bot: Bot) -> Bot:
        with self._lock:
            conn = self._conn()
            conn.execute("""
                INSERT INTO bots (id, name, title, description, persona, avatar,
                                  model, owner, enabled, created_at, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, title=excluded.title,
                    description=excluded.description, persona=excluded.persona,
                    avatar=excluded.avatar, model=excluded.model,
                    owner=excluded.owner, enabled=excluded.enabled,
                    metadata=excluded.metadata
            """, (bot.id, bot.name, bot.title, bot.description, bot.persona,
                  bot.avatar, bot.model, bot.owner, int(bot.enabled),
                  bot.created_at, json.dumps(bot.metadata or {})))
            conn.commit()
            conn.close()
        return bot

    def get(self, bot_id: str) -> Optional[Bot]:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
            conn.close()
        return self._row_to_bot(row) if row else None

    def list(self, owner: Optional[str] = None, enabled_only: bool = False) -> List[Bot]:
        sql = "SELECT * FROM bots"
        clauses, params = [], []
        if owner:
            clauses.append("owner = ?")
            params.append(owner)
        if enabled_only:
            clauses.append("enabled = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC"
        with self._lock:
            conn = self._conn()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        return [self._row_to_bot(r) for r in rows]

    def delete(self, bot_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            cur = conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
            conn.commit()
            conn.close()
        return cur.rowcount > 0

    def count(self) -> int:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT COUNT(*) FROM bots").fetchone()
            conn.close()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #

    def resolve(self, target: str, owner: Optional[str] = None) -> Bot:
        """
        Resolve a bot by id, name, @handle or title.

        Raises KeyError with the roster if nothing matches, or if the target
        is ambiguous.
        """
        raw = (target or "").strip()
        if raw.startswith("@"):
            raw = raw[1:]
        needle = raw.lower()
        bots = self.list(owner=owner)
        if not bots:
            raise KeyError("No bots exist yet")

        exact = [b for b in bots if b.id.lower() == needle]
        if exact:
            return exact[0]

        matches = [b for b in bots if needle in b.aliases()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            roster = ", ".join(f"{b.handle} ({b.name})" for b in bots)
            raise KeyError(f"Unknown bot '{target}'. Roster: {roster}")
        roster = ", ".join(f"{b.handle} ({b.name})" for b in matches)
        raise KeyError(f"Ambiguous bot '{target}' matches: {roster}")

    @staticmethod
    def _row_to_bot(row: sqlite3.Row) -> Bot:
        return Bot(
            id=row["id"], name=row["name"], title=row["title"],
            description=row["description"], persona=row["persona"],
            avatar=row["avatar"], model=row["model"], owner=row["owner"],
            enabled=bool(row["enabled"]), created_at=row["created_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
