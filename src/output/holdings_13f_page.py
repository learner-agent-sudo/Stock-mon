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
.sources { font-size: .72rem; margin-top: .2rem; color: #6b7d8e; }
.src-tag { margin-right: .6rem; white-space: nowrap; }
.src-ok { color: #69f0ae; } .src-warn { color: #ffb74d; } .src-err { color: #ff6e6e; }
nav { margin-top: .35rem; font-size: .78rem; }
nav a { color: #ff9800; text-decoration: none; margin-right: .75rem; border-bottom: 1px dotted #ff980050; }
.controls { margin: .6rem 0; font-size: .76rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
.toggle-btn { background: #0f1923; color: #8899aa; border: 1px solid #1e2d3d;
  border-radius: 3px; padding: .25rem .6rem; cursor: pointer; font: inherit; font-size: .74rem; }
.toggle-btn.active { background: #0d1b2a; color: #42a5f5; border-color: #1565c0; }
table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .8rem; }
thead th { text-align: right; padding: .5rem .5rem; background: #0a0e17;
  border-bottom: 1px solid #1e2d3d; color: #6b7d8e; font-size: .7rem;
  text-transform: uppercase; letter-spacing: .05em; cursor: pointer;
  user-select: none; white-space: nowrap;
  /* Stick to the top of the viewport so the column meaning stays visible
     as the list scrolls. */
  position: sticky; top: 0; z-index: 10;
  /* The 1px border doesn't stick with the cell, so simulate it with a
     box-shadow that always sits at the bottom of the header. */
  box-shadow: inset 0 -1px 0 #1e2d3d; }
thead th.stock { text-align: left; }
thead th.sorted::after { content: " \\25BC"; color: #ff9800; }
tbody td { padding: .4rem .5rem; border-bottom: 1px solid #152233; text-align: right; vertical-align: top; }
tbody td.stock { text-align: left; }
tr.stock-row { cursor: pointer; }
tr.stock-row:hover { background: #0f1923; }
.ticker { font-weight: 700; color: #fff; }
.issuer { color: #6b7d8e; font-size: .72rem; }
.wl-dot { color: #42a5f5; margin-left: .3rem; font-size: .7rem; }
.pos { color: #00e676; } .neg { color: #ff1744; } .flat { color: #6b7d8e; }
.detail-row td { background: #0c141d; padding: .5rem .75rem; }
.detail-row.hidden { display: none; }
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
}
"""

JS = """
function fmtSortState(th){
  document.querySelectorAll('thead th').forEach(function(h){h.classList.remove('sorted');});
  th.classList.add('sorted');
}
function sortBy(key, th, mode){
  var tbody = document.querySelector('tbody');
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
    return f'<div class="sources">Investors: {"".join(tags)}</div>' if tags else ""


def _render_detail(stock: dict) -> str:
    items = []
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


def _render_row(stock: dict) -> str:
    cusip = escape(stock["cusip"])
    ticker = stock.get("ticker") or ""
    label = escape(ticker) if ticker else escape((stock.get("issuer") or "?")[:18])
    issuer = escape(stock.get("issuer") or "")
    wl = '<span class="wl-dot" title="In your watchlist">◆</span>' if stock.get("in_watchlist") else ""
    ni = stock["net_investors"]
    ni_txt = f"+{ni}" if ni > 0 else str(ni)
    # Sort key for the Stock column: ticker if mapped, else issuer name.
    stock_key = (ticker or stock.get("issuer") or "").upper()
    row = (
        f'<tr class="stock-row" data-cusip="{cusip}" '
        f'data-stock="{escape(stock_key)}" '
        f'data-net_investors="{ni}" data-net_value="{stock["net_value"]}" '
        f'data-net_shares="{stock["net_shares"]}" data-watchlist="{1 if stock.get("in_watchlist") else 0}" '
        f'onclick="expand(\'{cusip}\')">'
        f'<td class="stock"><span class="ticker">{label}</span>{wl}<br>'
        f'<span class="issuer">{issuer}</span></td>'
        f'<td class="{_cls(ni)}">{ni_txt}<br><span class="issuer">{stock["buyers"]}b / {stock["sellers"]}s</span></td>'
        f'<td class="{_cls(stock["net_value"])}">{_fmt_money(stock["net_value"])}</td>'
        f'<td class="{_cls(stock["net_shares"])}">{_fmt_shares(stock["net_shares"])}</td>'
        f'</tr>'
        f'<tr class="detail-row hidden" id="d-{cusip}"><td colspan="4">{_render_detail(stock)}</td></tr>'
    )
    return row


def render(dataset: dict[str, Any]) -> str:
    generated = dataset.get("generated_at", "")
    try:
        when = datetime.fromisoformat(generated).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        when = generated
    as_of = dataset.get("as_of") or "?"
    stocks = dataset.get("stocks", [])

    rows = "".join(_render_row(s) for s in stocks) if stocks else ""
    if stocks:
        body = (f'<table><thead><tr>'
                f'<th class="stock" onclick="sortBy(\'stock\', this, \'string\')">Stock</th>'
                f'<th class="sorted" onclick="sortBy(\'net_investors\', this)">Investors net</th>'
                f'<th onclick="sortBy(\'net_value\', this)">$ net</th>'
                f'<th onclick="sortBy(\'net_shares\', this)">Shares net</th>'
                f'</tr></thead><tbody>{rows}</tbody></table>')
    else:
        # Surface the actual error reason — one investor's detail is usually
        # representative when they all fail the same way (e.g. SEC HTTP 403).
        errs = [i.get("detail") for i in dataset.get("investors", [])
                if i.get("status") != "ok" and i.get("detail")]
        reason = f'<div class="empty"><strong>Error:</strong> {escape(errs[0])}<br>' \
                 f'<span class="issuer">All {len(dataset.get("investors", []))} investors failed with the same/similar error. ' \
                 f'Hover any ✗ above for that investor’s specific message.</span></div>' if errs \
                 else '<div class="empty">No holdings changes found.</div>'
        body = reason

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
