"""Local wall-clock schedules. No scheduler process: the poller checks durable slots."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def clock(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("time must be HH:MM (00:00 through 23:59)")
    hour, minute = value.split(":")
    return int(hour), int(minute)


def digest_groups(value: str) -> dict[str, str]:
    groups: dict[str, str] = {}
    for entry in value.split(","):
        if not entry.strip():
            continue
        parts = entry.strip().split(":")
        if len(parts) != 2 or not re.fullmatch(r"[\w-]+", parts[0]):
            raise ValueError("NOTIFY_DIGEST must be group:daily,group:weekly")
        group, cadence = parts
        if group in groups or cadence not in ("daily", "weekly") or group == "all":
            raise ValueError("digest groups must be unique, not 'all', and daily or weekly")
        groups[group] = cadence
    return groups


@dataclass(frozen=True)
class Schedule:
    groups: dict[str, str] = field(default_factory=dict)
    quiet_hours: str = ""
    time: str = "08:00"
    day: str = "mon"

    def __post_init__(self) -> None:
        clock(self.time)
        if self.day not in DAYS:
            raise ValueError("NOTIFY_DIGEST_DAY must be mon, tue, wed, thu, fri, sat or sun")
        if self.quiet_hours:
            parts = self.quiet_hours.split("-")
            if len(parts) != 2 or clock(parts[0]) == clock(parts[1]):
                raise ValueError("quiet hours must be distinct HH:MM-HH:MM times")

    def quiet(self, now: float) -> bool:
        if not self.quiet_hours:
            return False
        start, end = (clock(part) for part in self.quiet_hours.split("-"))
        local = datetime.fromtimestamp(now)
        current = (local.hour, local.minute)
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def next_slot(self, group: str, now: float) -> float:
        """Next strictly future slot in the process timezone (including DST)."""
        hour, minute = clock(self.time)
        candidate = datetime.fromtimestamp(now).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if self.groups[group] == "weekly":
            candidate += timedelta(days=(DAYS.index(self.day) - candidate.weekday()) % 7)
        if candidate.timestamp() <= now:
            candidate += timedelta(days=7 if self.groups[group] == "weekly" else 1)
        return candidate.timestamp()
