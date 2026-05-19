from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover — Windows fallback
    fcntl = None  # type: ignore[assignment]

SHARD_SIZE = 10_000  # ids per shard

log = logging.getLogger(__name__)


@dataclass
class Record:
    id: int
    url: str
    fetched_at: str  # ISO-8601 UTC
    status: str
    http_status: int | None
    content_text: str
    raw_html_len: int
    error: str | None = None
    elapsed_ms: int | None = None


def shard_id_for(article_id: int) -> int:
    return article_id // SHARD_SIZE


def shard_path(data_dir: Path, article_id: int) -> Path:
    return data_dir / f"{shard_id_for(article_id):06d}.jsonl.gz"


class ShardWriter:
    """Crash-safe append-only gzip JSONL writer, sharded by id // SHARD_SIZE.

    Each `write()` opens the shard in append mode, writes one complete gzip
    member (gzip streams concatenate), and closes. A SIGKILL between writes
    leaves a well-formed file containing every previously-written record;
    a SIGKILL *during* a write loses only that single record because the
    final member's trailer never lands.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rec: Record) -> None:
        line = (json.dumps(asdict(rec), ensure_ascii=False) + "\n").encode("utf-8")
        path = shard_path(self.data_dir, rec.id)
        # Use a per-shard exclusive flock so two concurrent `run` instances
        # against the same data_dir cannot interleave gzip members within a
        # write() syscall. On Linux O_APPEND already guarantees atomic appends
        # up to PIPE_BUF/BUFSIZ, but the gzip member for an incompressible
        # payload can exceed that and tear. The lock is held only for the
        # duration of one record write — sub-millisecond — so contention is
        # negligible. fcntl is unavailable on Windows; fall back to the bare
        # append (Windows isn't a supported target).
        with open(path, "ab") as raw:
            if fcntl is not None:
                fcntl.flock(raw.fileno(), fcntl.LOCK_EX)
            try:
                with gzip.GzipFile(fileobj=raw, mode="ab") as gz:
                    gz.write(line)
                raw.flush()
                os.fsync(raw.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(raw.fileno(), fcntl.LOCK_UN)

    def close(self) -> None:
        # nothing to close — per-record open/close is the durability guarantee
        return

    def __enter__(self) -> "ShardWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def iter_shard_files(data_dir: Path, start_id: int, end_id: int) -> list[Path]:
    if not data_dir.exists():
        return []
    first = shard_id_for(start_id)
    last = shard_id_for(max(end_id - 1, start_id))
    out = []
    for sid in range(first, last + 1):
        p = data_dir / f"{sid:06d}.jsonl.gz"
        if p.exists():
            out.append(p)
    return out


def _iter_shard_records(path: Path):
    """Yield decoded records from a shard, tolerating a truncated tail.

    If gzip decompression fails partway through (e.g. SIGKILL mid-write left
    a corrupt final member), yield everything successfully decoded so far and
    log the truncation. Per-line JSON errors skip just that line.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            while True:
                try:
                    line = f.readline()
                except (OSError, EOFError, gzip.BadGzipFile) as e:
                    log.warning("shard %s truncated mid-stream: %s", path.name, e)
                    return
                if not line:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("shard %s has malformed JSON line, skipping", path.name)
                    continue
    except (OSError, gzip.BadGzipFile) as e:
        log.warning("shard %s unreadable: %s", path.name, e)


def load_done_ids(data_dir: Path, start_id: int, end_id: int) -> set[int]:
    """Scan existing shards covering [start_id, end_id) and return ids already written.

    Tolerates a truncated final gzip member in any shard: the records that were
    fully written are still recovered.
    """
    done: set[int] = set()
    for p in iter_shard_files(data_dir, start_id, end_id):
        for rec in _iter_shard_records(p):
            rid = rec.get("id")
            if isinstance(rid, int) and start_id <= rid < end_id:
                done.add(rid)
    return done


def iter_records(data_dir: Path) -> list[dict]:
    """Read every record across all shards. For stats only — loads into memory."""
    out: list[dict] = []
    if not data_dir.exists():
        return out
    for p in sorted(data_dir.glob("*.jsonl.gz")):
        out.extend(_iter_shard_records(p))
    return out
