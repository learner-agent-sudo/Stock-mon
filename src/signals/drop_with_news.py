"""Significant drop where news IS present — likely explained, lower priority."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..data_layer.news import NewsItem
from ..data_layer.prices import PriceData
from .base import Signal, SignalResult
from .drop_without_news import _threshold_for


class DropWithNewsSignal(Signal):
    name = "drop_with_news"

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
        if not news:
            return None

        return SignalResult(
            signal_name=self.name,
            symbol=stock["symbol"],
            severity=drop,
            headline=f"{stock['symbol']} dropped {drop:.2f}% — {len(news)} headline(s) found",
            details={
                "pct_change": price.pct_change,
                "last_close": price.last_close,
                "prior_close": price.prior_close,
                "threshold": threshold,
                "cap_tier": stock["cap_tier"],
                "description": stock["description"],
                "category": stock["category"],
            },
            news=news[:5],
        )
