import tempfile
import unittest
from pathlib import Path

from wechat_scraper.store import (
    Record,
    ShardWriter,
    iter_records,
    load_done_ids,
    shard_id_for,
    shard_path,
)


def _rec(rid: int, status: str = "success") -> Record:
    return Record(
        id=rid,
        url=f"https://mp.weixin.qq.com/s/{rid}",
        fetched_at="2026-05-19T00:00:00+00:00",
        status=status,
        http_status=200,
        content_text=f"text-{rid}",
        raw_html_len=1000,
        error=None,
        elapsed_ms=120,
    )


class TestShardLayout(unittest.TestCase):
    def test_shard_id_for(self):
        self.assertEqual(shard_id_for(0), 0)
        self.assertEqual(shard_id_for(9999), 0)
        self.assertEqual(shard_id_for(10000), 1)
        self.assertEqual(shard_id_for(3_000_001), 300)

    def test_shard_path(self):
        d = Path("/tmp/x")
        self.assertEqual(shard_path(d, 3_000_001).name, "000300.jsonl.gz")

    def test_shard_boundary_at_million_mark(self):
        # Records on either side of a SHARD_SIZE boundary land in adjacent shards.
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            with ShardWriter(data_dir) as w:
                w.write(_rec(2_999_999))
                w.write(_rec(3_000_000))
            names = sorted(p.name for p in data_dir.glob("*.jsonl.gz"))
            self.assertEqual(names, ["000299.jsonl.gz", "000300.jsonl.gz"])


class TestRoundtrip(unittest.TestCase):
    def test_write_then_iter(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            with ShardWriter(data_dir) as w:
                for rid in (3_000_001, 3_000_002, 3_010_001):
                    w.write(_rec(rid))
            recs = iter_records(data_dir)
            self.assertEqual({r["id"] for r in recs}, {3_000_001, 3_000_002, 3_010_001})
            # two shards: 300 and 301
            shards = sorted(p.name for p in data_dir.glob("*.jsonl.gz"))
            self.assertEqual(shards, ["000300.jsonl.gz", "000301.jsonl.gz"])

    def test_appends_across_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            with ShardWriter(data_dir) as w:
                w.write(_rec(3_000_001))
            with ShardWriter(data_dir) as w:
                w.write(_rec(3_000_002))
            recs = iter_records(data_dir)
            self.assertEqual({r["id"] for r in recs}, {3_000_001, 3_000_002})


class TestResume(unittest.TestCase):
    def test_load_done_ids_within_range(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            with ShardWriter(data_dir) as w:
                for rid in (3_000_001, 3_000_005, 3_010_001):
                    w.write(_rec(rid))
            done = load_done_ids(data_dir, 3_000_000, 3_010_000)
            self.assertEqual(done, {3_000_001, 3_000_005})

    def test_load_done_ids_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_done_ids(Path(td), 0, 1_000), set())

    def test_recovers_records_before_corrupt_tail(self):
        # Simulate a SIGKILL mid-write: write valid records, then truncate
        # the final gzip member. The records that completed should still be
        # recovered for resume; the truncated tail should not poison the shard.
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            with ShardWriter(data_dir) as w:
                for rid in (3_000_001, 3_000_002, 3_000_003):
                    w.write(_rec(rid))
            shard = data_dir / "000300.jsonl.gz"
            original = shard.read_bytes()
            # Drop the last 5 bytes — destroys the final gzip member's trailer.
            shard.write_bytes(original[:-5])
            done = load_done_ids(data_dir, 3_000_000, 3_010_000)
            # At minimum the first two records must come back; the third may
            # or may not depending on where the truncation lands within its
            # member. The critical contract is "not empty".
            self.assertGreaterEqual(len(done), 2)
            self.assertIn(3_000_001, done)
            self.assertIn(3_000_002, done)


class TestConcurrentWriters(unittest.TestCase):
    def test_two_processes_into_same_shard(self):
        # Regression: two `python -m wechat_scraper run` instances aimed at
        # the same data_dir would interleave gzip-member writes on the shared
        # shard file. Without an exclusive flock around each write a single
        # `write()` syscall holding a large gzip member could be torn,
        # corrupting subsequent records and silently dropping ids on resume.
        # Force the worst case: large random (incompressible) content so each
        # gzip member exceeds stdio BUFSIZ.
        import multiprocessing as mp
        import os
        import random
        import string

        def _worker(data_dir_str: str, start: int, n: int) -> None:
            # Re-import inside the child to avoid pickling issues.
            from pathlib import Path as _P

            from wechat_scraper.store import Record as _R
            from wechat_scraper.store import ShardWriter as _SW

            rng = random.Random(start)
            w = _SW(_P(data_dir_str))
            for i in range(n):
                # 50KB of pseudo-random ASCII per record. Even after gzip this
                # leaves >40KB per member — well above the 8KB stdio buffer.
                payload = "".join(rng.choices(string.ascii_letters, k=50_000))
                w.write(
                    _R(
                        id=start + i,
                        url=f"u{start + i}",
                        fetched_at="t",
                        status="success",
                        http_status=200,
                        content_text=payload,
                        raw_html_len=len(payload),
                        error=None,
                        elapsed_ms=10,
                    )
                )

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
            procs = [
                ctx.Process(target=_worker, args=(str(data_dir), start, 25))
                for start in (3_000_000, 3_000_100, 3_000_200, 3_000_300)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=30)
                self.assertEqual(p.exitcode, 0, "worker crashed")

            expected: set[int] = set()
            for s in (3_000_000, 3_000_100, 3_000_200, 3_000_300):
                expected |= set(range(s, s + 25))
            done = load_done_ids(data_dir, 3_000_000, 3_010_000)
            # All 100 unique ids must be recoverable — interleaving must not
            # corrupt the gzip stream.
            self.assertEqual(done, expected)


class TestCrashSafety(unittest.TestCase):
    def test_each_write_produces_recoverable_file(self):
        # After every single write the file on disk should be a valid gzip
        # that decodes to exactly the records written so far. This is the
        # SIGKILL-between-writes guarantee.
        import gzip
        import json

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            writer = ShardWriter(data_dir)
            for n, rid in enumerate((3_000_001, 3_000_002, 3_000_003), 1):
                writer.write(_rec(rid))
                shard = data_dir / "000300.jsonl.gz"
                with gzip.open(shard, "rt", encoding="utf-8") as f:
                    recs = [json.loads(l) for l in f if l.strip()]
                self.assertEqual(len(recs), n, f"after {n} writes")
            writer.close()


if __name__ == "__main__":
    unittest.main()
