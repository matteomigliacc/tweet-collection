"""Recover reference tweet IDs from X within each target's inclusive frame window."""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from read_account_csv import build_jobs, job_paths

# Worklists can be generated on a machine without twscrape installed.
def _fetching_imports():
    global API, export_dataset, extract_raw_tweets, store_raw
    from twscrape import API
    import export_dataset
    from tweet_collection import extract_raw_tweets, store_raw

RAW_DEFAULT = os.path.expanduser("~/Raw Data")
FMT = "%a %b %d %H:%M:%S %z %Y"
ACCOUNTS_DB = "data/accounts.db"


def reference_files(raw_dir: str, handle: str) -> list[Path]:
    """Every professors' file for this handle — top level or party subfolder."""
    pat = re.compile(r"^@?" + re.escape(handle) + r"[ _.]", re.I)
    return [p for p in (list(Path(raw_dir).glob("*.ndjson"))
                        + list(Path(raw_dir).glob("*/*.ndjson")))
            if pat.match(p.name)]


@lru_cache(maxsize=1)
def _reference_rows(paths: tuple[Path, ...]) -> tuple[tuple[str, str, date], ...]:
    """Read one handle's reference files once, streaming them line by line."""
    rows = []
    for path in paths:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj.get("data"), dict) and "rest_id" not in obj:
                    obj = obj["data"]
                leg = obj.get("legacy") or {}
                tid = obj.get("rest_id") or leg.get("id_str")
                created = leg.get("created_at")
                author = leg.get("user_id_str")
                if not tid or not created or author is None:
                    continue
                try:
                    created_date = datetime.strptime(created, FMT).date()
                except Exception:
                    continue
                rows.append((str(tid), str(author), created_date))
    return tuple(rows)


def reference_ids(paths, uid: str, since: date, until: date) -> set[str]:
    """Ids authored by uid inside [since, until] — this target's window only."""
    # This check is intentionally closed while collector.raw_tweet_is_eligible
    # is half-open: jobs pass their raw inclusive end date here, but collector's
    # callers first add one day and pass an exclusive end date.
    return {tid for tid, author, created in _reference_rows(tuple(paths))
            if author == str(uid) and since <= created <= until}


def obj_date(obj: dict) -> date | None:
    try:
        return datetime.strptime(obj["legacy"]["created_at"], FMT).date()
    except Exception:
        return None


def ensure_gone_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS fetch_gone (
                       tweet_id TEXT PRIMARY KEY,
                       handle   TEXT,
                       reason   TEXT,
                       checked_at TEXT)""")
    con.commit()


def mark_gone(con, tweet_id: str, handle: str, reason: str) -> None:
    con.execute("INSERT OR REPLACE INTO fetch_gone VALUES (?,?,?,?)",
                (tweet_id, handle, reason, datetime.now().isoformat(timespec="seconds")))


async def fetch_ids_into_db(api, con, handle: str, uid: str, since: date,
                            until: date, todo: list[str]) -> dict:
    """Fetch each id in `todo` from X and store what belongs in this database."""
    got = extra = absent = outside = 0
    for i, tid in enumerate(todo, 1):
        try:
            rep = await api.tweet_details_raw(int(tid))
        except Exception as exc:                      # never let one id kill the run
            print(f"  [id] {handle} {tid}: error {exc!r}", flush=True)
            continue
        objs = extract_raw_tweets(rep) if rep is not None else []
        if not any(str(o.get("rest_id")) == tid for o in objs):
            absent += 1
            mark_gone(con, tid, handle, "not returned")
        for obj in objs:
            if str((obj.get("legacy") or {}).get("user_id_str")) != uid:
                continue
            d = obj_date(obj)
            if d is None or not (since <= d <= until):
                outside += 1
                continue
            n = store_raw(con, obj, handle, "by_id")
            if str(obj.get("rest_id")) == tid:
                got += n
            else:
                extra += n
        if i % 25 == 0:   # commit in batches; also acts as a progress heartbeat
            con.commit()
            print(f"  [id] {handle} {i}/{len(todo)}: +{got} wanted, "
                  f"+{extra} thread-mates, {absent} gone", flush=True)
    con.commit()
    return {"recovered": got, "thread_mates": extra, "gone": absent,
            "out_of_window": outside}


def held_from_ndjson(path: Path) -> tuple[set[str], str | None]:
    """(ids, dominant author id) straight from an exported ndjson."""
    ids, authors = set(), Counter()
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj.get("data"), dict) and "rest_id" not in obj:
                obj = obj["data"]
            leg = obj.get("legacy") or {}
            tid = obj.get("rest_id") or leg.get("id_str")
            if tid:
                ids.add(str(tid))
                if leg.get("user_id_str"):
                    authors[str(leg["user_id_str"])] += 1
    return ids, (authors.most_common(1)[0][0] if authors else None)


async def fetch_target(api, job: dict, raw_dir: str, limit: int | None,
                       dry_run: bool, retry_gone: bool,
                       have_dir: str | None = None) -> dict:
    """Repair exactly one target: one handle, one window, one database."""
    handle, since, until = job["handle"], job["since"], job["until"]
    db, ndjson = job_paths(job)
    label = f"{job['party']}/{handle} {since}..{until}"
    refs = reference_files(raw_dir, handle)
    if not refs:
        return {"label": label, "skipped": "no reference file"}

    if have_dir:
        mirror = Path(have_dir) / job["party"] / f"{handle}.ndjson"
        if not mirror.exists():
            return {"label": label, "skipped": f"no mirror at {mirror}"}
        have, uid = held_from_ndjson(mirror)
        if not uid:
            return {"label": label, "skipped": "mirror has no author id"}
        want = reference_ids(refs, uid, since, until)
        todo = sorted(want - have)
        if limit:
            todo = todo[:limit]
        return {"label": label, "reference": len(want), "already_held": len(want & have),
                "known_gone": 0, "to_fetch": len(todo), "ids": todo}

    if not db.exists():
        return {"label": label, "skipped": "no database — collect it first"}
    readonly = dry_run or api is None
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) if readonly else sqlite3.connect(str(db))
    if not readonly:
        ensure_gone_table(con)
    # tweet_id and user_id are stored as INTEGER, the reference files carry them
    # as strings — compare as strings throughout or every id looks missing.
    have = {str(r[0]) for r in con.execute("SELECT tweet_id FROM tweets")}
    if not have:
        con.close()
        return {"label": label, "skipped": "database is empty"}

    # The replies tab embeds other authors, so this target's own id is whichever
    # dominates its table — not something to take on trust from the CSV.
    row = con.execute("""SELECT user_id FROM tweets WHERE user_id IS NOT NULL
                         GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
    if not row:
        con.close()
        return {"label": label, "skipped": "no user_id in database"}
    uid = str(row[0])

    want = reference_ids(refs, uid, since, until)
    has_gone = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fetch_gone'"
    ).fetchone()
    gone = set() if retry_gone or not has_gone else {
        str(r[0]) for r in con.execute("SELECT tweet_id FROM fetch_gone")}
    todo = sorted(want - have - gone)
    if limit:
        todo = todo[:limit]

    res = {"label": label, "reference": len(want), "already_held": len(want & have),
           "known_gone": len(want & gone), "to_fetch": len(todo), "ids": todo}
    if dry_run or api is None or not todo:
        con.close()
        return res

    counts = await fetch_ids_into_db(api, con, handle, uid, since, until, todo)
    total = con.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    con.close()

    export_dataset.export_ndjson(db, ndjson)
    res.update(counts, dataset_total=total)
    return res


async def fetch_ids(api, job: dict, todo: list[str]) -> dict:
    """Fetch an explicit id list into one target's database. Window rules apply."""
    handle, since, until = job["handle"], job["since"], job["until"]
    db, ndjson = job_paths(job)
    label = f"{job['party']}/{handle} {since}..{until}"
    if not db.exists():
        return {"label": label, "skipped": "no database — collect it first"}

    con = sqlite3.connect(str(db))
    ensure_gone_table(con)
    row = con.execute("""SELECT user_id FROM tweets WHERE user_id IS NOT NULL
                         GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
    if not row:
        con.close()
        return {"label": label, "skipped": "no user_id in database"}
    uid = str(row[0])
    have = {str(r[0]) for r in con.execute("SELECT tweet_id FROM tweets")}
    todo = [t for t in todo if t not in have]

    counts = await fetch_ids_into_db(api, con, handle, uid, since, until, todo)
    total = con.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    con.close()
    export_dataset.export_ndjson(db, ndjson)
    return {"label": label, "to_fetch": len(todo), "dataset_total": total, **counts}


async def run_worklist(path: str, limit: int | None) -> None:
    """Fetch a worklist produced by --emit-worklist on the machine holding ~/Raw Data."""
    _fetching_imports()
    work = json.load(open(path))
    jobs = {(j["handle"], j["party"], j["since"].isoformat(), j["until"].isoformat()): j
            for j in build_jobs()}
    api = API(ACCOUNTS_DB)
    tot = {"to_fetch": 0, "recovered": 0, "thread_mates": 0, "gone": 0}
    for item in work:
        key = (item["handle"], item["party"], item["since"], item["until"])
        job = jobs.get(key)
        if job is None:
            print(f"\n===== {item['handle']} =====\n  skipped — target not in the "
                  f"frame any more ({item['since']}..{item['until']})", flush=True)
            continue
        ids = item["ids"][:limit] if limit else item["ids"]
        res = await fetch_ids(api, job, ids)
        print(f"\n===== {res['label']} =====", flush=True)
        if "skipped" in res:
            print(f"  skipped — {res['skipped']}", flush=True)
            continue
        print(f"  to fetch {res['to_fetch']:,} | recovered {res['recovered']:,} "
              f"+ {res['thread_mates']:,} thread-mates | {res['gone']:,} not returned "
              f"| {res['out_of_window']:,} out-of-window | database now "
              f"{res['dataset_total']:,}", flush=True)
        for k in ("to_fetch", "recovered", "thread_mates", "gone"):
            tot[k] += res[k]
    print(f"\ntotal: {tot['to_fetch']:,} asked | {tot['recovered']:,} recovered "
          f"(+{tot['thread_mates']:,} thread-mates) | {tot['gone']:,} not returned",
          flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--handle", action="append", metavar="H",
                    help="handle to repair; repeatable. Every spell of it is done "
                         "in turn, each into its own database.")
    ap.add_argument("--all", action="store_true", help="every target in the frame")
    ap.add_argument("--raw", default=RAW_DEFAULT, metavar="DIR",
                    help=f"the professors' reference folder (default {RAW_DEFAULT})")
    ap.add_argument("--limit", type=int, metavar="N",
                    help="fetch at most N ids per target this run")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be fetched and exit")
    ap.add_argument("--retry-gone", action="store_true",
                    help="also re-ask for ids previously recorded as unreturnable")
    ap.add_argument("--emit-worklist", metavar="FILE",
                    help="write the ids that need fetching and exit, instead of "
                         "fetching. Run this where ~/Raw Data lives (3 GB of "
                         "reference files); the worklist itself is a few hundred kB.")
    ap.add_argument("--mirror", metavar="DIR", default="data/dataset_server",
                    help="with --emit-worklist: the rsync'd ndjson mirror to diff "
                         "against, since the local sqlite is a stale snapshot")
    ap.add_argument("--worklist", metavar="FILE",
                    help="fetch the ids in a file written by --emit-worklist. "
                         "Lets the collection box work without the reference folder.")
    args = ap.parse_args()

    if args.worklist:
        await run_worklist(args.worklist, args.limit)
        return
    if not os.path.isdir(args.raw):
        sys.exit(f"--raw: no such folder: {args.raw}")

    jobs = build_jobs()
    if args.handle:
        wanted = {h.lstrip("@").strip().lower() for h in args.handle}
        known = {j["handle"].lower() for j in jobs}
        unknown = wanted - known
        if unknown:
            sys.exit(f"--handle: not in the sampling frame: {', '.join(sorted(unknown))}")
        jobs = [j for j in jobs if j["handle"].lower() in wanted]
    elif not args.all:
        sys.exit("give --handle H (repeatable) or --all")

    emit = args.emit_worklist
    if not (emit or args.dry_run):
        _fetching_imports()
    api = None if (emit or args.dry_run) else API(ACCOUNTS_DB)
    worklist = []
    totals = {"to_fetch": 0, "recovered": 0, "thread_mates": 0, "gone": 0}
    for job in jobs:
        res = await fetch_target(api, job, args.raw, args.limit,
                                 args.dry_run, args.retry_gone,
                                 have_dir=args.mirror if emit else None)
        if emit and res.get("ids"):
            worklist.append({"handle": job["handle"], "party": job["party"],
                             "since": job["since"].isoformat(),
                             "until": job["until"].isoformat(), "ids": res["ids"]})
        print(f"\n===== {res['label']} =====", flush=True)
        if "skipped" in res:
            print(f"  skipped — {res['skipped']}", flush=True)
            continue
        print(f"  reference {res['reference']:,} | already held {res['already_held']:,} "
              f"| known gone {res['known_gone']:,} | to fetch {res['to_fetch']:,}", flush=True)
        totals["to_fetch"] += res["to_fetch"]
        if "recovered" in res:
            print(f"  recovered {res['recovered']:,} wanted "
                  f"+ {res['thread_mates']:,} thread-mates | {res['gone']:,} not returned "
                  f"| {res['out_of_window']:,} dropped as out-of-window "
                  f"| database now {res['dataset_total']:,}", flush=True)
            for k in ("recovered", "thread_mates", "gone"):
                totals[k] += res[k]

    if emit:
        with open(emit, "w") as fh:
            json.dump(worklist, fh)
        print(f"\nworklist -> {emit}: {len(worklist)} targets, "
              f"{totals['to_fetch']:,} ids", flush=True)
        return
    print(f"\nto fetch {totals['to_fetch']:,}"
          + ("" if args.dry_run else
             f" | recovered {totals['recovered']:,} (+{totals['thread_mates']:,} "
             f"thread-mates) | {totals['gone']:,} not returned"), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
