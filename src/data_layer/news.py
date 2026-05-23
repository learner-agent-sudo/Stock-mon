"""Fetch recent news headlines per ticker from Yahoo Finance and Google News RSS."""
from __future__ import annotations

import json
import os
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
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


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


def _fetch_feed(url: str, source: str, symbol: str) -> list[NewsItem]:
    if feedparser is None:
        return []
    try:
        feed = feedparser.parse(url)
    except Exception:  # noqa: BLE001
        return []
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


def fetch_news(symbol: str, *, since_hours: int = 36, query_hint: str | None = None) -> list[NewsItem]:
    """Fetch recent news for a symbol from Yahoo Finance and Google News.

    Returns items newer than `since_hours`. If a publish timestamp is missing,
    the item is kept (RSS sometimes omits it).
    """
    if os.environ.get(FIXTURE_ENV) == "1":
        return _fetch_from_fixture(symbol)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    yahoo_items = _fetch_feed(YAHOO_RSS.format(symbol=symbol), "yahoo", symbol)

    query = query_hint or symbol
    encoded = urllib.parse.quote_plus(query)
    google_items = _fetch_feed(GOOGLE_NEWS_RSS.format(query=encoded), "google", symbol)

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
    since_hours: int = 36,
) -> dict[str, list[NewsItem]]:
    return {
        symbol: fetch_news(symbol, since_hours=since_hours, query_hint=hint)
        for symbol, hint in symbols_with_hints.items()
    }
