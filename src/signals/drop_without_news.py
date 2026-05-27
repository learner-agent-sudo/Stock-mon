"""Significant drop with NO news coverage — the priority signal."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..data_layer.news import NewsItem
from ..data_layer.prices import PriceData
from .base import Signal, SignalResult


class DropWithoutNewsSignal(Signal):
    name = "drop_without_news"

    def evaluate(
        self,
        stock: sqlite3.Row,
        price: PriceData,
        news: list[NewsItem],
        context: dict[str, Any],
    ) -> SignalResult | None:
        if not price.ok or price.pct_change is None:
            return None
        if price.pct_change >= 0:
            return None

        threshold = _threshold_for(stock, context)
        drop = -price.pct_change
        if drop < threshold:
            return None
        if news:
            return None

        return SignalResult(
            signal_name=self.name,
            symbol=stock["symbol"],
            severity=drop,
            headline=f"{stock['symbol']} dropped {drop:.2f}% with NO news found",
            details={
                "pct_change": price.pct_change,
                "last_close": price.last_close,
                "prior_close": price.prior_close,
                "threshold": threshold,
                "cap_tier": stock["cap_tier"],
                "description": stock["description"],
                "category": stock["category"],
            },
        )


def _threshold_for(stock: sqlite3.Row, context: dict[str, Any]) -> float:
    tier = stock["cap_tier"] or "MID"
    thresholds = context.get("drop_thresholds", {})
    return thresholds.get(tier, thresholds.get("MID", 4.0))
