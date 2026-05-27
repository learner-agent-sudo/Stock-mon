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
  font-family: "SF Mono", "Cascadia Mono", "Fira Code", "Consolas", monospace;
  max-width: 960px; margin: 0 auto; padding: 1rem;
  color: #e8e6e3; background: #0a0e17; line-height: 1.45;
}
header { margin-bottom: 1rem; border-bottom: 1px solid #1e2d3d;
         padding-bottom: .75rem; }
h1 { font-size: 1.1rem; color: #ff9800; letter-spacing: .06em;
     text-transform: uppercase; margin-bottom: .15rem; }
.meta { color: #6b7d8e; font-size: .78rem; }
.stats { color: #6b7d8e; font-size: .72rem; margin-top: .15rem; }
nav { margin-top: .35rem; font-size: .78rem; }
nav a { color: #ff9800; text-decoration: none; margin-right: .75rem;
        border-bottom: 1px dotted #ff980050; }
nav a:hover { color: #ffb74d; border-bottom-color: #ffb74d; }
.banner { padding: .4rem .6rem; border-radius: 3px; font-size: .75rem;
          margin-bottom: .6rem; border-left: 3px solid; }
.banner.warn { background: #1a1400; border-color: #ff9800; color: #ffb74d; }
.banner.ok   { background: #001a0a; border-color: #00e676; color: #69f0ae; }
.section { background: #0f1923; border: 1px solid #1e2d3d; border-radius: 4px;
           margin-bottom: .6rem; overflow: hidden; }
.section h2 { padding: .45rem .65rem; font-size: .78rem;
              text-transform: uppercase; letter-spacing: .08em;
              border-bottom: 1px solid #1e2d3d; font-weight: 600; }
.section.alerts h2       { background: #0d1b2a; color: #42a5f5; }
.section.drop-no-news h2 { background: #1a0a0a; color: #ef5350; }
.section.drop-news h2    { background: #1a1400; color: #ffb74d; }
.section.gain h2         { background: #0a1a0f; color: #66bb6a; }
.result { padding: .5rem .65rem; border-top: 1px solid #152233; }
.result:first-of-type { border-top: 0; }
.stock-header { display: flex; flex-wrap: wrap; align-items: baseline;
                gap: .15rem .4rem; }
.symbol { font-weight: 700; font-size: .92rem; color: #fff; }
.stock-name { color: #6b7d8e; font-size: .75rem; }
.pct { font-weight: 700; font-size: .88rem; }
.pct.up   { color: #00e676; }
.pct.down { color: #ff1744; }
.price { color: #6b7d8e; font-size: .78rem; }
.headline { margin-top: .2rem; font-size: .78rem; color: #8899aa; }
.news { margin-top: .3rem; }
.news-item { padding: .3rem 0 .3rem .65rem; border-left: 2px solid #1e2d3d;
             margin: .2rem 0; }
.news-title { color: #e8e6e3; font-size: .78rem; font-weight: 600; }
.news .src { color: #4a5568; font-size: .65rem; margin-left: .3rem;
             font-style: italic; }
.news-summary { color: #8899aa; font-size: .72rem; margin: .15rem 0 0;
                line-height: 1.4; }
.read-more { color: #ff9800; font-size: .68rem; text-decoration: none;
             display: inline-block; margin-top: .15rem; }
.read-more:hover { color: #ffb74d; text-decoration: underline; }
.empty { color: #6b7d8e; font-style: italic; padding: .75rem; font-size: .82rem; }
footer { color: #3a4a5a; font-size: .68rem; margin-top: .75rem;
         text-align: center; padding-bottom: .4rem;
         border-top: 1px solid #1e2d3d; padding-top: .5rem; }

@media (max-width: 600px) {
  body { padding: .5rem; }
  h1 { font-size: .95rem; }
  .section h2 { padding: .4rem .5rem; font-size: .72rem; }
  .result { padding: .4rem .5rem; }
  .stock-header { gap: .1rem .25rem; }
  .symbol { font-size: .85rem; }
  .stock-name { font-size: .7rem; }
  .pct { font-size: .82rem; }
  .price { font-size: .72rem; }
  .headline { font-size: .72rem; }
  .news-item { padding-left: .5rem; }
  .news-title { font-size: .72rem; }
  .news-summary { font-size: .68rem; }
  nav a { margin-right: .4rem; font-size: .72rem; }
  footer { font-size: .62rem; }
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
        parts: list[str] = []
        for n in r.news[:3]:
            title_html = f'<span class="news-title">{escape(n.headline)}</span>'
            src_html = f'<span class="src">{escape(n.source)}</span>'
            summary_html = ""
            if n.summary:
                summary_html = f'<p class="news-summary">{escape(n.summary)}</p>'
            link_html = ""
            if n.url:
                link_html = f'<a class="read-more" href="{escape(n.url)}" target="_blank" rel="noopener">Read more &rarr;</a>'
            parts.append(f'<div class="news-item">{title_html} {src_html}{summary_html}{link_html}</div>')
        news_html = f'<div class="news">{"".join(parts)}</div>'

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

    total_articles = stats.get("total_articles", 0)
    tickers_with_news = stats.get("tickers_with_news", 0)
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
{body_html}
<footer>Personal monitoring tool. Not investment advice.</footer>
</body>
</html>
"""
