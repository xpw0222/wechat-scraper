# wechat_scraper

Clean rewrite of the WeChat article text-scraping pipeline. Reads pending URLs from the shared RDS, fetches each, extracts clean text, writes JSONL.gz shards.

See `REQUIREMENTS.md` for the full spec.

## Layout

```
src/wechat_scraper/    package: config, db, fetch, parse, store, pipeline, cli
tests/                 unittest suite + HTML fixtures
config/                config.example.ini (committed) + config.ini (gitignored)
scripts/               read-only DB sanity tools
data/raw/              JSONL.gz shards, one per 10k ids (gitignored)
data/logs/             per-run JSONL logs (gitignored)
```

## Setup

The deps (`requests`, `beautifulsoup4`, `lxml`, `pymysql`) are expected to be available system-wide (they already are on the Aliyun ECS). If running elsewhere:

```
pip install requests beautifulsoup4 lxml pymysql
```

Copy `config/config.example.ini` to `config/config.ini` and fill in RDS credentials. The example file already has the host pre-filled — only `user` and `password` need replacing.

## Commands

Run from the project root with the source dir on `PYTHONPATH`:

```bash
export PYTHONPATH=src
python -m wechat_scraper --help
```

### Sanity-check the DB (read-only)
```bash
python -m wechat_scraper inspect-db
python -m wechat_scraper inspect-db --start-id 3000000 --end-id 3500000
```

### Run the scraper
```bash
python -m wechat_scraper run --start-id 3000000 --end-id 3500000 --batch 200
```

Useful flags:
- `--limit N` — stop after N rows (smoke testing)
- `--bind-ip 192.168.x.y` — bind outgoing socket to a specific local IP
- `--progress-every 50` — stderr progress cadence
- `--data-dir path/to/data/raw` — override the shard output dir

### Summarise written shards
```bash
python -m wechat_scraper stats
```

### Resume

The pipeline scans existing shards on startup, so re-running the same `--start-id`/`--end-id` simply continues. No state file to manage.

## Long-running production runs

```bash
# Background with logs to file
nohup env PYTHONPATH=src python -m wechat_scraper run \
    --start-id 3000000 --end-id 3500000 --batch 200 \
    > data/logs/stdout.log 2>&1 &
disown

# Or in a tmux session for live attach:
tmux new -s scrape
PYTHONPATH=src python -m wechat_scraper run --start-id 3000000 --end-id 3500000
# Ctrl-b d to detach, `tmux attach -t scrape` to reattach
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover tests -v
```

## Known constraints

- **Single egress IP** — the current Aliyun host has one public IP, and WeChat rate-limits aggressively. Expect a high `verify` rate; those rows are left pending for a future retry. Resolving this requires the multi-IP plan in `/root/RuleOfLawEconomicDownturn/scraping/过程文档/多IP轮换爬取工作流.md`.
- **No writes to the production table.** The shared `gzh.gzharticlelist2.content` column is never touched. If/when downstream wants SQL access, write a one-shot migration that reads `data/raw/*.jsonl.gz` and INSERTs into a new side table.
