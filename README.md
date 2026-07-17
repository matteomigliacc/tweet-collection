# Populism Tweet Scraper

Collects tweets from a defined set of politicians' public X (Twitter) accounts for
downstream **populism analysis**, as academic research at Utrecht University.

The pipeline is split into decoupled stages so re-running analysis never re-scrapes:

```
politicians.csv ─┐
X account cookies┤
                 ▼
        [1] account-pool loader  ──►  data/accounts.db
                 ▼
        [2] two-pass collector   ──►  <target>.sqlite   (raw tweet JSON, tweet_id PK)
                 │  (checkpoint per handle → resumable)
                 ▼
        [3] flatten / export     ──►  .csv  or  .ndjson (tidy analysis view)
```

## How it works

Each politician is scraped in two passes:

- **Pass A — recent timeline:** the newest ~3,200 tweets (X's timeline cap).
- **Pass B — bounded window:** `from:handle since:.. until:..` split month-by-month,
  which bypasses the 3,200 ceiling for a fixed date range.

Raw tweet JSON is stored in SQLite with `tweet_id` as the primary key, so re-runs are
idempotent (`INSERT OR IGNORE`) and a per-handle checkpoint lets an interrupted run
**resume where it stopped** rather than restart.

## Layout

| Path | Purpose |
|------|---------|
| `src/add_accounts.py` | Interactively add X accounts by pasting cookie strings (writes `secrets/accounts.json`). |
| `src/load_accounts.py` | Load X accounts (via browser cookies) into twscrape's pool and verify them. |
| `src/collect.py` | Two-pass collector (the core engine). Also runnable as a CLI. |
| `src/scrape.py` | Interactive single-handle front-end (answer prompts, no flags). |
| `src/run_all.py` | Batch runner over the whole corpus from `leaders.csv` + `parties.csv`. |
| `src/flatten.py` | Flatten raw tweet JSON into a tidy CSV (or export raw `.ndjson`). |
| `frame/` | **Sampling-frame inputs** (committed): `leaders.csv`, `parties.csv`, `politicians.csv`. |
| `secrets/` | X account cookies — **git-ignored**, never committed. |
| `data/` | Scraped output — SQLite stores, corpus, CSV/ndjson exports, logs — **git-ignored**. |
| `docs/` | Design spec / notes. |

## Setup

After cloning, run the setup script — it creates the virtualenv, installs the
dependencies, prepares `secrets/accounts.json`, and then offers to walk you
through adding your X accounts right away:

```bash
./setup.sh
```

(If you prefer to do it by hand: `python3 -m venv .venv && source .venv/bin/activate
&& pip install -r requirements.txt`.)

### Provide X accounts

The scraper authenticates via browser cookies from real X accounts (redundancy +
overnight throughput). The easiest way is the interactive helper — it prompts for
each account's username and cookie string, validates them, and writes
`secrets/accounts.json` for you:

```bash
source .venv/bin/activate
python src/add_accounts.py
```

`setup.sh` offers to run this for you automatically at the end.

Each account needs a `username` and a `cookies` string containing at least
`auth_token` and `ct0` (grab these from your browser's X session cookies) —
e.g. `auth_token=abc123...; ct0=def456...`. `secrets/accounts.json` is
git-ignored, so **real cookies are never committed.** `add_accounts.py` also
offers to load and verify the pool for you when you're done; to do it separately:

```bash
python src/load_accounts.py
```

## Usage

**Interactive, one handle at a time** (easiest):

```bash
python src/scrape.py
```

**Batch over the whole corpus** (reads `frame/leaders.csv` + `frame/parties.csv`):

```bash
python src/run_all.py --dry-run   # list every target + its date window, scrape nothing
python src/run_all.py             # menu-driven; skips already-complete targets
```

**Direct CLI** (scriptable):

```bash
python src/collect.py --csv frame/politicians.csv --since 2017-01-01 --until 2026-07-01 -v
python src/flatten.py --db data/tweets.sqlite --out data/tweets_flat.csv
```

### Corpus rules (`run_all.py`)

- **Study floor:** nothing before `2017-01-01`.
- **Leaders:** their tenure only, clipped to the 2017 floor (`ongoing` → today).
- **Party accounts:** `2017-01-01` → today.
- A handle appearing under two parties (e.g. a leader change) is scraped into two
  separate files under the two party folders.

## Legal / ethical note

Collecting public political speech for research is permitted under EU research law
(DSM Directive Art. 3 TDM exception; DSA research provisions). Politicians' tweets are
still **personal data** under GDPR even when public, so storage, retention, and access
are governed by the project's Data Management Plan. Self-serve scraping also violates
X's Terms of Service and risks account suspension — a deliberate, documented choice
surfaced to the ethics / data-management review. See `docs/` for the full design and
governance notes.
