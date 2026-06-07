"""Insider transactions (SEC Form 4) for 13F-page stocks.

Form 4 reports buys/sells by a company's own officers and directors. An
open-market sale by the CEO/CFO can move a stock much like a 13F manager
trimming — arguably more, since they have the best view of their business.

We surface, per stock:
  - count of insider open-market BUYS (code P) vs SELLS (code S) in a window
  - net shares / net $ across those
  - a highlight of any C-suite (CEO/CFO/President/Chair) transactions

Scoped + budgeted like ticker_info: fetching for all ~7,700 13F stocks is
impractical, so the caller passes only the in-scope tickers (watchlist +
top-N by impact). Cached in data/insider_cache.json.

Transaction codes (Form 4): P = open-market purchase, S = open-market sale.
A (grant), M/X (option exercise), F (tax withholding), G (gift) are routine
and excluded — they're not discretionary market signals.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = REPO_ROOT / "data" / "insider_cache.json"
TICKER_CIK_PATH = REPO_ROOT / "data" / "ticker_cik_map.json"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
SEC_UA = "stock-mon/1.0 (contact: fredchan31@gmail.com)"

CACHE_TTL_SEC = 5 * 24 * 3600         # refresh roughly weekly
NEG_CACHE_TTL_SEC = 4 * 3600
LOOKBACK_DAYS = 120                   # how far back to count insider activity
MAX_FORM4_PER_CO = 12                 # cap filings parsed per company
DEFAULT_BUDGET_SEC = 300
THROTTLE_SEC = 0.12                   # SEC fair-access pacing

# Officer-title keywords that mark a "key management" transaction.
_CSUITE_RE = re.compile(
    r"\b(chief executive|ceo|chief financial|cfo|president|chair|"
    r"chief operating|coo|founder)\b", re.IGNORECASE)


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _sec_get(url: str):
    if requests is None:
        return None
    time.sleep(THROTTLE_SEC)
    try:
        return requests.get(url, headers={"User-Agent": SEC_UA}, timeout=20)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# ticker -> company CIK
# --------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_json(path: Path, data: dict) -> None:
    if not data:
        return
    try:
        path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
    except Exception as exc:  # noqa: BLE001
        print(f"  [13f] insider cache write failed: {type(exc).__name__}: {exc}")


def load_ticker_cik_map() -> dict[str, int]:
    """ticker (upper) -> company CIK, from SEC's company_tickers.json (cached)."""
    cached = _load_json(TICKER_CIK_PATH)
    if cached:
        return {k: int(v) for k, v in cached.items()}
    resp = _sec_get(SEC_TICKERS_URL)
    if resp is None or resp.status_code != 200:
        print("  [13f] insider: could not load company_tickers.json")
        return {}
    try:
        raw = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    # company_tickers.json is {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
    for row in raw.values():
        t = (row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if t and cik:
            out[t] = int(cik)
    _save_json(TICKER_CIK_PATH, out)
    print(f"  [13f] insider: loaded {len(out)} ticker->CIK mappings")
    return out


# --------------------------------------------------------------------------
# Form 4 fetch + parse
# --------------------------------------------------------------------------

def _recent_form4_accessions(cik: int) -> list[str]:
    resp = _sec_get(SUBMISSIONS_URL.format(cik=cik))
    if resp is None or resp.status_code != 200:
        return []
    try:
        recent = resp.json().get("filings", {}).get("recent", {})
    except Exception:  # noqa: BLE001
        return []
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    out = []
    for i, f in enumerate(forms):
        if f == "4" and i < len(dates) and dates[i] >= cutoff:
            out.append(accs[i])
        if len(out) >= MAX_FORM4_PER_CO:
            break
    return out


def _find_form4_xml(cik: int, accession: str) -> str | None:
    acc_nodash = accession.replace("-", "")
    base = ARCHIVE_BASE.format(cik=cik, acc=acc_nodash)
    idx = _sec_get(f"{base}/index.json")
    if idx is None or idx.status_code != 200:
        return None
    try:
        items = idx.json().get("directory", {}).get("item", [])
    except Exception:  # noqa: BLE001
        return None
    xmls = [it["name"] for it in items if it.get("name", "").lower().endswith(".xml")]
    # Form 4 primary doc usually contains "ownership"/"form4"/"doc4"; otherwise
    # take the first xml and verify content.
    xmls.sort(key=lambda n: ("4" not in n and "own" not in n.lower(), n))
    for name in xmls:
        resp = _sec_get(f"{base}/{name}")
        if resp is not None and resp.status_code == 200 and "ownershipDocument" in resp.text:
            return resp.text
    return None


def _parse_form4(xml_text: str) -> list[dict]:
    """Return a list of open-market transactions from a Form 4 doc.

    Each: {owner, title, is_csuite, code, shares, price, value, acquired}.
    Only P (purchase) and S (sale) non-derivative transactions are kept.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    def _txt(el, path_tags):
        cur = el
        for tag in path_tags:
            nxt = None
            for c in list(cur):
                if _strip_ns(c.tag) == tag:
                    nxt = c
                    break
            if nxt is None:
                return None
            cur = nxt
        return (cur.text or "").strip() if cur.text else None

    # Reporting owner name + officer title
    owner = title = None
    is_csuite = False
    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag == "rptOwnerName" and owner is None:
            owner = (el.text or "").strip()
        elif tag == "officerTitle" and title is None:
            title = (el.text or "").strip()
    if title and _CSUITE_RE.search(title):
        is_csuite = True

    txns: list[dict] = []
    for el in root.iter():
        if _strip_ns(el.tag) != "nonDerivativeTransaction":
            continue
        code = None
        shares = price = None
        acquired = None
        for sub in el.iter():
            st = _strip_ns(sub.tag)
            if st == "transactionCode":
                code = (sub.text or "").strip()
            elif st == "transactionShares":
                v = _txt(sub, ["value"]) or (sub.text or "")
                try:
                    shares = float(v)
                except (TypeError, ValueError):
                    shares = None
            elif st == "transactionPricePerShare":
                v = _txt(sub, ["value"]) or (sub.text or "")
                try:
                    price = float(v)
                except (TypeError, ValueError):
                    price = None
            elif st == "transactionAcquiredDisposedCode":
                acquired = _txt(sub, ["value"]) or (sub.text or "").strip()
        if code not in ("P", "S"):
            continue
        if shares is None:
            continue
        value = (shares * price) if price else None
        txns.append({
            "owner": owner or "?",
            "title": title or "",
            "is_csuite": is_csuite,
            "code": code,
            "shares": shares,
            "price": price,
            "value": value,
            "acquired": acquired,  # "A" acquired / "D" disposed
        })
    return txns


def _fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at", 0)
    ttl = NEG_CACHE_TTL_SEC if entry.get("unresolved") else CACHE_TTL_SEC
    return (time.time() - ts) < ttl


def _summarize(txns: list[dict]) -> dict:
    """Aggregate parsed transactions into the per-stock insider summary."""
    buys = [t for t in txns if t["code"] == "P"]
    sells = [t for t in txns if t["code"] == "S"]
    net_shares = sum(t["shares"] for t in buys) - sum(t["shares"] for t in sells)
    net_value = (sum(t["value"] or 0 for t in buys)
                 - sum(t["value"] or 0 for t in sells))
    csuite = [t for t in txns if t["is_csuite"]]
    # Build a short, de-duplicated highlight of C-suite moves.
    highlights = []
    seen = set()
    for t in sorted(csuite, key=lambda x: (x["value"] or 0), reverse=True):
        key = (t["owner"], t["code"])
        if key in seen:
            continue
        seen.add(key)
        verb = "bought" if t["code"] == "P" else "sold"
        highlights.append({
            "owner": t["owner"], "title": t["title"],
            "verb": verb, "shares": t["shares"], "value": t["value"],
        })
    return {
        "buys": len(buys),
        "sells": len(sells),
        "net_shares": net_shares,
        "net_value": net_value,
        "csuite_highlights": highlights[:4],
        "fetched_at": time.time(),
    }


def fetch_insider_summaries(
    tickers: list[str],
    budget_sec: float = DEFAULT_BUDGET_SEC,
) -> dict[str, dict]:
    """Return {ticker: insider_summary} for as many in-scope tickers as the
    budget allows. Cached; negative entries expire fast so transient SEC
    failures self-heal."""
    if requests is None:
        print("  [13f] insider: requests not installed — skipping")
        return {}
    cik_map = load_ticker_cik_map()
    if not cik_map:
        return {}
    cache = _load_json(CACHE_PATH)
    result: dict[str, dict] = {}
    todo: list[str] = []
    for t in tickers:
        t = (t or "").upper()
        if not t:
            continue
        entry = cache.get(t)
        if entry and _fresh(entry):
            if not entry.get("unresolved"):
                result[t] = entry
        else:
            todo.append(t)

    deadline = time.monotonic() + budget_sec
    new = failed = skipped = 0
    for i, t in enumerate(todo):
        if time.monotonic() > deadline:
            skipped = len(todo) - i
            break
        cik = cik_map.get(t)
        if not cik:
            cache[t] = {"fetched_at": time.time(), "unresolved": True}
            failed += 1
            continue
        accs = _recent_form4_accessions(cik)
        txns: list[dict] = []
        for acc in accs:
            xml = _find_form4_xml(cik, acc)
            if xml:
                txns.extend(_parse_form4(xml))
        if not accs:
            # No recent Form 4s is a valid result (quiet insiders), not a failure.
            summary = _summarize([])
            summary["empty"] = True
            cache[t] = summary
            result[t] = summary
            new += 1
        else:
            summary = _summarize(txns)
            cache[t] = summary
            result[t] = summary
            new += 1
        if (i + 1) % 10 == 0:
            _save_json(CACHE_PATH, cache)
    _save_json(CACHE_PATH, cache)
    print(f"  [13f] insider: {len(result)} usable | {new} new, "
          f"{failed} no-cik, {skipped} skipped (budget)")
    return result
