"""Load holdings + watchlist from a simple CSV seed file.

Used to rebuild the (ephemeral) SQLite DB at session start in the
Claude Code web environment, where the container is reclaimed and the
DB doesn't survive between sessions.

CSV columns:
    symbol, exchange, currency, category, description,
    target_price, drop_pct_threshold, quantity

`category` must be HOLDING or WATCHLIST. Empty cells are skipped.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_holdings.csv"


def _f(s: str | None) -> float | None:
    if s is None or s.strip() == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load(path: Path) -> tuple[int, int]:
    holdings = 0
    watchlist = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = (row.get("symbol") or "").strip()
            category = (row.get("category") or "").strip().upper()
            if not symbol or category not in {"HOLDING", "WATCHLIST"}:
                continue

            db.upsert_stock(
                symbol=symbol,
                category=category,
                exchange=(row.get("exchange") or "").strip() or None,
                currency=(row.get("currency") or "").strip() or None,
                description=(row.get("description") or "").strip() or None,
            )

            if category == "HOLDING":
                db.upsert_holding(symbol=symbol, quantity=_f(row.get("quantity")))
                holdings += 1
            else:
                db.upsert_watchlist_target(
                    symbol=symbol,
                    target_price=_f(row.get("target_price")),
                    drop_pct_threshold=_f(row.get("drop_pct_threshold")),
                )
                watchlist += 1
    return holdings, watchlist


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, nargs="?", default=DEFAULT_PATH)
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Seed file not found: {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    db.init_schema()
    db.seed_default_settings()
    holdings, watchlist = load(args.csv_path)
    print(f"Loaded {holdings} holdings, {watchlist} watchlist entries from {args.csv_path}")


if __name__ == "__main__":
    main()
