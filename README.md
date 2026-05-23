# Stock Briefing

A personal pre-market stock briefing tool. Fetches yesterday's price moves and
news for a list of stocks you monitor, runs plug-in signals (drops without
news, alerts, etc.), and publishes a static HTML page to GitHub Pages.

## Architecture

The briefing runs on **GitHub Actions**, not in the Claude Code session. This
matters because the Claude Code web sandbox has a restricted network allowlist
and can't reach Yahoo Finance — but a GitHub Actions runner has full network
access.

```
Claude Code web (IDE)        GitHub Actions (runtime)
  edit code, push       →      cron / manual trigger
                                installs deps
                                fetches yfinance + news
                                runs signals
                                writes docs/index.html
                                commits back to repo
                                              ↓
                                        GitHub Pages
                                        (public URL)
```

Four code layers, kept separate so each can be swapped without touching the
others:

1. **`src/data_layer/`** — fetch raw data (prices, news, fundamentals)
2. **`src/signals/`** — plug-in rules (one file per rule)
3. **`src/briefing/`** — assemble and rank the briefing
4. **`src/output/`** — render (terminal for dev, HTML for production)

Stocks, watchlist, and settings live in a single SQLite DB (`data/briefing.db`)
that is rebuilt from `data/seed_holdings.csv` at the start of every run.
The CSV is the source of truth — edit it to change what gets monitored.

## What the public sees

The output page exposes only ticker symbols, % moves, and headlines. It does
not include quantities, cost basis, or watchlist target prices, and the page
has a `noindex, nofollow` meta tag.

## Setup (one-time)

1. **Push to GitHub.** The repo at `learner-agent-sudo/stock-mon` is already
   set up; the workflow file lives at `.github/workflows/briefing.yml`.
2. **Enable GitHub Pages.** In repo settings → Pages, set the source to
   "Deploy from a branch", branch `claude/plan-v1-architecture-7beEc` (or
   `main` once merged), folder `/docs`.
3. **Trigger a manual run.** Actions tab → "Stock Briefing" → "Run workflow".
   Check the logs and the resulting `docs/index.html` commit.
4. **The cron runs daily** at 23:30 UTC Mon-Fri (= 07:30 HKT Tue-Sat). Edit
   the cron line in the workflow if you want a different time.

## Editing what gets monitored

Edit `data/seed_holdings.csv`. Columns:

```
symbol, exchange, currency, category, description,
target_price, drop_pct_threshold, quantity
```

- `category` is `HOLDING` or `WATCHLIST`
- `target_price` and `drop_pct_threshold` apply to watchlist rows only
- `quantity` is optional and never displayed publicly

## Local development

Inside the Claude Code session (no Yahoo network access), use the fixture:

```bash
STOCKMON_USE_FIXTURE=1 python scripts/run_briefing_to_html.py
```

This reads `tests/fixtures/prices_snapshot.json` and `news_snapshot.json`
instead of yfinance, so you can iterate on signal logic, ranking, or HTML
styling without network.

The `SessionStart` hook in `.claude/settings.json` reinstalls deps and
reseeds the DB at the start of every web session.

## Adding a new signal

1. Create `src/signals/your_signal.py` subclassing `Signal` from `base.py`.
2. Implement `.evaluate(stock, price, news, context)`.
3. Add an instance to `ENABLED_SIGNALS` in `src/briefing/builder.py`.

No other code changes needed.

## File map

```
.github/workflows/briefing.yml   # the production runtime
.claude/settings.json            # SessionStart hook for dev sessions
data/seed_holdings.csv           # source of truth for monitored stocks
docs/                            # GitHub Pages output
  index.html                     # the latest briefing
  history/                       # dated archive (html + json)
scripts/
  run_briefing_to_html.py        # entry point the workflow calls
  run_briefing.py                # terminal-only entry (dev)
  init_db.py
  load_seed.py
  load_holdings_from_ib.py
  add_watchlist.py
src/
  db.py, config.py
  data_layer/{prices,news,fundamentals}.py
  signals/{base,drop_without_news,drop_with_news,watchlist_target_hit,significant_gain}.py
  briefing/{builder,ranker}.py
  output/{terminal,html_page}.py
tests/
  fixtures/{prices,news}_snapshot.json
  test_data_coverage.py
```
