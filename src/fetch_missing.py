"""Fetch tweets the search index won't return, one at a time, straight from X.

Why this exists: X's search index permanently omits many old @-replies. Passes B
and C cannot reach them — @Gertjansegers was checkpoint-reset and re-scraped from
scratch in a run with zero backend errors and came back with the same 1,834-tweet
hole, not one recovered. But the tweets are not gone; they are simply not
*searchable*. `tweet_details` addresses a tweet by id and returns it fine.

So the professors' reference files stop being a data source and become a worklist:
every id they hold that our dataset does not gets fetched from X directly. What
lands in the dataset is the authentic current GraphQL object, not a copy of
someone else's scrape — which is what makes this preferable to injecting their
JSON wholesale.

    python src/fetch_missing.py --handle Gertjansegers --dry-run
    python src/fetch_missing.py --handle Gertjansegers
    python src/fetch_missing.py --all --limit 500

This works on the same unit as run_all.py: one *target*, meaning a handle within
one tenure or seat spell, writing to that spell's own database. A handle can be
several targets — JesseKlaver led GroenLinks, then PRO, then GroenLinks-PvdA, and
each has its own db under its own party folder. Nothing is ever written outside
the window that owns it, so the databases stay exactly as separable as the
scraper leaves them.

Stored with source="by_id", so the dataset can always be split into what search
found and what had to be fetched. Re-runs are cheap: ids that come back empty are
recorded in `fetch_gone` and skipped next time, so a deleted tweet is asked for
once rather than on every run.

Reference files live in ~/Raw Data on the Mac; on the scraper box point --raw at
wherever they were synced.
"""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_all import build_jobs, job_paths

# twscrape (and everything that imports it) is only needed to actually fetch.
# --emit-worklist runs on the Mac, which holds ~/Raw Data but no scraper venv,
# so the import is deferred rather than paid at module load.
def _fetching_imports():
    global API, flatten, extract_raw_tweets, store_raw
    from twscrape import API
    import flatten
    from collect import extract_raw_tweets, store_raw

RAW_DEFAULT = os.path.expanduser("~/Raw Data")
FMT = "%a %b %d %H:%M:%S %z %Y"
ACCOUNTS_DB = "data/accounts.db"


def reference_files(raw_dir: str, handle: str) -> list[Path]:
    """Every professors' file for this handle — top level or party subfolder.

    Some handles have a first *and* second run; both are read and merged by id,
    which is their most complete picture.
    """
    pat = re.compile(r"^@?" + re.escape(handle) + r"[ _.]", re.I)
    return [p for p in (list(Path(raw_dir).glob("*.ndjson"))
                        + list(Path(raw_dir).glob("*/*.ndjson")))
            if pat.match(p.name)]


def reference_ids(paths, uid: str, since: date, until: date) -> set[str]:
    """Ids authored by uid inside [since, until] — this target's window only.

    A reference file covers the handle's whole life, not one spell of it, so the
    window filter is what keeps a JesseKlaver GroenLinks tweet out of the PRO
    database.
    """
    want: set[str] = set()
    for path in paths:
        with open(path, errors="replace") as fh:
            # split on \n only: splitlines() shears JSON at U+2028 in tweet text
            for line in fh.read().split("\n"):
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
                tid, created = obj.get("rest_id") or leg.get("id_str"), leg.get("created_at")
                if not tid or not created or str(leg.get("user_id_str")) != str(uid):
                    continue
                try:
                    d = datetime.strptime(created, FMT).date()
                except Exception:
                    continue
                if since <= d <= until:
                    want.add(str(tid))
    return want


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


def held_from_ndjson(path: Path) -> tuple[set[str], str | None]:
    """(ids, dominant author id) straight from an exported ndjson.

    Used when building a worklist on the Mac, where data/dataset/*.sqlite is a
    historical snapshot and data/dataset_server/ holds the rsync'd truth.
    """
    ids, authors = set(), Counter()
    with open(path, errors="replace") as fh:
        for line in fh.read().split("\n"):
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
        return {"label": label, "skipped": "no database — scrape it first"}
    con = sqlite3.connect(str(db))
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
    gone = set() if retry_gone else {
        str(r[0]) for r in con.execute("SELECT tweet_id FROM fetch_gone")}
    todo = sorted(want - have - gone)
    if limit:
        todo = todo[:limit]

    res = {"label": label, "reference": len(want), "already_held": len(want & have),
           "known_gone": len(want & gone), "to_fetch": len(todo), "ids": todo}
    if dry_run or api is None or not todo:
        con.close()
        return res

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
            # A reply drags its whole thread down with it. Keep only this
            # target's own tweets, and only those inside this target's window —
            # otherwise a 2024 reply would land in the database that is supposed
            # to hold one spell of 2017-2021, and the spells stop being separable.
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
        if i % 25 == 0:
            con.commit()
            print(f"  [id] {handle} {i}/{len(todo)}: +{got} wanted, "
                  f"+{extra} thread-mates, {absent} gone", flush=True)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    con.close()

    flatten.export_ndjson(db, ndjson)
    res.update({"recovered": got, "thread_mates": extra, "gone": absent,
                "out_of_window": outside, "dataset_total": total})
    return res


async def fetch_ids(api, job: dict, todo: list[str]) -> dict:
    """Fetch an explicit id list into one target's database. Window rules apply."""
    handle, since, until = job["handle"], job["since"], job["until"]
    db, ndjson = job_paths(job)
    label = f"{job['party']}/{handle} {since}..{until}"
    if not db.exists():
        return {"label": label, "skipped": "no database — scrape it first"}

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

    got = extra = absent = outside = 0
    for i, tid in enumerate(todo, 1):
        try:
            rep = await api.tweet_details_raw(int(tid))
        except Exception as exc:
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
            got += n if str(obj.get("rest_id")) == tid else 0
            extra += 0 if str(obj.get("rest_id")) == tid else n
        if i % 25 == 0:
            con.commit()
            print(f"  [id] {handle} {i}/{len(todo)}: +{got} wanted, "
                  f"+{extra} thread-mates, {absent} gone", flush=True)
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    con.close()
    flatten.export_ndjson(db, ndjson)
    return {"label": label, "to_fetch": len(todo), "recovered": got,
            "thread_mates": extra, "gone": absent, "out_of_window": outside,
            "dataset_total": total}


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
                         "Lets the scraper box work without the reference folder.")
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
