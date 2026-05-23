"""Render the briefing as a static HTML page for GitHub Pages.

Privacy: this page is served from a public Pages URL. We expose only
the ticker symbols, % moves, and headlines. We never include
quantities, cost basis, or watchlist target prices.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape

from ..briefing.builder import Briefing
from ..signals.base import SignalResult


SECTION_TITLES = {
    "watchlist_target_hit": ("Alerts", "alerts"),
    "drop_without_news":    ("Drops without news", "drop-no-news"),
    "drop_with_news":       ("Drops with news", "drop-news"),
    "significant_gain":     ("Significant gains", "gain"),
}

SECTION_ORDER = [
    "watchlist_target_hit",
    "drop_without_news",
    "drop_with_news",
    "significant_gain",
]


CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 880px; margin: 0 auto; padding: 1rem;
  color: #1f2328; background: #f6f8fa; line-height: 1.5;
}
header { margin-bottom: 1.25rem; }
h1 { font-size: 1.35rem; margin-bottom: .2rem; }
.meta { color: #57606a; font-size: .85rem; }
.stats { color: #57606a; font-size: .8rem; margin-top: .2rem; }
nav { margin-top: .4rem; font-size: .85rem; }
nav a { color: #0969da; text-decoration: none; margin-right: .75rem; }
nav a:hover { text-decoration: underline; }
.banner { padding: .5rem .75rem; border-radius: 6px; font-size: .82rem;
          margin-bottom: .75rem; }
.banner.warn { background: #fff8c5; border: 1px solid #d4a72c; color: #7d4e00; }
.banner.ok   { background: #dafbe1; border: 1px solid #4ac26b; color: #116329; }
.section { background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
           margin-bottom: .75rem; overflow: hidden; }
.section h2 { padding: .6rem .75rem; font-size: .95rem;
              background: #f6f8fa; border-bottom: 1px solid #d0d7de; }
.section.alerts h2       { background: #ddf4ff; border-color: #54aeff; }
.section.drop-no-news h2 { background: #ffebe9; border-color: #ff8182; }
.section.drop-news h2    { background: #fff8c5; border-color: #d4a72c; }
.section.gain h2         { background: #dafbe1; border-color: #4ac26b; }
.result { padding: .6rem .75rem; border-top: 1px solid #eaeef2; }
.result:first-of-type { border-top: 0; }
.stock-header { display: flex; flex-wrap: wrap; align-items: baseline;
                gap: .25rem .5rem; }
.symbol { font-weight: 700; font-size: 1rem; }
.stock-name { color: #57606a; font-size: .82rem; }
.pct { font-weight: 600; font-size: .95rem; }
.pct.up   { color: #1a7f37; }
.pct.down { color: #cf222e; }
.price { color: #57606a; font-size: .85rem; }
.headline { margin-top: .25rem; font-size: .88rem; color: #424a53; }
.news { margin-top: .3rem; padding-left: 1rem; font-size: .82rem;
        list-style: disc; }
.news li { margin: .15rem 0; }
.news a { color: #0969da; text-decoration: none; }
.news a:hover { text-decoration: underline; }
.news .src { color: #57606a; font-size: .75rem; margin-left: .2rem; }
.empty { color: #57606a; font-style: italic; padding: 1rem; }
footer { color: #57606a; font-size: .75rem; margin-top: 1rem;
         text-align: center; padding-bottom: .5rem; }

@media (max-width: 600px) {
  body { padding: .6rem; }
  h1 { font-size: 1.15rem; }
  .section h2 { padding: .5rem .6rem; font-size: .88rem; }
  .result { padding: .5rem .6rem; }
  .stock-header { gap: .15rem .35rem; }
  .symbol { font-size: .92rem; }
  .stock-name { font-size: .78rem; }
  .pct { font-size: .88rem; }
  .price { font-size: .8rem; }
  .headline { font-size: .82rem; }
  .news { font-size: .78rem; padding-left: .8rem; }
  nav a { margin-right: .5rem; font-size: .8rem; }
  footer { font-size: .7rem; }
}
"""


def _redact_watchlist_headline(r: SignalResult) -> str:
    """Strip absolute target price from the headline for public output."""
    if r.signal_name != "watchlist_target_hit":
        return r.headline
    pct = r.details.get("pct_change")
    if pct is not None and pct < 0:
        return f"{r.symbol} watchlist alert: dropped {pct:+.2f}%"
    return f"{r.symbol} watchlist alert triggered"


def _render_result(r: SignalResult) -> str:
    pct = r.details.get("pct_change")
    last = r.details.get("last_close")
    desc = r.details.get("description") or ""

    pct_html = ""
    if pct is not None:
        cls = "up" if pct >= 0 else "down"
        pct_html = f'<span class="pct {cls}">{pct:+.2f}%</span>'

    price_html = f'<span class="price">@ {last:.2f}</span>' if last is not None else ""
    name_html = f'<span class="stock-name">{escape(desc)}</span>' if desc else ""
    headline = _redact_watchlist_headline(r)

    news_html = ""
    if r.news:
        items = "".join(
            f'<li><a href="{escape(n.url)}" target="_blank" rel="noopener">{escape(n.headline)}</a>'
            f'<span class="src">({escape(n.source)})</span></li>'
            for n in r.news[:3]
        )
        news_html = f'<ul class="news">{items}</ul>'

    return (
        f'<div class="result">'
        f'<div class="stock-header">'
        f'<span class="symbol">{escape(r.symbol)}</span>'
        f'{name_html} {pct_html} {price_html}'
        f'</div>'
        f'<p class="headline">{escape(headline)}</p>'
        f'{news_html}'
        f'</div>'
    )


def render(b: Briefing) -> str:
    generated_local = datetime.fromisoformat(b.generated_at).astimezone(timezone.utc)
    when = generated_local.strftime("%Y-%m-%d %H:%M UTC")

    grouped: dict[str, list[SignalResult]] = defaultdict(list)
    for r in b.results:
        grouped[r.signal_name].append(r)

    sections: list[str] = []
    for name in SECTION_ORDER:
        if name not in grouped:
            continue
        title, css_class = SECTION_TITLES[name]
        body = "".join(_render_result(r) for r in grouped[name])
        sections.append(
            f'<section class="section {css_class}">'
            f'<h2>{escape(title)} ({len(grouped[name])})</h2>{body}</section>'
        )

    body_html = "".join(sections) or '<div class="empty">No signals matched. Quiet day.</div>'
    stats = b.stats

    yahoo_ok = stats.get("yahoo_available", True)
    total_articles = stats.get("total_articles", 0)
    tickers_with_news = stats.get("tickers_with_news", 0)
    banner = ""
    if not yahoo_ok:
        banner = '<div class="banner warn">Yahoo Finance RSS is not available. News sourced from Google News only.</div>'

    news_line = f"{total_articles} articles from {tickers_with_news} tickers" if total_articles else "no articles found"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Stock Briefing — {when}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>Stock Briefing</h1>
  <div class="meta">Generated {when}</div>
  <div class="stats">
    {stats.get('stocks_checked', 0)} stocks &middot;
    {stats.get('prices_ok', 0)} ok &middot;
    {stats.get('prices_failed', 0)} failed &middot;
    {stats.get('signals_matched', 0)} signals &middot;
    {news_line}
  </div>
  <nav>
    <a href="./manage.html">Manage tickers &rarr;</a>
    <a href="./demo.html">View demo</a>
  </nav>
</header>
{banner}
{body_html}
<footer>Personal monitoring tool. Not investment advice.</footer>
</body>
</html>
"""
