"""
Cron expression parsing and next-run calculation.
Supports standard 5-field cron plus @-shorthands and intervals.

  Field:     minute(0-59) hour(0-23) dom(1-31) month(1-12) dow(0-6, 0=Sunday)
  Syntax:    *  ,  -  /   (e.g. "*/5 9-17 * * 1-5")
  Names:     JAN..DEC, SUN..SAT
  Shorthand: @yearly @monthly @weekly @daily @hourly
  Interval:  @every 30s | @every 5m | @every 2h | @every 1d
  One-shot:  @at 2026-10-01T09:00:00
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Set

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

SHORTHANDS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_INTERVAL_RE = re.compile(r"^@every\s+(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$", re.I)
_AT_RE = re.compile(r"^@at\s+(.+)$", re.I)


class CronError(ValueError):
    """Invalid cron expression."""


@dataclass
class CronSchedule:
    kind: str                      # "cron" | "interval" | "once"
    minutes: Set[int] = None
    hours: Set[int] = None
    days: Set[int] = None
    months: Set[int] = None
    weekdays: Set[int] = None
    dom_restricted: bool = False
    dow_restricted: bool = False
    interval_seconds: int = 0
    run_at: Optional[datetime] = None
    expression: str = ""

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        expr = (expression or "").strip()
        if not expr:
            raise CronError("Empty cron expression")

        lower = expr.lower()

        if lower in SHORTHANDS:
            expr = SHORTHANDS[lower]
            lower = expr.lower()

        m = _INTERVAL_RE.match(expr)
        if m:
            amount = int(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith("s"):
                seconds = amount
            elif unit.startswith("m"):
                seconds = amount * 60
            elif unit.startswith("h"):
                seconds = amount * 3600
            else:
                seconds = amount * 86400
            if seconds < 5:
                raise CronError("Interval must be at least 5 seconds")
            return cls(kind="interval", interval_seconds=seconds, expression=expr)

        m = _AT_RE.match(expr)
        if m:
            raw = m.group(1).strip()
            try:
                run_at = datetime.fromisoformat(raw)
            except ValueError as e:
                raise CronError(f"Invalid @at datetime: {raw}") from e
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            return cls(kind="once", run_at=run_at, expression=expr)

        fields = expr.split()
        if len(fields) != 5:
            raise CronError(f"Expected 5 fields, got {len(fields)}: {expr!r}")

        minute_s, hour_s, dom_s, month_s, dow_s = fields
        sched = cls(
            kind="cron",
            minutes=_parse_field(minute_s, 0, 59, {}),
            hours=_parse_field(hour_s, 0, 23, {}),
            days=_parse_field(dom_s, 1, 31, {}),
            months=_parse_field(month_s, 1, 12, MONTH_NAMES),
            # Day-of-week accepts 0-7 (both 0 and 7 are Sunday in cron)
            weekdays=_parse_field(dow_s, 0, 7, DOW_NAMES),
            dom_restricted=dom_s.strip() != "*",
            dow_restricted=dow_s.strip() != "*",
            expression=expr,
        )
        # Cron quirk: 7 is also Sunday
        if 7 in sched.weekdays:
            sched.weekdays.discard(7)
            sched.weekdays.add(0)
        return sched

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #

    def matches(self, dt: datetime) -> bool:
        if self.kind == "interval":
            return False  # intervals are state-based, not clock-based
        if self.kind == "once":
            if not self.run_at:
                return False
            return abs((dt.astimezone(timezone.utc) - self.run_at.astimezone(timezone.utc)).total_seconds()) < 30

        if dt.minute not in self.minutes or dt.hour not in self.hours:
            return False
        if dt.month not in self.months:
            return False

        dom_match = dt.day in self.days
        dow_match = (dt.weekday() + 1) % 7 in self.weekdays  # python: Mon=0 -> cron Sun=0

        # Standard cron semantics:
        #  - if both dom & dow restricted -> OR
        #  - if only one restricted -> that one
        #  - if neither -> always
        if self.dom_restricted and self.dow_restricted:
            return dom_match or dow_match
        if self.dom_restricted:
            return dom_match
        if self.dow_restricted:
            return dow_match
        return True

    # ------------------------------------------------------------------ #
    # Next run
    # ------------------------------------------------------------------ #

    def next_after(self, dt: datetime, max_days: int = 366 * 4) -> Optional[datetime]:
        """Next datetime strictly after `dt` (timezone-aware or naive as given)."""
        if self.kind == "once":
            if self.run_at and self.run_at > dt:
                return self.run_at
            return None

        if self.kind == "interval":
            return dt + timedelta(seconds=self.interval_seconds)

        # Cron: scan forward day by day, then hour/minute within matching days
        candidate = (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

        for _ in range(max_days):
            if candidate.month in self.months and self._day_matches(candidate):
                # Find the next matching hour/minute on this day
                for hour in sorted(self.hours):
                    if hour < candidate.hour:
                        continue
                    for minute in sorted(self.minutes):
                        if hour == candidate.hour and minute < candidate.minute:
                            continue
                        result = candidate.replace(hour=hour, minute=minute)
                        if result > dt:
                            return result
            # Advance to next day at 00:00
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
        return None

    def _day_matches(self, dt: datetime) -> bool:
        dom_match = dt.day in self.days
        dow_match = (dt.weekday() + 1) % 7 in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom_match or dow_match
        if self.dom_restricted:
            return dom_match
        if self.dow_restricted:
            return dow_match
        return True

    def describe(self) -> str:
        if self.kind == "interval":
            return f"every {self.interval_seconds}s"
        if self.kind == "once":
            return f"once at {self.run_at.isoformat() if self.run_at else '?'}"
        return self.expression


def _parse_field(field: str, low: int, high: int, names: dict) -> Set[int]:
    if field.strip() == "*":
        return set(range(low, high + 1))

    values: Set[int] = set()
    for part in field.split(","):
        part = part.strip().lower()
        if not part:
            raise CronError(f"Empty field element in {field!r}")

        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError as e:
                raise CronError(f"Invalid step in {field!r}") from e
            if step <= 0:
                raise CronError(f"Step must be positive in {field!r}")

        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, _, b = part.partition("-")
            start = _parse_value(a, names, field)
            end = _parse_value(b, names, field)
        else:
            start = end = _parse_value(part, names, field)

        if start > end:
            raise CronError(f"Range start > end in {field!r}")
        if start < low or end > high:
            raise CronError(f"Value out of range [{low},{high}] in {field!r}")

        values.update(range(start, end + 1, step))

    if not values:
        raise CronError(f"Empty set for field {field!r}")
    return values


def _parse_value(raw: str, names: dict, field: str) -> int:
    raw = raw.strip()
    if raw in names:
        return names[raw]
    try:
        return int(raw)
    except ValueError as e:
        raise CronError(f"Invalid value {raw!r} in {field!r}") from e
