"""Fetch recent news headlines per ticker via yfinance.

Previous approach used Yahoo Finance RSS and Google News RSS, both of
which are unreliable (deprecated / blocked from GitHub Actions IPs).
yfinance's Ticker.news property uses Yahoo's actual API and works.
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

FIXTURE_ENV = "STOCKMON_USE_FIXTURE"
FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "news_snapshot.json"


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
    summary = (
        article.get("summary")
        or _deep_get(article, "content", "summary")
        or ""
    )
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
    return _fetch_yfinance_news(symbol, since_hours)


def fetch_news_for_symbols(
    symbols_with_hints: dict[str, str | None],
    *,
    since_hours: int = 48,
) -> tuple[dict[str, list[NewsItem]], dict[str, Any]]:
    result: dict[str, list[NewsItem]] = {}
    total_articles = 0
    errors = 0
    for symbol, hint in symbols_with_hints.items():
        items = fetch_news(symbol, since_hours=since_hours, query_hint=hint)
        result[symbol] = items
        total_articles += len(items)
    tickers_with_news = sum(1 for v in result.values() if v)
    stats: dict[str, Any] = {
        "news_source": "yfinance",
        "total_articles": total_articles,
        "tickers_with_news": tickers_with_news,
        "tickers_without_news": len(result) - tickers_with_news,
    }
    print(f"  [news] yfinance: {total_articles} articles across {tickers_with_news}/{len(result)} tickers")
    return result, stats
