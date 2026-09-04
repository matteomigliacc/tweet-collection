"""Three-pass tweet collection with exact author and half-open date filtering."""
import argparse
import asyncio
import json
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

from read_account_csv import month_chunks

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "frame" / "politicians.csv"
DB = ROOT / "data" / "tweets.sqlite"
ACCOUNTS_DB = ROOT / "data" / "accounts.db"


def init_db(con: sqlite3.Connection) -> None:
    """Create or migrate the tweet and checkpoint tables."""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS tweets (
            tweet_id     INTEGER PRIMARY KEY,
            handle       TEXT NOT NULL,
            user_id      INTEGER,
            created_at   TEXT,
            source       TEXT,          -- first insertion: timeline/search/replies_tab/by_id
            raw_json     TEXT NOT NULL,
            collected_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tweets_handle ON tweets(handle);

        CREATE TABLE IF NOT EXISTS checkpoint (
            handle       TEXT PRIMARY KEY,
            user_id      INTEGER,
            recent_done  INTEGER DEFAULT 0,
            replies_done INTEGER DEFAULT 0,
            months_done  TEXT DEFAULT '[]',   -- JSON list of 'YYYY-MM' completed
            status       TEXT,                -- ok | not_found | error
            error        TEXT,
            updated_at   TEXT
        );
        """
    )
    # migrate pre-Pass-C databases (no replies_done column yet)
    try:
        con.execute("ALTER TABLE checkpoint ADD COLUMN replies_done INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    con.commit()




# Search is capped per month; keep the limit above the busiest month.
SEARCH_LIMIT = 20000


async def search_window(api, con, handle, uid, query_start: date, query_end: date,
                        keep_since: date, keep_until: date,
                        raw: bool) -> tuple[int, int]:
    """Search one full month and store only rows inside [keep_since, keep_until)."""
    q = (f"from:{handle} since:{query_start.isoformat()} "
         f"until:{query_end.isoformat()}")
    new = seen = 0
    if raw:
        async for rep in api.search_raw(q, limit=SEARCH_LIMIT):
            for obj in extract_raw_tweets(rep):
                seen += 1
                if not raw_tweet_is_eligible(obj, uid, keep_since, keep_until):
                    continue
                new += store_raw(con, obj, handle, "search")
    else:
        async for tw in api.search(q, limit=SEARCH_LIMIT):
            seen += 1
            if tw.user.id != uid or not (keep_since <= tw.date.date() < keep_until):
                continue
            new += store(con, tw, handle, uid, "search")
    return new, seen


def load_checkpoint(con: sqlite3.Connection, handle: str) -> dict:
    """Read a handle's progress, or return a fresh checkpoint."""
    row = con.execute(
        "SELECT recent_done, replies_done, months_done FROM checkpoint WHERE handle = ?",
        (handle,)
    ).fetchone()
    if row is None:
        return {"recent_done": 0, "replies_done": 0, "months_done": []}
    return {"recent_done": row[0], "replies_done": row[1] or 0,
            "months_done": json.loads(row[2] or "[]")}


def save_checkpoint(con, handle, user_id, recent_done, months_done, status,
                    error=None, replies_done=0):
    """Insert or update a handle checkpoint."""
    con.execute(
        """INSERT INTO checkpoint (handle, user_id, recent_done, replies_done,
                                   months_done, status, error, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(handle) DO UPDATE SET
               user_id=excluded.user_id, recent_done=excluded.recent_done,
               replies_done=excluded.replies_done,
               months_done=excluded.months_done, status=excluded.status,
               error=excluded.error, updated_at=excluded.updated_at""",
        (handle, user_id, recent_done, replies_done, json.dumps(months_done),
         status, error, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()


def store(con, tweet, handle, user_id, source) -> int:
    """Store a parsed twscrape tweet; return 1 if it was new."""
    cur = con.execute(
        """INSERT OR IGNORE INTO tweets
               (tweet_id, handle, user_id, created_at, source, raw_json, collected_at)
           VALUES (?,?,?,?,?,?,?)""",
        (tweet.id, handle, user_id, tweet.date.isoformat(), source,
         tweet.json(), datetime.now(timezone.utc).isoformat()),
    )
    return cur.rowcount


def _unwrap_result(res):
    """Return the raw GraphQL Tweet object (with legacy/rest_id), or None."""
    if not isinstance(res, dict):
        return None
    if res.get("__typename") == "TweetWithVisibilityResults":
        res = res.get("tweet")
    if isinstance(res, dict) and "rest_id" in res and "legacy" in res:
        return res
    return None


def extract_raw_tweets(rep) -> list[dict]:
    """Pull the top-level raw Tweet objects out of a raw GraphQL response."""
    out = []

    def from_entry(entry):
        content = entry.get("content", {}) or {}
        ic = content.get("itemContent")
        if ic:
            r = _unwrap_result((ic.get("tweet_results") or {}).get("result"))
            if r:
                out.append(r)
        for it in content.get("items", []) or []:
            ic2 = (it.get("item") or {}).get("itemContent") or {}
            r = _unwrap_result((ic2.get("tweet_results") or {}).get("result"))
            if r:
                out.append(r)

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "entries" and isinstance(v, list):
                    for e in v:
                        from_entry(e)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(rep.json())
    return out


def store_raw(con, obj, handle, source) -> int:
    """INSERT OR IGNORE one raw GraphQL Tweet object. Returns 1 if newly inserted."""
    leg = obj["legacy"]
    # X's date format: 'Wed Aug 14 09:15:00 +0000 2023' -> a datetime we can sort
    dt = datetime.strptime(leg["created_at"], "%a %b %d %H:%M:%S %z %Y")
    cur = con.execute(
        """INSERT OR IGNORE INTO tweets
               (tweet_id, handle, user_id, created_at, source, raw_json, collected_at)
           VALUES (?,?,?,?,?,?,?)""",
        (int(obj["rest_id"]), handle, int(leg.get("user_id_str") or 0),
         dt.isoformat(), source, json.dumps(obj, ensure_ascii=False),
         datetime.now(timezone.utc).isoformat()),
    )
    return cur.rowcount


def raw_tweet_is_eligible(obj: dict, uid: int, since: date, until: date) -> bool:
    """True for a target-authored raw tweet inside the requested [since, until)."""
    leg = obj.get("legacy") or {}
    if str(leg.get("user_id_str")) != str(uid):
        return False
    try:
        created = datetime.strptime(
            leg["created_at"], "%a %b %d %H:%M:%S %z %Y"
        ).date()
    except (KeyError, TypeError, ValueError):
        return False
    return since <= created < until


def read_frame(csv_path: Path) -> list[dict]:
    import csv
    with csv_path.open() as f:
        return list(csv.DictReader(f))


async def collect_handle(api, con, row, since, until, recent_limit,
                         skip_recent=False, skip_replies=False,
                         verbose=False, raw=False) -> None:
    """Scrape one account: resolve the handle to a user id, then run the passes."""
    handle = row["handle"].lstrip("@").strip()
    cp = load_checkpoint(con, handle)

    user = await api.user_by_login(handle)
    if user is None:
        save_checkpoint(con, handle, None, cp["recent_done"], cp["months_done"],
                        "not_found", "user_by_login returned None",
                        replies_done=cp["replies_done"])
        print(f"  [!] @{handle}: not found / suspended / renamed — skipped", flush=True)
        return
    uid = user.id
    if verbose:
        print(f"  [>] @{handle} resolved id={uid}, {user.statusesCount} tweets on profile",
              flush=True)

    # Pass A — recent timeline
    if not skip_recent and not cp["recent_done"]:
        n = 0
        if raw:
            async for rep in api.user_tweets_raw(uid, limit=recent_limit):
                for obj in extract_raw_tweets(rep):
                    if not raw_tweet_is_eligible(obj, uid, since, until):
                        continue
                    n += store_raw(con, obj, handle, "timeline")
        else:
            async for tw in api.user_tweets(uid, limit=recent_limit):
                if tw.user.id != uid or not (since <= tw.date.date() < until):
                    continue
                n += store(con, tw, handle, uid, "timeline")
                if verbose and n and n % 100 == 0:
                    print(f"  [A] @{handle}: {n} recent so far ...", flush=True)
        con.commit()
        cp["recent_done"] = 1
        save_checkpoint(con, handle, uid, 1, cp["months_done"], "ok",
                        replies_done=cp["replies_done"])
        print(f"  [A] @{handle}: +{n} recent tweets", flush=True)

    # Pass B — query full calendar months, store only the exact target window
    all_months = [s.strftime("%Y-%m") for s, _ in month_chunks(since, until)]
    for start, end in month_chunks(since, until):
        tag = start.strftime("%Y-%m")
        if tag in cp["months_done"]:
            continue
        if verbose:
            idx = all_months.index(tag) + 1
            print(f"  [B] @{handle} {tag} ({idx}/{len(all_months)}) "
                  f"[{datetime.now():%H:%M:%S}] querying ...", flush=True)
        t0 = time.perf_counter()
        n, _ = await search_window(
            api, con, handle, uid, start, end, since, until, raw
        )
        con.commit()
        cp["months_done"].append(tag)
        save_checkpoint(con, handle, uid, cp["recent_done"], cp["months_done"], "ok",
                        replies_done=cp["replies_done"])
        if verbose:
            total = con.execute(
                "SELECT COUNT(*) FROM tweets WHERE handle = ?", (handle,)
            ).fetchone()[0]
            print(f"  [B] @{handle} {tag}: +{n:>4} in {time.perf_counter()-t0:4.1f}s "
                  f"(handle total {total})", flush=True)
        else:
            print(f"  [B] @{handle} {tag}: +{n} tweets", flush=True)

    # Pass C — replies tab (recovers replies that X's search index omits)
    if not skip_replies and not cp["replies_done"]:
        n = 0
        if raw:
            async for rep in api.user_tweets_and_replies_raw(uid, limit=recent_limit):
                for obj in extract_raw_tweets(rep):
                    # The tab reaches outside the study window and embeds parent
                    # tweets by other authors; neither belongs in this target.
                    if not raw_tweet_is_eligible(obj, uid, since, until):
                        continue
                    n += store_raw(con, obj, handle, "replies_tab")
        else:
            async for tw in api.user_tweets_and_replies(uid, limit=recent_limit):
                if tw.user.id != uid or not (since <= tw.date.date() < until):
                    continue
                n += store(con, tw, handle, uid, "replies_tab")
                if verbose and n and n % 100 == 0:
                    print(f"  [C] @{handle}: {n} own tweets so far ...", flush=True)
        con.commit()
        cp["replies_done"] = 1
        save_checkpoint(con, handle, uid, cp["recent_done"], cp["months_done"], "ok",
                        replies_done=1)
        print(f"  [C] @{handle}: +{n} from replies tab", flush=True)


async def run_collection(frame, since, until, db_path,
                         recent_limit=3200, skip_recent=False, skip_replies=False,
                         verbose=False, raw=False) -> int:
    """Collect all handles in `frame` into `db_path`. Returns total tweets in the DB."""
    from twscrape import API

    api = API(str(ACCOUNTS_DB))
    con = sqlite3.connect(str(db_path))
    init_db(con)

    passes = ([] if skip_recent else ["A"]) + ["B"] + ([] if skip_replies else ["C"])
    mode = "passes " + "+".join(passes) + (" | raw" if raw else "")
    print(f"Collecting {len(frame)} handles | window {since}..{until} | {mode}\n", flush=True)
    for row in frame:
        try:
            await collect_handle(api, con, row, since, until, recent_limit,
                                 skip_recent=skip_recent, skip_replies=skip_replies,
                                 verbose=verbose, raw=raw)
        except Exception as e:
            h = row.get("handle", "?").lstrip("@").strip()
            prev = load_checkpoint(con, h)  # preserve any progress already made
            save_checkpoint(con, h, None, prev["recent_done"], prev["months_done"],
                            "error", str(e), replies_done=prev["replies_done"])
            print(f"  [x] @{h}: ERROR {e}", flush=True)

    total = con.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    print(f"\nDone. {total} tweets in {Path(db_path).name}", flush=True)
    con.close()
    return total


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--db", type=Path, default=DB, help="output SQLite path")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--recent-limit", type=int, default=3200)
    ap.add_argument("--skip-recent", action="store_true",
                    help="skip Pass A (recent timeline); search-only, date-bounded")
    ap.add_argument("--skip-replies", action="store_true",
                    help="skip Pass C (replies tab)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="stream per-month progress, timings, and running totals")
    args = ap.parse_args()

    since = datetime.strptime(args.since, "%Y-%m-%d").date()
    until = datetime.strptime(args.until, "%Y-%m-%d").date()

    frame = read_frame(args.csv)
    await run_collection(frame, since, until, args.db,
                         recent_limit=args.recent_limit,
                         skip_recent=args.skip_recent, skip_replies=args.skip_replies,
                         verbose=args.verbose)


if __name__ == "__main__":
    asyncio.run(main())
