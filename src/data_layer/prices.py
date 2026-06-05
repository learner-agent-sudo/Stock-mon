"""Fetch current price data from yfinance with retries and gentle error handling.

Prefers the live quote (last price vs previous close) so the briefing reflects
today's move even when the workflow runs before the US market opens; falls back
to daily candles when a live quote isn't available."""
from __future__ import annotations

import csv
import datetime as _dt
import io
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

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

FIXTURE_ENV = "STOCKMON_USE_FIXTURE"
FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "prices_snapshot.json"
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


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


def _fetch_quote(ticker, symbol: str) -> "PriceData | None":
    """Build PriceData from yfinance's live/fast quote.

    Uses last traded price vs the previous official close — matching what
    finance sites display intraday and after hours. Returns None (so the
    caller falls back to daily candles) if the quote is incomplete.
    """
    try:
        fi = ticker.fast_info
    except Exception:  # noqa: BLE001
        return None
    if fi is None:
        return None

    def _g(*names):
        for n in names:
            try:
                v = fi[n] if not hasattr(fi, n) else getattr(fi, n)
            except (KeyError, TypeError, AttributeError):
                v = None
            if v is not None and v == v:  # not None, not NaN
                return float(v)
        return None

    last_price = _g("last_price", "lastPrice")
    prev_close = _g("previous_close", "previousClose", "regular_market_previous_close")
    if last_price is None or prev_close is None or prev_close == 0:
        return None

    volume = None
    raw_vol = _g("last_volume", "lastVolume", "regular_market_volume")
    if raw_vol is not None:
        volume = int(raw_vol)

    pct = (last_price - prev_close) / prev_close * 100.0
    return PriceData(
        symbol=symbol,
        last_close=last_price,
        prior_close=prev_close,
        pct_change=pct,
        volume=volume,
        last_date=_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d"),
        ok=True,
        error="live quote",
    )


def _fetch_one(symbol: str, retries: int = 3, backoff: float = 1.5) -> PriceData:
    if yf is None:
        return PriceData(symbol, None, None, None, None, None, False, "yfinance not installed")

    last_err: str | None = None
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)

            # Prefer the live quote: last_price vs previous_close is what
            # finance sites show as "today's % move". history(period="5d")
            # only yields completed daily candles, so a run before the US
            # open (11:00 UTC, market opens 14:30 UTC) would otherwise report
            # yesterday's move — a full day stale and often the wrong sign.
            quote = _fetch_quote(ticker, symbol)
            if quote is not None:
                return quote

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


def _stooq_symbol(symbol: str) -> str | None:
    """Map an internal symbol to a Stooq symbol. Only US tickers are reliable."""
    if "." in symbol:
        # Suffixed (e.g. 0700.HK, ATD.TO) — Stooq's free intl coverage is unreliable.
        return None
    return f"{symbol.lower()}.us"


def _fetch_from_stooq(symbol: str) -> PriceData:
    """Fallback price source. Daily CSV, no API key. Best-effort, US only."""
    if requests is None:
        return PriceData(symbol, None, None, None, None, None, False, "requests not installed")
    stooq_sym = _stooq_symbol(symbol)
    if stooq_sym is None:
        return PriceData(symbol, None, None, None, None, None, False, "no stooq mapping")
    try:
        resp = requests.get(STOOQ_URL.format(symbol=stooq_sym), timeout=15)
        if resp.status_code != 200 or not resp.text.startswith("Date"):
            return PriceData(symbol, None, None, None, None, None, False, "stooq no data")
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        if len(rows) < 2:
            return PriceData(symbol, None, None, None, None, None, False, "stooq <2 rows")
        last, prior = rows[-1], rows[-2]
        last_close = float(last["Close"])
        prior_close = float(prior["Close"])
        pct = ((last_close - prior_close) / prior_close * 100.0) if prior_close else None
        volume = int(float(last["Volume"])) if last.get("Volume") else None
        return PriceData(
            symbol=symbol,
            last_close=last_close,
            prior_close=prior_close,
            pct_change=pct,
            volume=volume,
            last_date=last["Date"],
            ok=True,
            error="via stooq fallback",
        )
    except Exception as exc:  # noqa: BLE001
        return PriceData(symbol, None, None, None, None, None, False, f"stooq: {exc}")


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

    result: dict[str, PriceData] = {}
    stooq_recovered = 0
    for sym in symbols:
        data = _fetch_one(sym)
        if not data.ok:
            fallback = _fetch_from_stooq(sym)
            if fallback.ok:
                data = fallback
                stooq_recovered += 1
        result[sym] = data
    if stooq_recovered:
        print(f"  [prices] recovered {stooq_recovered} ticker(s) via Stooq fallback")
    return result
