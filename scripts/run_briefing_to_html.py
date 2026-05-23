"""Build the briefing and write it to docs/ for GitHub Pages.

Outputs:
- docs/index.html        — the latest briefing (overwritten each run)
- docs/history/<date>.html — a dated archive copy
- docs/history/<date>.json — raw payload for backtesting later

Also persists the briefing to the local SQLite history table.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.briefing import builder
from src.output import html_page, terminal


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
HISTORY_DIR = DOCS_DIR / "history"


def main() -> None:
    db.init_schema()
    db.seed_default_settings()

    briefing = builder.build()
    terminal.print_briefing(briefing)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    html = html_page.render(briefing)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")

    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    (HISTORY_DIR / f"{date_tag}.html").write_text(html, encoding="utf-8")

    payload = builder.to_json_payload(briefing)
    (HISTORY_DIR / f"{date_tag}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    briefing_id = db.save_briefing(json.dumps(payload))
    print(f"\nWrote docs/index.html and docs/history/{date_tag}.{{html,json}}")
    print(f"Saved briefing #{briefing_id} to local history.")


if __name__ == "__main__":
    main()
