from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from wechat_scraper.config import AppConfig
from wechat_scraper.db import PendingUrl, read_pending_urls
from wechat_scraper.fetch import FetchError, FetchTimeout, build_session, fetch_one
from wechat_scraper.parse import parse
from wechat_scraper.store import Record, ShardWriter, load_done_ids

log = logging.getLogger("wechat_scraper.pipeline")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(
    cfg: AppConfig,
    data_dir: Path,
    start_id: int,
    end_id: int,
    batch_size: int = 200,
    bind_ip: str | None = None,
    limit: int | None = None,
    progress_every: int = 50,
    log_fp=None,
) -> dict[str, int]:
    """Process pending URLs in [start_id, end_id); return status histogram."""
    done = load_done_ids(data_dir, start_id, end_id)
    log.info("resume: %d ids already done in range", len(done))

    session = build_session(cfg.http, bind_ip=bind_ip)
    counts: dict[str, int] = {}
    processed = 0

    def emit(rec: Record) -> None:
        if log_fp is not None:
            payload = asdict(rec) | {"content_text": None}
            log_fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
            log_fp.flush()

    with ShardWriter(data_dir) as writer:
        rows: Iterable[PendingUrl] = read_pending_urls(cfg.db, start_id, end_id, batch_size)
        for row in rows:
            if row.id in done:
                continue
            if limit is not None and processed >= limit:
                break

            fetched_at = _now_iso()
            http_status: int | None = None
            elapsed_ms: int | None = None
            raw_len = 0
            err: str | None = None
            try:
                fr = fetch_one(session, row.url, timeout=cfg.http.timeout_seconds)
                http_status = fr.http_status
                elapsed_ms = fr.elapsed_ms
                raw_len = len(fr.body)
                if http_status != 200:
                    status = "http_error"
                    content_text = ""
                    err = f"HTTP {http_status}"
                else:
                    pr = parse(fr.body)
                    status = pr.status
                    content_text = pr.content_text
                    err = pr.error
            except FetchTimeout as e:
                status = "timeout"
                content_text = ""
                err = str(e)
            except FetchError as e:
                status = "fetch_error"
                content_text = ""
                err = str(e)

            if status == "verify":
                log.warning("verify hit id=%d — not writing, will retry next run", row.id)
            else:
                rec = Record(
                    id=row.id,
                    url=row.url,
                    fetched_at=fetched_at,
                    status=status,
                    http_status=http_status,
                    content_text=content_text,
                    raw_html_len=raw_len,
                    error=err,
                    elapsed_ms=elapsed_ms,
                )
                writer.write(rec)
                emit(rec)

            counts[status] = counts.get(status, 0) + 1
            processed += 1
            if processed % progress_every == 0:
                log.info("progress id=%d processed=%d counts=%s", row.id, processed, counts)

            time.sleep(random.uniform(cfg.http.sleep_min, cfg.http.sleep_max))

    log.info("done: processed=%d counts=%s", processed, counts)
    return counts
