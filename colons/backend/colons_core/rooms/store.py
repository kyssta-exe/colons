"""
Group rooms: models and SQLite persistence.
"""
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_ROOM_MESSAGES = 500  # per room, pruned automatically
HISTORY_IN_PROMPT = 30   # transcript lines passed into member turns


@dataclass
class Room:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = "Room"
    owner: str = "default"
    members: List[str] = field(default_factory=list)  # bot ids
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RoomMessage:
    id: Optional[int] = None
    room_id: str = ""
    speaker: str = "user"          # "user" | bot id
    content: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


class RoomStore:
    """SQLite-backed rooms and room transcripts."""

    def __init__(self, path: str, max_messages: int = MAX_ROOM_MESSAGES):
        self.path = path
        self.max_messages = max_messages
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
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT 'default',
                    members TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS room_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_room_messages ON room_messages(room_id, id);
            """)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ #
    # Rooms
    # ------------------------------------------------------------------ #

    def save(self, room: Room) -> Room:
        with self._lock:
            conn = self._conn()
            conn.execute("""
                INSERT INTO rooms (id, name, owner, members, created_at, metadata)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, owner=excluded.owner,
                    members=excluded.members, metadata=excluded.metadata
            """, (room.id, room.name, room.owner, json.dumps(room.members),
                  room.created_at, json.dumps(room.metadata or {})))
            conn.commit()
            conn.close()
        return room

    def get(self, room_id: str) -> Optional[Room]:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
            conn.close()
        return self._row_to_room(row) if row else None

    def list(self, owner: Optional[str] = None) -> List[Room]:
        sql = "SELECT * FROM rooms"
        params: List[Any] = []
        if owner:
            sql += " WHERE owner = ?"
            params.append(owner)
        sql += " ORDER BY created_at ASC"
        with self._lock:
            conn = self._conn()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
        return [self._row_to_room(r) for r in rows]

    def delete(self, room_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            cur = conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
            conn.execute("DELETE FROM room_messages WHERE room_id = ?", (room_id,))
            conn.commit()
            conn.close()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    def append(self, message: RoomMessage) -> RoomMessage:
        with self._lock:
            conn = self._conn()
            cur = conn.execute("""
                INSERT INTO room_messages (room_id, speaker, content, created_at, metadata)
                VALUES (?,?,?,?,?)
            """, (message.room_id, message.speaker, message.content,
                  message.created_at, json.dumps(message.metadata or {})))
            message.id = cur.lastrowid
            conn.execute("""
                DELETE FROM room_messages WHERE room_id = ? AND id NOT IN (
                    SELECT id FROM room_messages WHERE room_id = ?
                    ORDER BY id DESC LIMIT ?
                )
            """, (message.room_id, message.room_id, self.max_messages))
            conn.commit()
            conn.close()
        return message

    def messages(self, room_id: str, limit: int = 100, offset: int = 0) -> List[RoomMessage]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM room_messages WHERE room_id = ?
                ORDER BY id ASC LIMIT ? OFFSET ?
            """, (room_id, limit, offset)).fetchall()
            conn.close()
        return [self._row_to_message(r) for r in rows]

    def recent(self, room_id: str, limit: int = HISTORY_IN_PROMPT) -> List[RoomMessage]:
        with self._lock:
            conn = self._conn()
            rows = conn.execute("""
                SELECT * FROM room_messages WHERE room_id = ?
                ORDER BY id DESC LIMIT ?
            """, (room_id, limit)).fetchall()
            conn.close()
        return [self._row_to_message(r) for r in reversed(rows)]

    def message_count(self, room_id: str) -> int:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT COUNT(*) FROM room_messages WHERE room_id = ?",
                               (room_id,)).fetchone()
            conn.close()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_room(row: sqlite3.Row) -> Room:
        return Room(
            id=row["id"], name=row["name"], owner=row["owner"],
            members=json.loads(row["members"]) if row["members"] else [],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> RoomMessage:
        return RoomMessage(
            id=row["id"], room_id=row["room_id"], speaker=row["speaker"],
            content=row["content"], created_at=row["created_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
