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
FMP_URL = "https://financialmodelingprep.com/api/v3/stock_news"
FMP_DAILY_BUDGET = 240  # stay under the 250/day free limit
_fmp_calls_made = 0


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


def _fetch_fmp_news(symbol: str, limit: int = 5) -> list[NewsItem]:
    global _fmp_calls_made
    key = os.environ.get("FMP_API_KEY")
    if not key or requests is None or _fmp_calls_made >= FMP_DAILY_BUDGET:
        return []
    _fmp_calls_made += 1
    try:
        resp = requests.get(
            FMP_URL,
            params={"tickers": symbol, "limit": limit, "apikey": key},
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

    # FMP only as a gap-filler when nothing else found anything, to respect
    # the 250/day free limit.
    if not merged:
        merged = _merge_news(_fetch_fmp_news(symbol))
    return merged


def fetch_news_for_symbols(
    symbols_with_hints: dict[str, str | None],
    *,
    since_hours: int = 48,
) -> tuple[dict[str, list[NewsItem]], dict[str, Any]]:
    active = ["yahoo"]
    if os.environ.get("FINNHUB_API_KEY"):
        active.append("finnhub")
    if os.environ.get("FMP_API_KEY"):
        active.append("fmp")
    sources_label = "+".join(active)

    result: dict[str, list[NewsItem]] = {}
    total_articles = 0
    for symbol, hint in symbols_with_hints.items():
        items = fetch_news(symbol, since_hours=since_hours, query_hint=hint)
        result[symbol] = items
        total_articles += len(items)
    tickers_with_news = sum(1 for v in result.values() if v)
    stats: dict[str, Any] = {
        "news_source": sources_label,
        "total_articles": total_articles,
        "tickers_with_news": tickers_with_news,
        "tickers_without_news": len(result) - tickers_with_news,
    }
    print(f"  [news] {sources_label}: {total_articles} articles across {tickers_with_news}/{len(result)} tickers")
    return result, stats
