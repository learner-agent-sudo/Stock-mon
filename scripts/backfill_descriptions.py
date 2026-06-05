"""Backfill Wikipedia summaries for every 13F stock that doesn't yet have a
description.

The weekly 13F build only attempts descriptions for the top-N most relevant
stocks (your watchlist + top-500 by $ impact). This script pushes that
boundary out for the whole 13F universe so that *eventually* every stock you
might click has a paragraph attached.

Bounded by both wall-clock budget and per-call throttling so it's safe to run
on its own schedule or via workflow_dispatch.

Usage:
    python scripts/backfill_descriptions.py [--budget-sec N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.data_layer import ticker_info  # noqa: E402

HISTORY = REPO / "docs" / "history"


def latest_archive() -> Path | None:
    files = sorted(HISTORY.glob("13f-*.json"))
    return files[-1] if files else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-sec", type=int, default=900,
                        help="Wall-clock budget for Wikipedia fetches (default 900s).")
    args = parser.parse_args()

    archive = latest_archive()
    if not archive:
        print("No 13F archive found in docs/history/ — run the 13F workflow first.")
        return 1

    data = json.loads(archive.read_text())
    stocks = data.get("stocks", [])
    # Collect issuer names of stocks that have NO yfinance info and NO wiki
    # summary attached. Sort by absolute net_value so the most-moved (most
    # likely to be clicked) get the first slots in the budget.
    candidates = [
        s for s in stocks
        if s.get("issuer") and not s.get("info") and not s.get("wiki")
    ]
    candidates.sort(key=lambda s: abs(s.get("net_value", 0)), reverse=True)
    names = list(dict.fromkeys(s["issuer"] for s in candidates))  # dedupe, preserve order

    print(f"Found {len(names)} unique issuer names without descriptions; "
          f"running Wikipedia backfill with {args.budget_sec}s budget.")
    if not names:
        print("Nothing to do.")
        return 0

    ticker_info.fetch_wiki_summaries(names, budget_sec=args.budget_sec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
