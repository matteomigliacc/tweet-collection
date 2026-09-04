# Tweet collection

This project collects tweets from Dutch political parties and their leaders,
keeps the dates needed for the study, and prepares the data for analysis in 4CAT.
It also compares the dataset with the professors' earlier exports, so researchers
can see which tweets are shared and which are missing.

The original collection, repairs, and 4CAT imports finished by August 2026.
The commands remain available for future work. Running `collection.py` without a
command only shows help.

## Start here

From this project folder, run:

```bash
.venv/bin/python collection.py
```

Add a command and `--help` to see its options, for example:

```bash
.venv/bin/python collection.py combine --help
```

On a new machine, `bash setup.sh` creates the Python environment, installs the
requirements, and offers to set up X login accounts. These logins provide access
to X; they are separate from the accounts whose tweets the study collects.
Keep login cookies and service credentials private.

## How the data moves

1. **Choose accounts and dates.** `frame/accounts.csv` lists both parties and
   leaders. Each row needs `handle`, `party`, `start`, and `end`. Dates use
   `YYYY-MM-DD` and include both the first and last day. `name`, `kind`, and
   `notes` describe the entry. Separate periods use separate rows.
2. **Collect tweets.** The code reads the timeline, searches overlapping calendar
   months, and reads replies. It stores only tweets by the requested account
   within the row's dates. The study covers 2017-03-23 through 2025-11-12.
3. **Store and export.** Each party/account has a SQLite database, which stores
   tweets and collection progress. An NDJSON file contains one tweet per line
   for later processing. A CSV export gives a table suitable for spreadsheet use.
4. **Combine or compare.** Combining applies the CSV dates, orders tweets by date,
   and removes duplicate tweet IDs. Comparison measures overlap with reference
   exports and separates missing tweets by the target account from other authors.
5. **Upload when needed.** 4CAT accepts either separate account datasets or a
   combined dataset split into parts. Repeating a separate-account upload creates
   another import; it does not update the existing one.

The database avoids duplicate tweet IDs when collection steps overlap. Search
results can be incomplete even when a step finishes, so saved progress alone
does not establish that every tweet was collected.

## Commands and scripts

Use `.venv/bin/python collection.py COMMAND` for the commands below. Individual
Python scripts can also be run directly.

| Command | Script | Why use it? |
|---|---|---|
| `collect` | `src/collect_dataset.py` | Collect the accounts and dates in the CSV. `--dry-run` lists them without collecting. |
| `account` | `src/collect_account.py` | Choose one account and dates through terminal questions. |
| `add-logins` | `src/add_logins.py` | Add the X logins used for collection. |
| `load-logins` | `src/load_logins.py` | Load and check those logins. |
| `export` | `src/export_dataset.py` | Turn a database into CSV; specify `--db` and `--out`. |
| `combine` | `analysis/combine_datasets.py` | Prepare one dataset without duplicate tweets. |
| `upload` | `src/upload_4cat.py` | Send separate account datasets to 4CAT. |
| `upload-combined` | `analysis/upload_combined_dataset.py` | Send a combined dataset to 4CAT in resumable parts. |
| `compare` | `analysis/recall_data.py` | Compare the local dataset copy with reference exports. |
| `report` | `analysis/comparison_report.py` | Turn comparison results into an HTML page. |
| `collect-missing` | `src/collect_missing.py` | Try collecting specific missing tweet IDs from reference files. |

`deploy/backup.sh` backs up server files to SURFdrive.
`deploy/upload_yoda.sh` transfers dataset files to Yoda; its `--dry-run` option
shows the proposed transfer without contacting Yoda. These use separate service
configuration and are not part of the command menu.

## Understanding the comparison

The reference exports are in `~/Raw Data`. The comparison reads the local server
copy in `data/dataset_server/` and uses the dates in `frame/accounts.csv`.
“Recall” means the percentage of reference tweet IDs found in our dataset.
`own_recall` excludes missing tweets attributed to another author. Reference
exports can include parent tweets and quoted tweets by other people.

Add `--other-authors` to include a broader count of other authors in the reference
files, across all their dates. This answers a different question from overlap
within the study dates, but now runs through the same comparison command.
You can save the results and make the HTML report with:

```bash
.venv/bin/python collection.py compare --other-authors --output analysis/dataset_recall.json
.venv/bin/python collection.py report
```

Use `--reference`, `--dataset`, and `--accounts` to choose different comparison
inputs. The report accepts `--input` for a different saved JSON file.

CSV fields also answer different questions: `reply_count` records how many
replies a tweet received, while `is_reply` says whether the tweet itself is a
reply. `retweet_count` and `is_retweet` follow the same distinction. Counts reflect
what X returned at collection time.

## Files used behind the scenes

You do not need to run every Python file. `src/tweet_collection.py` handles
requests and storage; `src/read_account_csv.py` reads the account list;
`src/client_4cat.py` handles 4CAT connections and tweet formatting.
`src/cli_prompts.py` shares terminal questions. `src/collection_errors.py`,
`src/notifications.py`, and `src/collection_summary.py` handle errors and optional
progress messages. `analysis/read_tweets.py` shares the code for reading tweet
files, authors, and dates.

## Where the data lives

The authoritative dataset is on the home server at
`/opt/populism-scraping/data/dataset/`. The Mac's `data/dataset_server/` is a copy
for analysis. `data/dataset/` on the Mac is test space, not the finished dataset.
Exports read all rows in a database; older databases may include dates outside
the current CSV. Combining and 4CAT upload apply the CSV date limits again.

Generated comparison files, temporary work, datasets, and private application
documents stay on this machine. Git tracks the code, account list, and guide.

The server's collection and backup timers were disabled when checked on
2026-08-30. Updating local code does not update that server or restart collection.

## Check code changes

Run the offline tests from this folder:

```bash
.venv/bin/python -m unittest discover -s tests
```

These check local behavior. They do not prove that live X requests, server
backups, or 4CAT uploads work. `AGENTS.md` gives coding assistants the operating
rules for this project.
