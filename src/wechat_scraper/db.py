from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Iterator

import pymysql

from wechat_scraper.config import DBConfig


@dataclass(frozen=True)
class PendingUrl:
    id: int
    url: str
    title: str | None


def _connect(db: DBConfig, connect_timeout: int = 30) -> pymysql.connections.Connection:
    return pymysql.connect(connect_timeout=connect_timeout, **db.as_pymysql_kwargs())


def read_pending_urls(
    db: DBConfig,
    start_id: int,
    end_id: int,
    batch_size: int = 200,
) -> Iterator[PendingUrl]:
    """Yield (id, url, title) for rows in [start_id, end_id) with Iscrawled in (NULL, 0)
    and a non-empty ContentUrl. Cursor-paginated by id; safe to interrupt.

    The connection is opened lazily on first iteration and guaranteed closed
    when the generator is exhausted, closed by the caller, or garbage-collected.
    """
    if start_id >= end_id:
        return
    cursor_id = start_id - 1
    with contextlib.closing(_connect(db)) as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, ContentUrl, Title
                    FROM gzharticlelist2
                    WHERE id > %s AND id < %s
                      AND (Iscrawled IS NULL OR Iscrawled = 0)
                      AND ContentUrl IS NOT NULL AND ContentUrl <> ''
                    ORDER BY id
                    LIMIT %s
                    """,
                    (cursor_id, end_id, batch_size),
                )
                rows = cur.fetchall()
            if not rows:
                return
            for rid, url, title in rows:
                yield PendingUrl(id=rid, url=url, title=title)
            cursor_id = rows[-1][0]


def count_pending(db: DBConfig, start_id: int | None = None, end_id: int | None = None) -> int:
    where = ["(Iscrawled IS NULL OR Iscrawled = 0)", "ContentUrl IS NOT NULL", "ContentUrl <> ''"]
    params: list = []
    if start_id is not None:
        where.append("id >= %s")
        params.append(start_id)
    if end_id is not None:
        where.append("id < %s")
        params.append(end_id)
    sql = f"SELECT COUNT(*) FROM gzharticlelist2 WHERE {' AND '.join(where)}"
    with contextlib.closing(_connect(db)) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def count_total(db: DBConfig) -> int:
    with contextlib.closing(_connect(db)) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM gzharticlelist2")
        return int(cur.fetchone()[0])
