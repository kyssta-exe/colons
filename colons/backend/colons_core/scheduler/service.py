"""
Scheduler service: runs due schedules in the background.

The service is intentionally decoupled from the agent: it calls an
`executor(schedule) -> Optional[str]` callback provided by the host app
(typically AgentManager), and an optional `notifier(schedule, result)`.
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from .cron import CronError, CronSchedule
from .store import Schedule, ScheduleStore

logger = logging.getLogger(__name__)

Executor = Callable[[Schedule], Awaitable[Optional[str]]]
Notifier = Callable[[Schedule, str], Awaitable[None]]


class SchedulerService:
    """Background scheduler with cron, interval, and one-shot schedules."""

    def __init__(
        self,
        store: ScheduleStore,
        executor: Executor,
        notifier: Optional[Notifier] = None,
        default_timezone: str = "UTC",
        tick_interval: float = 15.0,
        max_concurrent: int = 3,
    ):
        self.store = store
        self.executor = executor
        self.notifier = notifier
        self.default_timezone = default_timezone
        self.tick_interval = tick_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active: Dict[str, asyncio.Task] = {}
        self._history: List[Dict] = []
        self._max_history = 200

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        if self._running:
            return
        self._running = True
        self._recompute_all_next_runs()
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Scheduler started ({len(self.store.list())} schedules)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for task in self._active.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._active.values(), return_exceptions=True)
        self._active.clear()
        logger.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def add(self, schedule: Schedule, validate: bool = True) -> Schedule:
        if validate:
            CronSchedule.parse(schedule.cron)  # raises CronError
        schedule.next_run = self._compute_next_run(schedule)
        if not schedule.name:
            schedule.name = schedule.prompt[:40] or schedule.cron
        return self.store.save(schedule)

    def update(self, schedule_id: str, **changes) -> Optional[Schedule]:
        schedule = self.store.get(schedule_id)
        if not schedule:
            return None
        for key, value in changes.items():
            if hasattr(schedule, key) and value is not None:
                setattr(schedule, key, value)
        try:
            CronSchedule.parse(schedule.cron)
        except CronError:
            pass
        schedule.next_run = self._compute_next_run(schedule)
        return self.store.save(schedule)

    def remove(self, schedule_id: str) -> bool:
        return self.store.delete(schedule_id)

    def get(self, schedule_id: str) -> Optional[Schedule]:
        return self.store.get(schedule_id)

    def list(self) -> List[Schedule]:
        return self.store.list()

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def run_now(self, schedule_id: str) -> Optional[str]:
        schedule = self.store.get(schedule_id)
        if not schedule:
            return None
        return await self._execute(schedule, scheduled=False)

    async def _execute(self, schedule: Schedule, scheduled: bool = True) -> Optional[str]:
        async with self._semaphore:
            started = time.time()

            # Monitor mode: skip the LLM entirely when nothing changed
            if schedule.monitor_command:
                output = await self._run_monitor(schedule.monitor_command)
                digest = hashlib.sha256(output.encode()).hexdigest()
                if digest == schedule.last_monitor_hash and schedule.last_status in ("ok", "unchanged"):
                    schedule.last_run = time.time()
                    schedule.last_status = "unchanged"
                    schedule.last_error = None
                    schedule.run_count += 1
                    schedule.next_run = self._compute_next_run(schedule, after=time.time())
                    self.store.save(schedule)
                    self._history.append({
                        "schedule_id": schedule.id, "name": schedule.name,
                        "status": "unchanged", "error": None,
                        "duration_ms": round((time.time() - started) * 1000, 2),
                        "scheduled": scheduled, "at": time.time(),
                    })
                    logger.info(f"Schedule '{schedule.name}' unchanged; skipped")
                    return None
                schedule.last_monitor_hash = digest
                schedule.metadata["monitor_output"] = output[:4000]

            logger.info(f"Running schedule '{schedule.name}' ({schedule.id})")
            status = "ok"
            error = None
            result: Optional[str] = None
            try:
                result = await self.executor(schedule)
            except Exception as e:
                status = "error"
                error = f"{type(e).__name__}: {e}"
                logger.exception(f"Schedule '{schedule.name}' failed")

            schedule.last_run = time.time()
            schedule.last_status = status
            schedule.last_error = error
            schedule.run_count += 1
            if result and status == "ok":
                schedule.last_result = result[:8000]

            if schedule.cron.strip().lower().startswith("@at"):
                schedule.enabled = False
                schedule.next_run = None
            else:
                schedule.next_run = self._compute_next_run(schedule, after=time.time())

            self.store.save(schedule)
            duration = time.time() - started

            self._history.append({
                "schedule_id": schedule.id,
                "name": schedule.name,
                "status": status,
                "error": error,
                "duration_ms": round(duration * 1000, 2),
                "scheduled": scheduled,
                "at": time.time(),
            })
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            if result and self.notifier and schedule.notify_adapter:
                try:
                    await self.notifier(schedule, result)
                except Exception as e:
                    logger.warning(f"Schedule notification failed: {e}")

            return result

    async def _run_monitor(self, command: str, timeout: float = 30.0) -> str:
        """Run a monitor shell command and return its combined output."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return f"<monitor timeout after {timeout}s>"
            return (stdout or b"").decode("utf-8", errors="replace")[:8000]
        except Exception as e:
            return f"<monitor error: {e}>"

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.tick_interval)
                now = time.time()
                due = [
                    s for s in self.store.list(enabled_only=True)
                    if s.next_run is not None and s.next_run <= now
                    and s.id not in self._active
                ]
                for schedule in due:
                    task = asyncio.create_task(self._execute(schedule))
                    self._active[schedule.id] = task
                    task.add_done_callback(lambda t, sid=schedule.id: self._active.pop(sid, None))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")
                await asyncio.sleep(2)

    # ------------------------------------------------------------------ #
    # Next-run computation
    # ------------------------------------------------------------------ #

    def _now(self) -> datetime:
        if ZoneInfo and self.default_timezone:
            try:
                return datetime.now(ZoneInfo(self.default_timezone))
            except Exception:
                pass
        return datetime.now(timezone.utc)

    def _compute_next_run(self, schedule: Schedule, after: Optional[float] = None) -> Optional[float]:
        try:
            cron = CronSchedule.parse(schedule.cron)
        except CronError as e:
            logger.warning(f"Invalid cron for schedule {schedule.id}: {e}")
            return None

        if cron.kind == "interval":
            base = after if after is not None else time.time()
            return base + cron.interval_seconds

        if cron.kind == "once":
            return cron.run_at.timestamp() if cron.run_at else None

        tz_name = (schedule.metadata or {}).get("timezone", self.default_timezone)
        reference: datetime
        if after is not None:
            reference = datetime.fromtimestamp(after, tz=timezone.utc)
        else:
            reference = datetime.now(timezone.utc)

        if ZoneInfo and tz_name:
            try:
                reference = reference.astimezone(ZoneInfo(tz_name))
            except Exception:
                pass

        nxt = cron.next_after(reference)
        return nxt.timestamp() if nxt else None

    def _recompute_all_next_runs(self):
        for schedule in self.store.list(enabled_only=True):
            if schedule.next_run is None:
                schedule.next_run = self._compute_next_run(schedule)
                self.store.save(schedule)

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def history(self, limit: int = 50) -> List[Dict]:
        return self._history[-limit:]

    def status(self) -> Dict:
        schedules = self.store.list()
        return {
            "running": self._running,
            "timezone": self.default_timezone,
            "tick_interval": self.tick_interval,
            "total": len(schedules),
            "enabled": len([s for s in schedules if s.enabled]),
            "active_runs": len(self._active),
            "next_up": sorted(
                [
                    {"id": s.id, "name": s.name, "next_run": s.next_run}
                    for s in schedules if s.enabled and s.next_run
                ],
                key=lambda x: x["next_run"],
            )[:5],
        }

    @staticmethod
    def validate(expression: str) -> Dict:
        """Validate a cron expression without scheduling it."""
        cron = CronSchedule.parse(expression)
        now = datetime.now(timezone.utc)
        nxt = cron.next_after(now)
        return {
            "valid": True,
            "kind": cron.kind,
            "expression": cron.expression,
            "description": cron.describe(),
            "next_run": nxt.timestamp() if nxt else None,
            "next_run_iso": nxt.isoformat() if nxt else None,
        }
