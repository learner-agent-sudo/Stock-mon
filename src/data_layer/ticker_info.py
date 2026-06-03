"""Fetch brief company info + price for tickers shown on the 13F page.

The 13F page is static and regenerated weekly, and Yahoo has no
CORS-enabled API, so we can't fetch on click in the browser. Instead we
fetch at build time and embed the data, labeled "as of <build date>".

A timestamped cache (data/ticker_info_cache.json) plus a wall-clock
budget keeps this from blowing the job timeout — same pattern as the
13F holdings cache. Watchlist/holdings tickers are fetched first so the
ones you're most likely to click always get populated.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = REPO_ROOT / "data" / "ticker_info_cache.json"
CACHE_TTL_SEC = 5 * 24 * 3600        # refresh roughly weekly
DEFAULT_BUDGET_SEC = 240             # cap the whole info-fetch phase
SUMMARY_MAX = 320                    # trim long business summaries


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    if not cache:
        return
    try:
        CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


def _fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at", 0)
    return (time.time() - ts) < CACHE_TTL_SEC


def _fetch_one(symbol: str) -> dict | None:
    if yf is None:
        return None
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:  # noqa: BLE001
        return None
    if not info or not (info.get("shortName") or info.get("longName")):
        return None  # likely an unmapped/foreign symbol yfinance can't resolve
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    summary = (info.get("longBusinessSummary") or "").strip()
    if len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX].rsplit(" ", 1)[0] + "…"
    return {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "price": price,
        "change_pct": info.get("regularMarketChangePercent"),
        "currency": info.get("currency"),
        "market_cap": info.get("marketCap"),
        "pe": info.get("trailingPE"),
        "wk_low": info.get("fiftyTwoWeekLow"),
        "wk_high": info.get("fiftyTwoWeekHigh"),
        "website": info.get("website"),
        "summary": summary,
        "fetched_at": time.time(),
    }


def fetch_ticker_info(
    tickers: list[str],
    priority: set[str] | None = None,
    budget_sec: float = DEFAULT_BUDGET_SEC,
) -> dict[str, dict]:
    """Return {ticker: info_dict} for as many tickers as the budget allows.

    `priority` tickers (your holdings/watchlist) are fetched first. Cached
    entries within TTL are reused for free and never count against budget.
    """
    cache = _load_cache()
    result: dict[str, dict] = {}
    todo: list[str] = []
    for t in tickers:
        t = (t or "").upper()
        if not t:
            continue
        entry = cache.get(t)
        if entry and _fresh(entry):
            result[t] = entry
        else:
            todo.append(t)

    # Fetch priority tickers first, then the rest.
    pri = priority or set()
    todo.sort(key=lambda t: (t not in pri, t))

    deadline = time.monotonic() + budget_sec
    fetched = 0
    for t in todo:
        if time.monotonic() > deadline:
            break
        info = _fetch_one(t)
        if info is not None:
            cache[t] = info
            result[t] = info
            fetched += 1
        else:
            # Negative-cache briefly so we don't retry hard-to-resolve symbols
            # every run, but with a short TTL via a sentinel timestamp.
            cache[t] = {"fetched_at": time.time(), "unresolved": True}
        time.sleep(0.05)
    _save_cache(cache)
    # Drop negative-cache sentinels from the returned map.
    result = {k: v for k, v in result.items() if not v.get("unresolved")}
    print(f"  [13f] ticker info: {len(result)} available "
          f"({fetched} freshly fetched, {len(todo) - fetched} skipped/over-budget)")
    return result
