"""Watchlist target reached: price at/below target_price OR daily drop exceeds threshold."""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..data_layer.news import NewsItem
from ..data_layer.prices import PriceData
from .base import Signal, SignalResult


class WatchlistTargetHitSignal(Signal):
    name = "watchlist_target_hit"

    def evaluate(
        self,
        stock: sqlite3.Row,
        price: PriceData,
        news: list[NewsItem],
        context: dict[str, Any],
    ) -> SignalResult | None:
        if stock["category"] != "WATCHLIST":
            return None
        if not price.ok or price.last_close is None:
            return None

        target = _get_target(stock["symbol"])
        if target is None:
            return None
        target_price, drop_threshold = target

        reasons: list[str] = []
        if target_price is not None and price.last_close <= target_price:
            reasons.append(
                f"price {price.last_close:.2f} <= target {target_price:.2f}"
            )
        if (
            drop_threshold is not None
            and price.pct_change is not None
            and price.pct_change <= -abs(drop_threshold)
        ):
            reasons.append(
                f"drop {price.pct_change:+.2f}% breached {-abs(drop_threshold):.2f}%"
            )

        if not reasons:
            return None

        severity = 100.0
        if price.pct_change is not None and price.pct_change < 0:
            severity += -price.pct_change

        return SignalResult(
            signal_name=self.name,
            symbol=stock["symbol"],
            severity=severity,
            headline=f"WATCHLIST {stock['symbol']} hit: " + "; ".join(reasons),
            details={
                "last_close": price.last_close,
                "pct_change": price.pct_change,
                "target_price": target_price,
                "drop_pct_threshold": drop_threshold,
                "description": stock["description"],
                "category": stock["category"],
            },
            news=news[:5],
        )


def _get_target(symbol: str) -> tuple[float | None, float | None] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT target_price, drop_pct_threshold FROM watchlist_targets WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return row["target_price"], row["drop_pct_threshold"]
