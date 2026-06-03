"""Offline tests for the 13F net-flow logic (parsing + aggregation).

Live SEC/OpenFIGI calls can't run in CI sandboxes, so these exercise the
pure logic against XML fixtures via STOCKMON_USE_FIXTURE=1.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["STOCKMON_USE_FIXTURE"] = "1"

from src.data_layer import holdings_13f as h  # noqa: E402


def _by_cusip(stocks):
    return {s["cusip"]: s for s in stocks}


def test_infotable_parse_skips_options():
    xml = (h.FIXTURE_DIR / "1649339_latest.xml").read_text()
    parsed = h._parse_infotable(xml)
    # AAPL + NVDA equity rows; the NVDA Call row must be skipped.
    assert parsed["037833100"]["shares"] == 10
    assert parsed["67066G104"]["shares"] == 5  # not 5 + 9999
    assert parsed["67066G104"]["value"] == 5000


def test_aggregate_net_flow():
    berk = h.fetch_investor_holdings("Berkshire Hathaway", 1067983, "Warren Buffett")
    scion = h.fetch_investor_holdings("Scion Asset Management", 1649339, "Michael Burry")
    assert berk.status == "ok" and scion.status == "ok"
    assert berk.manager == "Warren Buffett"

    cusip_to_ticker = {
        "037833100": "AAPL", "594918104": "MSFT",
        "67066G104": "NVDA", "191216100": "KO",
    }
    categories = {"AAPL": "HOLDING", "NVDA": "WATCHLIST"}
    stocks = h.aggregate([berk, scion], cusip_to_ticker,
                         watchlist={"AAPL", "NVDA"}, categories=categories)
    s = _by_cusip(stocks)

    # Category marks flow through from the DB categories map.
    assert s["037833100"]["category"] == "HOLDING"
    assert s["67066G104"]["category"] == "WATCHLIST"
    assert s["594918104"]["category"] == ""  # MSFT not tracked

    # AAPL: Berkshire added (+20), Scion trimmed (-10) -> 1 buyer, 1 seller.
    assert s["037833100"]["buyers"] == 1
    assert s["037833100"]["sellers"] == 1
    assert s["037833100"]["net_investors"] == 0
    assert s["037833100"]["net_shares"] == 10
    assert s["037833100"]["net_value"] == 2000
    assert s["037833100"]["in_watchlist"] is True
    # Manager surfaces in the per-investor detail.
    managers = {d["manager"] for d in s["037833100"]["detail"]}
    assert "Warren Buffett" in managers and "Michael Burry" in managers

    # MSFT: new Berkshire position.
    assert s["594918104"]["net_investors"] == 1
    assert s["594918104"]["in_watchlist"] is False  # not in watchlist

    # NVDA: new Scion position (equity only).
    assert s["67066G104"]["net_investors"] == 1
    assert s["67066G104"]["net_shares"] == 5

    # KO: Berkshire exited.
    assert s["191216100"]["net_investors"] == -1
    assert s["191216100"]["net_shares"] == -30


def test_ranking_order():
    berk = h.fetch_investor_holdings("Berkshire Hathaway", 1067983)
    scion = h.fetch_investor_holdings("Scion Asset Management", 1649339)
    stocks = h.aggregate([berk, scion], {}, watchlist=set())
    # Highest net_investors first; ties broken by net_value desc.
    order = [s["cusip"] for s in stocks]
    assert order[0] == "594918104"  # MSFT: +1 investor, +15000
    assert order[1] == "67066G104"  # NVDA: +1 investor, +5000
    assert order[-1] == "191216100"  # KO: -1 investor


if __name__ == "__main__":
    test_infotable_parse_skips_options()
    test_aggregate_net_flow()
    test_ranking_order()
    print("all 13F tests passed")
