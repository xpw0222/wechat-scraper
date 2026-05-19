from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from wechat_scraper.config import load_config
from wechat_scraper.db import count_pending, count_total
from wechat_scraper.pipeline import run as run_pipeline
from wechat_scraper.store import iter_records

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "logs"


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    data_dir = Path(args.data_dir)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"run-{ts}.jsonl"
    with open(log_path, "w", encoding="utf-8") as log_fp:
        counts = run_pipeline(
            cfg=cfg,
            data_dir=data_dir,
            start_id=args.start_id,
            end_id=args.end_id,
            batch_size=args.batch,
            bind_ip=args.bind_ip,
            limit=args.limit,
            progress_every=args.progress_every,
            log_fp=log_fp,
        )
    print(json.dumps(counts, indent=2, ensure_ascii=False))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    recs = iter_records(data_dir)
    if not recs:
        print("no records found")
        return 0
    by_status = Counter(r.get("status", "?") for r in recs)
    print(f"total records: {len(recs)}")
    for status, n in by_status.most_common():
        print(f"  {status:14s} {n}")
    success_like = by_status.get("success", 0)
    terminal = sum(by_status.values())
    if terminal:
        print(f"success ratio: {success_like / terminal:.1%}")
    return 0


def cmd_inspect_db(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    total = count_total(cfg.db)
    pending_all = count_pending(cfg.db)
    print(f"gzharticlelist2 total rows: {total}")
    print(f"pending (Iscrawled in (NULL,0), non-empty URL): {pending_all}")
    if args.start_id is not None or args.end_id is not None:
        pending_range = count_pending(cfg.db, args.start_id, args.end_id)
        lo = args.start_id if args.start_id is not None else "-inf"
        hi = args.end_id if args.end_id is not None else "+inf"
        print(f"pending in [{lo}, {hi}): {pending_range}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wechat-scraper")
    p.add_argument("--config", default=None, help="path to config.ini (default: project config/config.ini)")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="fetch & parse a range of pending URLs")
    pr.add_argument("--start-id", type=int, required=True)
    pr.add_argument("--end-id", type=int, required=True)
    pr.add_argument("--batch", type=int, default=200)
    pr.add_argument("--bind-ip", default=None, help="local source IP to bind outgoing sockets to")
    pr.add_argument("--limit", type=int, default=None, help="stop after N rows processed")
    pr.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    pr.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    pr.add_argument("--progress-every", type=int, default=50)
    pr.set_defaults(func=cmd_run)

    ps = sub.add_parser("stats", help="summarise written shards by status")
    ps.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ps.set_defaults(func=cmd_stats)

    pi = sub.add_parser("inspect-db", help="print row counts (read-only)")
    pi.add_argument("--start-id", type=int, default=None)
    pi.add_argument("--end-id", type=int, default=None)
    pi.set_defaults(func=cmd_inspect_db)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.log_level)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
