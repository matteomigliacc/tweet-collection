# Populism Tweet Scraper

Collects tweets from Dutch party leaders' and party accounts' public X (Twitter)
profiles for downstream **populism analysis** — academic research at Utrecht
University (the "Wendy Project").

> **Status (July 2026): scraping is finished.** The complete dataset — every
> target in the sampling frame, study window 2017-03-23 → 2025-11-12 — was
> collected on a dedicated home server and validated against the professors'
> reference scrapes. The server's remaining jobs are uploading the datasets to
> 4CAT and the nightly SURFdrive backup. This repo remains the reproducible
> record: the code, the sampling frame, and the documentation.
>
> New to the code? **Start with [`docs/GUIDE.md`](docs/GUIDE.md)** — a
> script-by-script walkthrough written for a Python learner.

## Where the data lives

| Copy | Where | Role |
|------|-------|------|
| Authoritative | home server (Proxmox LXC 106) `/opt/populism-scraping/data/dataset/` | where scraping ran; source for uploads + backup |
| Local mirror | `data/dataset_server/` on this Mac (rsync'd, git-ignored) | analysis + validation input |
| Research copy | [4CAT](https://4cat.cdh.uu.nl), one dataset per handle, labeled `@handle (Party)` | what the research team actually uses |
| Backup | SURFdrive over WebDAV, nightly | disaster recovery |

One target = one handle within one tenure/seat window, stored as
`<Party>/<handle>.sqlite` (raw store + checkpoint) plus `<Party>/<handle>.ndjson`
(the export — one raw GraphQL tweet object per line).

## The pipeline

```
frame/leaders.csv + frame/parties.csv     WHO to scrape, and WHEN (committed)
        │
        ▼
[1] src/run_all.py      builds the target list, applies the study window,
        │               skips complete targets, logs + notifies (Teams/email)
        ▼
[2] src/collect.py      the engine: three passes per account (A/B/C below),
        │               checkpointed + idempotent — re-runs only ever ADD tweets
        ▼
[3] src/flatten.py      exports each SQLite store to .ndjson (the dataset
        │               format) or a tidy .csv for statistics
        ▼
[4] analysis/           recall check against the professors' reference scrapes
        ▼
[5] src/fetch_missing.py  repairs holes by fetching missing tweet ids directly
        ▼
[6] src/upload_4cat.py    uploads finished ndjsons to 4CAT
```

### The three passes (and why they exist)

- **Pass A — recent timeline:** the newest ~3,200 tweets (X's cap). Skipped in
  dataset runs; Pass B covers the same ground with date bounds.
- **Pass B — bounded window:** `from:handle since:.. until:..` searches, split
  month-by-month to sidestep the 3,200 ceiling.
- **Pass C — replies tab:** X's search index **silently omits many replies**
  (search-only recall can dip to ~80% on reply-heavy accounts), so the
  "Tweets & replies" tab is read too. Other people's tweets embedded in that
  tab are filtered out by author id.

Some old replies are *permanently* unsearchable — no amount of re-scraping
finds them. Those were recovered by `src/fetch_missing.py`: it diffs our
dataset against the professors' reference files and fetches each missing tweet
id directly (`source="by_id"` in the DB).

## Layout

| Path | Purpose |
|------|---------|
| `src/run_all.py` | Batch runner / main entry point (study-window rules live here). |
| `src/collect.py` | Three-pass collection engine. |
| `src/flatten.py` | Export ndjson (dataset format) or tidy CSV. |
| `src/fetch_missing.py` | By-id repair of tweets the search index omits. |
| `src/upload_4cat.py` | Upload ndjsons to 4CAT as labeled import datasets. |
| `src/errmon.py` | Backend-error watchdog (X errors return zero tweets, not exceptions). |
| `src/reports.py` / `src/notify.py` | Build / send Teams cards and fallback email. |
| `src/scrape.py` | Interactive ad-hoc scraper for any single handle. |
| `src/add_accounts.py` / `src/load_accounts.py` | Manage the X account pool (cookie auth). |
| `src/parse_archive_pdf.py` | Separate experiment: recover @MinPres 2022-23 from Rijksarchief OCR PDFs → `experiments/rutte-archive/`. |
| `analysis/` | Stdlib-only validation: `recall_data.py` → `dataset_recall.json` → `render_report.py` (HTML report); `foreign_census.py`. |
| `frame/` | **Sampling frame** (committed): `leaders.csv`, `parties.csv`, `politicians.csv`. Windows follow the professors' onboarding doc. |
| `deploy/` | systemd units + setup notes for the server (scrape timer, SURFdrive backup). |
| `docs/` | `GUIDE.md` code walkthrough; design spec. |
| `secrets/` | X cookies, webhook URLs, API tokens — **git-ignored**. |
| `data/` | Scraped output & mirrors — **git-ignored**. |

## Dataset rules (enforced by `src/run_all.py`)

- **Study window:** 2017-03-23 (Tweede Kamer installation) → 2025-11-12 (TK
  election). Nothing outside it.
- **Leaders:** scraped only for their leadership tenure (`frame/leaders.csv`;
  `ongoing` → the ceiling), clipped to the study window.
- **Party accounts:** only while the party held Tweede Kamer seats
  (`frame/parties.csv` seat spells; a party that left and returned has two
  rows into one file).
- A handle under two parties (e.g. Klaver: GroenLinks → GroenLinks-PvdA) gets
  two separate files under the two party folders.
- Storage is raw GraphQL tweet JSON, `tweet_id` primary key, `INSERT OR
  IGNORE`: **re-runs are idempotent and can only add tweets.** After any DB
  change, regenerate the ndjson (`flatten.export_ndjson`).

## Validation

The dataset was checked against the professors' own scrapes (`~/Raw Data`):
clip both to the tenure window, then recall = shared ids / professor ids in
window. `analysis/recall_data.py` computes it; `analysis/render_report.py`
renders the report. Residual gaps were repaired with `src/fetch_missing.py`.

## Setup (for a re-run or new machine)

```bash
./setup.sh                          # venv + dependencies + secrets scaffold
source .venv/bin/activate
python src/add_accounts.py          # paste X account cookies (auth_token + ct0)
python src/run_all.py --dry-run     # list every target and its window
python src/run_all.py               # interactive; or --all for unattended
```

The scraper authenticates with cookies from real X accounts; each needs
`auth_token` and `ct0`. `secrets/` is git-ignored — credentials never leave the
machine. Server deployment (timers, quotas, backup) is documented in
`deploy/README.md`.

## Legal / ethical note

Collecting public political speech for research is permitted under EU research
law (DSM Directive Art. 3 TDM exception; DSA research provisions). Politicians'
tweets are still **personal data** under GDPR even when public, so storage,
retention, and access are governed by the project's Data Management Plan.
Self-serve scraping also violates X's Terms of Service and risks account
suspension — a deliberate, documented choice surfaced to the ethics /
data-management review. See `docs/` for the full design and governance notes.
