"""
Schedule model + persistence (SQLite, colocated with the data dir).
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


@dataclass
class Schedule:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    cron: str = ""                  # cron expression / @every / @at
    prompt: str = ""                # what to ask the agent
    user_id: str = "default"
    agent_id: Optional[str] = None
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_run: Optional[float] = None
    last_status: Optional[str] = None     # ok | error
    last_error: Optional[str] = None
    next_run: Optional[float] = None
    run_count: int = 0
    # Optional delivery to a messaging channel
    notify_adapter: Optional[str] = None
    notify_chat_id: Optional[str] = None
    # Continuity / notepad / monitor mode
    continuity: bool = False        # include the previous result in the next run
    notes: str = ""                 # durable notepad injected into every run
    monitor_command: str = ""       # shell command; skip the LLM when its output is unchanged
    last_result: str = ""           # last successful run output (for continuity)
    last_monitor_hash: str = ""     # hash of the last monitor-command output
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


class ScheduleStore:
    """SQLite-backed schedule storage."""

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
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    cron TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    agent_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    last_run REAL,
                    last_status TEXT,
                    last_error TEXT,
                    next_run REAL,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    notify_adapter TEXT,
                    notify_chat_id TEXT,
                    continuity INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    monitor_command TEXT NOT NULL DEFAULT '',
                    last_result TEXT NOT NULL DEFAULT '',
                    last_monitor_hash TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
                CREATE INDEX IF NOT EXISTS idx_schedules_next ON schedules(next_run);
            """)
            # Migrate older databases
            existing = {row[1] for row in conn.execute("PRAGMA table_info(schedules)")}
            for column, ddl in (
                ("continuity", "ALTER TABLE schedules ADD COLUMN continuity INTEGER NOT NULL DEFAULT 0"),
                ("notes", "ALTER TABLE schedules ADD COLUMN notes TEXT NOT NULL DEFAULT ''"),
                ("monitor_command", "ALTER TABLE schedules ADD COLUMN monitor_command TEXT NOT NULL DEFAULT ''"),
                ("last_result", "ALTER TABLE schedules ADD COLUMN last_result TEXT NOT NULL DEFAULT ''"),
                ("last_monitor_hash", "ALTER TABLE schedules ADD COLUMN last_monitor_hash TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in existing:
                    conn.execute(ddl)
            conn.commit()
            conn.close()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def save(self, schedule: Schedule) -> Schedule:
        with self._lock:
            conn = self._conn()
            conn.execute("""
                INSERT INTO schedules (id, name, cron, prompt, user_id, agent_id, enabled,
                    created_at, last_run, last_status, last_error, next_run, run_count,
                    notify_adapter, notify_chat_id, continuity, notes, monitor_command,
                    last_result, last_monitor_hash, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, cron=excluded.cron, prompt=excluded.prompt,
                    user_id=excluded.user_id, agent_id=excluded.agent_id, enabled=excluded.enabled,
                    last_run=excluded.last_run, last_status=excluded.last_status,
                    last_error=excluded.last_error, next_run=excluded.next_run,
                    run_count=excluded.run_count, notify_adapter=excluded.notify_adapter,
                    notify_chat_id=excluded.notify_chat_id, continuity=excluded.continuity,
                    notes=excluded.notes, monitor_command=excluded.monitor_command,
                    last_result=excluded.last_result,
                    last_monitor_hash=excluded.last_monitor_hash,
                    metadata=excluded.metadata
            """, (
                schedule.id, schedule.name, schedule.cron, schedule.prompt,
                schedule.user_id, schedule.agent_id, int(schedule.enabled),
                schedule.created_at, schedule.last_run, schedule.last_status,
                schedule.last_error, schedule.next_run, schedule.run_count,
                schedule.notify_adapter, schedule.notify_chat_id,
                int(schedule.continuity), schedule.notes, schedule.monitor_command,
                schedule.last_result, schedule.last_monitor_hash,
                json.dumps(schedule.metadata or {}),
            ))
            conn.commit()
            conn.close()
        return schedule

    def get(self, schedule_id: str) -> Optional[Schedule]:
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            conn.close()
        return self._row_to_schedule(row) if row else None

    def list(self, enabled_only: bool = False) -> List[Schedule]:
        with self._lock:
            conn = self._conn()
            sql = "SELECT * FROM schedules"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql).fetchall()
            conn.close()
        return [self._row_to_schedule(r) for r in rows]

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            conn.commit()
            conn.close()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_schedule(row: sqlite3.Row) -> Schedule:
        return Schedule(
            id=row["id"], name=row["name"], cron=row["cron"], prompt=row["prompt"],
            user_id=row["user_id"], agent_id=row["agent_id"],
            enabled=bool(row["enabled"]), created_at=row["created_at"],
            last_run=row["last_run"], last_status=row["last_status"],
            last_error=row["last_error"], next_run=row["next_run"],
            run_count=row["run_count"], notify_adapter=row["notify_adapter"],
            notify_chat_id=row["notify_chat_id"],
            continuity=bool(row["continuity"]) if "continuity" in row.keys() else False,
            notes=row["notes"] if "notes" in row.keys() else "",
            monitor_command=row["monitor_command"] if "monitor_command" in row.keys() else "",
            last_result=row["last_result"] if "last_result" in row.keys() else "",
            last_monitor_hash=row["last_monitor_hash"] if "last_monitor_hash" in row.keys() else "",
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
