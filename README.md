# Populism Tweet Scraper

A reproducible pipeline for collecting tweets from Dutch political leaders and
party accounts for academic research at Utrecht University.

The project takes a list of accounts and relevant date periods, collects the
available tweets, saves its progress, checks the results against an independent
collection, and prepares the finished datasets for 4CAT.

It uses [twscrape](https://github.com/vladkens/twscrape) with browser session
cookies, so it does not require access to the official X API.

> **Project status:** the main dataset was collected and validated in July
> 2026. The code remains available so the collection can be understood,
> checked, repaired, or repeated.
>
> **Data notice:** tweets are personal data. The working dataset and account
> credentials are git-ignored and must be handled according to the project's
> Data Management Plan.

## What problem does it solve?

Collecting historical X data through a browser is slow and difficult to repeat.
Repeated searches can also produce different results. This project automates
the process while keeping a record of:

- which accounts and dates belong in the study;
- which collection steps have finished;
- where each tweet was found;
- which tweets were missing from an independent reference collection;
- which files were uploaded to 4CAT.

The scraper cannot guarantee a complete archive. X search sometimes omits older
replies and ordinary tweets, timeline endpoints are limited to roughly 3,200
items, and deleted or inaccessible tweets cannot be recovered. The validation
and repair tools make these limitations visible instead of treating every
successful request as a complete result.

## The workflow

```text
frame/leaders.csv + frame/parties.csv
          who should be collected, and when
                       │
                       ▼
          src/collect_dataset.py
          builds and schedules collection jobs
                       │
                       ▼
               src/collector.py
        collects tweets in three complementary ways
                       │
                       ▼
       data/dataset/<Party>/<handle>.sqlite
          raw working store + progress checkpoint
                       │
                       ▼
                src/flatten.py
              exports raw NDJSON files
                       │
              ┌────────┴────────┐
              ▼                 ▼
           analysis/     src/fetch_missing.py
        checks recall     repairs known gaps by ID
              │                 │
              └────────┬────────┘
                       ▼
              src/upload_4cat.py
       filters, converts, and uploads to 4CAT
```

## How tweets are collected

For each account, the collection engine uses up to three passes:

1. **Recent timeline** — reads the account's ordinary “Tweets” timeline. X
   limits this to approximately 3,200 items.
2. **Historical search** — searches `from:handle` month by month across the
   relevant period. Monthly queries avoid the result ceiling of one large
   search.
3. **Tweets and replies** — reads the account's “Tweets & replies” timeline to
   recover replies that X search may omit.

The passes overlap deliberately. Each tweet ID is unique in the database, so
finding the same tweet twice does not create a duplicate.

The replies timeline can contain parent tweets written by other users. The
collector checks the numerical author ID before accepting a timeline result.
New timeline results are also checked against the target's date window.

### Why three passes are still not always enough

- X search can silently omit accessible tweets.
- A query that reaches its result limit is silently truncated.
- The recent and replies timelines only expose approximately 3,200 items.
- Some backend errors return an empty result instead of raising an exception.
- Very active or reply-heavy accounts can therefore have larger historical
  gaps than quieter accounts.

`src/errmon.py` watches for silent backend failures.
`src/fetch_missing.py` can use known missing tweet IDs from a reference
collection and request them individually when they are still available.

## Inputs: the sampling frame

The two committed CSV files under `frame/` define the dataset:

- `leaders.csv` contains political leaders and their leadership periods.
- `parties.csv` contains official party accounts and periods in which the party
  held seats in the Tweede Kamer.

The main study window is **2017-03-23 through 2025-11-12**. Leadership and
seat-holding periods are clipped to that window.

One target is one handle under one party and date window. If the same person
appears under two parties, each period receives its own dataset file.

See [`frame/README.md`](frame/README.md) for the CSV columns and date rules.

## Outputs: SQLite, NDJSON, and 4CAT

The three formats serve different purposes.

### SQLite: working and provenance store

```text
data/dataset/<Party>/<handle>.sqlite
```

The SQLite database contains the raw tweet JSON, collection source, and
checkpoint information. Tweet IDs are primary keys and writes use
`INSERT OR IGNORE`, making repeated runs safe.

The checkpoint records:

- whether the recent-timeline pass finished;
- which calendar months were searched;
- whether the replies pass finished;
- the last status or error.

Existing databases may contain timeline records outside a target's final study
window. They are retained as a broad working archive.

### NDJSON: portable raw export

```text
data/dataset/<Party>/<handle>.ndjson
```

Each line contains one raw X GraphQL tweet object. The file is regenerated from
SQLite and is convenient for streaming, comparison, and transfer.

### 4CAT: filtered analysis dataset

`src/upload_4cat.py` applies the authoritative leadership or parliamentary
windows before upload. It then wraps each tweet in the format expected by
4CAT's Zeeschuimer importer and creates a labelled dataset such as:

```text
@Robjetten (D66)
```

The SQLite database is the collection record; the 4CAT version is the
date-filtered analysis dataset.

## Quick start

Requirements:

- Python 3.10 or newer;
- one or more X accounts with valid `auth_token` and `ct0` browser cookies.

Set up the project:

```bash
./setup.sh
source .venv/bin/activate
python src/add_accounts.py
```

`add_accounts.py` saves cookies in the git-ignored `secrets/accounts.json` and
can load and verify them immediately. To repeat only the loading step:

```bash
python src/load_accounts.py
```

Preview every target without contacting X:

```bash
python src/collect_dataset.py --dry-run
```

Choose one target interactively:

```bash
python src/collect_dataset.py
```

Run specific accounts:

```bash
python src/collect_dataset.py --all --only Robjetten,Nvanvroonhoven,VVD
```

Run every incomplete target:

```bash
python src/collect_dataset.py --all
```

For unattended operation, limits can be applied per run and per day:

```bash
python src/collect_dataset.py --all --limit 3 --daily-limit 15
```

Interrupted runs are safe to restart. Completed months and passes are skipped.

## Validation and repair

The dataset was compared with an independent Zeeschuimer collection using
tweet IDs inside the same date windows:

```text
recall = shared in-window tweet IDs / reference in-window tweet IDs
```

The comparison also identifies parent and quoted tweets written by other
accounts, which should not be counted as missing tweets from the target.

- `analysis/recall_data.py` calculates the comparison.
- `analysis/render_report.py` creates a self-contained HTML report.
- `analysis/foreign_census.py` measures other-author tweets in the reference
  files.
- `src/fetch_missing.py` fetches known missing IDs individually and records IDs
  that are no longer available.

These scripts are kept separate from the collection engine so the validation
does not simply repeat the scraper's assumptions.

## Running unattended

The project includes systemd services and timers for a headless server:

- bounded batch collection;
- checkpointed restarts;
- session logs;
- Teams notifications with email fallback;
- nightly SURFdrive backup with archived previous versions;
- automatic preparation and upload to 4CAT.

The production scraping timer is currently inactive because the main collection
has finished. The backup workflow remains useful.

See [`deploy/README.md`](deploy/README.md) for the server setup and operating
commands.

## Which script should I use?

| Script | Use it for |
|---|---|
| `src/collect_dataset.py` | Main entry point for the research dataset. |
| `src/collector.py` | Reusable three-pass collection engine; also has a lower-level CLI. |
| `src/flatten.py` | Export SQLite to raw NDJSON or a tidy CSV. |
| `src/upload_4cat.py` | Filter and upload completed datasets to 4CAT. |
| `src/fetch_missing.py` | Repair known gaps by requesting tweet IDs directly. |
| `src/add_accounts.py` | Add browser-cookie accounts interactively. |
| `src/load_accounts.py` | Load and verify the twscrape account pool. |
| `src/scrape_account.py` | Ad-hoc interactive scrape outside the study frame. |
| `src/errmon.py` | Detect otherwise silent X backend errors. |
| `src/reports.py` | Build Teams and email run summaries. |
| `src/notify.py` | Send Teams messages and fallback email. |
| `src/parse_archive_pdf.py` | Separate experiment for the Rutte PDF archive. |

For a script-by-script explanation of the Python code, read
[`docs/GUIDE.md`](docs/GUIDE.md).

## Repository layout

```text
frame/       sampling-frame CSV files
src/         collection, export, repair, and upload code
analysis/    independent validation and report generation
deploy/      headless-server services, timers, and backup script
docs/        detailed code walkthrough
secrets/     git-ignored credentials and configuration
data/        git-ignored databases, exports, and logs
```

## Security and data handling

- Never commit X cookies, SMTP passwords, Teams webhooks, or 4CAT tokens.
- Everything under `secrets/` is ignored except example templates.
- The collected dataset under `data/` is git-ignored.
- Several authenticated X accounts improve redundancy and throughput but do
  not remove X's collection limits.
- Review the project's Data Management Plan before sharing or retaining tweet
  data.
