# Populism Tweet Scraper

Collects the complete tweet history of Dutch party leaders and party accounts
on X (Twitter) for downstream **populism analysis** — built for academic
research at Utrecht University.

Given a *sampling frame* (CSV files listing who led which party when, and when
each party held parliamentary seats), it scrapes every account for exactly its
relevant window, stores the raw tweet JSON, and exports one clean
newline-delimited JSON file per account. Collection is **checkpointed and
idempotent**: interrupted runs resume where they stopped, and re-runs can only
ever add tweets, never duplicate or overwrite them.

Scraping happens via [twscrape](https://github.com/vladkens/twscrape)
(cookie-authenticated X accounts, no API access required).

> **Repository scope:** this repo contains the code, the sampling frame, and
> the documentation. The collected tweets themselves are not distributed here —
> they are personal data under GDPR and governed by the project's Data
> Management Plan.
>
> New to the code? **Start with [`docs/GUIDE.md`](docs/GUIDE.md)** — a
> script-by-script walkthrough that assumes only basic Python.

## How it works

```
frame/leaders.csv + frame/parties.csv     WHO to scrape, and WHEN
        │
        ▼
[1] src/run_all.py      builds the target list, applies the study window,
        │               skips complete targets, logs + notifies (Teams/email)
        ▼
[2] src/collect.py      the engine: three passes per account (A/B/C below),
        │               checkpointed + idempotent
        ▼
[3] src/flatten.py      exports each SQLite store to .ndjson (raw tweet
        │               objects) or a tidy .csv for statistics
        ▼
[4] analysis/           recall validation against an independent reference scrape
        ▼
[5] src/fetch_missing.py  repairs residual holes by fetching tweet ids directly
        ▼
[6] src/upload_4cat.py    uploads finished datasets to a 4CAT instance
```

One *target* = one handle within one date window, stored as
`<Party>/<handle>.sqlite` (raw store + checkpoint) plus `<Party>/<handle>.ndjson`
(the export — one raw GraphQL tweet object per line).

### The three passes (and why they exist)

- **Pass A — recent timeline:** the newest ~3,200 tweets (X's hard cap).
  Skipped in dataset runs; Pass B covers the same ground with date bounds.
- **Pass B — bounded window:** `from:handle since:.. until:..` searches, split
  month-by-month to sidestep the 3,200 ceiling.
- **Pass C — replies tab:** X's search index **silently omits many replies**
  (search-only recall can dip to ~80% on reply-heavy accounts), so the
  "Tweets & replies" tab is read too. Other authors' tweets embedded in that
  tab are filtered out by author id.

Two hard-won findings worth knowing if you build on this:

- A search query that exceeds its result limit is **silently truncated** — X
  returns the newest N with no error. Keep the per-month limit far above any
  plausible month (`SEARCH_LIMIT` in `collect.py`).
- Backend errors during search **return zero tweets instead of raising**, so a
  failing run looks like a quiet success. `src/errmon.py` watches the log
  stream for exactly this and raises the alarm.
- Some old replies are *permanently* absent from the search index; re-scraping
  can never find them. They can still be fetched one-by-one by tweet id —
  that's `src/fetch_missing.py`.

## Repository layout

| Path | Purpose |
|------|---------|
| `src/run_all.py` | Batch runner / main entry point (study-window rules live here). |
| `src/collect.py` | Three-pass collection engine. |
| `src/flatten.py` | Export ndjson (dataset format) or tidy CSV. |
| `src/fetch_missing.py` | By-id repair of tweets the search index omits. |
| `src/upload_4cat.py` | Upload ndjsons to [4CAT](https://4cat.nl) as labeled import datasets. |
| `src/errmon.py` | Backend-error watchdog (errors return zero tweets, not exceptions). |
| `src/reports.py` / `src/notify.py` | Build / send Teams cards and fallback email notifications. |
| `src/scrape.py` | Interactive ad-hoc scraper for any single handle. |
| `src/add_accounts.py` / `src/load_accounts.py` | Manage the X account pool (cookie auth). |
| `src/parse_archive_pdf.py` | Side experiment: recover tweets from an official OCR'd PDF archive. |
| `analysis/` | Stdlib-only validation: recall vs. a reference scrape, rendered as an HTML report. |
| `frame/` | **Sampling frame** (committed): `leaders.csv`, `parties.csv`, `politicians.csv`. |
| `deploy/` | systemd units + notes for running unattended on a server (timers, quotas, off-site backup). |
| `docs/` | `GUIDE.md` code walkthrough. |
| `secrets/` | Cookies, webhook URLs, API tokens — **git-ignored**, templates provided. |
| `data/` | Scraped output — **git-ignored**. |

## Dataset rules (enforced by `src/run_all.py`)

- **Study window:** 2017-03-23 (Tweede Kamer installation) → 2025-11-12 (TK
  election). Nothing outside it is collected.
- **Leaders:** scraped only for their leadership tenure (`frame/leaders.csv`;
  `ongoing` → the ceiling), clipped to the study window.
- **Party accounts:** only while the party held Tweede Kamer seats
  (`frame/parties.csv`; a party that left parliament and returned has two seat
  spells scraped into one file, skipping the gap).
- A handle that led two parties (e.g. GroenLinks → GroenLinks-PvdA) gets two
  separate files under the two party folders.
- Storage is raw GraphQL tweet JSON with `tweet_id` as primary key and
  `INSERT OR IGNORE` semantics: re-runs are idempotent.

## Validation

The dataset was validated against an independent reference scrape of the same
accounts: clip both datasets to the tenure window, then
**recall = shared tweet ids / reference ids in window**. `analysis/recall_data.py`
computes per-handle recall (attributing misses to their true author — reference
exports embed other people's tweets); `analysis/render_report.py` renders a
self-contained HTML report. Residual gaps were repaired with
`src/fetch_missing.py`.

## Setup

```bash
./setup.sh                          # venv + dependencies + secrets scaffold
source .venv/bin/activate
python src/add_accounts.py          # paste X account cookies (auth_token + ct0)
python src/run_all.py --dry-run     # list every target and its window
python src/run_all.py               # interactive; or --all for unattended runs
```

The scraper authenticates with session cookies from real X accounts (each
needs `auth_token` and `ct0`); several accounts give redundancy and
throughput. Everything under `secrets/` is git-ignored. For unattended
operation (systemd timers, daily quotas, notifications, off-site backup) see
`deploy/README.md`.

