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
import traceback
from pathlib import Path

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = REPO_ROOT / "data" / "ticker_info_cache.json"
CACHE_TTL_SEC = 5 * 24 * 3600        # refresh roughly weekly
# Budget for the whole info-fetch phase. The job cap is 20 min and the SEC
# + OpenFIGI phases are cache-fast after their first run, so we can give the
# ticker-info phase a generous window. At ~1s/call this covers ~550 tickers —
# comfortably more than the ~400 we target (watchlist + top 200 by impact),
# so on a warm cache the full scoped set resolves in a single run.
DEFAULT_BUDGET_SEC = 600
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
    except Exception as exc:  # noqa: BLE001
        # Surface the reason instead of pass-ing silently — a write failure
        # here destroys the whole point of caching.
        print(f"  [13f] ticker_info cache write failed: {type(exc).__name__}: {exc}")


def _fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at", 0)
    return (time.time() - ts) < CACHE_TTL_SEC


def _fetch_one(symbol: str) -> tuple[dict | None, str | None]:
    """Return (info, error_reason). info is None on failure; error_reason
    is a short string suitable for surfacing in logs."""
    if yf is None:
        return None, "yfinance not installed"
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as exc:  # noqa: BLE001 — yfinance raises many shapes
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"
    # Be lenient: accept the entry if ANY useful field is present.
    name = info.get("longName") or info.get("shortName")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mcap = info.get("marketCap")
    if not (name or price or mcap):
        return None, "empty info response"
    summary = (info.get("longBusinessSummary") or "").strip()
    if len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX].rsplit(" ", 1)[0] + "…"
    return {
        "name": name,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "price": price,
        "change_pct": info.get("regularMarketChangePercent"),
        "currency": info.get("currency"),
        "market_cap": mcap,
        "pe": info.get("trailingPE"),
        "wk_low": info.get("fiftyTwoWeekLow"),
        "wk_high": info.get("fiftyTwoWeekHigh"),
        "website": info.get("website"),
        "summary": summary,
        "fetched_at": time.time(),
    }, None


def fetch_ticker_info(
    tickers: list[str],
    priority: set[str] | None = None,
    budget_sec: float = DEFAULT_BUDGET_SEC,
) -> dict[str, dict]:
    """Return {ticker: info_dict} for as many tickers as the budget allows.

    `priority` tickers are fetched first. Cached entries within TTL are
    reused for free and never count against budget. The cache is saved
    after every successful fetch so progress accumulates across runs
    even if the budget runs out.
    """
    if yf is None:
        print("  [13f] ticker_info: yfinance not importable — skipping all fetches")
        return {}

    cache = _load_cache()
    result: dict[str, dict] = {}
    todo: list[str] = []
    for t in tickers:
        t = (t or "").upper()
        if not t:
            continue
        entry = cache.get(t)
        if entry and _fresh(entry):
            if not entry.get("unresolved"):
                result[t] = entry
        else:
            todo.append(t)

    pri = priority or set()
    todo.sort(key=lambda t: (t not in pri, t))

    deadline = time.monotonic() + budget_sec
    fetched = failed = skipped = 0
    first_error: str | None = None
    for i, t in enumerate(todo):
        if time.monotonic() > deadline:
            skipped = len(todo) - i
            break
        info, err = _fetch_one(t)
        if info is not None:
            cache[t] = info
            result[t] = info
            fetched += 1
        else:
            cache[t] = {"fetched_at": time.time(), "unresolved": True}
            failed += 1
            if first_error is None:
                first_error = f"{t}: {err}"
        # Save progress every 25 entries — first run with cold cache should
        # at least preserve partial results if anything kills the process.
        if (i + 1) % 25 == 0:
            _save_cache(cache)
        time.sleep(0.05)
    _save_cache(cache)
    print(f"  [13f] ticker_info: {len(result)} usable | "
          f"{fetched} new, {failed} failed, {skipped} skipped (budget)")
    if first_error and fetched == 0:
        print(f"  [13f] ticker_info: first failure was — {first_error}")
    return result
