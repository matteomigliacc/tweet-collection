# Sampling frame

The input lists that define **who** gets scraped. Edit these CSVs to change the
dataset — the scrapers read them directly. (Scraped *output* lands in the
git-ignored `data/` folder; keep inputs and outputs separate.)

## `leaders.csv` — party leaders, by tenure

One row per **leadership spell**. A person who led twice (or led two parties) gets
one row per spell, so their tweets are windowed to each tenure separately.

| Column | Meaning |
|--------|---------|
| `handle` | X handle, without `@`. |
| `name` | Display name (documentation only). |
| `party` | Party label; also the output subfolder name under `data/dataset/<party>/`. |
| `leader_start` | Tenure start, `YYYY-MM-DD`. |
| `leader_end` | Tenure end, `YYYY-MM-DD`, or `ongoing` for still-serving. |
| `notes` | Free text — provenance / justification for the dates. |

`run_all.py` clips every start to the **2017-01-01 study floor** and treats
`ongoing` as today.

## `parties.csv` — official party accounts, by seat-holding spell

One row per **spell the party held Tweede Kamer seats** (mirrors `leaders.csv`). A
party is scraped only while it was actually in parliament, so a party that entered
late starts at its first seats, and one that left and returned (e.g. 50PLUS) gets
one row per spell — both scraped into the same account file, skipping the gap.

| Column | Meaning |
|--------|---------|
| `handle` | X handle, without `@`. |
| `name` | Display name. |
| `party` | Party label; also the output subfolder under `data/dataset/<party>/`. |
| `seat_start` | First day of the spell, `YYYY-MM-DD` (Kamer-installation date, or a mid-term gain). |
| `seat_end` | Last day of the spell, `YYYY-MM-DD`, or `ongoing` for currently seated. |
| `notes` | Free text — seat counts / provenance. Avoid commas (unquoted CSV). |

`run_all.py` clips every `seat_start` to the **2017-01-01 study floor**, month-aligns
it, and treats `ongoing` as today.

## `politicians.csv` — generic frame for `collect.py`

A plain handle list used by the standalone `collect.py --csv` path (and its
default). Columns: `handle,name,party,country`. Handy for ad-hoc runs outside the
leader/party dataset.
