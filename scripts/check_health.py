"""Health check for the latest briefing and 13F outputs.

Inspects what got written to docs/ and prints a markdown diagnosis.
- Exits 0 if everything looks healthy.
- Exits 1 with a list of problems otherwise.

Designed to be called two ways:
1. As the final step of the build workflows (catches degraded but
   "successful" runs — e.g. workflow completed but all news sources
   returned empty, or no signals matched).
2. As a watchdog workflow that runs *after* the build windows
   (catches "the build didn't run at all today").
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY = REPO_ROOT / "docs" / "history"

# Tunables — easy to adjust later if you want stricter/looser alerts.
MIN_PRICES_OK = 100              # sane lower bound for the daily run
MIN_PRICE_SUCCESS_PCT = 70.0     # alert if >30% of tickers fail to price
TF13F_FRESHNESS_DAYS = 9         # 13F runs weekly; >9d means a missed run
MIN_HEALTHY_NEWS_SOURCES = 1     # at least one news source must work
REQUIRED_NEWS_SOURCE = "yahoo"   # primary source — others are nice-to-have


def _latest_briefing_json() -> Path | None:
    files = sorted(HISTORY.glob("[0-9]*-*.json"))
    return files[-1] if files else None


def _latest_13f_json() -> Path | None:
    files = sorted(HISTORY.glob("13f-*.json"))
    return files[-1] if files else None


def _briefing_date_from_name(p: Path) -> datetime | None:
    """history/<YYYY-MM-DD>-<HHMM>.json -> UTC datetime."""
    try:
        stem = p.stem            # "2026-06-01-1217"
        return datetime.strptime(stem, "%Y-%m-%d-%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def check_briefing() -> list[str]:
    problems: list[str] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_files = sorted(HISTORY.glob(f"{today}-*.json"))
    if not today_files:
        latest = _latest_briefing_json()
        latest_name = latest.name if latest else "none"
        return [
            f"**No briefing for today ({today})** — the daily cron didn't produce "
            f"output. Latest archive is `{latest_name}`."
        ]
    latest = today_files[-1]

    try:
        payload = json.loads(latest.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return problems + [f"**Briefing**: cannot read `{latest.name}` ({exc})."]

    # The deployed HTML should exist too — otherwise the page is broken
    # even if the JSON archive looks fine.
    if not (REPO_ROOT / "docs" / "index.html").exists():
        problems.append("**Deployed page missing** — `docs/index.html` is absent.")

    stats = payload.get("stats", {})
    prices_ok = stats.get("prices_ok", 0)
    checked = stats.get("stocks_checked", 0)
    if prices_ok < MIN_PRICES_OK:
        problems.append(
            f"**Prices look broken** — only {prices_ok} tickers returned valid prices "
            f"(threshold {MIN_PRICES_OK}). yfinance or upstream may be down."
        )
    if checked > 0:
        pct = prices_ok / checked * 100.0
        if pct < MIN_PRICE_SUCCESS_PCT:
            problems.append(
                f"**Price success rate dropped** — {prices_ok}/{checked} "
                f"({pct:.0f}%) — threshold {MIN_PRICE_SUCCESS_PCT:.0f}%. "
                f"Partial yfinance outage or many delisted tickers."
            )

    sources = stats.get("source_health") or []
    if sources:
        healthy = [s for s in sources if s.get("status") == "ok"]
        broken = [s for s in sources if s.get("status") not in ("ok", "disabled")]
        if len(healthy) < MIN_HEALTHY_NEWS_SOURCES:
            details = "; ".join(f"{s.get('name')}: {s.get('detail','?')}" for s in broken) or "all sources disabled"
            problems.append(f"**No news sources are healthy** — {details}.")
        # Yahoo is the primary source (only one with international coverage);
        # losing it specifically matters more than any single other source.
        yahoo = next((s for s in sources if s.get("name") == REQUIRED_NEWS_SOURCE), None)
        if yahoo is not None and yahoo.get("status") != "ok":
            problems.append(
                f"**Primary news source down** — yahoo: {yahoo.get('detail','?')}. "
                f"International tickers will have no news coverage."
            )

    if stats.get("total_articles", 0) == 0 and stats.get("tickers_with_news", 0) == 0:
        problems.append(
            "**No news articles fetched at all** — even though sources report healthy, "
            "nothing was returned. Check for an upstream change."
        )
    return problems


def check_13f() -> list[str]:
    problems: list[str] = []
    latest = _latest_13f_json()
    if latest is None:
        return ["**13F**: no archive in `docs/history/`. Run the 13F workflow at least once."]

    try:
        when = datetime.strptime(latest.stem.removeprefix("13f-"), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        problems.append(f"**13F**: cannot parse date from `{latest.name}`.")
        when = None

    if when is not None:
        age = datetime.now(timezone.utc) - when
        if age > timedelta(days=TF13F_FRESHNESS_DAYS):
            days = int(age.total_seconds() // 86400)
            problems.append(
                f"**13F is stale** — latest archive `{latest.name}` is {days}d old "
                f"(weekly cron expected; threshold {TF13F_FRESHNESS_DAYS}d)."
            )

    try:
        payload = json.loads(latest.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return problems + [f"**13F**: cannot read `{latest.name}` ({exc})."]

    if payload.get("investor_count", 0) == 0:
        investors = payload.get("investors", [])
        first_err = next((i.get("detail") for i in investors if i.get("detail")), "no detail")
        problems.append(
            f"**13F has zero healthy investors** — all {len(investors)} failed. "
            f"First error: `{first_err}`. SEC EDGAR may be unreachable from the runner."
        )
    return problems


def main() -> int:
    # CLI flags so workflows can scope the check to their own output.
    # No flags = check everything (watchdog mode).
    args = sys.argv[1:]
    check_b = "--13f" not in args
    check_f = "--briefing" not in args
    briefing_problems = check_briefing() if check_b else []
    f13_problems = check_13f() if check_f else []
    all_problems = briefing_problems + f13_problems

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not all_problems:
        print(f"✅ Health check passed at {today}.")
        print(f"  Latest briefing: {_latest_briefing_json().name if _latest_briefing_json() else 'n/a'}")
        print(f"  Latest 13F:      {_latest_13f_json().name if _latest_13f_json() else 'n/a'}")
        return 0

    print(f"❌ Health check failed at {today}.\n")
    for p in all_problems:
        print(f"- {p}")

    # GitHub Actions-friendly: dump the markdown body for the issue creator step.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        body = f"Health check failed at **{today}**.\n\n" + "\n".join(f"- {p}" for p in all_problems)
        body += "\n\n_This issue is auto-managed by the Health Watchdog workflow._"
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("body<<EOF_BODY\n")
            fh.write(body)
            fh.write("\nEOF_BODY\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
