"""Render the 13F net-flow dataset as a visual dashboard (static HTML).

The page ships one compact JSON payload (every stock x every investor move)
and a small vanilla-JS app that aggregates it client-side, so every chart,
tile and table re-renders against the same filter row:

  * investors — stock-pickers (default) / all / one specific investor
  * stocks    — all / only your holdings & watchlist
  * measure   — conviction (% of portfolio) / estimated $ flow / # investors

The previous version server-rendered all ~8,000 rows (with their hidden
detail panels) into a 15 MB page; the payload is ~1.5 MB and the tables
render 25 rows at a time.

Company snapshot cards (yfinance / Wikipedia / insider) are rendered here in
Python — every field escaped — and embedded as HTML strings; everything else
(investor names, tickers, issuer names) is inserted client-side with
textContent, never innerHTML.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from typing import Any

from ..data_layer.holdings_13f import quarter_label
from .timefmt import fmt_et

ACTIONS = ["NEW", "ADD", "TRIM", "EXIT"]  # index = action code in the payload


# --------------------------------------------------------------------------
# Small formatters (used by the server-rendered company cards)
# --------------------------------------------------------------------------

def _fmt_money(v: float) -> str:
    sign = "-" if v < 0 else "+"
    a = abs(v)
    if a >= 1_000_000_000:
        return f"{sign}${a/1_000_000_000:.1f}B"
    if a >= 1_000_000:
        return f"{sign}${a/1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}${a/1_000:.0f}K"
    return f"{sign}${a:.0f}"


def _cls(v: float) -> str:
    return "pos" if v > 0 else "neg" if v < 0 else "flat"


def _fmt_cap(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _fmt_num(v, suffix: str = "") -> str:
    try:
        return f"{float(v):.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _snapshot_when(entry: dict) -> str:
    ts = entry.get("fetched_at")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return ""
    return fmt_et(datetime.fromtimestamp(ts, tz=timezone.utc))


def _safe_url(url: str) -> str:
    """Only allow http(s) links from third-party data into href=."""
    u = (url or "").strip()
    return u if u.lower().startswith(("https://", "http://")) else ""


# --------------------------------------------------------------------------
# Server-rendered company cards (embedded as escaped HTML strings)
# --------------------------------------------------------------------------

def _render_info_card(stock: dict) -> str:
    """Company snapshot: yfinance first, Wikipedia as fallback, else ''.
    Outbound search links are added client-side for every stock."""
    info = stock.get("info")
    wiki = stock.get("wiki")
    ticker = stock.get("ticker") or ""
    issuer = stock.get("issuer") or ""

    if info:
        cur = info.get("currency") or ""
        price = info.get("price")
        price_txt = f"{price:,.2f} {cur}".strip() if isinstance(price, (int, float)) else "—"
        chg = info.get("change_pct")
        chg_html = (f' <span class="{_cls(chg)}">{chg:+.2f}%</span>'
                    if isinstance(chg, (int, float)) else "")
        sector = " · ".join(x for x in (info.get("sector"), info.get("industry")) if x)
        grid = [
            f'<div>Mkt cap <span>{_fmt_cap(info.get("market_cap"))}</span></div>',
            f'<div>P/E <span>{_fmt_num(info.get("pe"))}</span></div>',
        ]
        lo, hi = info.get("wk_low"), info.get("wk_high")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            grid.append(f'<div>52wk <span>{lo:,.2f}–{hi:,.2f}</span></div>')
        summary = info.get("summary") or ""
        summary_html = f'<p class="info-summary">{escape(summary)}</p>' if summary else ""
        website = _safe_url(info.get("website") or "")
        site_html = (f' &middot; <a class="info-link" href="{escape(website)}" '
                     f'target="_blank" rel="noopener">website ↗</a>') if website else ""
        when = _snapshot_when(info)
        name = info.get("name") or issuer or ticker
        return (
            f'<div class="info-card">'
            f'<div class="info-head"><span class="info-name">{escape(name)}</span>'
            f'<span class="info-sector">{escape(sector)}</span>'
            f'<span class="info-price">{escape(price_txt)}{chg_html}</span></div>'
            f'<div class="info-grid">{"".join(grid)}</div>'
            f'{summary_html}'
            f'<div class="info-source">Yahoo Finance snapshot'
            f'{" from " + escape(when) if when else ""}{site_html}</div>'
            f'</div>'
        )

    if wiki:
        title = wiki.get("title") or issuer or ticker
        wiki_url = _safe_url(wiki.get("url") or "")
        wiki_link = (f' &middot; <a class="info-link" href="{escape(wiki_url)}" '
                     f'target="_blank" rel="noopener">read on Wikipedia ↗</a>') if wiki_url else ""
        return (
            f'<div class="info-card">'
            f'<div class="info-head"><span class="info-name">{escape(title)}</span>'
            f'<span class="info-sector">{escape(wiki.get("description") or "")}</span></div>'
            f'<p class="info-summary">{escape(wiki.get("extract", ""))}</p>'
            f'<div class="info-source">Background from Wikipedia{wiki_link} '
            f'&middot; no price available for this symbol</div>'
            f'</div>'
        )
    return ""


def _render_insider(stock: dict) -> str:
    """Insider (Form 4) open-market buys/sells by company officers."""
    ins = stock.get("insider")
    if not ins:
        return ""
    buys, sells = ins.get("buys", 0), ins.get("sells", 0)
    if buys == 0 and sells == 0:
        return ""
    net_val = ins.get("net_value") or 0
    rows = []
    for h in ins.get("csuite_highlights", []):
        cls = "ins-buy" if h.get("verb") == "bought" else "ins-sell"
        val = f' (~{_fmt_money(float(h["value"]))[1:]})' if h.get("value") else ""
        sh = f'{int(h["shares"]):,}' if h.get("shares") else "?"
        title = f'<span class="ins-title">{escape(h["title"])}</span>' if h.get("title") else ""
        rows.append(
            f'<div class="insider-item"><span class="ins-who">{escape(h.get("owner", ""))}</span>'
            f'{title} <span class="{cls}">{escape(h.get("verb", ""))} {sh} sh{val}</span></div>'
        )
    return (f'<div class="insider-card">'
            f'<div class="sub-label">Company insiders · SEC Form 4, last ~120 days</div>'
            f'<div class="insider-head"><span class="ins-buy">{buys} buy</span> / '
            f'<span class="ins-sell">{sells} sell</span> &middot; net {_fmt_money(float(net_val))}</div>'
            f'{"".join(rows)}</div>')


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------

def build_payload(dataset: dict[str, Any]) -> dict[str, Any]:
    """Compact, client-side-aggregatable view of the dataset.

    inv:  [{n,m,s,st,status,ok,det,q,pq,h,tv}]  (index = investor id)
    stk:  [{c,t,n,k,sec,d:[[inv_id, action_code, shares_delta, flow, pct]]}]
    cards: {cusip: escaped HTML snapshot card}
    """
    investors = dataset.get("investors", [])
    idx = {i.get("name"): n for n, i in enumerate(investors)}
    inv_out = []
    for i in investors:
        status = i.get("status", "error")
        inv_out.append({
            "n": i.get("name", ""),
            "m": i.get("manager") or i.get("name", ""),
            "s": i.get("short") or (i.get("manager") or i.get("name", "")).split(" (")[0].split()[-1],
            "st": i.get("style") or "picker",
            "status": status,
            "ok": status == "ok",
            "det": i.get("detail") or "",
            "q": quarter_label(i.get("as_of")),
            "pq": quarter_label(i.get("prior_as_of")),
            "h": i.get("holdings", 0),
            "tv": i.get("total_value") or 0,
        })

    act_code = {a: n for n, a in enumerate(ACTIONS)}
    stk_out, cards = [], {}
    for s in dataset.get("stocks", []):
        moves = []
        for d in s.get("detail", []):
            ii = idx.get(d.get("investor"))
            ac = act_code.get(d.get("action"))
            if ii is None or ac is None:
                continue
            flow = d.get("flow")
            if flow is None:  # archive from before flow estimates existed
                flow = d.get("value_delta", 0)
            # pct to 4 significant figures, not fixed decimals: a 1e-5 % trim
            # must stay negative, not round to 0.
            moves.append([ii, ac, int(d.get("shares_delta", 0)), int(flow),
                          float(f"{float(d.get('pct', 0.0)):.4g}")])
        if not moves:
            continue
        cat = s.get("category") or ""
        info = s.get("info") or {}
        stk_out.append({
            "c": s.get("cusip", ""),
            "t": s.get("ticker") or "",
            "n": s.get("issuer") or "",
            "k": "H" if cat == "HOLDING" else "W" if cat == "WATCHLIST" else "",
            "sec": info.get("sector") or "",
            "d": moves,
        })
        card = _render_info_card(s) + _render_insider(s)
        if card:
            cards[s.get("cusip", "")] = card
    return {"inv": inv_out, "stk": stk_out, "cards": cards}


def _payload_json(payload: dict) -> str:
    """JSON safe to embed inside <script type="application/json">."""
    s = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return s.replace("</", "<\\/").replace("<!--", "\\u003c!--")


def _quarter_mix(dataset: dict) -> str:
    counts: dict[str, int] = {}
    for i in dataset.get("investors", []):
        if i.get("status") == "ok":
            q = quarter_label(i.get("as_of"))
            if q:
                counts[q] = counts.get(q, 0) + 1
    order = sorted(counts, key=lambda q: (q[-2:], q[1]), reverse=True)
    return " · ".join(f"{q} ×{counts[q]}" for q in order)


# --------------------------------------------------------------------------
# Static shell
# --------------------------------------------------------------------------

CSS = """
:root{
  --bg:#0a0e17; --card:#0f1923; --card2:#0c141d; --line:#1e2d3d; --line2:#152233;
  --ink:#e8e6e3; --ink2:#8899aa; --muted:#6b7d8e; --faint:#4a5568; --accent:#ff9800;
  /* Marks: validated buy/sell pair on the card surface (CVD dE 7.1 — legal
     with the secondary encodings used throughout: side of the axis, +/- sign
     and the N/+/-/x glyphs in the heatmap). */
  --buy:#0ca30c; --sell:#e66767; --track:#152233; --axis:#2c3e50;
  /* Delta text (same tokens as the daily briefing). */
  --up:#00e676; --down:#ff6b6b;
  color-scheme: dark;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"SF Mono","Cascadia Mono","Fira Code","Consolas",monospace;
  max-width:1040px;margin:0 auto;padding:1rem;color:var(--ink);background:var(--bg);line-height:1.45}
header{margin-bottom:.8rem;border-bottom:1px solid var(--line);padding-bottom:.7rem}
h1{font-size:1.1rem;color:var(--accent);letter-spacing:.06em;text-transform:uppercase}
.meta{color:var(--muted);font-size:.78rem;margin-top:.15rem}
.meta strong{color:var(--ink2);font-weight:600}
nav{margin-top:.35rem;font-size:.78rem}
nav a{color:var(--accent);text-decoration:none;margin-right:.75rem;border-bottom:1px dotted #ff980050}
.strip{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.45rem}
.chip{font:inherit;font-size:.68rem;border:1px solid var(--line);background:var(--card);color:var(--ink2);
  border-radius:3px;padding:.12rem .4rem;white-space:nowrap;cursor:pointer}
.chip .q{color:var(--muted);margin-left:.25rem}
.chip.ok .ic{color:var(--up)} .chip.stale .ic{color:#ffb74d} .chip.err .ic{color:var(--down)}
.chip.stale,.chip.err{cursor:help;opacity:.85}
.chip.sel{border-color:var(--accent);color:var(--ink)}
.chip.dim{opacity:.45}
/* Filter row: one row above everything it scopes. */
.filters{display:flex;flex-wrap:wrap;gap:.5rem 1.1rem;align-items:center;margin:.7rem 0 .3rem;font-size:.74rem}
.fgroup{display:flex;align-items:center;gap:.3rem;flex-wrap:wrap}
.flabel{color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-size:.64rem;margin-right:.1rem}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:4px;overflow:hidden}
.seg button{font:inherit;font-size:.72rem;background:var(--card);color:var(--ink2);border:0;
  padding:.3rem .55rem;cursor:pointer;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:#1a2733;color:var(--ink);box-shadow:inset 0 -2px 0 var(--accent)}
select{font:inherit;font-size:.72rem;background:var(--card);color:var(--ink2);border:1px solid var(--line);
  border-radius:4px;padding:.28rem .4rem;max-width:15rem}
.help{color:var(--muted);font-size:.7rem;margin:.1rem 0 .6rem;max-width:62rem}
.help b{color:var(--ink2);font-weight:600}
/* KPI tiles */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;margin-bottom:.6rem}
.tile{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:.55rem .65rem}
.tile .lbl{color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.05em}
.tile .val{font-size:1.25rem;font-weight:700;margin-top:.1rem;color:var(--ink)}
.tile .sub{color:var(--ink2);font-size:.68rem;margin-top:.1rem}
/* Cards */
.card{background:var(--card);border:1px solid var(--line);border-radius:4px;margin-bottom:.6rem}
.card>h2{font-size:.78rem;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
  padding:.5rem .65rem;border-bottom:1px solid var(--line);color:var(--ink);display:flex;
  justify-content:space-between;align-items:baseline;gap:.5rem;flex-wrap:wrap}
.card>h2 .hint{font-size:.66rem;color:var(--muted);text-transform:none;letter-spacing:0;font-weight:400}
.card .body{padding:.55rem .65rem}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}
.grid2>.card{margin-bottom:0}
.wrap-gap{margin-bottom:.6rem}
.empty{color:var(--muted);font-style:italic;font-size:.74rem;padding:.4rem 0}
/* Takeaways */
.takeaways{list-style:none;font-size:.78rem;line-height:1.5}
.takeaways li{padding:.28rem 0 .28rem 1rem;border-bottom:1px dotted var(--line2);position:relative}
.takeaways li:last-child{border-bottom:0}
.takeaways li::before{content:"›";position:absolute;left:.15rem;color:var(--accent)}
.takeaways .dim{color:var(--muted)}
.tk{font:inherit;font-weight:700;color:var(--ink);background:none;border:0;border-bottom:1px dotted var(--accent);
  cursor:pointer;padding:0}
.tk:hover,.tk:focus-visible{color:var(--accent);outline:none}
/* Ranked bar lists (most bought / most sold) */
.bars{display:flex;flex-direction:column}
.brow{font:inherit;color:inherit;background:none;border:0;text-align:left;cursor:pointer;width:100%;
  display:grid;grid-template-columns:6.4rem 1fr 4.6rem;align-items:center;gap:.45rem;
  min-height:26px;padding:1px .2rem;border-radius:3px}
.brow:hover,.brow:focus-visible{background:#132232;outline:none}
.brow .lab{font-size:.74rem;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.brow .lab .cnt{display:block;font-weight:400;color:var(--muted);font-size:.62rem;line-height:1.1}
.brow .val{font-size:.72rem;text-align:right;color:var(--ink2);white-space:nowrap}
.track{position:relative;height:14px}
.bar{position:absolute;top:0;height:14px;min-width:2px}
.bar.buy{left:0;background:var(--buy);border-radius:0 4px 4px 0}
.bar.sell{left:0;background:var(--sell);border-radius:0 4px 4px 0}
/* Diverging rows: sell bar grows left of the axis, buy bar right of it. */
.drow{font:inherit;color:inherit;background:none;border:0;text-align:left;cursor:pointer;width:100%;
  display:grid;grid-template-columns:7rem 1fr 1fr 5rem;align-items:center;gap:0;min-height:26px;
  padding:1px .2rem;border-radius:3px}
.drow:hover,.drow:focus-visible{background:#132232;outline:none}
.drow.static{cursor:default}
.drow.static:hover{background:none}
.drow .lab{font-size:.74rem;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:.4rem}
.drow .lab .cnt{display:block;font-weight:400;color:var(--muted);font-size:.62rem;line-height:1.1}
.drow .val{font-size:.72rem;text-align:right;color:var(--ink2);white-space:nowrap;padding-left:.4rem}
.half{position:relative;height:14px}
.half.l{border-right:1px solid var(--axis)}
.half.l .bar{left:auto;right:0;border-radius:4px 0 0 4px;background:var(--sell)}
.half.r .bar{left:0;border-radius:0 4px 4px 0;background:var(--buy)}
.half .num{position:absolute;top:-1px;font-size:.6rem;color:var(--muted);white-space:nowrap;line-height:16px}
.half.l .num{right:calc(var(--w) + 4px)}
.half.r .num{left:calc(var(--w) + 4px)}
.half.l .num.in{right:auto;left:calc(100% - var(--w) + 4px);color:#fff;font-weight:700}
.half.r .num.in{left:auto;right:calc(100% - var(--w) + 4px);color:#fff;font-weight:700}
.axis-head{display:grid;grid-template-columns:7rem 1fr 1fr 5rem;font-size:.6rem;color:var(--muted);
  text-transform:uppercase;letter-spacing:.05em;padding:0 .2rem .2rem}
.axis-head .l{text-align:right;padding-right:.4rem}
.axis-head .r{padding-left:.4rem}
.axis-head .v{text-align:right}
/* Investor rows */
.irow{font:inherit;color:inherit;background:none;border:0;text-align:left;cursor:pointer;width:100%;
  display:grid;grid-template-columns:9.5rem 1fr 1fr 5rem;align-items:center;min-height:30px;
  padding:2px .2rem;border-radius:3px;border-bottom:1px dotted var(--line2)}
.irow:hover,.irow:focus-visible{background:#132232;outline:none}
.irow.sel{background:#1a2733}
.irow .lab{font-size:.72rem;overflow:hidden;padding-right:.4rem}
.irow .lab b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.irow .lab .q{color:var(--muted);font-size:.6rem}
.irow .val{font-size:.7rem;text-align:right;color:var(--ink2);white-space:nowrap;padding-left:.4rem}
.irow .acts{grid-column:1/-1;font-size:.64rem;color:var(--muted);padding:.1rem 0 .15rem}
.irow .acts b{color:var(--ink2);font-weight:600}
/* Heatmap */
.hm{display:grid;gap:2px;align-items:stretch;font-size:.66rem}
.hm .colh{writing-mode:vertical-rl;transform:rotate(180deg);color:var(--ink2);white-space:nowrap;
  font-size:.62rem;padding:.2rem 0;justify-self:center;align-self:end}
.hm .rowh{font:inherit;font-size:.7rem;font-weight:700;color:var(--ink);background:none;border:0;
  text-align:left;cursor:pointer;padding:0 .3rem 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hm .rowh:hover,.hm .rowh:focus-visible{color:var(--accent);outline:none}
.hm .rowh .cnt{color:var(--muted);font-weight:400;font-size:.6rem;margin-left:.25rem}
.cell{height:22px;border-radius:3px;background:var(--line2);display:flex;align-items:center;
  justify-content:center;font-weight:700;font-size:.66rem;line-height:1}
.cell.a0{background:var(--buy);color:#fff}
.cell.a1{background:rgba(12,163,12,.42);color:var(--ink)}
.cell.a2{background:rgba(230,103,103,.42);color:var(--ink)}
.cell.a3{background:var(--sell);color:#fff}
.cell[tabindex]{cursor:default}
.cell[tabindex]:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.legend{display:flex;flex-wrap:wrap;gap:.3rem .9rem;font-size:.64rem;color:var(--ink2);margin-top:.5rem}
.legend span{display:inline-flex;align-items:center;gap:.3rem}
.legend i{display:inline-flex;width:16px;height:14px;border-radius:3px;align-items:center;justify-content:center;
  font-style:normal;font-weight:700;font-size:.58rem}
/* Tooltip */
#tip{position:fixed;z-index:50;pointer-events:none;background:#16222f;border:1px solid #2c3e50;border-radius:4px;
  padding:.4rem .55rem;font-size:.7rem;max-width:20rem;box-shadow:0 4px 14px rgba(0,0,0,.45);display:none}
#tip .tv{font-weight:700;color:var(--ink);font-size:.78rem}
#tip .tl{color:var(--ink2)}
#tip .tr{display:flex;gap:.4rem;align-items:baseline;margin-top:.12rem;color:var(--ink2)}
#tip .key{display:inline-block;width:10px;height:2px;border-radius:1px;transform:translateY(-3px)}
/* Tables */
.tools{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:.2rem 0 .5rem;font-size:.72rem}
.tools input{font:inherit;font-size:.74rem;background:var(--card);color:var(--ink);border:1px solid var(--line);
  border-radius:4px;padding:.3rem .5rem;width:14rem;max-width:100%}
.tools .muted{color:var(--muted)}
.section{background:var(--card);border:1px solid var(--line);border-radius:4px;margin:.6rem 0}
.section>h3{padding:.5rem .65rem;font-size:.78rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase;
  cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center}
.section.buy>h3{background:#0a1a0f;color:#66bb6a} .section.sell>h3{background:#1a0a0a;color:#ef5350}
.section.mixed>h3{background:#121a24;color:var(--ink2)}
.section>h3 .n{color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0}
.section.collapsed .tbl,.section.collapsed .more{display:none}
.caret{font-size:.7rem;transition:transform .15s} .section.collapsed .caret{transform:rotate(-90deg)}
table.tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:.78rem}
.tbl thead th{text-align:right;padding:.45rem .45rem;background:var(--card);border-bottom:1px solid var(--line);
  color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.05em;cursor:pointer;
  white-space:nowrap;position:sticky;top:0;z-index:5;font-weight:600}
.tbl thead th:first-child{text-align:left}
.tbl thead th .short{display:none}
#sectors .drow,#sectors .axis-head{grid-template-columns:8.8rem 1fr 1fr 4.8rem}
.irow{grid-template-columns:12rem 1fr 1fr 5rem}
.tbl thead th.sorted{color:var(--ink)}
.tbl thead th.sorted::after{content:" \\25BC";color:var(--accent)}
.tbl thead th.sorted.asc::after{content:" \\25B2"}
.tbl td{padding:.38rem .45rem;border-bottom:1px solid var(--line2);text-align:right;vertical-align:top;
  font-variant-numeric:tabular-nums}
.tbl td:first-child{text-align:left}
.tbl tr.srow{cursor:pointer}
.tbl tr.srow:hover{background:#132232}
.tbl tr.srow.open{background:#132232}
.tkr{font-weight:700;color:#fff}
.iss{color:var(--muted);font-size:.68rem;display:block;max-width:22rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sm{color:var(--muted);font-size:.66rem;display:block}
.pos{color:var(--up)} .neg{color:var(--down)} .flat{color:var(--muted)}
.tag{display:inline-block;font-size:.56rem;font-weight:700;width:1rem;text-align:center;border-radius:3px;
  padding:.04rem 0;margin-left:.3rem;vertical-align:middle;letter-spacing:.03em}
.tag.H{background:#1a3a2a;color:#69f0ae;border:1px solid #2e7d53}
.tag.W{background:#1a2a3a;color:#42a5f5;border:1px solid #1565c0}
.drowx td{background:var(--card2);padding:.55rem .75rem;text-align:left}
.more{padding:.45rem .65rem;display:flex;gap:.5rem;align-items:center;font-size:.7rem;color:var(--muted)}
.btn{font:inherit;font-size:.7rem;background:#132232;color:var(--ink2);border:1px solid var(--line);
  border-radius:3px;padding:.25rem .6rem;cursor:pointer}
.btn:hover{color:var(--ink)}
/* Detail panel */
.sub-label{color:var(--faint);font-size:.62rem;text-transform:uppercase;letter-spacing:.05em;margin:.1rem 0 .25rem}
.info-card,.insider-card{border-bottom:1px solid var(--line);margin-bottom:.5rem;padding-bottom:.5rem}
.info-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:.4rem .6rem}
.info-name{color:#fff;font-weight:700;font-size:.82rem}
.info-sector{color:var(--muted);font-size:.7rem}
.info-price{font-weight:700;font-size:.82rem}
.info-grid{display:flex;flex-wrap:wrap;gap:.2rem 1.2rem;margin-top:.35rem}
.info-grid div{font-size:.72rem;color:var(--ink2)} .info-grid span{color:var(--ink)}
.info-summary{color:var(--ink2);font-size:.72rem;margin-top:.35rem;line-height:1.45}
.info-source{color:var(--faint);font-size:.65rem;margin-top:.3rem}
.info-link{color:var(--accent);font-size:.7rem;text-decoration:none}
.info-link:hover{text-decoration:underline}
.links{display:flex;flex-wrap:wrap;gap:.25rem .7rem;margin:.1rem 0 .5rem}
.insider-head{font-size:.72rem;color:var(--ink2);margin-bottom:.2rem}
.ins-buy{color:#69f0ae;font-weight:600} .ins-sell{color:#ff8a80;font-weight:600}
.insider-item{font-size:.72rem;padding:.12rem 0}
.ins-who{color:var(--ink);font-weight:600} .ins-title{color:var(--muted);font-size:.68rem;margin-left:.3rem}
.mv{display:grid;grid-template-columns:3.2rem 1fr auto auto auto;gap:.2rem .8rem;font-size:.72rem;
  padding:.2rem 0;border-bottom:1px dotted var(--line2);align-items:baseline}
.mv:last-child{border-bottom:0}
.mv .a{font-weight:700} .a0{color:var(--up)} .a1{color:#69f0ae} .a2{color:#ffb74d} .a3{color:var(--down)}
.mv .who b{color:#fff} .mv .who span{color:var(--muted);font-size:.66rem;margin-left:.3rem}
.mv .r{text-align:right;white-space:nowrap}
footer{color:#3a4a5a;font-size:.66rem;margin-top:1rem;border-top:1px solid var(--line);padding-top:.5rem;line-height:1.5}
footer p{margin-bottom:.3rem}
@media (max-width:760px){
  body{padding:.6rem}
  .kpis{grid-template-columns:1fr 1fr}
  .grid2{grid-template-columns:1fr}
  .brow{grid-template-columns:5.4rem 1fr 4.2rem}
  .drow,.axis-head{grid-template-columns:5.2rem 1fr 1fr 4.2rem}
  #sectors .drow,#sectors .axis-head{grid-template-columns:6.4rem 1fr 1fr 4.2rem}
  .irow{grid-template-columns:7.4rem 1fr 1fr 4.2rem}
  .tbl{font-size:.72rem}
  .tbl td{padding:.34rem .28rem}
  .tbl thead th{padding:.4rem .28rem;font-size:.6rem}
  .tbl thead th .full{display:none}
  .tbl thead th .short{display:inline}
  .tbl .hide-sm{display:none}
  .mv{grid-template-columns:2.8rem 1fr auto auto}
  .mv .r.pct{display:none}
  .iss{max-width:8.5rem}
  .seg button{padding:.3rem .45rem}
}
"""

JS = r"""
(function(){
'use strict';
var D = JSON.parse(document.getElementById('payload').textContent);
var INV = D.inv, STK = D.stk, CARDS = D.cards || {};
var ACT = ['NEW','ADD','TRIM','EXIT'];
var GLYPH = ['N','+','−','×'];
var MEAS = {
  conv: {name:'Conviction', unit:'% of portfolio'},
  flow: {name:'Est. $ flow', unit:'dollars'},
  count:{name:'# investors', unit:'investors'}
};
var st = {group:'picker', inv:-1, scope:'all', measure:'conv', q:'',
          page:{buy:25,sell:25,mixed:25}, sort:{buy:null,sell:null,mixed:null}, open:null};
// Restore the viewer's last view — only values this version understands, so a
// stale or hand-edited preference can never break the page.
var ALLOWED = {group:['picker','all'], scope:['all','mine'], measure:['conv','flow','count']};
try {
  var saved = JSON.parse(localStorage.getItem('13f-view') || '{}') || {};
  Object.keys(ALLOWED).forEach(function(k){ if (ALLOWED[k].indexOf(saved[k]) >= 0) st[k] = saved[k]; });
} catch (e) {}
function resetPaging(){ st.page = {buy:25, sell:25, mixed:25}; }
function persist(){ try { localStorage.setItem('13f-view', JSON.stringify({group:st.group, scope:st.scope, measure:st.measure})); } catch (e) {} }

/* ---------- helpers ---------- */
function $(id){ return document.getElementById(id); }
function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function clear(n){ while (n.firstChild) n.removeChild(n.firstChild); return n; }
var MINUS = '−';
function sgn(v){ return v > 0 ? '+' : v < 0 ? MINUS : ''; }
function money(v, signed){
  var a = Math.abs(v), s = signed === false ? '' : sgn(v);
  if (a >= 1e9) return s + '$' + (a/1e9).toFixed(a >= 1e11 ? 0 : 1) + 'B';
  if (a >= 1e6) return s + '$' + (a/1e6).toFixed(a >= 1e8 ? 0 : 1) + 'M';
  if (a >= 1e3) return s + '$' + Math.round(a/1e3) + 'K';
  return s + '$' + Math.round(a);
}
function pct(v, signed){
  var a = Math.abs(v), s = signed === false ? '' : sgn(v);
  if (a > 0 && a < 0.001) return s + '<0.001%';
  return s + (a >= 10 ? a.toFixed(1) : a >= 0.1 ? a.toFixed(2) : a > 0 ? a.toFixed(3) : '0') + '%';
}
function shares(v){ var a = Math.abs(v); var t = a >= 1e9 ? (a/1e9).toFixed(1)+'B' : a >= 1e6 ? (a/1e6).toFixed(1)+'M' : a >= 1e4 ? Math.round(a/1e3)+'K' : a.toLocaleString('en-US'); return sgn(v) + t; }
function mval(r){ return st.measure === 'conv' ? r.conv : st.measure === 'flow' ? r.flow : r.ni; }
function fmtM(v, signed){
  if (st.measure === 'conv') return pct(v, signed);
  if (st.measure === 'flow') return money(v, signed);
  var a = Math.abs(v); return (signed === false ? '' : sgn(v)) + a + (a === 1 ? ' investor' : ' investors');
}
// Prose form: "+15.3% of portfolios" reads unambiguously in a sentence.
function fmtMlong(v){ return st.measure === 'conv' ? pct(v) + ' of portfolios, summed' : fmtM(v); }
function cmpDesc(a, b){ return (mval(b) - mval(a)) || (b.conv - a.conv) || (b.flow - a.flow); }
function label(s){ return s.t || (s.n.length > 14 ? s.n.slice(0, 13) + '…' : s.n); }
function cls(v){ return v > 0 ? 'pos' : v < 0 ? 'neg' : 'flat'; }
function invName(i){ return INV[i].m; }
function invShort(i){ return INV[i].s; }
function qkey(q){ return (q || '').slice(3) + (q || '').charAt(1); }  // "Q2'26" -> "262"
var SEC_SHORT = {'Communication Services':'Communication', 'Consumer Cyclical':'Cons. cyclical',
  'Consumer Defensive':'Cons. defensive', 'Financial Services':'Financials', 'Basic Materials':'Materials'};

/* ---------- aggregation ---------- */
function activeSet(){
  var s = {};
  if (st.inv >= 0) { s[st.inv] = 1; return s; }
  INV.forEach(function(v, i){ if (v.ok && (st.group === 'all' || v.st === 'picker')) s[i] = 1; });
  return s;
}
function compute(){
  var act = activeSet(), rows = [];
  for (var i = 0; i < STK.length; i++){
    var s = STK[i];
    if (st.scope === 'mine' && !s.k) continue;
    var r = null;
    for (var j = 0; j < s.d.length; j++){
      var d = s.d[j];
      if (!act[d[0]]) continue;
      if (!r) r = {s:s, b:0, se:0, flow:0, conv:0, gb:0, gs:0, cb:0, cs:0, nsh:0, moves:[]};
      if (d[1] < 2) { r.b++; r.gb += d[3]; r.cb += d[4]; } else { r.se++; r.gs += d[3]; r.cs += d[4]; }
      r.flow += d[3]; r.conv += d[4]; r.nsh += d[2]; r.moves.push(d);
    }
    if (r) { r.ni = r.b - r.se; rows.push(r); }
  }
  return {act:act, rows:rows};
}

/* ---------- tooltip ---------- */
var tip = $('tip'), tipAnchor = null;  // tipAnchor: element a focus-opened tooltip follows
function placeTip(x, y){
  var w = tip.offsetWidth, h = tip.offsetHeight;
  if (x + w > window.innerWidth - 8) x = Math.max(8, window.innerWidth - w - 8);
  if (y + h > window.innerHeight - 8) y = Math.max(8, y - h - 28);
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
function showTip(ev, build){
  clear(tip); build(tip); tip.style.display = 'block';
  if (ev && ev.type !== 'focus' && ev.clientX != null) { tipAnchor = null; placeTip(ev.clientX + 14, ev.clientY + 14); }
  else { tipAnchor = ev.currentTarget; var rc = tipAnchor.getBoundingClientRect(); placeTip(rc.left + 12, rc.bottom + 6); }
}
function hideTip(){ tip.style.display = 'none'; tipAnchor = null; }
function onScroll(){
  // A pointer tooltip would drift away from the mouse, so hide it; a keyboard
  // (focus) tooltip follows its element — focusing scrolls the element into view.
  if (tipAnchor && document.activeElement === tipAnchor) {
    var rc = tipAnchor.getBoundingClientRect(); placeTip(rc.left + 12, rc.bottom + 6);
  } else hideTip();
}
function bindTip(node, build){
  node.addEventListener('pointermove', function(ev){ if (ev.pointerType === 'mouse') showTip(ev, build); });
  node.addEventListener('pointerleave', hideTip);
  node.addEventListener('focus', function(ev){ showTip(ev, build); });
  node.addEventListener('blur', hideTip);
}
function tipStock(r){
  return function(t){
    t.appendChild(el('div', 'tv', fmtM(mval(r)) + '  ' + (r.s.t || '')));
    t.appendChild(el('div', 'tl', r.s.n + (r.s.sec ? ' · ' + r.s.sec : '')));
    t.appendChild(el('div', 'tr', 'Est. flow ' + money(r.flow) + ' · conviction ' + pct(r.conv)));
    t.appendChild(el('div', 'tr', r.b + ' buying · ' + r.se + ' selling'));
    r.moves.slice().sort(function(a, b){ return Math.abs(b[4]) - Math.abs(a[4]); }).slice(0, 4).forEach(function(d){
      var row = el('div', 'tr');
      var k = el('span', 'key'); k.style.background = d[1] < 2 ? 'var(--buy)' : 'var(--sell)';
      row.appendChild(k);
      row.appendChild(el('span', null, invShort(d[0]) + ' ' + ACT[d[1]] + ' ' + money(d[3]) + ' (' + pct(d[4]) + ')'));
      t.appendChild(row);
    });
    t.appendChild(el('div', 'tr', 'Click for details ↓'));
  };
}

/* ---------- filters ---------- */
function setPressed(groupId, val){
  Array.prototype.forEach.call($(groupId).querySelectorAll('button'), function(b){
    b.setAttribute('aria-pressed', b.dataset.v === val ? 'true' : 'false');
  });
}
function renderFilters(){
  setPressed('f-group', st.inv >= 0 ? '' : st.group);
  setPressed('f-scope', st.scope);
  setPressed('f-measure', st.measure);
  var sel = $('f-inv'); clear(sel);
  var o = el('option', null, st.inv >= 0 ? '— back to group —' : 'One investor…'); o.value = '-1'; sel.appendChild(o);
  INV.forEach(function(v, i){
    if (!v.ok) return;
    var op = el('option', null, v.m + ' (' + v.q + ')'); op.value = String(i);
    if (i === st.inv) op.selected = true;
    sel.appendChild(op);
  });
  var help = {
    conv: ['Conviction', ' = the share of their own portfolio each investor moved into (+) or out of (−) a stock, added up across investors. A 5% new position counts far more than a 0.01% nibble from a 6,000-stock fund, so this view surfaces real bets.'],
    flow: ['Est. $ flow', ' = change in shares × the quarter-end price in the filing (price moves stripped out). The biggest funds dominate this view.'],
    count:['# investors', ' = investors buying minus investors selling — breadth of agreement, whatever the size.']
  }[st.measure];
  var h = clear($('help'));
  h.appendChild(el('b', null, help[0])); h.appendChild(document.createTextNode(help[1]));
  if (st.inv >= 0) h.appendChild(document.createTextNode(' Showing only ' + invName(st.inv) + '.'));
  // investor strip highlights
  Array.prototype.forEach.call(document.querySelectorAll('.chip[data-i]'), function(c){
    var i = +c.dataset.i, act = activeSet();
    c.classList.toggle('sel', i === st.inv);
    c.classList.toggle('dim', INV[i].ok && !act[i]);
  });
}
function wireFilters(){
  function seg(id, fn){
    $(id).addEventListener('click', function(ev){
      var b = ev.target.closest('button'); if (!b) return; fn(b.dataset.v); persist(); renderAll();
    });
  }
  seg('f-group', function(v){ st.group = v; st.inv = -1; resetPaging(); });
  seg('f-scope', function(v){ st.scope = v; resetPaging(); });
  seg('f-measure', function(v){ st.measure = v; st.sort = {buy:null, sell:null, mixed:null}; resetPaging(); });
  $('f-inv').addEventListener('change', function(){ st.inv = +this.value; resetPaging(); renderAll(); });
  Array.prototype.forEach.call(document.querySelectorAll('.chip[data-i]'), function(c){
    c.addEventListener('click', function(){
      var i = +c.dataset.i; if (!INV[i].ok) return;
      st.inv = (st.inv === i) ? -1 : i; resetPaging(); renderAll();
      $('dash').scrollIntoView({behavior:'smooth', block:'start'});
    });
  });
  $('q').addEventListener('input', function(){ st.q = this.value.trim().toLowerCase(); st.page = {buy:25,sell:25,mixed:25}; renderTables(LAST); });
}

/* ---------- KPI tiles ---------- */
function tile(lbl, val, sub, valCls){
  var t = el('div', 'tile'); t.appendChild(el('div', 'lbl', lbl));
  var v = el('div', 'val ' + (valCls || ''), val); t.appendChild(v);
  if (sub) t.appendChild(el('div', 'sub', sub));
  return t;
}
function renderKpis(C){
  var rows = C.rows, gb = 0, gs = 0, nNew = 0, nExit = 0, up = 0, dn = 0;
  rows.forEach(function(r){ gb += r.gb; gs += r.gs; var m = mval(r); if (m > 0) up++; else if (m < 0) dn++;
    r.moves.forEach(function(d){ if (d[1] === 0) nNew++; else if (d[1] === 3) nExit++; }); });
  var k = clear($('kpis'));
  k.appendChild(tile('Net est. flow', money(gb + gs), 'bought ' + money(gb, false) + ' · sold ' + money(-gs, false), cls(gb + gs)));
  k.appendChild(tile('Stocks net bought / sold', up + ' / ' + dn, rows.length + ' stocks traded · by ' + MEAS[st.measure].name.toLowerCase()));
  k.appendChild(tile('New positions / exits', nNew + ' / ' + nExit, 'brand-new buys vs complete sell-outs'));
  var ids = Object.keys(C.act).map(Number), qs = {};
  ids.forEach(function(i){ qs[INV[i].q] = (qs[INV[i].q] || 0) + 1; });
  var qtxt = Object.keys(qs).sort(function(a, b){ return qkey(b) < qkey(a) ? -1 : qkey(b) > qkey(a) ? 1 : 0; })
    .map(function(q){ return q + ' ×' + qs[q]; }).join(' · ');
  k.appendChild(tile('Investors in view', String(ids.length), 'latest filing: ' + qtxt));
}

/* ---------- takeaways ---------- */
function tkBtn(r){
  var b = el('button', 'tk', label(r.s)); b.type = 'button';
  b.addEventListener('click', function(){ openStock(r.s.c); });
  return b;
}
function names(r, buy){
  return r.moves.filter(function(d){ return buy ? d[1] < 2 : d[1] >= 2; })
    .sort(function(a, b){ return Math.abs(b[4]) - Math.abs(a[4]); })
    .map(function(d){ return invShort(d[0]); });
}
function li(parts){
  var l = el('li');
  parts.forEach(function(p){ if (typeof p === 'string') l.appendChild(document.createTextNode(p)); else if (p) l.appendChild(p); });
  return l;
}
function renderTakeaways(C){
  var ul = clear($('takeaways')), rows = C.rows;
  if (!rows.length) { ul.appendChild(el('li', 'dim', 'No trades for this selection.')); return; }
  var who = st.inv >= 0 ? invName(st.inv) : (st.group === 'picker' ? 'The stock-pickers' : 'All tracked investors');
  var gb = 0, gs = 0; rows.forEach(function(r){ gb += r.gb; gs += r.gs; });
  ul.appendChild(li([who + ' bought ≈' + money(gb, false) + ' and sold ≈' + money(-gs, false) +
    ' — net ' + money(gb + gs) + ' across ' + rows.length + ' stock' + (rows.length === 1 ? '' : 's') +
    (st.scope === 'mine' ? ' on your lists' : '') + '.']));
  // biggest single bet / sale by % of portfolio
  // "Biggest" follows the measure: dollars in $-flow view, else % of portfolio.
  var key = st.measure === 'flow' ? 3 : 4, bestB = null, bestS = null;
  rows.forEach(function(r){ r.moves.forEach(function(d){
    if (d[1] < 2 && (!bestB || d[key] > bestB.d[key])) bestB = {r:r, d:d};
    if (d[1] >= 2 && (!bestS || d[key] < bestS.d[key])) bestS = {r:r, d:d};
  }); });
  if (bestB) ul.appendChild(li(['Biggest single bet: ' + invName(bestB.d[0]) + ' — ' +
    (bestB.d[1] === 0 ? 'NEW position in ' : 'added to '), tkBtn(bestB.r),
    ', ≈' + money(bestB.d[3], false) + ' (' + pct(bestB.d[4], false) + ' of the portfolio).']));
  if (bestS) ul.appendChild(li(['Biggest single sale: ' + invName(bestS.d[0]) + ' — ' +
    (bestS.d[1] === 3 ? 'sold out of ' : 'trimmed '), tkBtn(bestS.r),
    ', ≈' + money(-bestS.d[3], false) + ' (' + pct(-bestS.d[4], false) + ' of the portfolio).']));
  if (st.inv < 0) {
    var cb = rows.filter(function(r){ return r.b >= 2; }).sort(function(a, b){ return (b.ni - a.ni) || (b.conv - a.conv); })[0];
    if (cb) ul.appendChild(li(['Most agreed buy: ', tkBtn(cb), ' — ' + cb.b + ' buying (' + names(cb, true).join(', ') + ')' +
      (cb.se ? ', ' + cb.se + ' selling (' + names(cb, false).join(', ') + ').' : ', nobody selling.')]));
    var cs = rows.filter(function(r){ return r.se >= 2; }).sort(function(a, b){ return (a.ni - b.ni) || (a.conv - b.conv); })[0];
    if (cs) ul.appendChild(li(['Most agreed sell: ', tkBtn(cs), ' — ' + cs.se + ' selling (' + names(cs, false).join(', ') + ')' +
      (cs.b ? ', ' + cs.b + ' buying (' + names(cs, true).join(', ') + ').' : ', nobody buying.')]));
    var sp = rows.filter(function(r){ return r.b >= 2 && r.se >= 2; })
      .sort(function(a, b){ return (Math.min(b.b, b.se) - Math.min(a.b, a.se)) || ((b.b + b.se) - (a.b + a.se)); })[0];
    if (sp) ul.appendChild(li(['Split decision: ', tkBtn(sp), ' — ' + sp.b + ' buying vs ' + sp.se + ' selling.']));
  }
  var sec = sectorAgg(rows).filter(function(x){ return x.name !== 'Unclassified'; });
  if (sec.length) {
    var top = sec.slice().sort(function(a, b){ return b.net - a.net; });
    var parts = [];
    if (top[0].net > 0) parts.push('most bought sector: ' + top[0].name + ' (' + fmtMlong(top[0].net) + ')');
    var last = top[top.length - 1];
    if (last.net < 0) parts.push('most sold: ' + last.name + ' (' + fmtMlong(last.net) + ')');
    if (parts.length) ul.appendChild(li([parts.join('; ').replace(/^m/, 'M') + '.']));
  }
  if (st.scope === 'all') {
    var mine = rows.filter(function(r){ return r.s.k; });
    if (mine.length) {
      var mb = mine.filter(function(r){ return mval(r) > 0; }).sort(cmpDesc);
      var ms = mine.filter(function(r){ return mval(r) < 0; }).sort(cmpDesc).reverse();
      var p = ['Your stocks: ' + mb.length + ' net bought, ' + ms.length + ' net sold'];
      if (mb.length) { p.push(' — strongest buy '); p.push(tkBtn(mb[0])); }
      if (ms.length) { p.push(mb.length ? ', strongest sell ' : ' — strongest sell '); p.push(tkBtn(ms[0])); }
      p.push('.');
      ul.appendChild(li(p));
    } else {
      ul.appendChild(li([el('span', 'dim', 'None of your holdings or watchlist names were traded by these investors.')]));
    }
  }
}

/* ---------- ranked bars ---------- */
function barRow(r, v, max, side){
  var b = el('button', 'brow'); b.type = 'button';
  var lab = el('span', 'lab', label(r.s));
  if (r.s.k) lab.appendChild(el('span', 'tag ' + r.s.k, r.s.k));
  lab.appendChild(el('span', 'cnt', r.b + '↑ ' + r.se + '↓' + (r.s.t ? '' : ' · no ticker')));
  var tr = el('span', 'track'), bar = el('span', 'bar ' + side);
  bar.style.width = (max ? Math.max(1, Math.abs(v) / max * 100) : 0) + '%';
  tr.appendChild(bar);
  b.appendChild(lab); b.appendChild(tr); b.appendChild(el('span', 'val', fmtM(v)));
  b.setAttribute('aria-label', label(r.s) + ' ' + fmtM(v) + ', ' + r.b + ' buying, ' + r.se + ' selling');
  bindTip(b, tipStock(r));
  b.addEventListener('click', function(){ openStock(r.s.c); });
  return b;
}
var TOPN = 12;
function renderMovers(C){
  var buys = C.rows.filter(function(r){ return mval(r) > 0; }).sort(cmpDesc).slice(0, TOPN);
  var sells = C.rows.filter(function(r){ return mval(r) < 0; }).sort(cmpDesc).reverse().slice(0, TOPN);
  var max = Math.max(buys.length ? Math.abs(mval(buys[0])) : 0, sells.length ? Math.abs(mval(sells[0])) : 0);
  var bb = clear($('m-buy')), sb = clear($('m-sell'));
  if (!buys.length) bb.appendChild(el('div', 'empty', 'Nothing net bought in this view.'));
  buys.forEach(function(r){ bb.appendChild(barRow(r, mval(r), max, 'buy')); });
  if (!sells.length) sb.appendChild(el('div', 'empty', 'Nothing net sold in this view.'));
  sells.forEach(function(r){ sb.appendChild(barRow(r, mval(r), max, 'sell')); });
  $('m-unit').textContent = 'top ' + TOPN + ' by ' + MEAS[st.measure].name.toLowerCase() + ' · shared scale';
}

/* ---------- diverging rows ---------- */
function divRow(labelNode, sell, buy, max, valText, onClick, tipFn, sellTxt, buyTxt){
  var b = el(onClick ? 'button' : 'div', 'drow' + (onClick ? '' : ' static'));
  if (onClick) { b.type = 'button'; b.addEventListener('click', onClick); }
  b.appendChild(labelNode);
  var l = el('span', 'half l'), r = el('span', 'half r');
  var wl = max ? Math.abs(sell) / max * 100 : 0, wr = max ? Math.abs(buy) / max * 100 : 0;
  // Gross numbers sit just outside their bar; when the bar fills most of its
  // half there's no room outside, so the number moves inside the bar's end.
  if (sell) { var bl = el('span', 'bar'); bl.style.width = Math.max(1, wl) + '%'; l.appendChild(bl);
    if (sellTxt) { var nl = el('span', 'num' + (wl > 62 ? ' in' : ''), sellTxt); nl.style.setProperty('--w', Math.max(1, wl) + '%'); l.appendChild(nl); } }
  if (buy) { var br = el('span', 'bar'); br.style.width = Math.max(1, wr) + '%'; r.appendChild(br);
    if (buyTxt) { var nr = el('span', 'num' + (wr > 62 ? ' in' : ''), buyTxt); nr.style.setProperty('--w', Math.max(1, wr) + '%'); r.appendChild(nr); } }
  b.appendChild(l); b.appendChild(r); b.appendChild(el('span', 'val', valText));
  if (tipFn) bindTip(b, tipFn);
  return b;
}
function renderMine(C){
  var card = $('c-mine');
  if (st.scope === 'mine') { card.style.display = 'none'; return; }
  card.style.display = '';
  var body = clear($('mine'));
  var mine = C.rows.filter(function(r){ return r.s.k; });
  if (!mine.length) { body.appendChild(el('div', 'empty', 'None of your holdings or watchlist names were traded by these investors.')); return; }
  var buys = mine.filter(function(r){ return mval(r) > 0; }).sort(cmpDesc).slice(0, 8);
  var sells = mine.filter(function(r){ return mval(r) < 0; }).sort(cmpDesc).reverse().slice(0, 8);
  var list = buys.concat(sells.reverse());
  var max = 0; list.forEach(function(r){ max = Math.max(max, Math.abs(mval(r))); });
  var head = el('div', 'axis-head'); head.appendChild(el('span')); head.appendChild(el('span', 'l', '← net sold'));
  head.appendChild(el('span', 'r', 'net bought →')); head.appendChild(el('span', 'v', MEAS[st.measure].name)); body.appendChild(head);
  list.forEach(function(r){
    var v = mval(r), lab = el('span', 'lab', label(r.s));
    lab.appendChild(el('span', 'tag ' + r.s.k, r.s.k));
    lab.appendChild(el('span', 'cnt', r.b + '↑ ' + r.se + '↓'));
    var row = divRow(lab, v < 0 ? v : 0, v > 0 ? v : 0, max, fmtM(v), function(){ openStock(r.s.c); }, tipStock(r));
    row.setAttribute('aria-label', label(r.s) + ' ' + fmtM(v));
    body.appendChild(row);
  });
  var rest = mine.length - list.length;
  $('mine-n').textContent = mine.length + ' of your stocks traded' + (rest > 0 ? ' · showing the strongest ' + list.length : '');
}

/* ---------- sectors ---------- */
function sectorAgg(rows){
  var m = {};
  rows.forEach(function(r){
    var k = r.s.sec || 'Unclassified';
    var x = m[k] || (m[k] = {name:k, buy:0, sell:0, net:0, n:0, absFlow:0});
    var b = st.measure === 'conv' ? r.cb : st.measure === 'flow' ? r.gb : r.b;
    var s = st.measure === 'conv' ? r.cs : st.measure === 'flow' ? r.gs : -r.se;
    x.buy += b; x.sell += s; x.net += b + s; x.n++; x.absFlow += Math.abs(r.gb) + Math.abs(r.gs);
  });
  return Object.keys(m).map(function(k){ return m[k]; });
}
function renderSectors(C){
  var body = clear($('sectors'));
  var all = sectorAgg(C.rows);
  if (!all.length) { body.appendChild(el('div', 'empty', 'No trades for this selection.')); return; }
  var known = all.filter(function(x){ return x.name !== 'Unclassified'; }).sort(function(a, b){ return b.net - a.net; });
  var unc = all.filter(function(x){ return x.name === 'Unclassified'; })[0];
  // Scale from the classified sectors only — the catch-all bucket would
  // otherwise flatten every real sector into slivers.
  var max = 0; known.forEach(function(x){ max = Math.max(max, Math.abs(x.buy), Math.abs(x.sell)); });
  var head = el('div', 'axis-head'); head.appendChild(el('span')); head.appendChild(el('span', 'l', '← sold'));
  head.appendChild(el('span', 'r', 'bought →')); head.appendChild(el('span', 'v', 'net')); body.appendChild(head);
  if (!known.length) body.appendChild(el('div', 'empty', 'No sector data for these stocks yet.'));
  known.forEach(function(x){
    var lab = el('span', 'lab', SEC_SHORT[x.name] || x.name); lab.appendChild(el('span', 'cnt', x.n + ' stock' + (x.n === 1 ? '' : 's')));
    var row = divRow(lab, x.sell, x.buy, max, fmtM(x.net), null, function(t){
      t.appendChild(el('div', 'tv', fmtM(x.net) + ' net'));
      t.appendChild(el('div', 'tl', x.name + ' · ' + x.n + ' stocks'));
      t.appendChild(el('div', 'tr', 'bought ' + fmtM(x.buy, false) + ' · sold ' + fmtM(-x.sell, false)));
    }, x.sell ? fmtM(-x.sell, false) : '', x.buy ? fmtM(x.buy, false) : '');
    row.tabIndex = 0;
    row.setAttribute('aria-label', x.name + ': bought ' + fmtM(x.buy, false) + ', sold ' + fmtM(-x.sell, false) + ', net ' + fmtM(x.net));
    body.appendChild(row);
  });
  if (unc) body.appendChild(el('div', 'empty', 'Not shown: ' + unc.n + ' stock' + (unc.n === 1 ? '' : 's') +
    ' without sector data (bought ' + fmtM(unc.buy, false) + ', sold ' + fmtM(-unc.sell, false) + ', net ' + fmtM(unc.net) + ').'));
  var tot = 0, cov = 0; all.forEach(function(x){ tot += x.absFlow; if (x.name !== 'Unclassified') cov += x.absFlow; });
  $('sec-cov').textContent = tot ? 'sector known for ' + Math.round(cov / tot * 100) + '% of $ traded' : '';
}

/* ---------- investors ---------- */
function renderInvestors(C){
  var body = clear($('investors'));
  var ids = Object.keys(C.act).map(Number);
  var per = {};
  ids.forEach(function(i){ per[i] = {i:i, n:[0,0,0,0], buy:0, sell:0, tb:null, ts:null}; });
  C.rows.forEach(function(r){ r.moves.forEach(function(d){
    var p = per[d[0]]; if (!p) return;
    p.n[d[1]]++;
    var v = st.measure === 'conv' ? d[4] : st.measure === 'flow' ? d[3] : (d[1] < 2 ? 1 : -1);
    if (d[1] < 2) { p.buy += v; if (!p.tb || d[4] > p.tb.d[4]) p.tb = {r:r, d:d}; }
    else { p.sell += v; if (!p.ts || d[4] < p.ts.d[4]) p.ts = {r:r, d:d}; }
  }); });
  var list = ids.map(function(i){ return per[i]; }).sort(function(a, b){ return (Math.abs(b.buy) + Math.abs(b.sell)) - (Math.abs(a.buy) + Math.abs(a.sell)); });
  var max = 0; list.forEach(function(p){ max = Math.max(max, Math.abs(p.buy), Math.abs(p.sell)); });
  list.forEach(function(p){
    var v = INV[p.i];
    var b = el('button', 'irow' + (st.inv === p.i ? ' sel' : '')); b.type = 'button';
    var lab = el('span', 'lab'); lab.appendChild(el('b', null, v.m));
    lab.appendChild(el('span', 'q', v.q + ' vs ' + v.pq + (v.st === 'diversified' ? ' · diversified' : '')));
    b.appendChild(lab);
    var l = el('span', 'half l'), r = el('span', 'half r');
    if (p.sell) { var bl = el('span', 'bar'); bl.style.width = Math.max(1, Math.abs(p.sell) / max * 100) + '%'; l.appendChild(bl); }
    if (p.buy) { var br = el('span', 'bar'); br.style.width = Math.max(1, Math.abs(p.buy) / max * 100) + '%'; r.appendChild(br); }
    b.appendChild(l); b.appendChild(r);
    b.appendChild(el('span', 'val', fmtM(p.buy + p.sell)));
    var acts = el('span', 'acts');
    var total = p.n[0] + p.n[1] + p.n[2] + p.n[3];
    if (!total) acts.appendChild(document.createTextNode('no position changes in this view'));
    else {
      acts.appendChild(document.createTextNode('NEW ' + p.n[0] + ' · ADD ' + p.n[1] + ' · TRIM ' + p.n[2] + ' · EXIT ' + p.n[3]));
      if (p.tb) { acts.appendChild(document.createTextNode(' — top buy ')); acts.appendChild(el('b', null, label(p.tb.r.s))); }
      if (p.ts) { acts.appendChild(document.createTextNode(p.tb ? ', top sell ' : ' — top sell ')); acts.appendChild(el('b', null, label(p.ts.r.s))); }
    }
    b.appendChild(acts);
    b.setAttribute('aria-label', v.m + ': bought ' + fmtM(p.buy, false) + ', sold ' + fmtM(-p.sell, false) + '. Click to show only this investor.');
    bindTip(b, function(t){
      t.appendChild(el('div', 'tv', fmtM(p.buy + p.sell) + ' net'));
      t.appendChild(el('div', 'tl', v.m + ' · ' + v.n));
      t.appendChild(el('div', 'tr', 'bought ' + fmtM(p.buy, false) + ' · sold ' + fmtM(-p.sell, false)));
      t.appendChild(el('div', 'tr', v.h + ' holdings · 13F book ' + money(v.tv, false)));
      t.appendChild(el('div', 'tr', st.inv === p.i ? 'Click to show the whole group again' : 'Click to show only this investor'));
    });
    b.addEventListener('click', function(){ st.inv = (st.inv === p.i) ? -1 : p.i; hideTip(); resetPaging(); renderAll(); $('dash').scrollIntoView({behavior:'smooth', block:'start'}); });
    body.appendChild(b);
  });
  if (!list.length) body.appendChild(el('div', 'empty', 'No investors in this view.'));
}

/* ---------- heatmap ---------- */
function renderHeatmap(C){
  var body = clear($('heatmap'));
  if (st.inv >= 0) { body.appendChild(el('div', 'empty', 'Pick a group (not a single investor) to compare who agrees.')); return; }
  var rows = C.rows.filter(function(r){ return r.b + r.se >= 2; })
    .sort(function(a, b){ return ((b.b + b.se) - (a.b + a.se)) || (Math.abs(mval(b)) - Math.abs(mval(a))); }).slice(0, 16);
  if (!rows.length) { body.appendChild(el('div', 'empty', 'No stock was traded by two or more of these investors.')); return; }
  rows.sort(cmpDesc);
  var colCount = {};
  rows.forEach(function(r){ r.moves.forEach(function(d){ colCount[d[0]] = (colCount[d[0]] || 0) + 1; }); });
  var cols = Object.keys(colCount).map(Number).sort(function(a, b){ return (colCount[b] - colCount[a]) || (a - b); });
  var g = el('div', 'hm');
  g.style.gridTemplateColumns = 'minmax(4.5rem, 7rem) repeat(' + cols.length + ', minmax(16px, 1fr))';
  g.appendChild(el('span'));
  cols.forEach(function(i){ var h = el('span', 'colh', invShort(i)); h.title = invName(i); g.appendChild(h); });
  rows.forEach(function(r){
    var rh = el('button', 'rowh', label(r.s)); rh.type = 'button';
    rh.appendChild(el('span', 'cnt', r.b + '↑' + r.se + '↓'));
    rh.addEventListener('click', function(){ openStock(r.s.c); });
    bindTip(rh, tipStock(r));
    g.appendChild(rh);
    var by = {}; r.moves.forEach(function(d){ by[d[0]] = d; });
    cols.forEach(function(i){
      var d = by[i];
      var c = el('span', 'cell' + (d ? ' a' + d[1] : ''), d ? GLYPH[d[1]] : '');
      if (d) {
        c.tabIndex = 0;
        c.setAttribute('aria-label', invName(i) + ' ' + ACT[d[1]] + ' ' + label(r.s) + ' ' + money(d[3]));
        bindTip(c, function(t){
          t.appendChild(el('div', 'tv', ACT[d[1]] + '  ' + money(d[3])));
          t.appendChild(el('div', 'tl', invName(i) + ' · ' + (r.s.t || r.s.n)));
          t.appendChild(el('div', 'tr', shares(d[2]) + ' shares · ' + pct(d[4]) + ' of portfolio'));
        });
      }
      g.appendChild(c);
    });
  });
  body.appendChild(g);
  var lg = el('div', 'legend');
  [['a0', 'N', 'NEW position'], ['a1', '+', 'added'], ['a2', '−', 'trimmed'], ['a3', '×', 'sold out'], ['', '', 'no change']].forEach(function(x){
    var s = el('span'); var sw = el('i', 'cell ' + x[0], x[1]); sw.style.height = '14px'; s.appendChild(sw); s.appendChild(document.createTextNode(x[2])); lg.appendChild(s);
  });
  body.appendChild(lg);
}

/* ---------- tables ---------- */
var LAST = null;
var COLS = [
  {k:'stock', t:'Stock', f:function(r){ return (r.s.t || r.s.n).toUpperCase(); }, str:true},
  {k:'ni', t:'Investors', st:'Inv.', f:function(r){ return r.ni; }, tip:'Investors buying minus investors selling'},
  {k:'conv', t:'Conviction', st:'Conv.', f:function(r){ return r.conv; }, tip:'% of portfolio moved, summed across investors'},
  {k:'flow', t:'Est. $ flow', st:'$ flow', f:function(r){ return r.flow; }, tip:'Change in shares x quarter-end price'},
  {k:'nsh', t:'Shares', f:function(r){ return r.nsh; }, cls:'hide-sm', tip:'Net change in shares'}
];
var DEF_SORT = {conv:'conv', flow:'flow', count:'ni'};
function tableRows(C, which){
  var q = st.q;
  return C.rows.filter(function(r){
    var m = mval(r);
    if (which === 'buy' ? !(m > 0) : which === 'sell' ? !(m < 0) : m !== 0) return false;
    if (q && (r.s.t.toLowerCase().indexOf(q) < 0 && r.s.n.toLowerCase().indexOf(q) < 0)) return false;
    return true;
  });
}
function sortRows(rows, which){
  var s = st.sort[which] || {k:DEF_SORT[st.measure], asc: which === 'sell'};
  var col = COLS.filter(function(c){ return c.k === s.k; })[0];
  rows.sort(function(a, b){
    var x = col.f(a), y = col.f(b);
    var d = col.str ? (x < y ? -1 : x > y ? 1 : 0) : (x - y);
    if (!s.asc) d = -d;
    return d || (b.conv - a.conv);
  });
  return s;
}
function detailNode(r){
  var td = el('td'); td.colSpan = 5;
  var card = CARDS[r.s.c];
  if (card) { var w = el('div'); w.innerHTML = card; td.appendChild(w); }
  else td.appendChild(el('div', 'sub-label', 'No company snapshot embedded for ' + (r.s.t || r.s.n)));
  var links = el('div', 'links'), qn = encodeURIComponent(r.s.n || r.s.t), t = encodeURIComponent(r.s.t);
  function a(txt, href){ var x = el('a', 'info-link', txt); x.href = href; x.target = '_blank'; x.rel = 'noopener'; links.appendChild(x); }
  if (r.s.t) { a('Yahoo ↗', 'https://finance.yahoo.com/quote/' + t); a('News ↗', 'https://finance.yahoo.com/quote/' + t + '/news'); }
  a('Wikipedia ↗', 'https://en.wikipedia.org/wiki/Special:Search?search=' + qn);
  a('Google ↗', 'https://www.google.com/search?q=' + qn + '+stock');
  a('SEC EDGAR ↗', 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=' + qn + '&type=10-K');
  td.appendChild(links);
  td.appendChild(el('div', 'sub-label', 'Who traded it · 13F, latest vs prior quarter'));
  r.moves.slice().sort(function(a, b){ return Math.abs(b[3]) - Math.abs(a[3]); }).forEach(function(d){
    var v = INV[d[0]], m = el('div', 'mv');
    m.appendChild(el('span', 'a a' + d[1], ACT[d[1]]));
    var who = el('span', 'who'); who.appendChild(el('b', null, v.m)); who.appendChild(el('span', null, v.n + ' · ' + v.q)); m.appendChild(who);
    m.appendChild(el('span', 'r ' + cls(d[2]), shares(d[2]) + ' sh'));
    m.appendChild(el('span', 'r ' + cls(d[3]), money(d[3])));
    m.appendChild(el('span', 'r pct ' + cls(d[4]), pct(d[4]) + ' of port.'));
    td.appendChild(m);
  });
  var tr = el('tr', 'drowx'); tr.id = 'd-' + r.s.c; tr.appendChild(td);
  return tr;
}
function renderTable(C, which, title){
  var sec = $('t-' + which);
  var rows = tableRows(C, which);
  var s = sortRows(rows, which);
  clear(sec);
  if (which === 'mixed' && !rows.length) { sec.style.display = 'none'; return; }
  sec.style.display = '';
  var h = el('h3'); var ht = el('span', null, title + ' '); ht.appendChild(el('span', 'n', '(' + rows.length + ' stocks)'));
  h.appendChild(ht); h.appendChild(el('span', 'caret', '▼'));
  h.addEventListener('click', function(){ sec.classList.toggle('collapsed'); });
  sec.appendChild(h);
  var tbl = el('table', 'tbl'), thead = el('thead'), hr = el('tr');
  COLS.forEach(function(c){
    var th = el('th', (c.cls || '') + (s.k === c.k ? ' sorted' + (s.asc ? ' asc' : '') : ''));
    th.appendChild(el('span', 'full', c.t)); th.appendChild(el('span', 'short', c.st || c.t));
    if (c.tip) th.title = c.tip;
    th.setAttribute('aria-sort', s.k === c.k ? (s.asc ? 'ascending' : 'descending') : 'none');
    th.addEventListener('click', function(){
      var cur = st.sort[which] || {k:DEF_SORT[st.measure], asc: which === 'sell'};
      st.sort[which] = (cur.k === c.k) ? {k:c.k, asc:!cur.asc} : {k:c.k, asc: !!c.str || which === 'sell'};
      renderTable(LAST, which, title);
    });
    hr.appendChild(th);
  });
  thead.appendChild(hr); tbl.appendChild(thead);
  var tb = el('tbody');
  var n = Math.min(rows.length, st.page[which]);
  for (var i = 0; i < n; i++) (function(r){
    var tr = el('tr', 'srow'); tr.dataset.c = r.s.c;
    var c0 = el('td'); c0.appendChild(el('span', 'tkr', label(r.s)));
    if (r.s.k) c0.appendChild(el('span', 'tag ' + r.s.k, r.s.k));
    c0.appendChild(el('span', 'iss', r.s.n + (r.s.sec ? ' · ' + r.s.sec : '')));
    tr.appendChild(c0);
    var c1 = el('td', cls(r.ni), (r.ni > 0 ? '+' : r.ni < 0 ? MINUS : '') + Math.abs(r.ni));
    c1.appendChild(el('span', 'sm', r.b + 'b/' + r.se + 's')); tr.appendChild(c1);
    tr.appendChild(el('td', cls(r.conv), pct(r.conv)));
    tr.appendChild(el('td', cls(r.flow), money(r.flow)));
    tr.appendChild(el('td', 'hide-sm ' + cls(r.nsh), shares(r.nsh)));
    tr.tabIndex = 0;
    tr.setAttribute('aria-expanded', st.open === r.s.c ? 'true' : 'false');
    tr.addEventListener('click', function(){ toggleDetail(r, tr); });
    tr.addEventListener('keydown', function(ev){ if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggleDetail(r, tr); } });
    tb.appendChild(tr);
    if (st.open === r.s.c) { tr.classList.add('open'); tb.appendChild(detailNode(r)); }
  })(rows[i]);
  tbl.appendChild(tb); sec.appendChild(tbl);
  if (rows.length > n) {
    var more = el('div', 'more');
    more.appendChild(document.createTextNode('Showing ' + n + ' of ' + rows.length + ' '));
    var b1 = el('button', 'btn', 'Show 25 more'); b1.type = 'button';
    b1.addEventListener('click', function(){ st.page[which] += 25; renderTable(LAST, which, title); });
    var b2 = el('button', 'btn', 'Show all'); b2.type = 'button';
    b2.addEventListener('click', function(){ st.page[which] = rows.length; renderTable(LAST, which, title); });
    more.appendChild(b1); more.appendChild(b2); sec.appendChild(more);
  }
}
function toggleDetail(r, tr){
  var ex = document.getElementById('d-' + r.s.c);
  if (ex) { ex.parentNode.removeChild(ex); tr.classList.remove('open'); tr.setAttribute('aria-expanded', 'false'); if (st.open === r.s.c) st.open = null; return; }
  st.open = r.s.c; tr.classList.add('open'); tr.setAttribute('aria-expanded', 'true');
  tr.parentNode.insertBefore(detailNode(r), tr.nextSibling);
}
function renderTables(C){
  renderTable(C, 'buy', 'Net bought');
  renderTable(C, 'sell', 'Net sold');
  renderTable(C, 'mixed', 'Balanced / mixed');
  $('t-count').textContent = C.rows.length + ' stocks in this view';
}
function openStock(cusip){
  hideTip();
  var r = LAST.rows.filter(function(x){ return x.s.c === cusip; })[0];
  if (!r) return;
  var m = mval(r), which = m > 0 ? 'buy' : m < 0 ? 'sell' : 'mixed';
  var titles = {buy:'Net bought', sell:'Net sold', mixed:'Balanced / mixed'};
  if (st.q) { st.q = ''; $('q').value = ''; renderTables(LAST); }
  var rows = tableRows(LAST, which); sortRows(rows, which);
  var pos = rows.indexOf(r);
  if (pos >= st.page[which]) st.page[which] = Math.ceil((pos + 1) / 25) * 25;
  st.open = cusip;
  $('t-' + which).classList.remove('collapsed');
  renderTable(LAST, which, titles[which]);
  var tr = document.querySelector('#t-' + which + ' tr.srow[data-c="' + cusip + '"]');
  if (tr) { tr.scrollIntoView({behavior:'smooth', block:'center'}); tr.focus && tr.focus(); }
}

/* ---------- main ---------- */
function renderAll(){
  hideTip();
  var C = compute(); LAST = C;
  renderFilters(); renderKpis(C); renderTakeaways(C); renderMovers(C); renderMine(C);
  renderSectors(C); renderInvestors(C); renderHeatmap(C); renderTables(C);
}
wireFilters();
renderAll();
window.addEventListener('scroll', onScroll, {passive:true});
document.documentElement.dataset.ready = '1';
})();
"""


def render(dataset: dict[str, Any]) -> str:
    generated = dataset.get("generated_at", "")
    when = fmt_et(generated) if generated else "?"
    stocks = dataset.get("stocks", [])
    payload = build_payload(dataset)
    mix = _quarter_mix(dataset)

    chips = []
    for n, i in enumerate(dataset.get("investors", [])):
        status = i.get("status", "error")
        label = i.get("short") or i.get("manager") or i.get("name", "")
        q = quarter_label(i.get("as_of"))
        if status == "ok":
            title = (f"{i.get('manager', '')} · {i.get('name', '')} — {i.get('holdings', 0)} holdings, "
                     f"{q} vs {quarter_label(i.get('prior_as_of'))}"
                     f"{' · diversified fund' if i.get('style') == 'diversified' else ''}"
                     f"{' · ' + i['units_note'] if i.get('units_note') else ''}. Click to show only them.")
            chips.append(f'<button type="button" class="chip ok" data-i="{n}" title="{escape(title)}">'
                         f'<span class="ic">✓</span> {escape(label)}<span class="q">{escape(q)}</span></button>')
        elif status == "stale":
            chips.append(f'<span class="chip stale" title="{escape(i.get("name", ""))}: {escape(i.get("detail", ""))}">'
                         f'<span class="ic">⚠</span> {escape(label)}<span class="q">stale {escape(q)}</span></span>')
        else:
            chips.append(f'<span class="chip err" title="{escape(i.get("name", ""))}: {escape(i.get("detail", "") or status)}">'
                         f'<span class="ic">✗</span> {escape(label)}<span class="q">{escape(status)}</span></span>')

    n_pick = sum(1 for i in dataset.get("investors", []) if i.get("status") == "ok" and i.get("style") != "diversified")
    n_all = sum(1 for i in dataset.get("investors", []) if i.get("status") == "ok")

    if stocks:
        dash = f"""
<div class="filters" role="group" aria-label="Filters">
  <div class="fgroup"><span class="flabel">Investors</span>
    <span class="seg" id="f-group"><button type="button" data-v="picker" aria-pressed="true" title="Concentrated stock-pickers">Stock-pickers ({n_pick})</button><button type="button" data-v="all" aria-pressed="false" title="Adds quant / multi-manager funds with thousands of positions">All ({n_all})</button></span>
    <select id="f-inv" aria-label="Show one investor"></select>
  </div>
  <div class="fgroup"><span class="flabel">Stocks</span>
    <span class="seg" id="f-scope"><button type="button" data-v="all" aria-pressed="true">All</button><button type="button" data-v="mine" aria-pressed="false" title="Only your holdings (H) and watchlist (W)">My H/W</button></span>
  </div>
  <div class="fgroup"><span class="flabel">Measure</span>
    <span class="seg" id="f-measure"><button type="button" data-v="conv" aria-pressed="true">Conviction</button><button type="button" data-v="flow" aria-pressed="false">$ flow</button><button type="button" data-v="count" aria-pressed="false"># investors</button></span>
  </div>
</div>
<p class="help" id="help"></p>
<div class="kpis" id="kpis"></div>
<section class="card"><h2>Key takeaways <span class="hint">auto-generated from the filings · click a ticker for details</span></h2>
  <div class="body"><ul class="takeaways" id="takeaways"></ul></div></section>
<div class="grid2 wrap-gap">
  <section class="card"><h2>Most bought <span class="hint" id="m-unit"></span></h2><div class="body bars" id="m-buy"></div></section>
  <section class="card"><h2>Most sold <span class="hint">hover for who · click for details</span></h2><div class="body bars" id="m-sell"></div></section>
</div>
<section class="card" id="c-mine"><h2>Your holdings &amp; watchlist <span class="hint" id="mine-n"></span></h2><div class="body" id="mine"></div></section>
<div class="grid2 wrap-gap">
  <section class="card"><h2>Sector rotation <span class="hint" id="sec-cov"></span></h2><div class="body" id="sectors"></div></section>
  <section class="card"><h2>Who agrees <span class="hint">stocks traded by 2+ investors</span></h2><div class="body" id="heatmap"></div></section>
</div>
<section class="card"><h2>What each investor did <span class="hint">sold ← → bought · click to focus on one investor</span></h2><div class="body" id="investors"></div></section>
<div class="tools"><input id="q" type="search" placeholder="Search ticker or company…" aria-label="Search stocks"><span class="muted" id="t-count"></span></div>
<section class="section buy" id="t-buy"></section>
<section class="section sell" id="t-sell"></section>
<section class="section mixed" id="t-mixed"></section>
<div id="tip" role="tooltip"></div>
<script type="application/json" id="payload">{_payload_json(payload)}</script>
<script>{JS}</script>"""
        noscript = '<noscript><p class="empty">This dashboard needs JavaScript enabled.</p></noscript>'
    else:
        errs = [i.get("detail") for i in dataset.get("investors", [])
                if i.get("status") != "ok" and i.get("detail")]
        dash = (f'<div class="empty"><strong>Error:</strong> {escape(errs[0])}<br>'
                f'All {len(dataset.get("investors", []))} investors failed with the same or a similar error. '
                f'Hover any ✗ above for that investor’s message.</div>') if errs else \
            '<div class="empty">No holdings changes found.</div>'
        noscript = ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>13F Net Flow — {escape(mix.split(' ')[0] if mix else 'n/a')}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>13F Net Flow</h1>
  <div class="meta">What famous investors bought &amp; sold last quarter &middot;
    latest filings <strong>{escape(mix or 'n/a')}</strong> &middot; built <strong>{escape(when)}</strong></div>
  <div class="strip" aria-label="Investors">{"".join(chips)}</div>
  <nav><a href="./index.html">&larr; Daily briefing</a><a href="./manage.html">Manage tickers</a></nav>
</header>
<main id="dash">
{noscript}
{dash}
</main>
<footer>
  <p><b>How to read this.</b> Each investor's latest 13F is compared with their previous one. <b>Est. $ flow</b> = change in
  shares × the quarter-end price implied by the filing (so a position that was trimmed while its price rose still counts as
  selling). <b>Conviction</b> = that trade as a % of the investor's whole 13F portfolio, summed across investors.</p>
  <p>13F filings are quarterly, due 45 days after quarter end, and show US-listed long positions only (no shorts; options
  excluded). Investors file on different days, so near a deadline some are a quarter behind — each one's quarter is shown
  above. Filers more than a quarter behind are marked stale and excluded. Prices in the company cards come from Yahoo
  Finance at the date shown. Not investment advice.</p>
</footer>
</body>
</html>
"""
