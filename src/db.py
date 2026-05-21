"""SQLite schema and helpers for the briefing database."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "briefing.db"


def get_db_path() -> Path:
    return Path(os.environ.get("BRIEFING_DB_PATH", DEFAULT_DB_PATH))


SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    symbol       TEXT PRIMARY KEY,
    exchange     TEXT,
    currency     TEXT,
    description  TEXT,
    category     TEXT NOT NULL CHECK (category IN ('HOLDING', 'WATCHLIST')),
    cap_tier     TEXT CHECK (cap_tier IN ('LARGE', 'MID', 'SMALL')),
    notes        TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS holdings (
    symbol               TEXT PRIMARY KEY REFERENCES stocks(symbol) ON DELETE CASCADE,
    quantity             REAL,
    cost_basis_per_share REAL,
    purchase_date        TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_targets (
    symbol             TEXT PRIMARY KEY REFERENCES stocks(symbol) ON DELETE CASCADE,
    target_price       REAL,
    drop_pct_threshold REAL,
    notes              TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS briefing_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stocks_category ON stocks(category);
CREATE INDEX IF NOT EXISTS idx_stocks_active   ON stocks(active);
"""


DEFAULT_SETTINGS = {
    "large_cap_threshold_pct": "3",
    "small_cap_threshold_pct": "5",
    "mid_cap_threshold_pct": "4",
    "small_cap_marketcap_usd": "2000000000",
    "large_cap_marketcap_usd": "10000000000",
    "gain_threshold_pct": "5",
    "small_cap_rank_boost": "1.5",
    "briefing_time": "07:30",
    "email_to": "",
}


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def seed_default_settings(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


def get_setting(key: str, default: str | None = None, db_path: Path | None = None) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str, db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def upsert_stock(
    symbol: str,
    category: str,
    *,
    exchange: str | None = None,
    currency: str | None = None,
    description: str | None = None,
    cap_tier: str | None = None,
    notes: str | None = None,
    active: bool = True,
    db_path: Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stocks (symbol, exchange, currency, description, category, cap_tier, notes, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                exchange    = COALESCE(excluded.exchange, stocks.exchange),
                currency    = COALESCE(excluded.currency, stocks.currency),
                description = COALESCE(excluded.description, stocks.description),
                category    = excluded.category,
                cap_tier    = COALESCE(excluded.cap_tier, stocks.cap_tier),
                notes       = COALESCE(excluded.notes, stocks.notes),
                active      = excluded.active,
                updated_at  = CURRENT_TIMESTAMP
            """,
            (symbol, exchange, currency, description, category, cap_tier, notes, 1 if active else 0),
        )


def upsert_holding(
    symbol: str,
    *,
    quantity: float | None = None,
    cost_basis_per_share: float | None = None,
    purchase_date: str | None = None,
    db_path: Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO holdings (symbol, quantity, cost_basis_per_share, purchase_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                quantity             = COALESCE(excluded.quantity, holdings.quantity),
                cost_basis_per_share = COALESCE(excluded.cost_basis_per_share, holdings.cost_basis_per_share),
                purchase_date        = COALESCE(excluded.purchase_date, holdings.purchase_date)
            """,
            (symbol, quantity, cost_basis_per_share, purchase_date),
        )


def upsert_watchlist_target(
    symbol: str,
    *,
    target_price: float | None = None,
    drop_pct_threshold: float | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO watchlist_targets (symbol, target_price, drop_pct_threshold, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                target_price       = COALESCE(excluded.target_price, watchlist_targets.target_price),
                drop_pct_threshold = COALESCE(excluded.drop_pct_threshold, watchlist_targets.drop_pct_threshold),
                notes              = COALESCE(excluded.notes, watchlist_targets.notes)
            """,
            (symbol, target_price, drop_pct_threshold, notes),
        )


def get_active_stocks(category: str | None = None, db_path: Path | None = None) -> list[sqlite3.Row]:
    query = "SELECT * FROM stocks WHERE active = 1"
    params: tuple = ()
    if category:
        query += " AND category = ?"
        params = (category,)
    query += " ORDER BY symbol"
    with connect(db_path) as conn:
        return list(conn.execute(query, params).fetchall())


def save_briefing(payload_json: str, db_path: Path | None = None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO briefing_history (payload_json) VALUES (?)",
            (payload_json,),
        )
        return cur.lastrowid
