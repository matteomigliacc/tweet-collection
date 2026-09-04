# Tweet collection

This project collects tweets from Dutch political parties and their leaders,
keeps the dates needed for the study, and prepares the data for analysis in 4CAT.
It also compares the dataset with reference exports, so researchers
can see which tweets are shared and which are missing.

## Getting started

You need Python 3.10 or newer, Bash, and an X login with valid session cookies.
The commands below are for macOS or Linux. On Windows, use WSL.

Clone the repository, then install the dependencies:

```bash
git clone https://github.com/matteomigliacc/populism-tweet-scraper.git
cd populism-tweet-scraper
bash setup.sh
source .venv/bin/activate
python collection.py --help
```

Setup creates a Python environment and offers to add your X login cookies.
You can also add logins later with `python collection.py add-logins`, or edit
`secrets/accounts.json` using the supplied example and run
`python collection.py load-logins`. Loading logins makes a request to X to check
that they work. These logins are separate from the accounts you want to study.
Keep cookies and credentials private.

Edit `frame/accounts.csv` to choose the accounts and dates for your project.
The included rows describe the original Dutch political study; replace them
if you want a different collection. Preview the account list before collecting:

```bash
python collection.py collect --dry-run
```

Start collection with terminal prompts:

```bash
python collection.py collect
```

For a non-interactive run, use `python collection.py collect --all`.
Running `collection.py` without a command only shows help. Add `--help` to any
command to see its options.

## How the data moves

1. **Choose accounts and dates.** `frame/accounts.csv` lists both parties and
   leaders. Each row needs `handle`, `party`, `start`, and `end`. Dates use
   `YYYY-MM-DD` and include both the first and last day. `name`, `kind`, and
   `notes` describe the entry. Separate periods use separate rows.
2. **Collect tweets.** The code reads the timeline, searches overlapping calendar
   months, and reads replies. It stores only tweets by the requested account
   within the row's dates. The included study dates span 2017-03-23 through 2025-11-12.
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
| `compare` | `analysis/recall_data.py` | Compare collected tweets with reference exports. |
| `report` | `analysis/comparison_report.py` | Turn comparison results into an HTML page. |
| `collect-missing` | `src/collect_missing.py` | Try collecting specific missing tweet IDs from reference files. |

`deploy/backup.sh` supports backups to SURFdrive.
`deploy/upload_yoda.sh` transfers dataset files to Yoda; its `--dry-run` option
shows the proposed transfer without contacting Yoda. These use separate service
configuration and are not part of the command menu. The deployment scripts
contain settings from the original installation; adapt them before use.

## Understanding the comparison

Supply a folder of reference exports and the dataset you want to compare.
The comparison uses the account list and its exact date windows.
“Recall” means the percentage of reference tweet IDs found in the collected dataset.
`own_recall` excludes missing tweets attributed to another author. Reference
exports can include parent tweets and quoted tweets by other people.

Add `--other-authors` to include a broader count of other authors in the reference
files, across all their dates. This answers a different question from overlap
within the study dates, but now runs through the same comparison command.
You can save the results and make the HTML report with:

```bash
.venv/bin/python collection.py compare --reference data/reference --dataset data/dataset --accounts frame/accounts.csv --other-authors --output analysis/dataset_recall.json
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

## Data files and optional uploads

Collection writes files under `data/dataset/<party>/`: a SQLite database for
each account and an NDJSON export with one tweet per line. Login details are
stored in `secrets/accounts.json` and the login database in `data/accounts.db`.
Datasets, credentials, and generated analysis results are excluded from Git;
they are not included when you download this repository.

To combine your collected files:

```bash
.venv/bin/python collection.py combine --dataset data/dataset --out data/combined/all_tweets_sorted.ndjson
```

Exports read every row in a database. Combining and 4CAT upload apply the account
CSV's date limits again, which matters if you change those dates later.

4CAT is optional and requires your own instance and credentials. To preview
separate-account uploads from your collection folder:

```bash
.venv/bin/python collection.py upload --dataset data/dataset --no-sync --dry-run
```

Use `--dataset` explicitly for combining, comparison, and uploads: some scripts
retain defaults from the original study. Keep `--no-sync` for uploads to use
only your selected files. Before uploading, create `secrets/fourcat.json`:

```json
{
  "base_url": "https://your-4cat-instance.example",
  "api_token": "YOUR_API_TOKEN"
}
```

Replace these placeholders with your instance URL and API token, then run the
upload command without `--dry-run`.

## Check code changes

Run the offline tests from this folder:

```bash
.venv/bin/python -m unittest discover -s tests
```

These check local behavior. They do not prove that live X requests, server
backups, or 4CAT uploads work. `AGENTS.md` gives coding assistants the operating
rules for this project.
