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
import re
import time
import traceback
import urllib.parse
from pathlib import Path

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = REPO_ROOT / "data" / "ticker_info_cache.json"
WIKI_CACHE_PATH = REPO_ROOT / "data" / "wiki_summary_cache.json"
CACHE_TTL_SEC = 5 * 24 * 3600        # refresh roughly weekly
# Unresolved (negative-cached) entries get a much shorter TTL so a transient
# yfinance rate-limit doesn't lock out a real ticker for the full positive
# TTL. 4h means a single run's failures self-heal by the next day's run,
# while still preventing pointless retries within the same backstop cluster.
NEG_CACHE_TTL_SEC = 4 * 3600
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
    ttl = NEG_CACHE_TTL_SEC if entry.get("unresolved") else CACHE_TTL_SEC
    return (time.time() - ts) < ttl


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


# --------------------------------------------------------------------------
# Wikipedia fallback — used for stocks yfinance couldn't resolve.
# Free, no API key, generous rate limits.
# --------------------------------------------------------------------------

WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_UA = "stock-mon/1.0 (contact: fredchan31@gmail.com)"
WIKI_BUDGET_SEC = 300
WIKI_EXTRACT_MAX = 480

# Strip common legal/structural suffixes so "AMER SPORTS INC" searches as
# "Amer Sports" — gets far better Wikipedia hits.
_CORP_SUFFIX_RE = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|LLC|CO|COMPANY|"
    r"HOLDINGS?|GROUP|PLC|N\.V\.|NV|S\.A\.|SA|AG|SE|TRUST|PARTNERS|"
    r"LP|L\.P\.|CLASS\s+[A-Z])\b\.?,?",
    re.IGNORECASE,
)


def _clean_for_wiki(name: str) -> str:
    n = _CORP_SUFFIX_RE.sub("", name)
    n = re.sub(r"\s+", " ", n).strip(" ,.-")
    return n


def _load_wiki_cache() -> dict:
    if WIKI_CACHE_PATH.exists():
        try:
            return json.loads(WIKI_CACHE_PATH.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_wiki_cache(cache: dict) -> None:
    if not cache:
        return
    try:
        WIKI_CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    except Exception as exc:  # noqa: BLE001
        print(f"  [13f] wiki cache write failed: {type(exc).__name__}: {exc}")


def _wiki_search_title(query: str) -> str | None:
    """Use Wikipedia search to resolve a free-text query to a canonical title."""
    try:
        r = requests.get(
            WIKI_SEARCH_URL,
            params={
                "action": "query", "format": "json",
                "list": "search", "srsearch": query, "srlimit": 1,
            },
            timeout=10,
            headers={"User-Agent": WIKI_UA, "Accept": "application/json"},
        )
        if r.status_code != 200:
            return None
        hits = r.json().get("query", {}).get("search", [])
        return hits[0]["title"] if hits else None
    except Exception:  # noqa: BLE001
        return None


def _wiki_fetch_summary(title: str) -> dict | None:
    try:
        url = WIKI_SUMMARY_URL.format(title=urllib.parse.quote(title.replace(" ", "_")))
        r = requests.get(url, timeout=10, headers={"User-Agent": WIKI_UA})
        if r.status_code != 200:
            return None
        d = r.json()
        extract = (d.get("extract") or "").strip()
        if not extract:
            return None
        if len(extract) > WIKI_EXTRACT_MAX:
            extract = extract[:WIKI_EXTRACT_MAX].rsplit(" ", 1)[0] + "…"
        page_url = (d.get("content_urls", {}).get("desktop", {}).get("page")
                    or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}")
        return {
            "title": d.get("title", title),
            "extract": extract,
            "url": page_url,
            "description": d.get("description"),
            "fetched_at": time.time(),
        }
    except Exception:  # noqa: BLE001
        return None


def fetch_wiki_summaries(issuer_names: list[str], budget_sec: float = WIKI_BUDGET_SEC) -> dict[str, dict]:
    """Bulk Wikipedia lookup for issuer names. Returns {original_name: summary_dict}.

    Uses a cache keyed by the cleaned name so name variants ("APPLE INC" /
    "APPLE INC.") share results. Negative-cached entries have a short TTL
    (same as ticker_info) so transient failures recover next run.
    """
    if requests is None:
        print("  [13f] wiki: requests not installed — skipping")
        return {}
    cache = _load_wiki_cache()
    result: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    for name in issuer_names:
        if not name:
            continue
        key = _clean_for_wiki(name) or name
        entry = cache.get(key)
        if entry and _fresh(entry):
            if not entry.get("unresolved"):
                result[name] = entry
        else:
            todo.append((name, key))

    deadline = time.monotonic() + budget_sec
    new = failed = 0
    first_error: str | None = None
    for i, (orig, key) in enumerate(todo):
        if time.monotonic() > deadline:
            break
        # Append " company" to bias toward business pages over disambiguation.
        title = _wiki_search_title(f"{key} company") or _wiki_search_title(key)
        summ = _wiki_fetch_summary(title) if title else None
        if summ:
            cache[key] = summ
            result[orig] = summ
            new += 1
        else:
            cache[key] = {"fetched_at": time.time(), "unresolved": True}
            failed += 1
            if first_error is None and title is None:
                first_error = f"{orig}: no search hit"
        if (i + 1) % 20 == 0:
            _save_wiki_cache(cache)
        time.sleep(0.15)  # be polite to Wikipedia
    _save_wiki_cache(cache)
    print(f"  [13f] wiki: {len(result)} usable | {new} new, {failed} failed")
    if first_error and new == 0:
        print(f"  [13f] wiki: first failure was — {first_error}")
    return result
