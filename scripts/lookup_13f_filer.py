"""Find or check 13F filers on SEC EDGAR — for editing data/super_investors.json.

    python scripts/lookup_13f_filer.py "pershing square, greenlight, 1549575"

Each comma-separated term is either
* a CIK (digits): prints that entity's name and its recent 13F-HR filings, or
* a name: finds every EDGAR entity whose name contains all its words, and
  prints the ones that have filed a 13F-HR (most recent filer first).

For each filer it shows the quarters reported, the date each was filed and
how many days after quarter end that was, so you can tell a live filer from
one that stopped (e.g. a fund that moved its 13F to a new entity).

Run it from GitHub (Actions -> "13F filer lookup" -> Run workflow) if SEC
is not reachable from your machine.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import date

import requests

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# Every EDGAR entity name with its CIK, one "NAME:CIK:" per line.
CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
UA = os.environ.get("SEC_USER_AGENT") or "stock-mon 13F filer lookup"
MAX_MATCHES = 60      # per name term; a broader name needs more words
SHOW_FILINGS = 4


def _get(url: str, **kw) -> requests.Response:
    time.sleep(0.15)  # SEC fair access: well under 10 requests/s
    return requests.get(url, headers={"User-Agent": UA}, timeout=60, **kw)


def name_matches(terms: list[str]) -> dict[str, list[tuple[str, int]]]:
    """{term: [(entity name, cik)]} — one pass over EDGAR's ~1M-line name list."""
    wanted = {t: [w for w in re.split(r"\W+", t.upper()) if w] for t in terms}
    out: dict[str, dict[int, str]] = {t: {} for t in terms}
    with _get(CIK_LOOKUP_URL, stream=True) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            name, _, cik = raw.decode("latin-1").rstrip().rstrip(":").rpartition(":")
            if not cik.isdigit():
                continue
            upper = name.upper()
            for t, words in wanted.items():
                if all(w in upper for w in words):
                    out[t].setdefault(int(cik), name)
    return {t: sorted(((n, c) for c, n in m.items()), key=lambda x: x[1]) for t, m in out.items()}


def filings(cik: int) -> tuple[str, list[dict]]:
    resp = _get(SUBMISSIONS_URL.format(cik=cik))
    if resp.status_code != 200:
        return f"(HTTP {resp.status_code})", []
    data = resp.json()
    rec = data.get("filings", {}).get("recent", {})
    rows = [
        {"form": f, "filed": fd, "period": rd}
        for f, fd, rd in zip(rec.get("form", []), rec.get("filingDate", []), rec.get("reportDate", []))
        if f in ("13F-HR", "13F-HR/A", "13F-NT")
    ]
    return data.get("name") or "?", rows


def describe(cik: int, name_hint: str = "") -> tuple[str, str] | None:
    entity, rows = filings(cik)
    hr = [r for r in rows if r["form"] == "13F-HR"]
    if not hr and name_hint:
        return None
    lines = [f"CIK {cik:>8}  {entity}"]
    if not hr:
        lines.append("    no 13F-HR filings")
    for r in hr[:SHOW_FILINGS]:
        lag = ""
        try:
            lag = f"  (day {(date.fromisoformat(r['filed']) - date.fromisoformat(r['period'])).days})"
        except ValueError:
            pass
        lines.append(f"    {r['period']} filed {r['filed']}{lag}")
    others = sorted({r["form"] for r in rows if r["form"] != "13F-HR"})
    if others:
        lines.append(f"    also: {', '.join(others)}")
    return (hr[0]["filed"] if hr else ""), "\n".join(lines)


def main(argv: list[str]) -> int:
    terms = [t.strip() for t in ",".join(argv).split(",") if t.strip()]
    if not terms:
        print(__doc__)
        return 2
    by_name = name_matches([t for t in terms if not t.isdigit()]) if any(not t.isdigit() for t in terms) else {}
    for term in terms:
        print(f"\n=== {term}")
        if term.isdigit():
            print(describe(int(term))[1])
            continue
        matches = by_name[term]
        if len(matches) > MAX_MATCHES:
            print(f"{len(matches)} entities match — showing the first {MAX_MATCHES}; add a word to narrow it.")
            matches = matches[:MAX_MATCHES]
        found = [d for n, c in matches if (d := describe(c, n))]
        found.sort(reverse=True)  # most recent 13F-HR first
        for _, text in found:
            print(text)
        print(f"({len(found)} of {len(matches)} matching entities have filed a 13F-HR)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
