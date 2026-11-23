"""Offline tests for scripts/check_health.py (the watchdog's freshness rules).

Run: python3 tests/test_health.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_health", ROOT / "scripts" / "check_health.py")
ch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ch)

UTC = timezone.utc
HEALTHY = {"stats": {"prices_ok": 180, "stocks_checked": 200, "total_articles": 40,
                     "tickers_with_news": 25,
                     "source_health": [{"name": "yahoo", "status": "ok"}]}}
HEALTHY_13F = {"investor_count": 30, "investors": []}


class Repo:
    """A throwaway docs/ tree that check_health is pointed at."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "docs" / "history").mkdir(parents=True)
        (root / "docs" / "index.html").write_text("<html></html>")
        self._saved = ch.REPO_ROOT, ch.HISTORY
        ch.REPO_ROOT, ch.HISTORY = root, root / "docs" / "history"
        return self

    def __exit__(self, *exc):
        ch.REPO_ROOT, ch.HISTORY = self._saved
        self._tmp.cleanup()

    def briefing(self, when: datetime, payload=HEALTHY):
        (ch.HISTORY / f"{when:%Y-%m-%d-%H%M}.json").write_text(json.dumps(payload))

    def thirteen_f(self, day: str, payload=HEALTHY_13F):
        (ch.HISTORY / f"13f-{day}.json").write_text(json.dumps(payload))


def t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def test_watchdog_after_midnight_is_not_an_alarm():
    # The real false alarm: GitHub started the 19:00 watchdog at 00:03, so the
    # UTC date had rolled over before the day's first (also late) briefing.
    with Repo() as r:
        r.briefing(t("2026-11-22 18:35"))
        r.briefing(t("2026-11-22 22:19"))
        assert ch.check_briefing(now=t("2026-11-23 00:03")) == []
        # Still fine late the next afternoon, before that day's first run.
        assert ch.check_briefing(now=t("2026-11-23 19:30")) == []


def test_a_whole_missed_day_is_still_caught():
    with Repo() as r:
        r.briefing(t("2026-11-21 22:01"))
        problems = ch.check_briefing(now=t("2026-11-23 00:03"))
        assert len(problems) == 1 and "No briefing in the last 24h" in problems[0]
        assert "2026-11-21-2201.json" in problems[0] and "26h old" in problems[0]


def test_age_boundary():
    with Repo() as r:
        r.briefing(t("2026-11-22 12:00"))
        assert ch.check_briefing(now=t("2026-11-23 12:00")) == []
        assert ch.check_briefing(now=t("2026-11-23 12:01")) != []


def test_content_checks_run_on_newest_archive():
    with Repo() as r:
        r.briefing(t("2026-11-22 18:35"))
        broken = {"stats": dict(HEALTHY["stats"], prices_ok=5)}
        r.briefing(t("2026-11-22 22:19"), broken)
        problems = ch.check_briefing(now=t("2026-11-23 00:03"))
        assert any("Prices look broken" in p for p in problems), problems
        (ch.REPO_ROOT / "docs" / "index.html").unlink()
        assert any("Deployed page missing" in p for p in ch.check_briefing(now=t("2026-11-23 00:03")))


def test_13f_archives_are_not_mistaken_for_briefings():
    with Repo() as r:
        r.briefing(t("2026-11-20 22:57"))
        r.thirteen_f("2026-11-22")          # newer, but not a briefing
        problems = ch.check_briefing(now=t("2026-11-23 00:03"))
        assert problems and "2026-11-20-2257.json" in problems[0]


def test_no_archive_at_all():
    with Repo():
        problems = ch.check_briefing(now=t("2026-11-23 00:03"))
        assert len(problems) == 1 and "No briefing archive at all" in problems[0]


def test_13f_freshness():
    with Repo() as r:
        r.thirteen_f("2026-11-21")
        assert ch.check_13f(now=t("2026-11-23 00:03")) == []
        assert any("13F is stale" in p for p in ch.check_13f(now=t("2026-12-01 00:01")))


def test_main_exit_codes_and_issue_body():
    now = datetime.now(UTC)
    with Repo() as r, tempfile.NamedTemporaryFile("r+", suffix=".out") as out:
        r.thirteen_f(f"{now:%Y-%m-%d}")
        r.briefing(now - timedelta(hours=2))
        saved_argv, saved_env = sys.argv, os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ["GITHUB_OUTPUT"] = out.name
            sys.argv = ["check_health.py"]
            with redirect_stdout(io.StringIO()):
                assert ch.main() == 0
            for f in ch.HISTORY.glob("2*.json"):
                f.unlink()
            r.briefing(now - timedelta(hours=30))
            with redirect_stdout(io.StringIO()):
                assert ch.main() == 1
            body = Path(out.name).read_text()
            assert "body<<EOF_BODY" in body and "No briefing in the last 24h" in body
        finally:
            sys.argv = saved_argv
            if saved_env is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = saved_env


if __name__ == "__main__":
    test_watchdog_after_midnight_is_not_an_alarm()
    test_a_whole_missed_day_is_still_caught()
    test_age_boundary()
    test_content_checks_run_on_newest_archive()
    test_13f_archives_are_not_mistaken_for_briefings()
    test_no_archive_at_all()
    test_13f_freshness()
    test_main_exit_codes_and_issue_body()
    print("all health-check tests passed")
