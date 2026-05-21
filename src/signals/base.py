"""Signal plug-in interface.

A signal inspects a single stock's price + news data and returns a
SignalResult (or None to indicate no match). The briefing builder runs
all enabled signals over every active stock and collects matches.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..data_layer.news import NewsItem
from ..data_layer.prices import PriceData


@dataclass
class SignalResult:
    signal_name: str
    symbol: str
    severity: float
    headline: str
    details: dict[str, Any] = field(default_factory=dict)
    news: list[NewsItem] = field(default_factory=list)


class Signal:
    """Subclasses set `name` and implement `evaluate`."""

    name: str = "signal"

    def evaluate(
        self,
        stock: sqlite3.Row,
        price: PriceData,
        news: list[NewsItem],
        context: dict[str, Any],
    ) -> SignalResult | None:
        raise NotImplementedError
