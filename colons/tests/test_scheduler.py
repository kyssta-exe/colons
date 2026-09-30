"""Tests for cron parsing and the scheduler service."""
import asyncio
from datetime import datetime, timezone

import pytest

from colons_core.scheduler import (
    CronError, CronSchedule, Schedule, SchedulerService, ScheduleStore,
)


# --------------------------------------------------------------------------- #
# Cron parsing
# --------------------------------------------------------------------------- #

def test_parse_every_minute():
    c = CronSchedule.parse("* * * * *")
    assert c.kind == "cron"
    assert c.matches(datetime(2026, 1, 1, 10, 30))
    assert c.minutes == set(range(60))


def test_parse_specific_time():
    c = CronSchedule.parse("30 9 * * *")
    assert c.matches(datetime(2026, 1, 1, 9, 30))
    assert not c.matches(datetime(2026, 1, 1, 9, 31))
    assert not c.matches(datetime(2026, 1, 1, 10, 30))


def test_parse_lists_and_ranges():
    c = CronSchedule.parse("0,15,30,45 9-17 * * *")
    assert c.hours == set(range(9, 18))
    assert c.minutes == {0, 15, 30, 45}


def test_parse_steps():
    c = CronSchedule.parse("*/15 * * * *")
    assert c.minutes == {0, 15, 30, 45}
    c2 = CronSchedule.parse("0 */6 * * *")
    assert c2.hours == {0, 6, 12, 18}


def test_parse_names():
    c = CronSchedule.parse("0 9 * jan-mar mon")
    assert c.months == {1, 2, 3}
    assert c.weekdays == {1}


def test_dow_sunday_zero_and_seven():
    c = CronSchedule.parse("0 0 * * 0")
    assert c.weekdays == {0}
    c7 = CronSchedule.parse("0 0 * * 7")
    assert c7.weekdays == {0}


def test_dom_dow_or_semantics():
    # When both dom and dow are restricted, cron uses OR
    c = CronSchedule.parse("0 0 1 * mon")
    # Jan 1 2026 is a Thursday -> dom matches
    assert c.matches(datetime(2026, 1, 1))
    # A Monday that isn't the 1st -> dow matches
    assert c.matches(datetime(2026, 1, 5))
    # A Tuesday that's not the 1st -> no match
    assert not c.matches(datetime(2026, 1, 6))


def test_weekday_matching():
    # Every weekday at 9
    c = CronSchedule.parse("0 9 * * 1-5")
    assert c.matches(datetime(2026, 1, 5, 9, 0))    # Monday
    assert not c.matches(datetime(2026, 1, 4, 9, 0))  # Sunday


def test_shorthands():
    assert CronSchedule.parse("@daily").expression == "0 0 * * *"
    assert CronSchedule.parse("@hourly").expression == "0 * * * *"
    assert CronSchedule.parse("@weekly").expression == "0 0 * * 0"


def test_interval_parsing():
    c = CronSchedule.parse("@every 30s")
    assert c.kind == "interval" and c.interval_seconds == 30
    assert CronSchedule.parse("@every 5m").interval_seconds == 300
    assert CronSchedule.parse("@every 2 hours").interval_seconds == 7200
    assert CronSchedule.parse("@every 1d").interval_seconds == 86400


def test_interval_too_small():
    with pytest.raises(CronError):
        CronSchedule.parse("@every 2s")


def test_once_parsing():
    c = CronSchedule.parse("@at 2026-10-01T09:00:00")
    assert c.kind == "once"
    assert c.run_at.year == 2026


def test_invalid_expressions():
    for bad in ["", "* * *", "60 * * * *", "* 24 * * *", "abc", "*/0 * * * *", "5-1 * * * *"]:
        with pytest.raises(CronError):
            CronSchedule.parse(bad)


def test_next_after_simple():
    c = CronSchedule.parse("30 9 * * *")
    nxt = c.next_after(datetime(2026, 1, 1, 8, 0))
    assert nxt == datetime(2026, 1, 1, 9, 30)
    # After today's 9:30, next is tomorrow
    nxt2 = c.next_after(datetime(2026, 1, 1, 10, 0))
    assert nxt2 == datetime(2026, 1, 2, 9, 30)


def test_next_after_month_boundary():
    c = CronSchedule.parse("0 0 1 * *")
    nxt = c.next_after(datetime(2026, 1, 15))
    assert nxt == datetime(2026, 2, 1, 0, 0)


def test_next_after_leap_year():
    c = CronSchedule.parse("0 0 29 2 *")
    nxt = c.next_after(datetime(2026, 1, 1))
    assert nxt == datetime(2028, 2, 29, 0, 0)


# --------------------------------------------------------------------------- #
# Scheduler service
# --------------------------------------------------------------------------- #

@pytest.fixture
def schedule_store(tmp_path):
    return ScheduleStore(str(tmp_path / "schedules.db"))


@pytest.mark.asyncio
async def test_add_and_list_schedule(schedule_store):
    runs = []

    async def executor(schedule):
        runs.append(schedule.id)
        return "done"

    scheduler = SchedulerService(schedule_store, executor, tick_interval=0.1)
    schedule = Schedule(cron="* * * * *", prompt="say hi", name="greeting")
    scheduler.add(schedule)

    assert schedule.next_run is not None
    assert len(scheduler.list()) == 1
    stored = scheduler.get(schedule.id)
    assert stored.prompt == "say hi"


@pytest.mark.asyncio
async def test_add_invalid_cron_rejected(schedule_store):
    async def executor(schedule):
        return ""

    scheduler = SchedulerService(schedule_store, executor)
    with pytest.raises(CronError):
        scheduler.add(Schedule(cron="not a cron", prompt="x"))


@pytest.mark.asyncio
async def test_run_now_executes_and_records(schedule_store):
    runs = []

    async def executor(schedule):
        runs.append(schedule.name)
        return f"result for {schedule.name}"

    scheduler = SchedulerService(schedule_store, executor)
    schedule = scheduler.add(Schedule(cron="0 0 * * *", prompt="daily report", name="report"))

    result = await scheduler.run_now(schedule.id)
    assert result == "result for report"
    assert runs == ["report"]

    updated = scheduler.get(schedule.id)
    assert updated.run_count == 1
    assert updated.last_status == "ok"
    assert updated.last_run is not None
    history = scheduler.history()
    assert history[-1]["status"] == "ok"


@pytest.mark.asyncio
async def test_execution_error_recorded(schedule_store):
    async def failing(schedule):
        raise RuntimeError("boom")

    scheduler = SchedulerService(schedule_store, failing)
    schedule = scheduler.add(Schedule(cron="0 0 * * *", prompt="x", name="failing"))
    await scheduler.run_now(schedule.id)

    updated = scheduler.get(schedule.id)
    assert updated.last_status == "error"
    assert "boom" in updated.last_error


@pytest.mark.asyncio
async def test_due_schedule_runs_via_loop(schedule_store):
    runs = []

    async def executor(schedule):
        runs.append(schedule.id)
        return "ok"

    scheduler = SchedulerService(schedule_store, executor, tick_interval=0.05)
    # Interval schedule due almost immediately
    schedule = Schedule(cron="@every 5s", prompt="tick", name="interval")
    scheduler.add(schedule)
    # Force it due now
    schedule.next_run = __import__("time").time() - 1
    schedule_store.save(schedule)

    await scheduler.start()
    await asyncio.sleep(0.3)
    await scheduler.stop()

    assert schedule.id in runs


@pytest.mark.asyncio
async def test_interval_reschedules_after_run(schedule_store):
    async def executor(schedule):
        return "ok"

    scheduler = SchedulerService(schedule_store, executor)
    schedule = scheduler.add(Schedule(cron="@every 60s", prompt="x", name="interval"))
    first_next = schedule.next_run

    await scheduler.run_now(schedule.id)
    updated = scheduler.get(schedule.id)
    assert updated.next_run > first_next


@pytest.mark.asyncio
async def test_one_shot_disables_after_run(schedule_store):
    async def executor(schedule):
        return "done"

    scheduler = SchedulerService(schedule_store, executor)
    schedule = scheduler.add(Schedule(cron="@at 2030-01-01T00:00:00", prompt="once", name="once"))
    await scheduler.run_now(schedule.id)

    updated = scheduler.get(schedule.id)
    assert updated.enabled is False
    assert updated.next_run is None


@pytest.mark.asyncio
async def test_notifier_called_when_configured(schedule_store):
    notifications = []

    async def executor(schedule):
        return "the result"

    async def notifier(schedule, result):
        notifications.append((schedule.name, result))

    scheduler = SchedulerService(schedule_store, executor, notifier=notifier)
    schedule = scheduler.add(Schedule(
        cron="0 0 * * *", prompt="x", name="n",
        notify_adapter="telegram", notify_chat_id="123",
    ))
    await scheduler.run_now(schedule.id)
    assert notifications == [("n", "the result")]


@pytest.mark.asyncio
async def test_persistence_across_instances(schedule_store):
    async def executor(schedule):
        return ""

    scheduler = SchedulerService(schedule_store, executor)
    scheduler.add(Schedule(cron="0 9 * * *", prompt="persistent", name="p"))

    # New store + service over the same file
    store2 = ScheduleStore(schedule_store.path)
    scheduler2 = SchedulerService(store2, executor)
    assert len(scheduler2.list()) == 1
    assert scheduler2.list()[0].prompt == "persistent"


def test_validate_helper():
    result = SchedulerService.validate("*/5 * * * *")
    assert result["valid"] is True
    assert result["next_run_iso"]


# --------------------------------------------------------------------------- #
# Continuity, notepad, monitor mode
# --------------------------------------------------------------------------- #

def test_build_schedule_prompt_notes_and_continuity():
    from colons_api.manager import build_schedule_prompt

    base = Schedule(cron="0 0 * * *", prompt="Summarize the deploy queue")
    assert build_schedule_prompt(base) == "Summarize the deploy queue"

    base.notes = "Known flaky test: test_upload"
    prompt = build_schedule_prompt(base)
    assert "Notepad (persistent across runs)" in prompt
    assert "test_upload" in prompt
    assert prompt.endswith("Summarize the deploy queue")

    base.continuity = True
    base.last_result = "Yesterday: 3 deploys, all green."
    prompt = build_schedule_prompt(base)
    assert "Previous run result" in prompt
    assert "3 deploys" in prompt

    base.metadata["monitor_output"] = "queue depth: 7"
    prompt = build_schedule_prompt(base)
    assert "Monitor command output" in prompt
    assert "queue depth: 7" in prompt


def test_build_schedule_prompt_no_continuity_without_last_result():
    from colons_api.manager import build_schedule_prompt

    schedule = Schedule(cron="0 0 * * *", prompt="Do it", continuity=True)
    prompt = build_schedule_prompt(schedule)
    assert "Previous run result" not in prompt


@pytest.mark.asyncio
async def test_last_result_stored_on_success(schedule_store):
    async def executor(schedule):
        return "run output for continuity"

    scheduler = SchedulerService(schedule_store, executor)
    schedule = scheduler.add(Schedule(cron="0 0 * * *", prompt="x", name="r"))
    await scheduler.run_now(schedule.id)

    assert scheduler.get(schedule.id).last_result == "run output for continuity"


@pytest.mark.asyncio
async def test_monitor_mode_skips_llm_when_unchanged(schedule_store):
    runs = []

    async def executor(schedule):
        runs.append(schedule.id)
        return f"processed: {(schedule.metadata or {}).get('monitor_output', '')}"

    scheduler = SchedulerService(schedule_store, executor)
    schedule = Schedule(cron="0 0 * * *", prompt="handle change",
                        name="monitor", monitor_command="echo stable-state")
    scheduler.add(schedule)

    # First run: no previous hash -> executes
    result = await scheduler.run_now(schedule.id)
    assert result is not None
    assert len(runs) == 1
    assert scheduler.get(schedule.id).last_status == "ok"
    assert "stable-state" in scheduler.get(schedule.id).last_result

    # Second run: same output -> skipped entirely
    result = await scheduler.run_now(schedule.id)
    assert result is None
    assert len(runs) == 1  # executor never called again
    updated = scheduler.get(schedule.id)
    assert updated.last_status == "unchanged"
    assert updated.run_count == 2


@pytest.mark.asyncio
async def test_monitor_mode_runs_when_output_changes(schedule_store):
    runs = []
    state = {"count": 0}

    async def executor(schedule):
        runs.append(schedule.id)
        return f"handled: {(schedule.metadata or {}).get('monitor_output', '')}"

    import os

    scheduler = SchedulerService(schedule_store, executor)
    os.environ["STATE"] = "one"
    schedule = Schedule(
        cron="0 0 * * *", prompt="handle change", name="monitor",
        monitor_command="echo state-$STATE",
    )
    scheduler.add(schedule)

    await scheduler.run_now(schedule.id)
    os.environ["STATE"] = "two"
    await scheduler.run_now(schedule.id)

    assert len(runs) == 2
    assert "state-two" in (scheduler.get(schedule.id).last_result or "")


@pytest.mark.asyncio
async def test_metadata_columns_migrate(tmp_path):
    """Old schedule DBs without the new columns still open and work."""
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE schedules (
            id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', cron TEXT NOT NULL,
            prompt TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
            last_run REAL, last_status TEXT, last_error TEXT, next_run REAL,
            run_count INTEGER NOT NULL DEFAULT 0, notify_adapter TEXT,
            notify_chat_id TEXT, metadata TEXT NOT NULL DEFAULT '{}'
        );
    """)
    conn.commit()
    conn.close()

    store = ScheduleStore(path)
    schedule = store.save(Schedule(cron="0 0 * * *", prompt="legacy"))
    loaded = store.get(schedule.id)
    assert loaded.continuity is False
    assert loaded.notes == ""
    assert loaded.last_result == ""
