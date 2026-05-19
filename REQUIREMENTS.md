# wechat_scraper — requirements

## Purpose

Read pending WeChat article URLs from the shared Aliyun RDS, fetch each page, extract clean text, and write results to local sharded JSONL.gz files.

The shared production table is **read-only** for this project. The scraped content does not flow back into `gzharticlelist2.content`; downstream consumers should treat `data/raw/*.jsonl.gz` as the new source of truth.

## Input

- RDS MySQL: `rm-wz9rso27picw6w8q77o.mysql.rds.aliyuncs.com:3306`, db `gzh`, table `gzharticlelist2`.
- SELECT-only on columns: `id`, `ContentUrl`, `Title`, `Iscrawled`.
- Pending row predicate: `(Iscrawled IS NULL OR Iscrawled = 0) AND ContentUrl IS NOT NULL AND ContentUrl <> ''`.
- Caller restricts the work by `--start-id` / `--end-id`.

## Processing

1. Cursor-paginate pending rows in id order, batch size configurable (default 200).
2. For each url, issue one `GET` via `requests.Session`. Optional `--bind-ip` binds the outgoing socket to a specific local IP via `SourceAddressAdapter`.
3. Classify the response into one terminal status:
   - `success` — content extracted (≥ 50 chars)
   - `empty` — page returned but no `content_noencode` found, or extraction shorter than 50 chars
   - `deleted` — markers `内容已被发布者删除` / `此内容因违规无法查看`
   - `verify` — captcha markers (`安全验证 / 滑块验证 / 环境异常 / 完成验证 / secitptpage / cap_appid / poc_token / cap_sid`)
   - `http_error` — status code ≠ 200
   - `timeout` — `requests.exceptions.Timeout`
   - `fetch_error` — other network exception
   - `parse_error` — exception during HTML parsing
4. Extract text: regex match either `content_noencode: JsDecode('...')` (older form) or `content_noencode: '...'` (newer form), unwrap hex escapes (`\x3c`, etc.), parse with BeautifulSoup+lxml, drop `<script>/<style>`, concatenate text nodes, retain `<img>` as `[IMG:src]`.
5. Sleep `uniform(sleep_min, sleep_max)` between requests.

## Output

- Path: `data/raw/{id // 10_000:06d}.jsonl.gz` — one gzip-JSONL file per 10k id range.
- Record schema (one JSON object per line):
  ```
  {
    "id": int,
    "url": str,
    "fetched_at": ISO-8601 UTC,
    "status": one of the above,
    "http_status": int | null,
    "content_text": str,
    "raw_html_len": int,
    "error": str | null,
    "elapsed_ms": int | null
  }
  ```
- `verify` is the only status **not** written: leaves the id pending so a future run (with rotated IP / solved captcha) can retry.
- All other statuses, including `empty` and `deleted`, are written. This makes coverage measurable without re-fetching.

## Resumability

On startup, the pipeline scans every shard file overlapping the requested `[start_id, end_id)` range, builds the set of already-written ids, and skips them in the read loop. No external state file is used.

## Logging

- One JSONL record per attempt to `data/logs/run-<utc>.jsonl` (full record minus `content_text` to keep size manageable).
- Human progress to stderr every `--progress-every` records (default 50).

## Configuration

- `config/config.ini` — RDS credentials (`[database]`) + HTTP defaults (`[http]`).
- A template at `config/config.example.ini` is committed; the real `config.ini` is `.gitignore`d.

## Out of scope

- Multi-IP rotation, `ip_pool_status` table, hermes captcha solver. The single-IP design has a known high verify rate; rotating IPs requires resolving Stage 0 of `/root/RuleOfLawEconomicDownturn/scraping/过程文档/多IP轮换爬取工作流.md` (multiple distinct public egress IPs).
- Backfilling `gzharticlelist2.content`.
- The manual public-account onboarding workflow (29 unresolved 公众号 in `/root/RuleOfLawEconomicDownturn/scraping/过程文档/手动处理指南.md`) — that's a `gzhlist`-seeding task, not a content-scraping task.
- Saving raw HTML to disk. Records carry `raw_html_len` only.

## Verification (acceptance)

- `python -m unittest discover tests` — all tests green.
- `python -m wechat_scraper inspect-db` — reports the current pending count.
- A short live run (`--limit 5`) writes shards under `data/raw/` and the second invocation of the same command logs `resume: N ids already done` and does not re-fetch them.
- `python -m wechat_scraper stats` reports a non-zero `success` count.
