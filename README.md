# Stock Briefing

A personal pre-market stock briefing tool. Fetches yesterday's price moves and
news for your holdings and watchlist, runs a set of plug-in signals
(drops without news, watchlist targets hit, etc.), and prints a ranked briefing.

## Architecture

Four layers, kept separate so each can be swapped without touching the others:

1. **`src/data_layer/`** — fetch raw data (prices, news, fundamentals)
2. **`src/signals/`** — plug-in rules (one file per rule)
3. **`src/briefing/`** — assemble and rank the briefing
4. **`src/output/`** — render (terminal now, email and HTML later)

State lives in a single SQLite database at `data/briefing.db` (holdings,
watchlist, settings, history).

## Phases

- **Phase 1 (current):** local script, terminal output. De-risks data quality.
- **Phase 2:** email delivery.
- **Phase 3:** scheduled run + HTML page.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env       # edit if needed
python scripts/init_db.py  # creates data/briefing.db with default settings
```

## Loading holdings from Interactive Brokers

```bash
python scripts/load_holdings_from_ib.py path/to/ib_activity_statement.csv
```

## Adding a watchlist entry

```bash
python scripts/add_watchlist.py SYMBOL --target-price 123.45 --drop-pct 5
```

## Running the briefing

```bash
python scripts/run_briefing.py
```

## Verifying data coverage

Before relying on the briefing, check that yfinance covers every ticker:

```bash
python -m pytest tests/test_data_coverage.py -s
```

This prints three lists: tickers that worked, failed, and looked suspicious.
Fix exchange suffixes (e.g. `TXT` → `TXT.WA` for Warsaw) before continuing.

## Adding a new signal

1. Create `src/signals/your_signal.py` subclassing `Signal` from `base.py`.
2. Implement `.evaluate(stock, price_data, news_data)`.
3. Add it to the enabled signals list in `src/briefing/builder.py`.

No other code changes needed.
