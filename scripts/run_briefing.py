"""Main entry point: fetch data, run signals, rank, print, persist."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.briefing import builder
from src.output import terminal


def main() -> None:
    db.init_schema()
    db.seed_default_settings()

    briefing = builder.build()
    terminal.print_briefing(briefing)

    payload = builder.to_json_payload(briefing)
    briefing_id = db.save_briefing(json.dumps(payload))
    print(f"\nSaved briefing #{briefing_id} to history.")


if __name__ == "__main__":
    main()
