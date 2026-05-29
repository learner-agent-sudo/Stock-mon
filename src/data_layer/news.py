"""Fetch recent news headlines per ticker from multiple sources.

Sources, in order:
1. yfinance (Yahoo) — always on, no key, best international coverage
2. Finnhub — if FINNHUB_API_KEY is set; US-focused, 60 calls/min free
3. FMP — if FMP_API_KEY is set; US-focused, 250 calls/day free. Used only
   as a gap-filler for tickers that got nothing from the first two, to
   stay within the daily limit.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

FIXTURE_ENV = "STOCKMON_USE_FIXTURE"
FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "news_snapshot.json"

FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
# Current stable endpoint. The old /api/v3/stock_news (param "tickers") is a
# legacy endpoint that returns non-200 on the free tier.
FMP_URL = "https://financialmodelingprep.com/stable/news/stock"
FMP_DAILY_BUDGET = 240  # stay under the 250/day free limit
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_CALL_CAP = 60  # safety cap on per-run GDELT calls (it's a gap-filler)
GDELT_TIMEOUT_SEC = 5  # GDELT can be slow/blocked from CI IPs — fail fast
GDELT_THROTTLE_SEC = 0.2  # politeness delay; GDELT throttles bursty callers
# Hard wall-clock budget for ALL gap-filler calls (GDELT + FMP) combined.
# Primary sources (Yahoo/Finnhub) always run; gap-fillers stop once this is
# exhausted so a slow/blocked source can never run the job to its timeout.
GAP_FILLER_BUDGET_SEC = 150
HEALTH_CANARY = "AAPL"  # liquid US ticker used to probe source availability
HEALTH_CANARY_HINT = "Apple Inc"  # company-name canary for name-based sources
_fmp_calls_made = 0
_gdelt_calls_made = 0
_gap_filler_deadline = 0.0  # monotonic deadline; set per run
_gdelt_enabled = True  # disabled for the run if its health probe fails
_fmp_enabled = True


@dataclass
class NewsItem:
    symbol: str
    headline: str
    url: str
    source: str
    published_at: datetime | None
    summary: str = ""


def _effective_since_hours(base: int) -> int:
    weekday = datetime.now(timezone.utc).weekday()
    if weekday == 5:
        return max(base, 60)
    if weekday == 6:
        return max(base, 84)
    return base


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def _extract_article(article: dict) -> tuple[str, str, str, Any, str]:
    """Extract title, link, publisher, publish_time, summary from a yfinance news dict."""
    title = (
        article.get("title")
        or _deep_get(article, "content", "title")
        or ""
    )
    link = (
        article.get("link")
        or article.get("url")
        or _deep_get(article, "content", "canonicalUrl", "url")
        or _deep_get(article, "content", "clickThroughUrl", "url")
        or ""
    )
    publisher = (
        article.get("publisher")
        or _deep_get(article, "content", "provider", "displayName")
        or "yahoo"
    )
    pub_ts = (
        article.get("providerPublishTime")
        or article.get("publishedAt")
        or _deep_get(article, "content", "pubDate")
    )
    summary_raw = (
        article.get("summary")
        or _deep_get(article, "content", "summary")
        or ""
    )
    desc_raw = (
        article.get("description")
        or _deep_get(article, "content", "description")
        or ""
    )
    summary_clean = _strip_html(summary_raw) if "<" in summary_raw else summary_raw
    desc_clean = _strip_html(desc_raw) if "<" in desc_raw else desc_raw
    summary = summary_clean if len(summary_clean) >= len(desc_clean) else desc_clean
    return title, link, publisher, pub_ts, summary


def _deep_get(d: dict, *keys: str) -> Any:
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)  # type: ignore
    return d


def _fetch_yfinance_news(symbol: str, since_hours: int) -> list[NewsItem]:
    if yf is None:
        return []
    hours = _effective_since_hours(since_hours)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        ticker = yf.Ticker(symbol)
        raw_news = ticker.news or []
    except Exception as exc:  # noqa: BLE001
        print(f"  [news] yfinance {symbol}: {exc}", file=sys.stderr)
        return []

    items: list[NewsItem] = []
    seen: set[str] = set()
    for article in raw_news:
        title, link, publisher, pub_ts, summary = _extract_article(article)

        published_at = None
        if pub_ts:
            try:
                if isinstance(pub_ts, (int, float)):
                    published_at = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                elif isinstance(pub_ts, str):
                    published_at = datetime.fromisoformat(pub_ts.replace("Z", "+00:00"))
            except (TypeError, ValueError, OSError):
                pass

        if published_at is not None and published_at < cutoff:
            continue

        if not title:
            continue

        key = link or title
        if key in seen:
            continue
        seen.add(key)

        items.append(NewsItem(
            symbol=symbol,
            headline=title.strip(),
            url=link,
            source=publisher,
            published_at=published_at,
            summary=summary.strip(),
        ))

    items.sort(key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


def _fetch_finnhub_news(symbol: str, since_hours: int) -> list[NewsItem]:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key or requests is None:
        return []
    hours = _effective_since_hours(since_hours)
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(hours=hours)).strftime("%Y-%m-%d")
    to = now.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            FINNHUB_URL,
            params={"symbol": symbol, "from": frm, "to": to, "token": key},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []
    items: list[NewsItem] = []
    for a in data if isinstance(data, list) else []:
        ts = a.get("datetime")
        published_at = None
        if ts:
            try:
                published_at = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
        items.append(NewsItem(
            symbol=symbol,
            headline=(a.get("headline") or "").strip(),
            url=a.get("url") or "",
            source=a.get("source") or "finnhub",
            published_at=published_at,
            summary=(a.get("summary") or "").strip(),
        ))
    return items


def _gap_filler_budget_ok() -> bool:
    """True while the shared gap-filler time budget for this run remains."""
    return time.monotonic() < _gap_filler_deadline


def _fetch_fmp_news(symbol: str, limit: int = 5) -> list[NewsItem]:
    global _fmp_calls_made
    key = os.environ.get("FMP_API_KEY")
    if not key or requests is None or not _fmp_enabled or _fmp_calls_made >= FMP_DAILY_BUDGET:
        return []
    if not _gap_filler_budget_ok():
        return []
    _fmp_calls_made += 1
    try:
        resp = requests.get(
            FMP_URL,
            params={"symbols": symbol, "limit": limit, "apikey": key},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []
    items: list[NewsItem] = []
    for a in data if isinstance(data, list) else []:
        published_at = None
        pub = a.get("publishedDate")
        if pub:
            try:
                published_at = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
        items.append(NewsItem(
            symbol=symbol,
            headline=(a.get("title") or "").strip(),
            url=a.get("url") or "",
            source=a.get("site") or "fmp",
            published_at=published_at,
            summary=(a.get("text") or "").strip(),
        ))
    return items


def _gdelt_query(symbol: str, query_hint: str | None) -> str:
    """Build a GDELT query. Prefer the company name (works for non-US tickers);
    fall back to the bare symbol."""
    name = (query_hint or "").strip()
    if name:
        return f'"{name}"'
    return symbol


def _parse_gdelt_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fetch_gdelt_news(symbol: str, query_hint: str | None, since_hours: int) -> list[NewsItem]:
    """Global news via GDELT DOC 2.0. Keyless, worldwide coverage — the best
    fit for non-US tickers that Finnhub/FMP don't cover. Queried by company
    name when available."""
    global _gdelt_calls_made
    if requests is None or not _gdelt_enabled or _gdelt_calls_made >= GDELT_CALL_CAP:
        return []
    if not _gap_filler_budget_ok():
        return []
    _gdelt_calls_made += 1
    # Be polite to GDELT's rate limiter — it throttles bursty callers.
    time.sleep(GDELT_THROTTLE_SEC)
    hours = _effective_since_hours(since_hours)
    params = {
        "query": f"{_gdelt_query(symbol, query_hint)} sourcelang:english",
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 10,
        "sort": "DateDesc",
        "timespan": f"{hours}h",
    }
    try:
        resp = requests.get(GDELT_URL, params=params, timeout=10)
        if resp.status_code != 200 or not resp.text.strip():
            return []
        data = resp.json()
    except Exception:  # noqa: BLE001 — GDELT occasionally returns non-JSON / times out
        return []
    items: list[NewsItem] = []
    for a in data.get("articles", []) if isinstance(data, dict) else []:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        items.append(NewsItem(
            symbol=symbol,
            headline=title,
            url=a.get("url") or "",
            source=a.get("domain") or "gdelt",
            published_at=_parse_gdelt_date(a.get("seendate")),
            summary="",
        ))
    return items


def _merge_news(*lists: list[NewsItem]) -> list[NewsItem]:
    combined: list[NewsItem] = []
    seen: set[str] = set()
    for lst in lists:
        for item in lst:
            if not item.headline:
                continue
            key = (item.url or item.headline).lower()
            if key in seen:
                continue
            seen.add(key)
            combined.append(item)
    combined.sort(key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return combined


def _probe_yahoo() -> dict[str, str]:
    if yf is None:
        return {"name": "yahoo", "status": "error", "detail": "yfinance not installed"}
    try:
        items = _fetch_yfinance_news(HEALTH_CANARY, since_hours=168)
        return {"name": "yahoo", "status": "ok", "detail": f"{len(items)} canary articles"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "yahoo", "status": "error", "detail": str(exc)[:80]}


def _probe_finnhub() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return {"name": "finnhub", "status": "disabled", "detail": "no API key set"}
    if requests is None:
        return {"name": "finnhub", "status": "error", "detail": "requests not installed"}
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    to = now.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            FINNHUB_URL,
            params={"symbol": HEALTH_CANARY, "from": frm, "to": to, "token": key},
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return {"name": "finnhub", "status": "error", "detail": str(exc)[:80]}
    if resp.status_code == 200:
        data = resp.json() if resp.text else []
        n = len(data) if isinstance(data, list) else 0
        return {"name": "finnhub", "status": "ok", "detail": f"{n} canary articles"}
    return {"name": "finnhub", "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:60]}"}


def _probe_fmp() -> dict[str, str]:
    global _fmp_calls_made
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return {"name": "fmp", "status": "disabled", "detail": "no API key set"}
    if requests is None:
        return {"name": "fmp", "status": "error", "detail": "requests not installed"}
    _fmp_calls_made += 1
    try:
        resp = requests.get(
            FMP_URL,
            params={"symbols": HEALTH_CANARY, "limit": 1, "apikey": key},
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return {"name": "fmp", "status": "error", "detail": str(exc)[:80]}
    if resp.status_code == 200:
        data = resp.json() if resp.text else []
        n = len(data) if isinstance(data, list) else 0
        return {"name": "fmp", "status": "ok", "detail": f"{n} canary articles"}
    return {"name": "fmp", "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:60]}"}


def _probe_gdelt() -> dict[str, str]:
    if requests is None:
        return {"name": "gdelt", "status": "error", "detail": "requests not installed"}
    params = {
        "query": f'"{HEALTH_CANARY_HINT}" sourcelang:english',
        "mode": "ArtList", "format": "json", "maxrecords": 5, "timespan": "168h",
    }
    try:
        resp = requests.get(GDELT_URL, params=params, timeout=GDELT_TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001
        return {"name": "gdelt", "status": "error", "detail": str(exc)[:80]}
    if resp.status_code != 200:
        return {"name": "gdelt", "status": "error", "detail": f"HTTP {resp.status_code}: {resp.text[:60]}"}
    try:
        n = len(resp.json().get("articles", []))
    except Exception:  # noqa: BLE001 — non-JSON body counts as a failure
        return {"name": "gdelt", "status": "error", "detail": f"non-JSON body: {resp.text[:50]}"}
    return {"name": "gdelt", "status": "ok", "detail": f"{n} canary articles"}


def check_source_health() -> list[dict[str, str]]:
    """Probe each news source once with a canary ticker so the page can show
    which sources are live, disabled (no key), or erroring (and why)."""
    return [_probe_yahoo(), _probe_finnhub(), _probe_fmp(), _probe_gdelt()]


def _fetch_from_fixture(symbol: str) -> list[NewsItem]:
    snapshot = json.loads(FIXTURE_PATH.read_text())
    raw = snapshot.get(symbol, [])
    return [
        NewsItem(
            symbol=symbol,
            headline=i.get("headline", ""),
            url=i.get("url", ""),
            source=i.get("source", "fixture"),
            published_at=None,
        )
        for i in raw
    ]


def fetch_news(symbol: str, *, since_hours: int = 48, query_hint: str | None = None) -> list[NewsItem]:
    if os.environ.get(FIXTURE_ENV) == "1":
        return _fetch_from_fixture(symbol)

    yahoo = _fetch_yfinance_news(symbol, since_hours)
    finnhub = _fetch_finnhub_news(symbol, since_hours)
    merged = _merge_news(yahoo, finnhub)

    # Gap-fillers — only run when the fast primary sources found nothing, so
    # we don't fire an extra HTTP call per ticker across the whole portfolio.
    # GDELT first (keyless, global — best for the non-US tickers that Yahoo/
    # Finnhub miss), then FMP as the budget-limited last resort.
    if not merged:
        gdelt = _fetch_gdelt_news(symbol, query_hint, since_hours)
        merged = _merge_news(gdelt)
    if not merged:
        merged = _merge_news(_fetch_fmp_news(symbol))
    return merged


def fetch_news_for_symbols(
    symbols_with_hints: dict[str, str | None],
    *,
    since_hours: int = 48,
) -> tuple[dict[str, list[NewsItem]], dict[str, Any]]:
    global _gap_filler_deadline, _gdelt_enabled, _fmp_enabled, _gdelt_calls_made, _fmp_calls_made
    _gdelt_calls_made = 0
    _fmp_calls_made = 0
    # Probe each source once up front so the page can report availability.
    # Skip in fixture/offline mode — no network calls there.
    if os.environ.get(FIXTURE_ENV) == "1":
        source_health = [{"name": "fixture", "status": "ok", "detail": "offline fixture mode"}]
    else:
        source_health = check_source_health()
    for h in source_health:
        print(f"  [news] source {h['name']}: {h['status']} ({h['detail']})")

    # If a gap-filler's canary probe failed (e.g. GDELT blocked from this IP),
    # disable it for the whole run so we don't waste a call per ticker. Cap the
    # combined gap-filler time so a slow source can never run out the job clock.
    health_by_name = {h["name"]: h["status"] for h in source_health}
    _gdelt_enabled = health_by_name.get("gdelt") == "ok"
    _fmp_enabled = health_by_name.get("fmp") == "ok"
    _gap_filler_deadline = time.monotonic() + GAP_FILLER_BUDGET_SEC

    active = [h["name"] for h in source_health if h["status"] == "ok"]
    sources_label = "+".join(active) if active else "none"

    result: dict[str, list[NewsItem]] = {}
    total_articles = 0
    for symbol, hint in symbols_with_hints.items():
        items = fetch_news(symbol, since_hours=since_hours, query_hint=hint)
        result[symbol] = items
        total_articles += len(items)
    tickers_with_news = sum(1 for v in result.values() if v)
    stats: dict[str, Any] = {
        "news_source": sources_label,
        "source_health": source_health,
        "total_articles": total_articles,
        "tickers_with_news": tickers_with_news,
        "tickers_without_news": len(result) - tickers_with_news,
    }
    print(f"  [news] {sources_label}: {total_articles} articles across {tickers_with_news}/{len(result)} tickers")
    return result, stats
