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
# 核心改动：引入 RotatingFetchBridge
from wechat_scraper.fetch import FetchError, FetchTimeout, RotatingFetchBridge
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

    # 核心改动：初始化多 IP 持久化连接桥
    bridge = RotatingFetchBridge(cfg.http, cfg.network, override_bind_ip=bind_ip)
    
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

            # 针对当前 URL，允许在遇到 verify 时换 IP 重试（最大重试次数 = 可用 IP 总数）
            max_retries = len(bridge.ips)
            attempt = 0
            
            while attempt < max_retries:
                fetched_at = _now_iso()
                http_status: int | None = None
                elapsed_ms: int | None = None
                raw_len = 0
                err: str | None = None
                
                try:
                    # 使用当前 IP 发送请求，并带回 egress_ip
                    fr = bridge.fetch(row.url)
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

                # 判定是否触发反爬验证码
                if status == "verify":
                    attempt += 1
                    # 轮换到下一个 IP，并获取新 IP 供日志打印
                    old_ip = bridge.current_ip
                    new_ip = bridge.rotate()
                    log.warning(
                        "verify hit id=%d via IP %s. Rotating to %s (Attempt %d/%d)", 
                        row.id, old_ip, new_ip, attempt, max_retries
                    )
                    
                    if attempt < max_retries:
                        # 冷却一下立刻用新 IP 重试该 URL
                        time.sleep(random.uniform(0.5, 1.5))
                        continue
                    else:
                        # 所有 IP 全被耗尽拦截，只能放弃本条
                        log.error("All IPs in pool hit verify for id=%d. Skipping.", row.id)
                        break
                else:
                    # 抓取成功（或普通的非 verify 错误如 404/deleted），跳出重试循环
                    break

            # 统计与存储
            if status == "verify":
                # 如果经历了所有重试依然是 verify，不写入文件，维持 pending 供下次运行
                counts[status] = counts.get(status, 0) + 1
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
                # 日志中加上当前活跃 IP 标识，极大方便在 tmux 终端进行运维肉眼观测
                log.info(
                    "progress id=%d processed=%d current_ip=%s counts=%s", 
                    row.id, processed, bridge.current_ip, counts
                )

            time.sleep(random.uniform(cfg.http.sleep_min, cfg.http.sleep_max))

    log.info("done: processed=%d counts=%s", processed, counts)
    return counts
