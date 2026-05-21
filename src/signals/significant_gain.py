"""Notable upside move on a holding or watchlist stock."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..data_layer.news import NewsItem
from ..data_layer.prices import PriceData
from .base import Signal, SignalResult


class SignificantGainSignal(Signal):
    name = "significant_gain"

    def evaluate(
        self,
        stock: sqlite3.Row,
        price: PriceData,
        news: list[NewsItem],
        context: dict[str, Any],
    ) -> SignalResult | None:
        if not price.ok or price.pct_change is None:
            return None
        if price.pct_change <= 0:
            return None

        threshold = context.get("gain_threshold_pct", 5.0)
        if price.pct_change < threshold:
            return None

        return SignalResult(
            signal_name=self.name,
            symbol=stock["symbol"],
            severity=price.pct_change,
            headline=f"{stock['symbol']} gained {price.pct_change:+.2f}%",
            details={
                "pct_change": price.pct_change,
                "last_close": price.last_close,
                "prior_close": price.prior_close,
                "threshold": threshold,
                "cap_tier": stock["cap_tier"],
            },
            news=news[:5],
        )
