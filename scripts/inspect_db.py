#!/usr/bin/env python3
"""Sanity-check the RDS counts. Read-only."""
from __future__ import annotations

import sys
from pathlib import Path

# allow running directly without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_scraper.config import load_config  # noqa: E402
from wechat_scraper.db import count_pending, count_total  # noqa: E402


def main() -> int:
    cfg = load_config()
    print(f"host    : {cfg.db.host}")
    print(f"database: {cfg.db.database}")
    print(f"total rows in gzharticlelist2 : {count_total(cfg.db)}")
    print(f"pending (Iscrawled in (NULL,0), non-empty URL): {count_pending(cfg.db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
