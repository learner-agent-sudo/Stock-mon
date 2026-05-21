"""Add or update a watchlist stock with optional alert thresholds.

Usage:
    python scripts/add_watchlist.py NVDA --target-price 100 --drop-pct 5
    python scripts/add_watchlist.py TXT.WA --drop-pct 7 --notes "Polish small-cap"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="Yahoo Finance symbol (e.g. NVDA, TXT.WA)")
    parser.add_argument("--target-price", type=float, default=None)
    parser.add_argument("--drop-pct", type=float, default=None,
                        help="Alert if yesterday's drop exceeds this percent")
    parser.add_argument("--exchange", default=None)
    parser.add_argument("--currency", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()

    db.init_schema()
    db.upsert_stock(
        symbol=args.symbol,
        category="WATCHLIST",
        exchange=args.exchange,
        currency=args.currency,
        description=args.description,
        notes=args.notes,
    )
    db.upsert_watchlist_target(
        symbol=args.symbol,
        target_price=args.target_price,
        drop_pct_threshold=args.drop_pct,
        notes=args.notes,
    )
    print(f"Watchlist entry saved: {args.symbol}")
    if args.target_price is not None:
        print(f"  target_price = {args.target_price}")
    if args.drop_pct is not None:
        print(f"  drop_pct_threshold = {args.drop_pct}")


if __name__ == "__main__":
    main()
