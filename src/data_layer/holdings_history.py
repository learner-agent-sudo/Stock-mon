"""Estimated cost basis and gain/loss for 13F trades, rebuilt from history.

A 13F filing only lists what an investor held at quarter end: shares and
market value per security. It says nothing about what they paid, when in
the quarter they traded, or whether a sale realised a gain or a loss.

This module reconstructs an estimate from the investor's past filings:

* history: up to HISTORY_QUARTERS of each stock-picker's 13F info tables,
  cached in data/13f_history.json. Filings never change, so each one is
  downloaded once; a time budget spreads the first backfill over a few runs,
  newest quarters first.
* prices: value / shares in a filing is that quarter-end's price. A trade
  made during a quarter is priced at the midpoint of the quarter-end prices
  on either side of it, and those two prices bound the estimate (lo / hi).
* splits: a share count that jumps x k while the price drops to 1/k, with
  the other holders' share counts jumping too, is a split, not a trade.
* cost: average cost. Buys move it; sales don't.

For each trade between an investor's prior and latest filing:

    pnl = trade price / average cost - 1     (lo / hi: quarter-end range)

A position already held in the oldest filing available ("pre") gets that
filing's price as a stand-in cost; it is flagged so the page can say so.
Everything here is an estimate, and the page labels it that way.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from statistics import median

from . import holdings_13f as h13

HISTORY_PATH = h13.DATA_DIR / "13f_history.json"
HISTORY_QUARTERS = 40          # ten years of quarter-ends per investor
BUDGET_SEC = 300               # per run; the first backfill spans a few runs
XML_ERA_START = "2013-06-30"   # 13F information tables are XML from Q2 2013
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
MAX_GAP_DAYS = 100             # consecutive quarter-ends are 90-92 days apart
# Split detection: the quarter-end price moved by ~1/k and the holders'
# share counts by ~k. Ratios k cover the splits and reverse splits seen in
# practice; the tolerances absorb price drift inside the quarter.
SPLITS = (2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50, 100)
SPLIT_PRICE_TOL = 0.15
SPLIT_SHARES_TOL = 0.30
# A "trade price vs cost" outside this band means a broken price series
# (e.g. a row filed in $ thousands inside a dollar filing), not a real trade.
PNL_SANE = (-0.999, 50.0)


# --------------------------------------------------------------------------
# Cache: {cik: {"q": {report_date: {"a": accession, "h": {cusip: [shares, value]}}},
#               "x": [accessions without an XML info table],
#               "old": [[report_date, accession, filing_date], ...]}}
# Values are kept as filed; units are normalized when the cache is read.
# --------------------------------------------------------------------------

def _fixture() -> bool:
    return os.environ.get(h13.FIXTURE_ENV) == "1"


def load_cache() -> dict:
    if _fixture() or not HISTORY_PATH.exists():
        return {}
    try:
        data = json.loads(HISTORY_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_cache(cache: dict) -> None:
    if _fixture() or not cache:
        return
    try:
        HISTORY_PATH.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


def compact(holdings: dict[str, dict]) -> dict[str, list[int]]:
    return {c: [int(h.get("shares") or 0), int(h.get("value") or 0)]
            for c, h in holdings.items() if (h.get("shares") or 0) > 0}


def qend_back(d: str, n: int) -> str:
    """The quarter-end n quarters before quarter-end d ('2026-09-30', 4 -> '2025-09-30')."""
    idx = int(d[:4]) * 4 + (int(d[5:7]) - 1) // 3 - n
    y, q = divmod(idx, 4)
    return f"{y}-{('03-31', '06-30', '09-30', '12-31')[q]}"


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)


def merge_filings(*lists: list[dict]) -> list[dict]:
    """Newest report date first, one filing per report date (latest filed wins)."""
    allf = sorted((f for lst in lists for f in lst), key=lambda f: f.get("filing_date", ""), reverse=True)
    seen, out = set(), []
    for f in allf:
        if f["report_date"] not in seen:
            seen.add(f["report_date"])
            out.append(f)
    return sorted(out, key=lambda f: f["report_date"], reverse=True)


# --------------------------------------------------------------------------
# Backfill from EDGAR
# --------------------------------------------------------------------------

def _fetch_infotable(cik: int, accession: str) -> tuple[str, str | None]:
    """('ok', xml) | ('none', None) when the filing has no XML info table
    (worth remembering) | ('error', None) when it's worth retrying later."""
    base = h13.ARCHIVE_BASE.format(cik=cik, acc=accession.replace("-", ""))
    idx = h13._sec_get(f"{base}/index.json")
    if idx is None:
        return "error", None
    if idx.status_code == 404:
        return "none", None
    if idx.status_code != 200:
        return "error", None
    try:
        items = idx.json().get("directory", {}).get("item", [])
    except ValueError:
        return "error", None
    cands = [it.get("name", "") for it in items
             if it.get("name", "").lower().endswith(".xml")
             and it.get("name", "").lower() != "primary_doc.xml"]
    cands.sort(key=lambda n: ("table" not in n.lower() and "info" not in n.lower(), n))
    for name in cands:
        r = h13._sec_get(f"{base}/{name}")
        if r is None or r.status_code not in (200, 404):
            return "error", None
        if r.status_code == 200 and "infoTable" in r.text:
            return "ok", r.text
    return "none", None


def _older_filings(pages: list[dict], since: str, out_of_time) -> list[dict] | None:
    """13F-HR filings from EDGAR's older submission pages that reach back to
    `since`. None if a page could not be read (retry next run)."""
    found: list[dict] = []
    for p in pages:
        name = p.get("name") or ""
        if not name or (p.get("filingTo") or "9999") < since:
            continue
        if out_of_time():
            return None
        r = h13._sec_get(SUBMISSIONS_PAGE_URL.format(name=name))
        if r is None or r.status_code != 200:
            return None
        try:
            found += h13.filings_13f(r.json())
        except ValueError:
            return None
    return found


def _seed(inv, q: dict) -> None:
    """The latest and prior filings were parsed this run: store them."""
    for k, hold in ((0, inv.latest), (1, inv.prior)):
        if len(inv.filings) > k and hold:
            f = inv.filings[k]
            if (q.get(f["report_date"]) or {}).get("a") != f["accession"]:
                q[f["report_date"]] = {"a": f["accession"], "h": compact(hold)}


def backfill(pickers: list, cache: dict, budget_sec: float) -> dict:
    """Download missing past filings, breadth-first (every investor's most
    recent missing quarter before anyone's older ones), within the budget and
    the SEC phase deadline. Mutates `cache`; returns counters for the log."""
    stats = {"fetched": 0, "no_xml": 0, "errors": 0, "pending": 0}
    deadline = time.monotonic() + max(0.0, budget_sec)

    def out_of_time() -> bool:
        now = time.monotonic()
        return now > deadline or bool(h13._sec_phase_deadline and now > h13._sec_phase_deadline)

    plans = []
    for inv in pickers:
        e = cache.setdefault(str(inv.cik), {})
        q, x = e.setdefault("q", {}), e.setdefault("x", [])
        _seed(inv, q)
        since = max(XML_ERA_START, qend_back(inv.as_of, HISTORY_QUARTERS - 1))
        filings = list(inv.filings)
        if ("old" not in e and inv.filing_pages
                and (not filings or filings[-1]["report_date"] > since)):
            older = _older_filings(inv.filing_pages, since, out_of_time)
            if older is not None:
                e["old"] = [[f["report_date"], f["accession"], f["filing_date"]] for f in older]
        filings = merge_filings(filings, [{"report_date": r, "accession": a, "filing_date": fd}
                                          for r, a, fd in e.get("old", [])])
        wanted = [f for f in filings if since <= f["report_date"] <= inv.as_of][:HISTORY_QUARTERS]
        keep = {f["report_date"] for f in wanted}
        for d in [d for d in q if d not in keep]:   # bound the cache to the window
            del q[d]
        todo = [f for f in wanted
                if (q.get(f["report_date"]) or {}).get("a") != f["accession"] and f["accession"] not in x]
        plans.append([inv, q, x, todo, 0])

    depth = 0
    while any(depth < len(p[3]) for p in plans):
        for p in plans:
            inv, q, x, todo, _ = p
            if depth >= len(todo):
                continue
            if out_of_time():
                stats["pending"] = sum(len(pp[3]) - pp[4] for pp in plans)
                return stats
            f = todo[depth]
            status, xml = _fetch_infotable(inv.cik, f["accession"])
            p[4] += 1
            if status == "ok":
                q[f["report_date"]] = {"a": f["accession"], "h": compact(h13._parse_infotable(xml))}
                stats["fetched"] += 1
            elif status == "none":
                x.append(f["accession"])
                stats["no_xml"] += 1
            else:
                stats["errors"] += 1
            if (stats["fetched"] + stats["no_xml"]) % 10 == 9:
                save_cache(cache)   # keep progress if the job is cut short
        depth += 1
    return stats


# --------------------------------------------------------------------------
# Position series, price index, splits
# --------------------------------------------------------------------------

def prices_of(compact_h: dict[str, list[int]]) -> dict[str, tuple[int, float | None]]:
    """{cusip: [shares, value as filed]} -> {cusip: (shares, $ per share)}."""
    as_dicts = {c: {"shares": v[0], "value": v[1]} for c, v in compact_h.items()}
    fixed, _ = h13.normalize_value_units(as_dicts)
    return {c: (d["shares"], d["value"] / d["shares"] if d["value"] > 0 else None)
            for c, d in fixed.items() if d["shares"] > 0}


def series(inv, entry: dict | None) -> tuple[list[str], dict[str, dict]]:
    """The investor's contiguous run of quarter-ends ending at its latest
    filing, with {cusip: (shares, price)} per quarter. The filings parsed
    this run always win over cached copies."""
    q = {d: v["h"] for d, v in ((entry or {}).get("q") or {}).items() if isinstance(v, dict) and "h" in v}
    if inv.as_of and inv.latest:
        q[inv.as_of] = compact(inv.latest)
    if inv.prior_as_of and inv.prior:
        q[inv.prior_as_of] = compact(inv.prior)
    dates = sorted(d for d in q if inv.as_of and d <= inv.as_of)
    run: list[str] = dates[-1:]
    for d in reversed(dates[:-1]):
        if _days(d, run[0]) > MAX_GAP_DAYS:
            break
        run.insert(0, d)
    return run, {d: prices_of(q[d]) for d in run}


def indexes(all_series: list[tuple[list[str], dict]]) -> tuple[dict, dict]:
    """Median quarter-end price per (cusip, date) across every filer we have,
    and each holder's share-count ratio per (cusip, date) vs the quarter before."""
    px: dict[tuple[str, str], list[float]] = {}
    ratios: dict[tuple[str, str], list[float]] = {}
    for dates, hold in all_series:
        for i, d in enumerate(dates):
            prev = hold[dates[i - 1]] if i else None
            for c, (s, p) in hold[d].items():
                if p:
                    px.setdefault((c, d), []).append(p)
                if prev and c in prev and prev[c][0] > 0:
                    ratios.setdefault((c, d), []).append(s / prev[c][0])
    return {k: median(v) for k, v in px.items()}, ratios


def split_factor(cusip: str, d_prev: str, d: str, price: dict, ratios: dict) -> float:
    """Share multiplier from a split between two quarter-ends (1.0 if none)."""
    p0, p1 = price.get((cusip, d_prev)), price.get((cusip, d))
    if not p0 or not p1:
        return 1.0
    r = p1 / p0
    if 0.55 <= r <= 1.8:
        return 1.0
    rs = ratios.get((cusip, d))
    if not rs:
        return 1.0
    m = median(rs)
    for k in SPLITS:
        for mult in (float(k), 1.0 / k):    # k-for-1 split, or 1-for-k reverse split
            if abs(r * mult - 1) <= SPLIT_PRICE_TOL and abs(m / mult - 1) <= SPLIT_SHARES_TOL:
                return mult
    return 1.0


def estimate(dates: list[str], hold: dict[str, dict], cusip: str,
             price: dict, ratios: dict) -> dict | None:
    """Average-cost walk over the series; returns the estimate for the last
    step (prior -> latest filing), or None when no cost can be estimated
    (a brand-new position, or no prices)."""
    n = len(dates)
    if n < 2:
        return None

    def px(d: str) -> float | None:
        own = hold[d].get(cusip)
        return own[1] if own and own[1] else price.get((cusip, d))

    shares, cost, since, pre = 0.0, None, None, False
    for i, d in enumerate(dates):
        s = float(hold[d].get(cusip, (0, None))[0])
        if i == 0:
            if s > 0:
                shares, cost, since, pre = s, px(d), d, True
            continue
        f = split_factor(cusip, dates[i - 1], d, price, ratios)
        p_prev, p_now = px(dates[i - 1]), px(d)
        if f != 1.0:
            shares *= f
            cost = cost / f if cost else cost
            p_prev = p_prev / f if p_prev else p_prev
        known = [p for p in (p_prev, p_now) if p]
        lo, hi = (min(known), max(known)) if known else (None, None)
        mid = (lo + hi) / 2 if known else None
        delta = s - shares
        if i == n - 1:
            if shares <= 0 or not cost or not mid or abs(delta) < 0.5:
                return None
            return {"pnl": mid / cost - 1, "lo": lo / cost - 1, "hi": hi / cost - 1,
                    "basis": cost, "px": mid, "since": since, "pre": pre,
                    "held_q": (n - 1) - dates.index(since)}
        if delta > 0:
            if shares <= 0:
                cost, since, pre = mid, d, False      # a new holding spell
            elif cost and mid:
                cost = (cost * shares + mid * delta) / s
        elif delta < 0 and s <= 0:
            cost, since, pre = None, None, False
        shares = s
    return None


def _issuer_key(name: str) -> str:
    return "".join(ch for ch in (name or "").upper() if ch.isalnum())


def _same_issuer(a: str, b: str) -> bool:
    """'ACMECORP' vs 'ACMECORPNEW': one name is the other plus a suffix."""
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 6 and long_.startswith(short)


def reidentified(inv) -> set[str]:
    """Exits whose issuer re-appears under a new CUSIP in the same filing —
    a CUSIP change (merger, reverse split, re-listing), not a sale."""
    new_keys = [_issuer_key(inv.latest[c].get("issuer", "")) for c in inv.latest if c not in inv.prior]
    return {c for c in inv.prior if c not in inv.latest
            and any(_same_issuer(_issuer_key(inv.prior[c].get("issuer", "")), k) for k in new_keys)}


def _sig(x: float, digits: int = 4) -> float:
    return float(f"{x:.{digits}g}")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def build_estimates(investors: list, budget_sec: float = 0.0) -> dict[str, dict[str, dict]]:
    """{investor name: {cusip: estimate}} for every stock-picker trade between
    its prior and latest filing that has an estimable cost."""
    ok = [i for i in investors if i.status == "ok" and i.as_of]
    pickers = [i for i in ok if i.style != "diversified" and i.prior_as_of]
    cache = load_cache()
    if not _fixture() and pickers:
        stats = backfill(pickers, cache, budget_sec)
        save_cache(cache)
        print(f"  [13f] history: fetched {stats['fetched']} past filing(s) "
              f"({stats['no_xml']} without an XML table, {stats['errors']} error(s), "
              f"{stats['pending']} left for the next run)")

    # Every healthy filer (the diversified funds too) feeds the price and
    # split indexes; only the stock-pickers get cost estimates.
    picker_ids = {id(i) for i in pickers}
    all_series, mine = [], {}
    for inv in ok:
        is_picker = id(inv) in picker_ids
        s = series(inv, cache.get(str(inv.cik)) if is_picker else None)
        all_series.append(s)
        if is_picker:
            mine[inv.name] = s
    price, ratios = indexes(all_series)

    out: dict[str, dict[str, dict]] = {}
    depth = []
    for inv in pickers:
        dates, hold = mine[inv.name]
        depth.append(f"{inv.short or inv.name} {len(dates)}q")
        if len(dates) < 2 or dates[-1] != inv.as_of or dates[-2] != inv.prior_as_of:
            continue
        skip = reidentified(inv)
        res = {}
        for c in set(inv.latest) | set(inv.prior):
            if c in skip or inv.latest.get(c, {}).get("shares", 0) == inv.prior.get(c, {}).get("shares", 0):
                continue
            e = estimate(dates, hold, c, price, ratios)
            if e and PNL_SANE[0] < e["lo"] and e["hi"] < PNL_SANE[1]:
                res[c] = {"pnl": _sig(e["pnl"]), "lo": _sig(e["lo"]), "hi": _sig(e["hi"]),
                          "basis": _sig(e["basis"], 5), "px": _sig(e["px"], 5),
                          "since": e["since"], "pre": e["pre"], "held_q": e["held_q"]}
        if res:
            out[inv.name] = res
    print(f"  [13f] history: cost estimates for {sum(len(v) for v in out.values())} trade(s); "
          f"depth: {', '.join(depth)}")
    return out
