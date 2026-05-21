"""Data coverage check: does yfinance return usable data for every active stock?

Not a pass/fail test — it prints three lists (worked, suspicious, failed) so we
can decide which symbols need exchange suffix fixes (e.g. TXT -> TXT.WA) before
running the full briefing.

Run:
    python -m pytest tests/test_data_coverage.py -s
or directly:
    python tests/test_data_coverage.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.data_layer import prices


def check_coverage() -> dict[str, list[str]]:
    rows = db.get_active_stocks()
    symbols = [r["symbol"] for r in rows]
    if not symbols:
        print("No active stocks in DB. Run scripts/load_holdings_from_ib.py first.")
        return {"worked": [], "suspicious": [], "failed": []}

    print(f"Checking {len(symbols)} symbols via yfinance...\n")
    results = prices.fetch_prices(symbols)

    worked: list[str] = []
    suspicious: list[str] = []
    failed: list[str] = []

    for sym, data in results.items():
        if not data.ok:
            failed.append(f"{sym}  ({data.error})")
        elif data.is_suspicious:
            suspicious.append(
                f"{sym}  last={data.last_close} prior={data.prior_close} vol={data.volume} date={data.last_date}"
            )
        else:
            worked.append(
                f"{sym}  {data.pct_change:+.2f}%  close={data.last_close}  date={data.last_date}"
            )

    print(f"=== Worked ({len(worked)}) ===")
    for line in worked:
        print(f"  {line}")
    print(f"\n=== Suspicious ({len(suspicious)}) ===")
    for line in suspicious:
        print(f"  {line}")
    print(f"\n=== Failed ({len(failed)}) ===")
    for line in failed:
        print(f"  {line}")

    return {"worked": worked, "suspicious": suspicious, "failed": failed}


def test_data_coverage() -> None:
    """Pytest entry: never fails — coverage is informational."""
    result = check_coverage()
    assert isinstance(result, dict)


if __name__ == "__main__":
    check_coverage()
