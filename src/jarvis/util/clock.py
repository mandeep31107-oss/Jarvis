"""Time helpers. Jarvis stores every timestamp as UTC ISO-8601."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(value: str | None) -> float | None:
    """Age of a timestamp in days, or ``None`` when it cannot be parsed."""
    dt = parse_iso(value)
    if dt is None:
        return None
    return (utcnow() - dt).total_seconds() / 86400.0
