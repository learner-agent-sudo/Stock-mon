"""Rank signal results, boosting small-cap stocks per user setting."""
from __future__ import annotations

from typing import Iterable

from .. import config
from ..signals.base import SignalResult


# Priority order for signal types (higher = surfaced first within same severity bucket).
SIGNAL_PRIORITY = {
    "watchlist_target_hit": 100,
    "drop_without_news": 80,
    "drop_with_news": 60,
    "significant_gain": 40,
}


def rank(results: Iterable[SignalResult]) -> list[SignalResult]:
    boost = config.get_float("small_cap_rank_boost", 1.5)

    def score(r: SignalResult) -> float:
        cap_tier = r.details.get("cap_tier")
        sev = r.severity
        if cap_tier == "SMALL":
            sev *= boost
        return SIGNAL_PRIORITY.get(r.signal_name, 0) * 1000 + sev

    return sorted(results, key=score, reverse=True)
