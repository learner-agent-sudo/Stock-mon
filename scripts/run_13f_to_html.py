"""Build the 13F net-flow page and write it to docs/ for GitHub Pages.

Outputs:
- docs/13f.html              — latest 13F net-flow view (overwritten)
- docs/history/13f-<date>.json — raw dataset archive
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.data_layer import holdings_13f
from src.output import holdings_13f_page

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
HISTORY_DIR = DOCS_DIR / "history"


def main() -> None:
    db.init_schema()
    db.seed_default_settings()

    try:
        rows = db.get_active_stocks()
        watchlist = {s["symbol"].upper() for s in rows}
        # TICKER -> 'HOLDING' | 'WATCHLIST' so the page can mark each stock.
        categories = {s["symbol"].upper(): s["category"] for s in rows}
    except Exception:  # noqa: BLE001 — page is still useful without watchlist matching
        watchlist = set()
        categories = {}

    dataset = holdings_13f.build_dataset(watchlist, categories)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    html = holdings_13f_page.render(dataset)
    (DOCS_DIR / "13f.html").write_text(html, encoding="utf-8")

    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (HISTORY_DIR / f"13f-{date_tag}.json").write_text(json.dumps(dataset, indent=2), encoding="utf-8")

    print(f"\nWrote docs/13f.html ({len(dataset.get('stocks', []))} stocks, "
          f"{dataset.get('investor_count', 0)} investors)")


if __name__ == "__main__":
    main()
