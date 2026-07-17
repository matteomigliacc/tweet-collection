# Sampling frame

The input lists that define **who** gets scraped. Edit these CSVs to change the
corpus — the scrapers read them directly. (Scraped *output* lands in the
git-ignored `data/` folder; keep inputs and outputs separate.)

## `leaders.csv` — party leaders, by tenure

One row per **leadership spell**. A person who led twice (or led two parties) gets
one row per spell, so their tweets are windowed to each tenure separately.

| Column | Meaning |
|--------|---------|
| `handle` | X handle, without `@`. |
| `name` | Display name (documentation only). |
| `party` | Party label; also the output subfolder name under `data/corpus/<party>/`. |
| `leader_start` | Tenure start, `YYYY-MM-DD`. |
| `leader_end` | Tenure end, `YYYY-MM-DD`, or `ongoing` for still-serving. |
| `notes` | Free text — provenance / justification for the dates. |

`run_all.py` clips every start to the **2017-01-01 study floor** and treats
`ongoing` as today.

## `parties.csv` — official party accounts

One row per party account; scraped over the full study window (2017-01-01 → today).

| Column | Meaning |
|--------|---------|
| `handle` | X handle, without `@`. |
| `name` | Display name. |
| `party` | Party label / output subfolder. |

## `politicians.csv` — generic frame for `collect.py`

A plain handle list used by the standalone `collect.py --csv` path (and its
default). Columns: `handle,name,party,country`. Handy for ad-hoc runs outside the
leader/party corpus.
