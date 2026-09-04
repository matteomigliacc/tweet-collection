# Working on this project

Use plain English and direct sentences. Call the work tweet collection and the
result a dataset. Explain what a script does and why someone would use it.
Use subagents only when the task needs separate independent checks.

`collection.py` lists the commands without starting work. `README.md` explains
the workflow. Read the relevant code before changing or describing it.
Run `.venv/bin/python -m unittest discover -s tests` after code changes.
Keep credentials out of code, logs, chat, and documentation.
Keep generated results, temporary files, and private research documents out of
Git. Preserve existing local work. Ask before force-pushing rewritten history.

## Production work

Collection, repairs, validation, and 4CAT imports finished by August 2026.
Only restart collection, reset checkpoints, edit production databases, transfer
data, upload to 4CAT, deploy code, or enable timers when the user asks for that
specific action. Local refactoring is not permission to change the server.

The server was checked on 2026-08-30. Both `scrape.timer` and `backup.timer` were
disabled then. Check again before stating their current status. Existing service
names and server paths still use their original spelling; do not rename them as
part of local wording changes.

## Data and server access

The server holds the authoritative dataset. Use one SSH connection with this key:
`ssh -i ~/.ssh/id_ed25519 root@192.168.1.106`.
Do not try lists of usernames, passwords, or keys.

- Container: CTID 106, `populism-scraper`, unprivileged Debian 13.
- Project: `/opt/populism-scraping`.
- Dataset: `/opt/populism-scraping/data/dataset/`.
- Mac copy for analysis: `data/dataset_server/`.
- Mac test space: `data/dataset/`; it is not the production dataset.

On 2026-08-30, the account list produced 53 collection entries. The server had
54 SQLite/NDJSON pairs because `50PLUS/LianedenHaan` was retained outside the
current list. `backup_pre_rescrape_20260720/` held 16 backup databases; do not count
these as current datasets. There is no server `AGENTS.md`; these rules apply there.
For an authorized copy, use rsync with the listed SSH key and include only NDJSON
files. macOS openrsync uses protocol 29 and does not support `--info` flags.

## Account and date rules

`src/read_account_csv.py` reads `frame/accounts.csv`. Required columns are
`handle`, `party`, `start`, and `end`; `name`, `kind`, and `notes` are metadata.
All dates are explicit and inclusive. The study runs from 2017-03-23 through
2025-11-12. Keep separate periods and exclude the gaps between them.
Each party/account uses its own file; periods for that pair share a database.

`collect_dataset.py` adds one day to the CSV end date. `tweet_collection.py`
includes the start and excludes that adjusted end: `[since, until)`.
Searches span overlapping full months, but stored tweets must match the account
and exact allowed dates. Keep both checks in `raw_tweet_is_eligible()`.
X responses can include other authors' tweets, especially in replies.

## Collection and progress

`src/tweet_collection.py` reads the timeline, searches by month, and reads replies.
Timeline and replies return about 3,200 results each; search uses a 20,000 limit.
The database uses tweet IDs to avoid duplicates. The first stored copy keeps its
source, raw response, and collection time. Failed work keeps earlier progress.

`is_complete()` reads checkpoint flags, not evidence of final dataset completeness.
On 2026-08-30 it marked 33 of 53 databases incomplete despite later repairs and
validation. X can return empty or incomplete results without raising an error.
`collection_errors.ErrorMonitor` checks logged failures, but neither checkpoints
nor the monitor prove complete coverage. Do not restart work based on flags alone.

A requested full recollection needs a backup of the exact SQLite file before
resetting `months_done='[]', recent_done=0, replies_done=0`.
The server lacks the `sqlite3` command; use `.venv/bin/python` with Python's
`sqlite3` module. Re-export NDJSON after changing a database.

## Exports, comparisons, and uploads

`export_dataset.export_ndjson()` rewrites NDJSON from every database row in date
order. Old databases may contain tweets outside the current CSV dates.
Combining and 4CAT uploads apply those date limits again.
`upload_4cat.py` accepts only exact party/account matches in the CSV. It converts
tweets to 4CAT's format, fills missing IDs, and removes unavailable quoted-tweet
stubs. Each separate-account upload creates a new import; it does not update one.
Its default is the Mac copy and it can run rsync. On the server, explicitly set
`--dataset data/dataset`. The 4CAT secret was absent there on 2026-08-30.

`analysis/recall_data.py` compares reference exports in `~/Raw Data` with the Mac
copy by tweet ID within the CSV dates. It skips dataset files outside the list.
Recall is shared reference IDs divided by reference IDs. `own_recall` excludes
missing tweets attributed to other accounts. Optional `--other-authors` counts
other authors across all reference dates, not only the study dates.
`analysis/read_tweets.py` reads IDs from `rest_id`, dates from `legacy.created_at`,
and author handles from the response. Keep the difference between tweet type
(`is_reply`, `is_retweet`) and reactions received (`reply_count`, `retweet_count`).

## Existing server tools
Logs are in `journalctl -u scrape.service -e` and `data/dataset/scrape_*.log`.
`data/dataset/.quota.json` tracks attempts by the server's calendar date.
Always give twscrape `--db data/accounts.db`; otherwise it can create an accidental
`accounts.db` in the working directory. Check its location and contents before
deleting any unexpected database. Keep backup and Yoda transfer scripts available.
