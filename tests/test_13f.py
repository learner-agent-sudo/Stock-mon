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


def test_estimated_flow_strips_price_moves():
    # Trimmed 10 of 100 shares while the price doubled: reported value went
    # UP (+$8,000) but the investor was a SELLER — flow must be negative.
    prv = {"shares": 100, "value": 10_000}       # $100/share
    cur = {"shares": 90, "value": 18_000}        # $200/share
    assert h.estimated_flow(-10, cur, prv) == -2_000
    # Full exit: priced at the prior filing's price.
    assert h.estimated_flow(-100, {}, prv) == -10_000
    # New position: the whole current value.
    assert h.estimated_flow(50, {"shares": 50, "value": 7_500}, {}) == 7_500
    assert h.estimated_flow(0, cur, prv) == 0


def test_flow_and_pct_in_aggregate():
    inv = h.InvestorHoldings(name="Fund A", cik=1)
    inv.prior = {"C1": {"issuer": "ONE", "shares": 100, "value": 10_000},
                 "C2": {"issuer": "TWO", "shares": 10, "value": 90_000}}
    inv.latest = {"C1": {"issuer": "ONE", "shares": 90, "value": 18_000},
                  "C2": {"issuer": "TWO", "shares": 10, "value": 82_000}}
    stocks = {s["cusip"]: s for s in h.aggregate([inv], {}, watchlist=set())}
    assert "C2" not in stocks                       # HOLD — no share change
    one = stocks["C1"]
    assert one["sellers"] == 1 and one["net_value"] == 8_000   # value rose...
    assert one["net_flow"] == -2_000                            # ...but it was a sale
    # Denominator = max(latest 100k, prior 100k) = 100k -> -2%.
    assert abs(one["conviction"] - (-2.0)) < 1e-9
    assert abs(one["detail"][0]["pct"] - (-2.0)) < 1e-9


def test_normalize_value_units_thousands():
    filed = {"A": {"shares": 1_000, "value": 150},     # $0.15/share -> thousands
             "B": {"shares": 2_000, "value": 80},
             "C": {"shares": 500, "value": 40}}
    fixed, rescaled = h.normalize_value_units(filed)
    assert rescaled and fixed["A"]["value"] == 150_000
    assert filed["A"]["value"] == 150                   # input not mutated
    dollars = {"A": {"shares": 1_000, "value": 150_000}}
    same, rescaled = h.normalize_value_units(dollars)
    assert not rescaled and same is dollars
    # Bond-heavy book (price ~1.0 per $1 principal) must NOT be rescaled.
    bonds = {"A": {"shares": 1_000_000, "value": 1_010_000}}
    assert not h.normalize_value_units(bonds)[1]


def test_mark_stale_and_quarter_label():
    assert h.quarter_label("2026-06-30") == "Q2'26"
    assert h.quarter_label("2025-12-31") == "Q4'25"
    assert h.quarter_label(None) == "" and h.quarter_label("bad") == ""

    def inv(name, as_of, status="ok"):
        i = h.InvestorHoldings(name=name, cik=0, status=status)
        i.as_of = as_of
        return i
    newest, one_behind = inv("A", "2026-09-30"), inv("B", "2026-06-30")
    two_behind, errored = inv("C", "2026-03-31"), inv("D", "2011-12-31", "error")
    flagged = h.mark_stale([newest, one_behind, two_behind, errored])
    assert flagged == [two_behind]
    assert two_behind.status == "stale" and "Q1'26" in two_behind.detail
    assert newest.status == one_behind.status == "ok"
    assert errored.status == "error"                    # untouched
    # Stale investors are excluded from aggregation.
    two_behind.prior = {"C9": {"issuer": "X", "shares": 1, "value": 10}}
    assert h.aggregate([two_behind], {}, watchlist=set()) == []


def test_page_payload_and_render():
    from src.output import holdings_13f_page as page
    berk = h.fetch_investor_holdings("Berkshire Hathaway", 1067983, "Warren Buffett")
    scion = h.fetch_investor_holdings("Scion Asset Management", 1649339, "Michael Burry")
    scion.style, berk.short = "diversified", "Buffett"
    stocks = h.aggregate([berk, scion], {"037833100": "AAPL"}, watchlist={"AAPL"},
                         categories={"AAPL": "HOLDING"})
    stocks[0]["info"] = {"name": "</script><b>x", "sector": "Tech", "fetched_at": 1.0,
                         "website": "javascript:alert(1)"}
    dataset = {
        "generated_at": "2026-11-14T17:00:00+00:00", "as_of": "2026-03-31",
        "investor_count": 2,
        "investors": [
            {"name": i.name, "manager": i.manager, "short": i.short, "style": i.style,
             "status": i.status, "as_of": i.as_of, "prior_as_of": i.prior_as_of,
             "holdings": len(i.latest), "total_value": h.portfolio_value(i.latest)}
            for i in (berk, scion)
        ],
        "stocks": stocks,
    }
    payload = page.build_payload(dataset)
    assert [i["s"] for i in payload["inv"]] == ["Buffett", "Burry"]
    assert payload["inv"][1]["st"] == "diversified" and payload["inv"][0]["q"] == "Q1'26"
    aapl = next(s for s in payload["stk"] if s["c"] == "037833100")
    assert aapl["k"] == "H" and aapl["t"] == "AAPL"
    # [investor, action code, shares delta, flow, pct] per move; ADD=1, TRIM=2.
    assert sorted((m[0], m[1], m[2]) for m in aapl["d"]) == [(0, 1, 20), (1, 2, -10)]
    # Every move carries the prior position weight; a NEW position starts at 0.
    for s in payload["stk"]:
        for m in s["d"]:
            assert len(m) == 6 and (m[5] is None or m[5] >= 0)
            if m[1] == 0:
                assert m[5] == 0
    html = page.render(dataset)
    # The embedded payload can't terminate its <script> early, and an unsafe
    # website URL from third-party data never becomes a link.
    body = html.split('id="payload">', 1)[1].split("</script>", 1)[0]
    assert "</script" not in body and "javascript:alert" not in html
    assert "13F Net Flow" in html and "Stock-pickers (1)" in html and "All (2)" in html
    for chart in ('id="capmap"', 'id="posmap"', 'id="pyr-size"', 'id="pyr-cap"', 'id="invmap"'):
        assert chart in html


def test_prior_weight_and_market_cap():
    from src.output import holdings_13f_page as page
    # Trimmed 25 of 100 shares = -1% of the book -> the position was 4% before.
    assert page._prior_weight({"shares_delta": -25, "shares_prior": 100, "pct": -1.0}) == 4.0
    # Exit: the whole prior position.
    assert page._prior_weight({"shares_delta": -100, "shares_prior": 100, "pct": -2.5}) == 2.5
    assert page._prior_weight({"shares_delta": 50, "shares_prior": 0, "pct": 3.0}) == 0.0
    # Archives from before shares_prior existed, and degenerate rows.
    assert page._prior_weight({"shares_delta": 10, "pct": 1.0}) is None
    assert page._prior_weight({"shares_delta": 0, "shares_prior": 5, "pct": 0.0}) is None
    assert page._prior_weight({"shares_delta": 10, "shares_prior": 5, "pct": -1.0}) is None  # sign mismatch
    assert page._market_cap({"market_cap": 1_234_567_890_123}) == 1.23e12
    for bad in (None, 0, -5, True, float("nan"), float("inf"), "1e9"):
        assert page._market_cap({"market_cap": bad}) is None
    ds = {"investors": [{"name": "A", "status": "ok"}],
          "stocks": [{"cusip": "C1", "ticker": "X", "info": {"market_cap": 5e9},
                      "detail": [{"investor": "A", "action": "TRIM", "shares_delta": -25,
                                  "shares_prior": 100, "flow": -10, "pct": -1.0}]},
                     {"cusip": "C2", "ticker": "Y",   # old archive: no flow / shares_prior
                      "detail": [{"investor": "A", "action": "ADD", "shares_delta": 5,
                                  "value_delta": 7, "pct": 0.5}]}]}
    stk = {s["c"]: s for s in page.build_payload(ds)["stk"]}
    assert stk["C1"]["mc"] == 5e9 and stk["C1"]["d"][0] == [0, 2, -25, -10, -1.0, 4.0]
    assert "mc" not in stk["C2"] and stk["C2"]["d"][0] == [0, 1, 5, 7, 0.5, None]


if __name__ == "__main__":
    test_infotable_parse_skips_options()
    test_aggregate_net_flow()
    test_ranking_order()
    test_estimated_flow_strips_price_moves()
    test_flow_and_pct_in_aggregate()
    test_normalize_value_units_thousands()
    test_mark_stale_and_quarter_label()
    test_page_payload_and_render()
    test_prior_weight_and_market_cap()
    print("all 13F tests passed")
