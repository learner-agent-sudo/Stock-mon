"""Fetch recent news headlines per ticker from Yahoo Finance and Google News RSS."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import feedparser
except ImportError:  # pragma: no cover
    feedparser = None  # type: ignore

FIXTURE_ENV = "STOCKMON_USE_FIXTURE"
FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "news_snapshot.json"


YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}+stock&hl=en-US&gl=US&ceid=US:en"
REQUEST_DELAY = 0.3


@dataclass
class NewsItem:
    symbol: str
    headline: str
    url: str
    source: str
    published_at: datetime | None


def _parse_published(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


_yahoo_available: bool | None = None


def check_yahoo_availability() -> bool:
    """Probe Yahoo RSS with a known-good ticker. Cache the result."""
    global _yahoo_available
    if _yahoo_available is not None:
        return _yahoo_available
    if feedparser is None:
        _yahoo_available = False
        return False
    try:
        feed = feedparser.parse(YAHOO_RSS.format(symbol="AAPL"))
        status = getattr(feed, "status", 0)
        _yahoo_available = status == 200 and len(feed.entries) > 0
    except Exception:  # noqa: BLE001
        _yahoo_available = False
    label = "available" if _yahoo_available else "NOT available"
    print(f"  [news] Yahoo Finance RSS: {label}")
    return _yahoo_available


def _fetch_feed(url: str, source: str, symbol: str) -> list[NewsItem]:
    if feedparser is None:
        return []
    try:
        feed = feedparser.parse(url)
    except Exception:  # noqa: BLE001
        return []
    if not feed.entries:
        status = getattr(feed, "status", "?")
        if status != 200 and status != "?":
            print(f"  [news] {source} {symbol}: HTTP {status}", file=sys.stderr)
    items: list[NewsItem] = []
    for entry in feed.entries:
        items.append(
            NewsItem(
                symbol=symbol,
                headline=getattr(entry, "title", "").strip(),
                url=getattr(entry, "link", ""),
                source=source,
                published_at=_parse_published(entry),
            )
        )
    return items


def _fetch_from_fixture(symbol: str) -> list[NewsItem]:
    snapshot = json.loads(FIXTURE_PATH.read_text())
    items = snapshot.get(symbol, [])
    return [
        NewsItem(
            symbol=symbol,
            headline=i.get("headline", ""),
            url=i.get("url", ""),
            source=i.get("source", "fixture"),
            published_at=None,
        )
        for i in items
    ]


def _effective_since_hours(base: int) -> int:
    """Widen the news window on weekends so Friday's news is still visible."""
    weekday = datetime.now(timezone.utc).weekday()
    if weekday == 5:  # Saturday
        return max(base, 60)
    if weekday == 6:  # Sunday
        return max(base, 84)
    return base


def fetch_news(symbol: str, *, since_hours: int = 48, query_hint: str | None = None) -> list[NewsItem]:
    """Fetch recent news for a symbol from Yahoo Finance and Google News.

    Returns items newer than `since_hours` (auto-widened on weekends).
    If a publish timestamp is missing, the item is kept (RSS sometimes omits it).
    """
    if os.environ.get(FIXTURE_ENV) == "1":
        return _fetch_from_fixture(symbol)

    hours = _effective_since_hours(since_hours)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    yahoo_items: list[NewsItem] = []
    if check_yahoo_availability():
        yahoo_items = _fetch_feed(YAHOO_RSS.format(symbol=symbol), "yahoo", symbol)
        time.sleep(REQUEST_DELAY)

    query = f"{query_hint} {symbol}" if query_hint else symbol
    encoded = urllib.parse.quote_plus(query)
    google_items = _fetch_feed(GOOGLE_NEWS_RSS.format(query=encoded), "google", symbol)
    time.sleep(REQUEST_DELAY)

    combined: list[NewsItem] = []
    seen_urls: set[str] = set()
    for item in yahoo_items + google_items:
        if item.published_at is not None and item.published_at < cutoff:
            continue
        key = item.url or item.headline
        if key in seen_urls:
            continue
        seen_urls.add(key)
        combined.append(item)

    combined.sort(key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return combined


def fetch_news_for_symbols(
    symbols_with_hints: dict[str, str | None],
    *,
    since_hours: int = 48,
) -> tuple[dict[str, list[NewsItem]], dict[str, Any]]:
    """Returns (news_map, news_stats) where news_stats tracks source availability."""
    check_yahoo_availability()
    result: dict[str, list[NewsItem]] = {}
    total_articles = 0
    for symbol, hint in symbols_with_hints.items():
        items = fetch_news(symbol, since_hours=since_hours, query_hint=hint)
        result[symbol] = items
        total_articles += len(items)
    tickers_with_news = sum(1 for v in result.values() if v)
    stats = {
        "yahoo_available": bool(_yahoo_available),
        "total_articles": total_articles,
        "tickers_with_news": tickers_with_news,
        "tickers_without_news": len(result) - tickers_with_news,
    }
    print(f"  [news] {total_articles} articles across {tickers_with_news}/{len(result)} tickers")
    return result, stats
