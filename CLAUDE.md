use fable subagents when you need more intelligence

# What this project is

Tweet scraper for the Utrecht University "Wendy Project" (populism research):
collects Dutch party leaders' and party accounts' tweets via twscrape for
downstream populism analysis. **Scraping is FINISHED (July 2026)** — the full
dataset was collected on the server and validated; remaining server jobs are
4CAT uploads (`src/upload_4cat.py`) and the nightly SURFdrive backup.
`README.md` documents the pipeline; `docs/GUIDE.md` is the script-by-script
code walkthrough; `deploy/README.md` documents the server deployment; `docs/`
has the design spec.

**Dataset rules** (`src/collect_dataset.py`): study window 2017-03-23 (TK installation) →
2025-11-12 (TK election, `CEILING`); leaders scraped only for their tenure
(`frame/leaders.csv`, `ongoing` → ceiling); party accounts per `frame/parties.csv`
seat windows.
Output is one `data/dataset/<Party>/<handle>.{sqlite,ndjson}` pair per target
(git-ignored). Raw GraphQL tweet JSON, `tweet_id` PK, `INSERT OR IGNORE` — re-runs
are idempotent and can only add tweets.

# Production ran on the home server, not this Mac

The authoritative dataset lives on a Proxmox LXC; `data/dataset_server/` is the
local rsync mirror (the local `data/dataset/` scratch copy was deleted in the
2026-07 cleanup). The Rutte-archive experiment output lives in
`experiments/rutte-archive/` (git-ignored), not under `data/`.

| | |
|---|---|
| Container | CTID 106 `populism-scraper`, Debian 13, unprivileged |
| Address | `192.168.1.106` — `ssh -i ~/.ssh/id_ed25519_scraper root@192.168.1.106` |
| Install | `/opt/populism-scraping` (venv at `.venv/`) |
| Schedule | `scrape.timer` 5×/day (01/06/11/16/21h +0–2h jitter) → `collect_dataset.py --all --limit 3 --daily-limit 15` |
| Daily quota | `data/dataset/.quota.json` (15/day, resets per calendar day) |
| Notify | Teams Adaptive Card via Workflows webhook (`secrets/teams.json`); email fallback (`secrets/smtp.json`) |
| Logs | `journalctl -u scrape.service -e`; `data/dataset/scrape_*.log` |

Server specifics:
- **Never loop over usernames/passwords/keys when connecting** — a single clean
  key-auth attempt only (credential-guessing loops trip the security classifier).
- No `sqlite3` CLI on the server — inspect DBs with `.venv/bin/python` + the
  `sqlite3` module.
- twscrape CLI must be pointed at the pool explicitly (`--db data/accounts.db`);
  bare `twscrape accounts` creates a stray empty `./accounts.db` (delete it).
- macOS rsync is openrsync (protocol 29): no `--info` flags. Pull the dataset with
  `rsync -rtz -e "ssh -i ~/.ssh/id_ed25519_scraper" --include='*/'
  --include='*.ndjson' --exclude='*' root@192.168.1.106:/opt/populism-scraping/data/dataset/ data/dataset_server/`
- Deploy code changes by rsync-ing the changed `src/` files over, units to
  `/etc/systemd/system/` + `systemctl daemon-reload && systemctl restart scrape.timer`.

# Scraping architecture gotchas (hard-won)

- X's search index (Pass B `from:handle` queries) **silently omits many replies
  and some plain tweets** — search-only recall can be as low as ~80% for
  reply-heavy accounts. Pass A (`user_tweets`) reads the "Tweets" tab, which
  **excludes replies**. That's why **Pass C** (`user_tweets_and_replies`, the
  Replies tab, ~3,200 cap) exists in `collector.py` — it recovered the missing
  ~20% for HenriBontenbal/mirjambikker. `collect_dataset.py` runs all three passes; a
  target isn't `is_complete` until both `recent_done=1` and `replies_done=1`.
- The replies tab embeds **parent tweets by other authors**, and
  `extract_raw_tweets()` (src/collector.py) emits *every* tweet in a response —
  Pass C therefore filters on `obj["legacy"]["user_id_str"] == uid`; keep that
  filter for any new tab-based pass or foreign tweets pollute the handle's DB.
- Checkpoints: each target DB has a `checkpoint` table (`months_done` JSON list,
  `recent_done` + `replies_done` flags; `replies_done` is auto-migrated into old
  DBs). A month is marked done **even if the search returned little**. To force
  a full re-scrape: back up the `.sqlite`, then reset `months_done='[]',
  recent_done=0, replies_done=0` for the handle. Data is never lost
  (`INSERT OR IGNORE`).
- After any DB change, regenerate the ndjson with `flatten.export_ndjson(db, out)`.

# Validation against the professors' reference data

Reference scrapes live in `~/Raw Data` (top level + party subfolders). **The entire
folder is the professors' work, including the July-2026 first/second-run files**
(confirmed by Matteo 2026-07-19). Some handles have first + second runs:
merge them by tweet ID for the professors' "most complete picture".

The agreed metric: clip both datasets to the tenure window
`[max(leader_start, 2017-01-01), min(leader_end, today)]`, then
**recall = |shared IDs| / |professor IDs in window|**. NDJSON parsing: id from
`rest_id`, date from `legacy.created_at` (`%a %b %d %H:%M:%S %z %Y`).
