"""Shared time formatting — render all user-facing timestamps in US Eastern.

Uses zoneinfo so daylight saving is handled automatically (EDT in summer,
EST in winter) rather than hardcoding an offset.
"""
from __future__ import annotations

from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover — fall back to a fixed EST offset
    from datetime import timedelta
    _ET = timezone(timedelta(hours=-5), name="EST")


def to_et(dt: datetime) -> datetime:
    """Convert any datetime (naive treated as UTC) to US Eastern."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET)


def fmt_et(dt_or_iso, with_date: bool = True) -> str:
    """Format a datetime or ISO string as 'YYYY-MM-DD HH:MM EDT/EST'."""
    if isinstance(dt_or_iso, str):
        try:
            dt = datetime.fromisoformat(dt_or_iso)
        except (ValueError, TypeError):
            return dt_or_iso
    else:
        dt = dt_or_iso
    et = to_et(dt)
    fmt = "%Y-%m-%d %H:%M %Z" if with_date else "%H:%M %Z"
    return et.strftime(fmt)


def now_et_str() -> str:
    return fmt_et(datetime.now(timezone.utc))
