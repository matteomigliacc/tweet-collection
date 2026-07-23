# Code walkthrough — how this project works, script by script

A reading guide for the codebase, written for someone who can read Python but
doesn't live in it. Each section says what a script does, how it does it, and
which Python ideas it leans on. Read it top-to-bottom once; after that it works
as a reference.

## The big picture

The project collected every tweet by Dutch party leaders and party accounts
during the study window **2017-03-23 → 2025-11-12** (Tweede Kamer installation
to TK election). Scraping ran on a home server and is now **finished**; the
code remains so the dataset can be understood, validated, repaired, and
re-created if ever needed.

Data flows through five stages:

```
frame/*.csv          WHO to scrape and WHEN (committed to git)
     │
     ▼
src/collect_dataset.py  decides the targets and their date windows
     │  calls
     ▼
src/collector.py        actually talks to X, three passes per account
     │  writes
     ▼
data/dataset/<Party>/<handle>.sqlite     raw tweet JSON, one DB per target
     │  exported by src/flatten.py
     ▼
data/dataset/<Party>/<handle>.ndjson     one tweet per line — THE dataset
     │
     ├── analysis/recall_data.py    validated against the professors' scrapes
     ├── src/fetch_missing.py       holes repaired by fetching tweet ids directly
     └── src/upload_4cat.py         uploaded to 4CAT for the actual research
```

Two ideas keep everything safe:

1. **Idempotent writes.** Every tweet insert is `INSERT OR IGNORE` with the
   tweet id as primary key. Running anything twice can never duplicate or
   overwrite data — re-runs only ever *add* tweets.
2. **Checkpoints.** Each target's database has a `checkpoint` table recording
   which months are done. An interrupted run resumes where it stopped instead
   of starting over.

---

## `src/collector.py` — the scraping engine

The heart of the project. For one account it runs up to three passes:

- **Pass A (recent):** `user_tweets` — the account's "Tweets" tab, newest
  ~3,200 tweets (a hard cap X imposes). This overlaps with Pass B, but can
  recover otherwise accessible tweets that X's search index omitted. Results
  outside the target's tenure window are discarded.
- **Pass B (window):** searches `from:handle since:.. until:..` one calendar
  month at a time. Splitting by month sidesteps the 3,200 cap: each month is
  its own query. `month_chunks()` produces the (start, end) pairs.
- **Pass C (replies):** `user_tweets_and_replies` — the "Replies" tab. This
  exists because X's *search index* silently omits many replies; Pass B alone
  missed ~20% of reply-heavy accounts. The replies tab also shows the tweets
  being replied *to* (written by other people), so Pass C filters: only objects
  whose `user_id_str` equals the target's own user id and whose dates fall
  inside the target window are stored.

Other things to notice:

- `SEARCH_LIMIT = 20000` — a month that produces more results than the limit
  is *silently truncated* by X (no error, no warning). 20,000 is far above any
  real month; the old limit of 1,000 quietly ate election months.
- `extract_raw_tweets()` digs the actual tweet objects out of X's deeply
  nested GraphQL response. It recursively walks the JSON looking for
  `entries` arrays — the shape X uses for timeline pages.
- The `checkpoint` table: `months_done` is a JSON list like
  `["2019-01", "2019-02", ...]`; `recent_done` and `replies_done` are 0/1
  flags for Passes A and C.

**Python ideas here:** `async`/`await` (twscrape is asynchronous — `async for
tweet in api.search(...)` pulls results as they stream in); `sqlite3` from the
standard library; `yield` in `month_chunks` (a *generator*: the function
produces values one at a time instead of building a whole list).

## `src/collect_dataset.py` — the dataset runner (main entry point)

Turns the two frame CSVs into a list of "jobs" (one handle + one date window
each), then scrapes whichever jobs aren't complete yet. Everything dataset-y is
decided here: the FLOOR/CEILING study window, tenure clipping, one
folder per party. Run with `--dry-run` to see the whole target list without
scraping anything.

Two modes:

- **Interactive** (no flags): prints a numbered menu, you pick a target.
- **`--all`** (what the server's systemd timer ran): scrapes every incomplete
  target in order, isolating failures so one bad account can't kill the batch,
  respecting `--limit` (per run) and `--daily-limit` (per calendar day,
  persisted in `data/dataset/.quota.json` so restarts can't cheat).

It also wires up the operational extras: a `_Tee` class duplicates all console
output into a timestamped log file; `errmon.ErrorMonitor` watches for backend
errors during the run; at the end one summary is posted to Teams (falling back
to email) using the builders in `reports.py`.

**Python ideas:** `argparse` for command-line flags; `asyncio.run()` to start
the async world from a normal script; `try/except` around each job so an
exception is recorded rather than fatal.

## `src/reports.py` — notification payloads

No logic, just formatting: takes the numbers `collect_dataset.py` gathered and builds
the Teams "Adaptive Cards" (JSON structures Microsoft renders as chat
messages) and the fallback HTML email. Kept separate so the ~200 lines of
layout don't bury the scraping logic in collect_dataset.py.

## `src/notify.py` — actually sending things

`send_teams(card)` posts to a Teams Workflows webhook; `send_email(...)` sends
via SMTP. Both read their settings from git-ignored files in `secrets/` and
**never raise** — if notifications are misconfigured they log a warning and
return False, because a notification problem must never abort a scrape.

## `src/errmon.py` — the backend-error watchdog

Exists because of a nasty failure mode: when X's backend errors on a search
query, twscrape logs the error but the query just returns *zero tweets* — no
exception. The month is then checkpointed as "done" while being empty. On
2026-07-19 this silently dropped ~36,500 tweets while the summary said
"0 failed".

`ErrorMonitor` attaches a *sink* to the loguru log stream (every log line
passes through `record()`), pattern-matches backend-error lines with regular
expressions, and posts a Teams card every N minutes if a window had errors. It
distinguishes real errors (`Dependency`, `DeadlineExceeded`, ...: data being
lost) from benign rate-limit waits. Run `python src/errmon.py` for its
self-test.

**Python ideas:** regular expressions (`re`); `collections.Counter`;
`@property`; an `asyncio` background task that ticks every N minutes.

## `src/flatten.py` — exports

Two exporters from a target's SQLite DB:

- `export_ndjson(db, out)` — writes the raw tweet JSON, one object per line
  ("newline-delimited JSON"). **This is the dataset format.** Always regenerate
  the ndjson after any change to a DB.
- `flatten_db(db, out)` — a tidy CSV with one row per tweet and the columns a
  statistics package wants (text, date, like counts, is_reply, ...). Derived
  view only; the raw JSON stays the source of truth.

## `src/fetch_missing.py` — the by-id repair tool

The search index doesn't just *delay* some replies — it permanently omits
them. Re-scraping cannot recover those (proven: a full checkpoint-reset
re-scrape of @Gertjansegers returned the identical 1,834-tweet hole). But the
tweets still exist, and asking X for a tweet *by its id* (`tweet_details`)
returns it fine.

So this script uses the professors' reference files as a shopping list: every
tweet id they hold that our dataset lacks is fetched directly and stored with
`source="by_id"`. Fetched objects pass the same two filters as Pass C (right
author, inside the window). Ids X no longer returns (deleted accounts/tweets)
are recorded in a `fetch_gone` table so they're only asked for once.

Because the 3 GB reference folder lives on the Mac and the account pool lives
on the server, the work splits: `--emit-worklist` (Mac) writes just the missing
ids to a small JSON file; `--worklist` (server) fetches them.

## `src/upload_4cat.py` — publishing to 4CAT

Uploads each `<handle>.ndjson` to the university's 4CAT instance as an import
dataset. Each tweet is wrapped in the "Zeeschuimer envelope" 4CAT's importer
expects, clipped to the study window, and the dataset is labeled
`@handle (Party)`. Includes workarounds for two 4CAT importer crashes
(missing `id` field; deleted quoted tweets) — see the comments in
`to_zeeschuimer()`.

## `src/scrape_account.py`, `src/collector.py --csv` — ad-hoc scraping

`scrape_account.py` is a question-and-answer front-end for scraping any single handle
outside the dataset rules (it just calls `run_collection` like everything
else). `collector.py` can also run standalone with flags. Neither touches
`data/dataset/`.

## `src/add_accounts.py`, `src/load_accounts.py` — the account pool

twscrape needs real X accounts to authenticate its requests. `add_accounts.py`
interactively collects username + cookie strings into the git-ignored
`secrets/accounts.json`; `load_accounts.py` loads them into twscrape's pool
(`data/accounts.db`) and does a live test fetch to prove the cookies work.

## `src/parse_archive_pdf.py` — the Rutte archive experiment

A separate one-off: @markrutte's account went private, and @MinPres tweets from
2022-23 were recovered by parsing the Rijksarchief's OCR'd screenshot PDFs
(word coordinates → tweets, fuzzy dedup of overlapping screenshots). Its output
lives in `experiments/rutte-archive/`, deliberately outside the dataset — the
tweets have no real ids and only day-precision dates.

---

## `analysis/` — validating the dataset

All stdlib-only, all read-only: they import nothing from `src/`, so they stay
an *independent* check on the scraper rather than sharing its assumptions.

- **`recall_data.py`** — compares `data/dataset_server/` against the
  professors' scrapes in `~/Raw Data`. For each handle: what share of the
  professors' tweet ids (inside the tenure window) do we hold? Attributes
  missing ids to their real author, because the professors' Zeeschuimer
  exports embed other people's tweets (parents of replies, quoted tweets)
  that were never ours to collect. Output: `analysis/dataset_recall.json`.
- **`render_report.py`** — turns that JSON into a self-contained HTML report
  page (no JavaScript frameworks, rows baked in).
- **`foreign_census.py`** — counts exactly those other-author tweets in the
  professors' files, so keep/drop decisions rest on numbers.

## `frame/` — who gets scraped (the sampling frame)

Three committed CSVs. `leaders.csv`: one row per leadership spell (handle,
party, start, end — "ongoing" = until the ceiling). `parties.csv`: party
accounts with their parliamentary seat spells. `politicians.csv`: a plain
handle list for ad-hoc runs. The date windows follow the professors'
onboarding document; rows carry a `notes` column with provenance.

## `data/` — outputs (git-ignored)

- **`data/dataset_server/`** — rsync mirror of the server's finished dataset.
  The local authoritative copy.
- `data/dataset/` — where a *local* scrape would write; historically a stale
  snapshot (cleaned up 2026-07).
- `data/accounts.db` — twscrape's account pool.

## `deploy/` — the server units

systemd service/timer pairs that ran the show on the Proxmox container
(CTID 106): `scrape.timer` fired `collect_dataset.py --all --limit 3 --daily-limit 15`
five times a day; `backup.timer` pushes a nightly corpus backup to SURFdrive
over WebDAV. See `deploy/README.md`.

---

## Python concepts that repeat everywhere

- **`async` / `await`** — cooperative multitasking. `async def` marks a
  function that can pause; `await` is where it pauses (usually: waiting on the
  network). `async for` iterates over results that arrive over time.
  `asyncio.run(main())` starts it all. twscrape is async, so everything that
  touches it is too.
- **Generators (`yield`)** — a function that produces a sequence lazily,
  one item per request, without building a list in memory.
- **`pathlib.Path`** — object-oriented file paths: `ROOT / "data" / "x.txt"`
  instead of string concatenation. `ROOT = Path(__file__).resolve().parent.parent`
  means "the folder two levels above this file", i.e. the project root —
  so scripts work no matter which directory you run them from.
- **f-strings** — `f"@{handle}: {n} tweets"` interpolates variables into
  text. Format specs after a colon: `{n:,}` adds thousands separators,
  `{x:>8}` right-aligns in 8 characters, `{dt:%Y-%m-%d}` formats a date.
- **`dict.get(key)` chains** — `(obj.get("legacy") or {}).get("created_at")`
  digs into nested JSON without crashing when a level is missing.
- **`INSERT OR IGNORE`** (SQL, not Python) — insert unless the primary key
  already exists. The single feature that makes every re-run safe.
- **`try/except` as armor** — around network calls and per-target loops, so
  one failure is logged and skipped instead of ending a 6-hour run. Note the
  pattern: except clauses here *record* the error (checkpoint, log, counter)
  rather than hiding it.
- **`if __name__ == "__main__":`** — code under this line runs only when the
  file is executed directly, not when another script imports it. That's how
  `errmon.py` can be both a module and its own self-test.
