"""Import holdings from an Interactive Brokers Activity Statement CSV.

IB Activity Statements are multi-section CSVs. We look for the
'Open Positions' or 'Positions' section and import stocks (asset
category 'Stocks') as HOLDING entries.

Usage:
    python scripts/load_holdings_from_ib.py path/to/statement.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db


# Map IB exchange/listing prefixes to Yahoo Finance suffixes.
# Tickers without a suffix on Yahoo (e.g. NASDAQ/NYSE/AMEX US listings) get "".
IB_EXCHANGE_TO_YAHOO_SUFFIX = {
    "NASDAQ": "",
    "NYSE": "",
    "ARCA": "",
    "AMEX": "",
    "BATS": "",
    "PINK": "",
    "TSE": ".TO",       # Toronto
    "VENTURE": ".V",
    "LSE": ".L",        # London
    "LSEETF": ".L",
    "SEHK": ".HK",      # Hong Kong
    "SGX": ".SI",       # Singapore
    "ASX": ".AX",       # Australia
    "WSE": ".WA",       # Warsaw
    "GETTEX": ".DE",
    "FWB": ".DE",
    "IBIS": ".DE",
    "TSEJ": ".T",       # Tokyo
    "ENEXT.BE": ".BR",
    "SBF": ".PA",       # Paris
    "EBS": ".SW",       # Swiss
    "MEXI": ".MX",
}


def yahoo_symbol(symbol: str, exchange: str | None) -> str:
    """Best-effort mapping of an IB symbol to a Yahoo Finance symbol."""
    if not exchange:
        return symbol
    suffix = IB_EXCHANGE_TO_YAHOO_SUFFIX.get(exchange.upper().strip())
    if suffix is None:
        return symbol
    if suffix and symbol.endswith(suffix):
        return symbol
    return f"{symbol}{suffix}"


def parse_ib_csv(path: Path) -> list[dict]:
    """Extract stock positions from an IB Activity Statement CSV.

    The IB format places a section header row, then a column header row,
    then data rows. We scan for rows where field[0] starts with a known
    positions section name and field[1] == 'Header' or 'Data'.
    """
    positions: list[dict] = []
    current_section: str | None = None
    headers: list[str] | None = None

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            section = row[0]
            if section in {"Open Positions", "Positions"}:
                row_type = row[1] if len(row) > 1 else ""
                if row_type == "Header":
                    current_section = section
                    headers = row[2:]
                    continue
                if row_type == "Data" and current_section and headers:
                    record = dict(zip(headers, row[2:]))
                    asset_category = (
                        record.get("Asset Category")
                        or record.get("AssetCategory")
                        or ""
                    ).strip()
                    if asset_category and asset_category.lower() != "stocks":
                        continue
                    positions.append(record)
    return positions


def _to_float(s: str | None) -> float | None:
    if s is None or s.strip() == "":
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def import_positions(path: Path) -> tuple[int, int]:
    """Returns (inserted, skipped)."""
    records = parse_ib_csv(path)
    inserted = 0
    skipped = 0
    for r in records:
        symbol = (r.get("Symbol") or "").strip()
        if not symbol:
            skipped += 1
            continue
        exchange = (r.get("Listing Exch") or r.get("Exchange") or "").strip() or None
        currency = (r.get("Currency") or "").strip() or None
        description = (r.get("Description") or "").strip() or None
        qty = _to_float(r.get("Quantity"))
        cost = _to_float(r.get("Cost Price") or r.get("CostBasis"))
        ysym = yahoo_symbol(symbol, exchange)
        db.upsert_stock(
            symbol=ysym,
            category="HOLDING",
            exchange=exchange,
            currency=currency,
            description=description,
        )
        db.upsert_holding(
            symbol=ysym,
            quantity=qty,
            cost_basis_per_share=cost,
        )
        inserted += 1
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="IB Activity Statement CSV file")
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"File not found: {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    db.init_schema()
    db.seed_default_settings()

    inserted, skipped = import_positions(args.csv_path)
    print(f"Imported {inserted} holdings (skipped {skipped}).")


if __name__ == "__main__":
    main()
