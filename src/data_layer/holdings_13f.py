"""13F institutional-holdings net-flow tracker.

Pulls each curated investor's two most recent 13F-HR filings from SEC
EDGAR, diffs them per security, and aggregates per-stock net flow across
investors. Identifies stocks by CUSIP (what 13F uses) and maps CUSIP ->
ticker via OpenFIGI so we can show tickers and match the watchlist.

Everything is best-effort and per-investor isolated: one bad filing or a
blocked source never sinks the whole run. SEC and OpenFIGI are both
HTTP-only and may be blocked from some networks; failures degrade
gracefully and are reported through source-health.
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
INVESTORS_PATH = DATA_DIR / "super_investors.json"
CUSIP_CACHE_PATH = DATA_DIR / "cusip_map.json"
FIXTURE_ENV = "STOCKMON_USE_FIXTURE"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "edgar"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
# SEC asks for a descriptive UA with contact info or it returns 403.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "stock-mon/1.0 (contact: fredchan31@gmail.com)")
SEC_THROTTLE_SEC = 0.2  # SEC fair-access: stay well under 10 req/s


@dataclass
class InvestorHoldings:
    name: str
    cik: int
    manager: str = ""
    status: str = "ok"          # ok | error | mismatch
    detail: str = ""
    as_of: str | None = None    # report date of latest filing
    prior_as_of: str | None = None
    latest: dict[str, dict] = field(default_factory=dict)  # cusip -> {issuer, shares, value}
    prior: dict[str, dict] = field(default_factory=dict)


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def _sec_get(url: str) -> requests.Response | None:
    if requests is None:
        return None
    time.sleep(SEC_THROTTLE_SEC)
    try:
        return requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=20)
    except Exception:  # noqa: BLE001
        return None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------
# EDGAR: locate and parse 13F information tables
# --------------------------------------------------------------------------

def _latest_two_13f(cik: int) -> tuple[str | None, list[dict]]:
    """Return (entity_name, [latest_filing, prior_filing]) for 13F-HR forms.

    Each filing dict: {accession, filing_date, report_date}.
    """
    resp = _sec_get(SUBMISSIONS_URL.format(cik=cik))
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "no-response"
        raise RuntimeError(f"submissions HTTP {code}")
    data = resp.json()
    name = data.get("name")
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    fdates = recent.get("filingDate", [])
    rdates = recent.get("reportDate", [])
    filings = [
        {"accession": accs[i], "filing_date": fdates[i], "report_date": rdates[i]}
        for i, f in enumerate(forms)
        if f == "13F-HR"
    ]
    # recent[] is newest-first, but sort defensively by filing date.
    filings.sort(key=lambda x: x["filing_date"], reverse=True)
    return name, filings[:2]


def _find_infotable_xml(cik: int, accession: str) -> str | None:
    """Fetch a filing's info-table XML text. The cover page is primary_doc.xml;
    the holdings live in the other .xml in the filing directory."""
    acc_nodash = accession.replace("-", "")
    base = ARCHIVE_BASE.format(cik=cik, acc=acc_nodash)
    idx = _sec_get(f"{base}/index.json")
    if idx is None or idx.status_code != 200:
        return None
    items = idx.json().get("directory", {}).get("item", [])
    candidates = [
        it["name"] for it in items
        if it.get("name", "").lower().endswith(".xml")
        and it.get("name", "").lower() != "primary_doc.xml"
    ]
    # Prefer names that look like an info table.
    candidates.sort(key=lambda n: ("table" not in n.lower() and "info" not in n.lower(), n))
    for name in candidates:
        resp = _sec_get(f"{base}/{name}")
        if resp is not None and resp.status_code == 200 and "infoTable" in resp.text:
            return resp.text
    return None


def _parse_infotable(xml_text: str) -> dict[str, dict]:
    """Parse an information table into {cusip: {issuer, shares, value}}.

    Equities only: rows with a putCall value (options) are skipped so the
    share/flow numbers reflect actual stock positions. Multiple rows for the
    same CUSIP (different managers/discretion) are summed.
    """
    holdings: dict[str, dict] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return holdings
    for el in root.iter():
        if _strip_ns(el.tag) != "infoTable":
            continue
        row: dict[str, str] = {}
        shares = 0
        for child in el.iter():
            tag = _strip_ns(child.tag)
            if tag in ("nameOfIssuer", "cusip", "value", "putCall"):
                row[tag] = (child.text or "").strip()
            elif tag == "sshPrnamt":
                try:
                    shares = int(float((child.text or "0").strip()))
                except ValueError:
                    shares = 0
        if row.get("putCall"):  # skip option positions
            continue
        cusip = (row.get("cusip") or "").strip().upper()
        if not cusip:
            continue
        try:
            value = int(float(row.get("value") or 0))
        except ValueError:
            value = 0
        entry = holdings.setdefault(cusip, {"issuer": row.get("nameOfIssuer", ""), "shares": 0, "value": 0})
        entry["shares"] += shares
        entry["value"] += value
    return holdings


def fetch_investor_holdings(name: str, cik: int, manager: str = "") -> InvestorHoldings:
    """Fetch and parse an investor's latest two 13F-HR filings."""
    inv = InvestorHoldings(name=name, cik=cik, manager=manager)
    if os.environ.get(FIXTURE_ENV) == "1":
        return _fixture_investor(name, cik, manager)
    try:
        entity_name, filings = _latest_two_13f(cik)
    except Exception as exc:  # noqa: BLE001
        inv.status = "error"
        inv.detail = str(exc)[:120]
        return inv
    # Sanity-check the CIK actually belongs to who we think it does.
    if entity_name and not _name_matches(name, entity_name):
        inv.status = "mismatch"
        inv.detail = f"CIK resolves to '{entity_name}', expected '{name}'"
        return inv
    if not filings:
        inv.status = "error"
        inv.detail = "no 13F-HR filings found"
        return inv
    parsed: list[dict] = []
    for f in filings:
        xml_text = _find_infotable_xml(cik, f["accession"])
        parsed.append(_parse_infotable(xml_text) if xml_text else {})
    inv.latest = parsed[0]
    inv.as_of = filings[0]["report_date"]
    if len(filings) > 1:
        inv.prior = parsed[1]
        inv.prior_as_of = filings[1]["report_date"]
    if not inv.latest:
        inv.status = "error"
        inv.detail = "could not parse latest info table"
    return inv


def _name_matches(expected: str, actual: str) -> bool:
    """Loose match: first significant word of expected appears in actual."""
    exp = expected.lower().replace(",", " ").split()
    act = actual.lower()
    return any(len(w) > 3 and w in act for w in exp)


# --------------------------------------------------------------------------
# CUSIP -> ticker via OpenFIGI (cached)
# --------------------------------------------------------------------------

def _load_cusip_cache() -> dict[str, str]:
    if CUSIP_CACHE_PATH.exists():
        try:
            return json.loads(CUSIP_CACHE_PATH.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_cusip_cache(cache: dict[str, str]) -> None:
    try:
        CUSIP_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


def resolve_cusip_tickers(cusips: set[str]) -> dict[str, str]:
    """Map CUSIP -> ticker via OpenFIGI, caching results. Unmapped CUSIPs are
    cached as "" so we don't re-query them every run."""
    cache = _load_cusip_cache()
    todo = sorted(c for c in cusips if c not in cache)
    if not todo or requests is None or os.environ.get(FIXTURE_ENV) == "1":
        return cache
    key = os.environ.get("OPENFIGI_API_KEY")
    headers = {"Content-Type": "application/json"}
    batch = 100 if key else 10  # OpenFIGI: 100 jobs/req with key, 10 without
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        body = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
        try:
            resp = requests.post(OPENFIGI_URL, headers=headers, json=body, timeout=20)
            if resp.status_code != 200:
                break  # rate-limited or blocked; keep what we have
            results = resp.json()
        except Exception:  # noqa: BLE001
            break
        for c, r in zip(chunk, results):
            ticker = ""
            for d in (r.get("data") or []):
                t = d.get("ticker")
                if t:
                    ticker = t
                    break
            cache[c] = ticker
        time.sleep(2.6 if not key else 0.3)  # respect 25 req/min without key
    _save_cusip_cache(cache)
    return cache


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def _classify(prior_shares: int, latest_shares: int) -> str:
    if prior_shares == 0 and latest_shares > 0:
        return "NEW"
    if prior_shares > 0 and latest_shares == 0:
        return "EXIT"
    if latest_shares > prior_shares:
        return "ADD"
    if latest_shares < prior_shares:
        return "TRIM"
    return "HOLD"


def aggregate(investors: list[InvestorHoldings], cusip_to_ticker: dict[str, str],
              watchlist: set[str]) -> list[dict]:
    """Per-stock net flow across investors, comparing each investor's latest
    vs prior filing."""
    stocks: dict[str, dict] = {}
    for inv in investors:
        if inv.status not in ("ok",):
            continue
        cusips = set(inv.latest) | set(inv.prior)
        for cusip in cusips:
            cur = inv.latest.get(cusip, {})
            prv = inv.prior.get(cusip, {})
            cur_sh, prv_sh = cur.get("shares", 0), prv.get("shares", 0)
            cur_val, prv_val = cur.get("value", 0), prv.get("value", 0)
            action = _classify(prv_sh, cur_sh)
            if action == "HOLD":
                continue
            issuer = cur.get("issuer") or prv.get("issuer") or ""
            st = stocks.setdefault(cusip, {
                "cusip": cusip, "issuer": issuer,
                "ticker": cusip_to_ticker.get(cusip, ""),
                "buyers": 0, "sellers": 0, "net_investors": 0,
                "net_value": 0, "net_shares": 0, "detail": [],
            })
            if not st["issuer"]:
                st["issuer"] = issuer
            if action in ("NEW", "ADD"):
                st["buyers"] += 1
            else:
                st["sellers"] += 1
            st["net_shares"] += cur_sh - prv_sh
            st["net_value"] += cur_val - prv_val
            st["detail"].append({
                "investor": inv.name,
                "manager": inv.manager,
                "action": action,
                "shares_delta": cur_sh - prv_sh,
                "value_delta": cur_val - prv_val,
                "shares_now": cur_sh,
                "shares_prior": prv_sh,
            })
    out = []
    for st in stocks.values():
        st["net_investors"] = st["buyers"] - st["sellers"]
        ticker = st["ticker"].upper() if st["ticker"] else ""
        st["in_watchlist"] = bool(ticker and ticker in watchlist)
        st["detail"].sort(key=lambda d: abs(d["value_delta"]), reverse=True)
        out.append(st)
    out.sort(key=lambda s: (s["net_investors"], s["net_value"]), reverse=True)
    return out


def build_dataset(watchlist_tickers: set[str] | None = None) -> dict[str, Any]:
    """Top-level: load investors, fetch holdings, map CUSIPs, aggregate."""
    watchlist = {t.upper() for t in (watchlist_tickers or set())}
    config = json.loads(INVESTORS_PATH.read_text())
    investors: list[InvestorHoldings] = []
    for entry in config.get("investors", []):
        inv = fetch_investor_holdings(entry["name"], int(entry["cik"]), entry.get("manager", ""))
        print(f"  [13f] {inv.name}: {inv.status}"
              + (f" ({inv.detail})" if inv.detail else "")
              + (f" — {len(inv.latest)} holdings, as of {inv.as_of}" if inv.status == "ok" else ""))
        investors.append(inv)

    all_cusips: set[str] = set()
    for inv in investors:
        all_cusips |= set(inv.latest) | set(inv.prior)
    cusip_to_ticker = resolve_cusip_tickers(all_cusips)

    stocks = aggregate(investors, cusip_to_ticker, watchlist)
    ok = [i for i in investors if i.status == "ok"]
    latest_dates = [i.as_of for i in ok if i.as_of]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": max(latest_dates) if latest_dates else None,
        "investor_count": len(ok),
        "investors": [
            {"name": i.name, "manager": i.manager, "cik": i.cik, "status": i.status,
             "detail": i.detail, "as_of": i.as_of, "prior_as_of": i.prior_as_of,
             "holdings": len(i.latest)}
            for i in investors
        ],
        "stocks": stocks,
        "watchlist_size": len(watchlist),
        "cusips_mapped": sum(1 for v in cusip_to_ticker.values() if v),
        "cusips_total": len(all_cusips),
    }


# --------------------------------------------------------------------------
# Fixture mode (offline testing)
# --------------------------------------------------------------------------

def _fixture_investor(name: str, cik: int, manager: str = "") -> InvestorHoldings:
    inv = InvestorHoldings(name=name, cik=cik, manager=manager)
    latest_p = FIXTURE_DIR / f"{cik}_latest.xml"
    prior_p = FIXTURE_DIR / f"{cik}_prior.xml"
    if not latest_p.exists():
        inv.status = "error"
        inv.detail = "no fixture"
        return inv
    inv.latest = _parse_infotable(latest_p.read_text())
    inv.as_of = "2026-03-31"
    if prior_p.exists():
        inv.prior = _parse_infotable(prior_p.read_text())
        inv.prior_as_of = "2025-12-31"
    return inv
