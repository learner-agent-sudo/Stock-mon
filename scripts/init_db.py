"""First-time setup: create tables and seed default settings."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db


def main() -> None:
    db.init_schema()
    db.seed_default_settings()
    print(f"Initialized {db.get_db_path()}")
    print("Seeded default settings:")
    for key, value in db.DEFAULT_SETTINGS.items():
        print(f"  {key} = {value}")


if __name__ == "__main__":
    main()
