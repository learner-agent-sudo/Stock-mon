"""Fetch yesterday's price data from yfinance with retries and gentle error handling."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - dependency may be missing in early dev
    yf = None  # type: ignore

FIXTURE_ENV = "STOCKMON_USE_FIXTURE"
FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "prices_snapshot.json"


@dataclass
class PriceData:
    symbol: str
    last_close: float | None
    prior_close: float | None
    pct_change: float | None
    volume: int | None
    last_date: str | None
    ok: bool
    error: str | None = None

    @property
    def is_suspicious(self) -> bool:
        if not self.ok:
            return False
        if self.last_close is None or self.prior_close is None:
            return True
        if self.volume is not None and self.volume == 0:
            return True
        return False


def _fetch_one(symbol: str, retries: int = 3, backoff: float = 1.5) -> PriceData:
    if yf is None:
        return PriceData(symbol, None, None, None, None, None, False, "yfinance not installed")

    last_err: str | None = None
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            # 5d window gives us at least two trading days even after a holiday.
            hist = ticker.history(period="5d", auto_adjust=False)
            if hist is None or hist.empty:
                last_err = "no history returned"
            elif len(hist) < 2:
                row = hist.iloc[-1]
                return PriceData(
                    symbol=symbol,
                    last_close=float(row["Close"]),
                    prior_close=None,
                    pct_change=None,
                    volume=int(row["Volume"]) if row["Volume"] == row["Volume"] else None,
                    last_date=str(hist.index[-1].date()),
                    ok=True,
                    error="only one trading day available",
                )
            else:
                last = hist.iloc[-1]
                prior = hist.iloc[-2]
                last_close = float(last["Close"])
                prior_close = float(prior["Close"])
                pct = ((last_close - prior_close) / prior_close * 100.0) if prior_close else None
                volume = int(last["Volume"]) if last["Volume"] == last["Volume"] else None
                return PriceData(
                    symbol=symbol,
                    last_close=last_close,
                    prior_close=prior_close,
                    pct_change=pct,
                    volume=volume,
                    last_date=str(hist.index[-1].date()),
                    ok=True,
                )
        except Exception as exc:  # noqa: BLE001 — yfinance throws many shapes
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < retries - 1:
            time.sleep(backoff ** attempt)
    return PriceData(symbol, None, None, None, None, None, False, last_err)


def _fetch_from_fixture(symbol: str, snapshot: dict) -> PriceData:
    raw = snapshot.get(symbol)
    if raw is None:
        return PriceData(symbol, None, None, None, None, None, False, "not in fixture")
    last_close = raw.get("last_close")
    prior_close = raw.get("prior_close")
    pct = ((last_close - prior_close) / prior_close * 100.0) if (last_close and prior_close) else None
    return PriceData(
        symbol=symbol,
        last_close=last_close,
        prior_close=prior_close,
        pct_change=pct,
        volume=raw.get("volume"),
        last_date=raw.get("last_date"),
        ok=True,
    )


def fetch_prices(symbols: Iterable[str]) -> dict[str, PriceData]:
    """Fetch price data for each symbol; never raises.

    If STOCKMON_USE_FIXTURE=1, read from tests/fixtures/prices_snapshot.json
    instead of hitting yfinance. Used for offline dev iteration.
    """
    if os.environ.get(FIXTURE_ENV) == "1":
        snapshot = json.loads(FIXTURE_PATH.read_text())
        return {sym: _fetch_from_fixture(sym, snapshot) for sym in symbols}
    return {sym: _fetch_one(sym) for sym in symbols}
