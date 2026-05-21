"""Settings access. Thin wrapper over the settings table."""
from __future__ import annotations

from . import db


def get(key: str, default: str | None = None) -> str | None:
    return db.get_setting(key, default)


def get_float(key: str, default: float) -> float:
    raw = db.get_setting(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_int(key: str, default: int) -> int:
    raw = db.get_setting(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def set(key: str, value: str) -> None:
    db.set_setting(key, value)
