"""Run all enabled signals over every active stock and assemble the briefing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import config, db
from ..data_layer import news as news_layer
from ..data_layer import prices as prices_layer
from ..signals.base import Signal, SignalResult
from ..signals.drop_with_news import DropWithNewsSignal
from ..signals.drop_without_news import DropWithoutNewsSignal
from ..signals.significant_gain import SignificantGainSignal
from ..signals.watchlist_target_hit import WatchlistTargetHitSignal
from . import ranker


ENABLED_SIGNALS: list[Signal] = [
    WatchlistTargetHitSignal(),
    DropWithoutNewsSignal(),
    DropWithNewsSignal(),
    SignificantGainSignal(),
]


@dataclass
class Briefing:
    generated_at: str
    results: list[SignalResult] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _build_context() -> dict[str, Any]:
    return {
        "drop_thresholds": {
            "LARGE": config.get_float("large_cap_threshold_pct", 3.0),
            "MID": config.get_float("mid_cap_threshold_pct", 4.0),
            "SMALL": config.get_float("small_cap_threshold_pct", 5.0),
        },
        "gain_threshold_pct": config.get_float("gain_threshold_pct", 5.0),
    }


def build() -> Briefing:
    stocks = db.get_active_stocks()
    symbols = [s["symbol"] for s in stocks]

    price_map = prices_layer.fetch_prices(symbols)
    hints = {s["symbol"]: s["description"] for s in stocks}
    news_map = news_layer.fetch_news_for_symbols(hints)

    context = _build_context()
    results: list[SignalResult] = []
    for stock in stocks:
        price = price_map.get(stock["symbol"])
        if price is None:
            continue
        news = news_map.get(stock["symbol"], [])
        for signal in ENABLED_SIGNALS:
            try:
                result = signal.evaluate(stock, price, news, context)
            except Exception as exc:  # noqa: BLE001
                result = None
                # Keep going on per-signal errors so one bad rule doesn't sink the run.
                print(f"[warn] signal {signal.name} failed on {stock['symbol']}: {exc}")
            if result is not None:
                results.append(result)

    ranked = ranker.rank(results)
    ok = sum(1 for p in price_map.values() if p.ok)
    failed = len(price_map) - ok

    return Briefing(
        generated_at=datetime.now(timezone.utc).isoformat(),
        results=ranked,
        stats={
            "stocks_checked": len(stocks),
            "prices_ok": ok,
            "prices_failed": failed,
            "signals_matched": len(ranked),
        },
    )


def to_json_payload(b: Briefing) -> dict[str, Any]:
    return {
        "generated_at": b.generated_at,
        "stats": b.stats,
        "results": [
            {
                **{k: v for k, v in asdict(r).items() if k != "news"},
                "news": [
                    {
                        "headline": n.headline,
                        "url": n.url,
                        "source": n.source,
                        "published_at": n.published_at.isoformat() if n.published_at else None,
                    }
                    for n in r.news
                ],
            }
            for r in b.results
        ],
    }
