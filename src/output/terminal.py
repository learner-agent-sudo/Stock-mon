"""Print a formatted briefing to the terminal, color-coded by signal type."""
from __future__ import annotations

import os
import sys
from collections import defaultdict

from ..briefing.builder import Briefing
from ..signals.base import SignalResult


# ANSI colors — disabled if stdout isn't a TTY or NO_COLOR is set.
def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


_C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


def _c(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{_C.get(color, '')}{text}{_C['reset']}"


SECTION_TITLES = {
    "watchlist_target_hit": ("WATCHLIST TARGETS HIT", "cyan"),
    "drop_without_news":    ("DROPS WITHOUT NEWS",    "red"),
    "drop_with_news":       ("DROPS WITH NEWS",       "yellow"),
    "significant_gain":     ("SIGNIFICANT GAINS",     "green"),
}

SECTION_ORDER = [
    "watchlist_target_hit",
    "drop_without_news",
    "drop_with_news",
    "significant_gain",
]


def _format_result(r: SignalResult) -> str:
    pct = r.details.get("pct_change")
    last = r.details.get("last_close")
    tier = r.details.get("cap_tier")
    parts = [_c(r.symbol, "bold")]
    if tier:
        parts.append(_c(f"[{tier}]", "dim"))
    if pct is not None:
        col = "green" if pct >= 0 else "red"
        parts.append(_c(f"{pct:+.2f}%", col))
    if last is not None:
        parts.append(_c(f"@ {last:.2f}", "dim"))
    line = "  " + " ".join(parts)
    line += "\n    " + r.headline
    if r.news:
        line += "\n" + "\n".join(
            f"      • {n.headline}  {_c('(' + n.source + ')', 'dim')}"
            for n in r.news[:3]
        )
    return line


def render(b: Briefing) -> str:
    out: list[str] = []
    out.append(_c("=" * 70, "dim"))
    out.append(_c(f"  Stock Briefing — {b.generated_at}", "bold"))
    out.append(_c("=" * 70, "dim"))

    stats = b.stats
    out.append(
        f"  Checked {stats.get('stocks_checked', 0)} stocks  |  "
        f"prices ok: {stats.get('prices_ok', 0)}  "
        f"failed: {stats.get('prices_failed', 0)}  |  "
        f"signals matched: {stats.get('signals_matched', 0)}"
    )
    out.append("")

    if not b.results:
        out.append(_c("  No signals matched. Quiet day.", "dim"))
        return "\n".join(out)

    grouped: dict[str, list[SignalResult]] = defaultdict(list)
    for r in b.results:
        grouped[r.signal_name].append(r)

    for name in SECTION_ORDER:
        if name not in grouped:
            continue
        title, color = SECTION_TITLES[name]
        out.append(_c(f"── {title} ({len(grouped[name])}) ──", color))
        for r in grouped[name]:
            out.append(_format_result(r))
            out.append("")
    return "\n".join(out)


def print_briefing(b: Briefing) -> None:
    print(render(b))
