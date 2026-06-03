"""Render the 13F net-flow dataset as a static, sortable HTML page.

Rows are server-rendered with data-* attributes; a little vanilla JS
handles column sorting, the watchlist toggle, and per-row expand. Works
without a backend and stays readable if JS is disabled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "SF Mono","Cascadia Mono","Fira Code","Consolas",monospace;
  max-width: 1000px; margin: 0 auto; padding: 1rem;
  color: #e8e6e3; background: #0a0e17; line-height: 1.45; }
header { margin-bottom: 1rem; border-bottom: 1px solid #1e2d3d; padding-bottom: .75rem; }
h1 { font-size: 1.1rem; color: #ff9800; letter-spacing: .06em; text-transform: uppercase; }
.meta { color: #6b7d8e; font-size: .78rem; margin-top: .15rem; }
/* Investor health line — explicitly wrap so 18+ tags never force horizontal scroll. */
.sources { display: flex; flex-wrap: wrap; gap: .25rem .7rem; align-items: baseline;
  font-size: .72rem; margin-top: .3rem; color: #6b7d8e; }
.sources .src-label { font-weight: 600; }
.src-tag { white-space: nowrap; }
.src-ok { color: #69f0ae; } .src-warn { color: #ffb74d; } .src-err { color: #ff6e6e; }
nav { margin-top: .35rem; font-size: .78rem; }
nav a { color: #ff9800; text-decoration: none; margin-right: .75rem; border-bottom: 1px dotted #ff980050; }
.controls { margin: .6rem 0; font-size: .76rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
.toggle-btn { background: #0f1923; color: #8899aa; border: 1px solid #1e2d3d;
  border-radius: 3px; padding: .25rem .6rem; cursor: pointer; font: inherit; font-size: .74rem; }
.toggle-btn.active { background: #0d1b2a; color: #42a5f5; border-color: #1565c0; }
/* Each table lives in its own section with a colored, collapsible header. */
.section { background: #0f1923; border: 1px solid #1e2d3d; border-radius: 4px;
  margin: .6rem 0; overflow: hidden; }
.section h2 { padding: .5rem .65rem; font-size: .8rem; font-weight: 600;
  letter-spacing: .04em; text-transform: uppercase; cursor: pointer; user-select: none;
  display: flex; justify-content: space-between; align-items: center; }
.section.inflow  h2 { background: #0a1a0f; color: #66bb6a; }
.section.outflow h2 { background: #1a0a0a; color: #ef5350; }
.section h2 .caret { font-size: .7rem; transition: transform .15s; }
.section.collapsed h2 .caret { transform: rotate(-90deg); }
.section.collapsed table { display: none; }
table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .8rem; }
thead th { text-align: right; padding: .5rem .5rem; background: #0f1923;
  border-bottom: 1px solid #1e2d3d; color: #6b7d8e; font-size: .7rem;
  text-transform: uppercase; letter-spacing: .05em; cursor: pointer;
  user-select: none; white-space: nowrap;
  position: sticky; top: 0; z-index: 10;
  box-shadow: inset 0 -1px 0 #1e2d3d; }
thead th.stock { text-align: left; }
thead th.sorted::after { content: " \\25BC"; color: #ff9800; }
/* Short labels on narrow screens — the full label only appears via title=. */
thead th .full  { display: inline; }
thead th .short { display: none; }
tbody td { padding: .4rem .5rem; border-bottom: 1px solid #152233; text-align: right; vertical-align: top; }
tbody td.stock { text-align: left; }
tr.stock-row { cursor: pointer; }
tr.stock-row:hover { background: #0f1923; }
.ticker { font-weight: 700; color: #fff; }
.issuer { color: #6b7d8e; font-size: .72rem; }
/* HOLDING / WATCHLIST marks — match the daily briefing's H/W tags. */
.cat-tag { display: inline-block; font-size: .56rem; font-weight: 700;
  width: 1rem; text-align: center; border-radius: 3px; padding: .04rem 0;
  margin-left: .3rem; letter-spacing: .03em; vertical-align: middle; }
.cat-tag.holding  { background: #1a3a2a; color: #69f0ae; border: 1px solid #2e7d53; }
.cat-tag.watching { background: #1a2a3a; color: #42a5f5; border: 1px solid #1565c0; }
.pos { color: #00e676; } .neg { color: #ff1744; } .flat { color: #6b7d8e; }
.detail-row td { background: #0c141d; padding: .5rem .75rem; }
.detail-row.hidden { display: none; }
/* Company info card shown above the per-investor detail. */
.info-card { border-bottom: 1px solid #1e2d3d; margin-bottom: .5rem; padding-bottom: .5rem; }
.info-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: .4rem .6rem; }
.info-name { color: #fff; font-weight: 700; font-size: .82rem; }
.info-sector { color: #6b7d8e; font-size: .7rem; }
.info-price { font-weight: 700; font-size: .82rem; color: #e8e6e3; }
.info-grid { display: flex; flex-wrap: wrap; gap: .2rem 1.2rem; margin-top: .35rem; }
.info-grid div { font-size: .72rem; color: #8899aa; }
.info-grid span { color: #e8e6e3; }
.info-summary { color: #8899aa; font-size: .72rem; margin-top: .35rem; line-height: 1.45; }
.info-link { color: #ff9800; font-size: .7rem; text-decoration: none; }
.info-link:hover { text-decoration: underline; }
.info-detail-label { color: #4a5568; font-size: .65rem; text-transform: uppercase;
  letter-spacing: .05em; margin: .1rem 0 .25rem; }
.info-missing { color: #6b7d8e; font-size: .72rem; font-style: italic; }
.detail-item { display: flex; justify-content: space-between; gap: 1rem;
  padding: .2rem 0; border-bottom: 1px dotted #152233; font-size: .74rem; }
.detail-item:last-child { border-bottom: 0; }
.act { font-weight: 700; width: 3.2rem; display: inline-block; }
.act-NEW { color: #00e676; } .act-ADD { color: #69f0ae; }
.act-TRIM { color: #ffb74d; } .act-EXIT { color: #ff1744; }
.inv-name { flex: 1; }
.inv-name .mgr { color: #fff; font-weight: 700; }
.inv-name .firm { color: #6b7d8e; font-size: .7rem; margin-left: .35rem; }
.empty { color: #6b7d8e; font-style: italic; padding: 1rem; }
footer { color: #3a4a5a; font-size: .68rem; margin-top: 1rem; text-align: center;
  border-top: 1px solid #1e2d3d; padding-top: .5rem; }
@media (max-width: 600px) {
  body { padding: .5rem; } table { font-size: .72rem; }
  .issuer { display: block; } thead th { padding: .35rem .3rem; font-size: .62rem; }
  tbody td { padding: .35rem .3rem; }
  thead th .full  { display: none; }
  thead th .short { display: inline; }
  .section h2 { padding: .4rem .5rem; font-size: .72rem; }
  .controls { font-size: .7rem; }
}
"""

JS = """
function fmtSortState(th){
  // Only un-mark headers within the same table — the other table's
  // sort indicator should stay put.
  var table = th.closest('table');
  table.querySelectorAll('thead th').forEach(function(h){h.classList.remove('sorted');});
  th.classList.add('sorted');
}
function sortBy(key, th, mode){
  var table = th.closest('table');
  var tbody = table.querySelector('tbody');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr.stock-row'));
  if (mode === 'string') {
    rows.sort(function(a,b){ return a.dataset[key].localeCompare(b.dataset[key]); });
  } else {
    rows.sort(function(a,b){ return parseFloat(b.dataset[key]) - parseFloat(a.dataset[key]); });
  }
  rows.forEach(function(r){
    var d = document.getElementById('d-' + r.dataset.cusip);
    tbody.appendChild(r); if (d) tbody.appendChild(d);
  });
  fmtSortState(th);
}
function toggleSection(h2){
  h2.parentElement.classList.toggle('collapsed');
}
function toggleWatchlist(btn){
  var on = btn.classList.toggle('active');
  document.querySelectorAll('tr.stock-row').forEach(function(r){
    var show = !on || r.dataset.watchlist === '1';
    r.style.display = show ? '' : 'none';
    var d = document.getElementById('d-' + r.dataset.cusip);
    if (d && !d.classList.contains('expanded')) d.style.display = 'none';
    else if (d) d.style.display = show ? '' : 'none';
  });
}
function expand(cusip){
  var d = document.getElementById('d-' + cusip);
  if (!d) return;
  var hidden = d.classList.toggle('hidden');
  d.classList.toggle('expanded', !hidden);
}
"""


def _fmt_money(v: int) -> str:
    sign = "-" if v < 0 else "+"
    a = abs(v)
    if a >= 1_000_000_000:
        return f"{sign}${a/1_000_000_000:.1f}B"
    if a >= 1_000_000:
        return f"{sign}${a/1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}${a/1_000:.0f}K"
    return f"{sign}${a}"


def _fmt_shares(v: int) -> str:
    sign = "-" if v < 0 else "+"
    return f"{sign}{abs(v):,}"


def _cls(v: int) -> str:
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


def _render_info_card(stock: dict, as_of: str) -> str:
    """Brief company snapshot shown when a stock row is expanded. Data is a
    build-time yfinance snapshot, so it's explicitly dated."""
    info = stock.get("info")
    ticker = stock.get("ticker") or ""
    if not info:
        which = f"{ticker} " if ticker else ""
        return (f'<div class="info-card"><span class="info-missing">'
                f'No company snapshot available for {escape(which)}'
                f'(symbol may be unmapped or non-US).</span></div>')

    cur = info.get("currency") or ""
    price = info.get("price")
    price_txt = f'{price:,.2f} {cur}'.strip() if isinstance(price, (int, float)) else "—"
    chg = info.get("change_pct")
    chg_html = ""
    if isinstance(chg, (int, float)):
        chg_html = f' <span class="{_cls(chg)}">{chg:+.2f}%</span>'

    sector = " · ".join(x for x in (info.get("sector"), info.get("industry")) if x)
    rng = ""
    if isinstance(info.get("wk_low"), (int, float)) and isinstance(info.get("wk_high"), (int, float)):
        rng = f'{info["wk_low"]:,.2f}–{info["wk_high"]:,.2f}'

    grid = []
    grid.append(f'<div>Mkt cap <span>{_fmt_cap(info.get("market_cap"))}</span></div>')
    grid.append(f'<div>P/E <span>{_fmt_num(info.get("pe"))}</span></div>')
    if rng:
        grid.append(f'<div>52wk <span>{escape(rng)}</span></div>')

    summary = info.get("summary") or ""
    summary_html = f'<p class="info-summary">{escape(summary)}</p>' if summary else ""

    website = info.get("website") or ""
    link_html = ""
    if website:
        link_html = f' &middot; <a class="info-link" href="{escape(website)}" target="_blank" rel="noopener">website ↗</a>'

    name = info.get("name") or stock.get("issuer") or ticker
    return (
        f'<div class="info-card">'
        f'<div class="info-head">'
        f'<span class="info-name">{escape(name)}</span>'
        f'<span class="info-sector">{escape(sector)}</span>'
        f'<span class="info-price">{escape(price_txt)}{chg_html}</span>'
        f'</div>'
        f'<div class="info-grid">{"".join(grid)}</div>'
        f'{summary_html}'
        f'<div class="info-summary" style="margin-top:.3rem;color:#4a5568;font-size:.65rem;">'
        f'Snapshot as of {escape(str(as_of))}{link_html}</div>'
        f'</div>'
    )


def _render_source_health(dataset: dict) -> str:
    tags = []
    for inv in dataset.get("investors", []):
        status = inv.get("status")
        cls = {"ok": "src-ok", "mismatch": "src-warn"}.get(status, "src-err")
        icon = {"ok": "✓", "mismatch": "!"}.get(status, "✗")
        label = inv.get("manager") or inv.get("name", "")
        title = inv.get("detail") or (f"{inv.get('name','')} — {inv.get('holdings',0)} holdings, as of {inv.get('as_of')}")
        tags.append(f'<span class="src-tag {cls}" title="{escape(str(title))}">'
                    f'{icon} {escape(label)}</span>')
    return (f'<div class="sources"><span class="src-label">Investors:</span>'
            f'{"".join(tags)}</div>') if tags else ""


def _render_detail(stock: dict) -> str:
    items = ['<div class="info-detail-label">Who bought / sold</div>']
    for d in stock.get("detail", []):
        act = d["action"]
        manager = d.get("manager") or ""
        firm = d.get("investor") or ""
        if manager:
            who = f'<span class="mgr">{escape(manager)}</span><span class="firm">{escape(firm)}</span>'
        else:
            who = f'<span class="mgr">{escape(firm)}</span>'
        items.append(
            f'<div class="detail-item">'
            f'<span class="inv-name"><span class="act act-{act}">{act}</span>{who}</span>'
            f'<span class="{_cls(d["shares_delta"])}">{_fmt_shares(d["shares_delta"])} sh</span>'
            f'<span class="{_cls(d["value_delta"])}">{_fmt_money(d["value_delta"])}</span>'
            f'</div>'
        )
    return "".join(items)


def _render_row(stock: dict, as_of: str = "") -> str:
    cusip = escape(stock["cusip"])
    ticker = stock.get("ticker") or ""
    label = escape(ticker) if ticker else escape((stock.get("issuer") or "?")[:18])
    issuer = escape(stock.get("issuer") or "")
    category = stock.get("category") or ""
    if category == "HOLDING":
        mark = '<span class="cat-tag holding" title="You hold this">H</span>'
    elif category == "WATCHLIST":
        mark = '<span class="cat-tag watching" title="On your watchlist">W</span>'
    else:
        mark = ""
    ni = stock["net_investors"]
    ni_txt = f"+{ni}" if ni > 0 else str(ni)
    # Sort key for the Stock column: ticker if mapped, else issuer name.
    stock_key = (ticker or stock.get("issuer") or "").upper()
    detail_body = _render_info_card(stock, as_of) + _render_detail(stock)
    row = (
        f'<tr class="stock-row" data-cusip="{cusip}" '
        f'data-stock="{escape(stock_key)}" '
        f'data-net_investors="{ni}" data-net_value="{stock["net_value"]}" '
        f'data-net_shares="{stock["net_shares"]}" data-watchlist="{1 if stock.get("in_watchlist") else 0}" '
        f'onclick="expand(\'{cusip}\')">'
        f'<td class="stock"><span class="ticker">{label}</span>{mark}<br>'
        f'<span class="issuer">{issuer}</span></td>'
        f'<td class="{_cls(ni)}">{ni_txt}<br><span class="issuer">{stock["buyers"]}b / {stock["sellers"]}s</span></td>'
        f'<td class="{_cls(stock["net_value"])}">{_fmt_money(stock["net_value"])}</td>'
        f'<td class="{_cls(stock["net_shares"])}">{_fmt_shares(stock["net_shares"])}</td>'
        f'</tr>'
        f'<tr class="detail-row hidden" id="d-{cusip}"><td colspan="4">{detail_body}</td></tr>'
    )
    return row


def _render_section(stocks: list[dict], *, css_class: str, title: str,
                    initial_sort_key: str, as_of: str = "") -> str:
    """One table per +/- section. Each table owns its own sticky header
    and its own column-sort state, so sorting Inflow doesn't affect Outflow."""
    rows = "".join(_render_row(s, as_of) for s in stocks)
    sort_classes = {
        "net_investors": "sorted",
        "net_value": "",
        "net_shares": "",
        "stock": "",
    }
    sort_classes[initial_sort_key] = "sorted"
    return (
        f'<section class="section {css_class}">'
        f'<h2 onclick="toggleSection(this)">'
        f'<span>{escape(title)} <span class="issuer">({len(stocks)} stocks)</span></span>'
        f'<span class="caret">&#9660;</span></h2>'
        f'<table><thead><tr>'
        f'<th class="stock {sort_classes["stock"]}" onclick="sortBy(\'stock\', this, \'string\')">'
        f'<span class="full">Stock</span><span class="short">Stock</span></th>'
        f'<th class="{sort_classes["net_investors"]}" '
        f'title="Buyers minus sellers among the tracked investors" '
        f'onclick="sortBy(\'net_investors\', this)">'
        f'<span class="full">Investors net</span><span class="short">Inv &plusmn;</span></th>'
        f'<th class="{sort_classes["net_value"]}" '
        f'title="Net dollar value added minus removed across investors" '
        f'onclick="sortBy(\'net_value\', this)">'
        f'<span class="full">$ net</span><span class="short">$ &plusmn;</span></th>'
        f'<th class="{sort_classes["net_shares"]}" '
        f'title="Net share count change across investors" '
        f'onclick="sortBy(\'net_shares\', this)">'
        f'<span class="full">Shares net</span><span class="short">Sh &plusmn;</span></th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
        f'</section>'
    )


def render(dataset: dict[str, Any]) -> str:
    generated = dataset.get("generated_at", "")
    try:
        when = datetime.fromisoformat(generated).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        when = generated
    as_of = dataset.get("as_of") or "?"
    stocks = dataset.get("stocks", [])

    if stocks:
        inflow = [s for s in stocks if s["net_investors"] >= 0]
        outflow = [s for s in stocks if s["net_investors"] < 0]
        # Each section pre-sorted by net_investors so they look right on load
        # even before any column header is tapped.
        inflow.sort(key=lambda s: (s["net_investors"], s["net_value"]), reverse=True)
        outflow.sort(key=lambda s: (s["net_investors"], s["net_value"]))
        body = (
            _render_section(inflow, css_class="inflow",
                            title="Net inflow (buyers ≥ sellers)",
                            initial_sort_key="net_investors", as_of=str(as_of)) +
            _render_section(outflow, css_class="outflow",
                            title="Net outflow (sellers > buyers)",
                            initial_sort_key="net_investors", as_of=str(as_of))
        )
    else:
        # Surface the actual error reason — one investor's detail is usually
        # representative when they all fail the same way (e.g. SEC HTTP 403).
        errs = [i.get("detail") for i in dataset.get("investors", [])
                if i.get("status") != "ok" and i.get("detail")]
        body = (
            f'<div class="empty"><strong>Error:</strong> {escape(errs[0])}<br>'
            f'<span class="issuer">All {len(dataset.get("investors", []))} investors failed with the same/similar error. '
            f'Hover any ✗ above for that investor’s specific message.</span></div>'
        ) if errs else '<div class="empty">No holdings changes found.</div>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>13F Net Flow — {as_of}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>13F Net Flow</h1>
  <div class="meta">Super-investor buys &amp; sells &middot; quarter as of {escape(str(as_of))}
    &middot; {dataset.get('investor_count', 0)} investors &middot; generated {escape(when)}</div>
  {_render_source_health(dataset)}
  <nav><a href="./index.html">&larr; Daily briefing</a><a href="./manage.html">Manage tickers</a></nav>
</header>
<div class="controls">
  <button class="toggle-btn" onclick="toggleWatchlist(this)">Show only my watchlist</button>
  <span class="issuer">Tap a row for per-investor detail &middot; tap a column to sort</span>
</div>
{body}
<footer>13F data is filed quarterly with a ~45-day lag and shows long positions only
(no shorts, options excluded). Directional context, not real-time. Not investment advice.</footer>
<script>{JS}</script>
</body>
</html>
"""
