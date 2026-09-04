"""Collect frame targets into SQLite and NDJSON; CSV dates are inclusive.

Checkpoint completion describes collection progress, not validated dataset completeness."""
import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

import read_account_csv
from read_account_csv import month_chunks
from tweet_collection import run_collection
from export_dataset import export_ndjson
import collection_errors
import notifications
import collection_summary

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "dataset"
FRAME = ROOT / "frame" / "accounts.csv"


def build_jobs() -> list[dict]:
    return read_account_csv.build_jobs(FRAME)


def job_paths(job: dict) -> tuple[Path, Path]:
    return read_account_csv.job_paths(job, OUT)


def expected_months(since: date, until: date) -> set[str]:
    return {s.strftime("%Y-%m") for s, _ in month_chunks(since, until)}


def is_complete(db: Path, handle: str, since: date, until: date) -> bool:
    """Return whether all three passes cover the target window."""
    if not db.exists():
        return False
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            """SELECT months_done, recent_done, replies_done
               FROM checkpoint WHERE handle = ?""",
            (handle,)).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()
    if not row or not row[1] or not row[2]:
        return False
    return expected_months(since, until + timedelta(days=1)) <= set(json.loads(row[0] or "[]"))


async def run_one(job: dict) -> int:
    """Collect one target and export its working store to NDJSON."""
    db, nd_path = job_paths(job)
    db.parent.mkdir(parents=True, exist_ok=True)
    handle = job["handle"]
    tag = f"{job['party']}/{handle}"
    if is_complete(db, handle, job["since"], job["until"]):
        print(f"{tag} is already complete ({job['since']} -> {job['until']}). Re-exporting ndjson.")
        n = export_ndjson(db, nd_path)
        print(f"-> {nd_path.relative_to(ROOT)} ({n} tweets)")
        return n
    print(f"\nScraping {tag}   window {job['since']} -> {job['until']} (from frame CSV)\n")
    frame = [{"handle": handle, "name": "", "party": job["party"], "country": "NL"}]
    until_excl = job["until"] + timedelta(days=1)  # include the end date itself
    await run_collection(frame, job["since"], until_excl, db,
                         skip_recent=False, verbose=True, raw=True)
    n = export_ndjson(db, nd_path)
    print(f"\n-> {nd_path.relative_to(ROOT)} ({n} tweets)")
    return n


def print_menu(jobs: list[dict]) -> None:
    print("\nWhich target do you want to tackle?\n")
    for i, j in enumerate(jobs, 1):
        db, _ = job_paths(j)
        done = "✓" if is_complete(db, j["handle"], j["since"], j["until"]) else " "
        kind = "  [party acct]" if j["kind"] == "party" else ""
        print(f"  [{done}] {i:2}. {j['party']:16} @{j['handle']:18} "
              f"{j['since']} -> {j['until']}{kind}")


class _Tee:
    """Write console output to several streams."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def setup_logging() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    log_path = OUT / f"collection_{datetime.now():%Y%m%d_%H%M%S}.log"
    fh = open(log_path, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, fh)
    logger.add(str(log_path), level="INFO", enqueue=True)
    print(f"[log] writing this session to {log_path.relative_to(ROOT)}")
    return log_path


QUOTA_FILE = OUT / ".quota.json"


def _daily_count() -> int:
    """Return how many targets have been attempted today."""
    try:
        d = json.loads(QUOTA_FILE.read_text())
        return int(d.get("count", 0)) if d.get("date") == date.today().isoformat() else 0
    except Exception:
        return 0


def _bump_daily_count() -> int:
    n = _daily_count() + 1
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUOTA_FILE.write_text(json.dumps({"date": date.today().isoformat(), "count": n}))
    return n


async def run_batch(jobs: list[dict], limit: int | None = None,
                    daily_limit: int | None = None,
                    notify_each: bool = False,
                    error_report_min: float = 10.0) -> int:
    """Non-interactive: collect incomplete targets once, isolating failures."""
    done = skipped = partial = failed = 0
    records: list[dict] = []
    total = len(jobs)
    session_start = time.monotonic()
    monitor = collection_errors.ErrorMonitor(window_minutes=error_report_min) if error_report_min else None
    if monitor:
        monitor.start()
    for i, job in enumerate(jobs, 1):
        db, _ = job_paths(job)
        tag = f"{job['party']}/{job['handle']}"
        if is_complete(db, job["handle"], job["since"], job["until"]):
            skipped += 1
            continue
        if limit is not None and len(records) >= limit:
            print(f"\n[limit] reached per-run cap of {limit} — stopping.")
            break
        if daily_limit is not None and _daily_count() >= daily_limit:
            print(f"\n[quota] daily cap of {daily_limit} already reached today — stopping.")
            break
        print(f"\n===== [{i}/{total}] {tag} =====")
        if monitor:
            monitor.current_target = tag
        _bump_daily_count()   # count the attempt up-front (a crash still consumes a slot)
        rec = {"party": job["party"], "handle": job["handle"],
               "since": job["since"], "until": job["until"], "tweets": None}
        t0 = time.monotonic()
        try:
            rec["tweets"] = await run_one(job)
        except KeyboardInterrupt:
            print("\n  interrupted — progress checkpointed; re-run to resume")
            if monitor:
                await monitor.stop()   # flush the partial window before dying
                print(f"[errors] {monitor.final_summary()}")
            raise
        except Exception as e:  # one bad handle must not abort the whole batch
            failed += 1
            logger.error(f"{tag} failed: {e!r}")
            print(f"  !! {tag} failed: {e!r} — continuing")
            rec["seconds"] = time.monotonic() - t0
            rec["status"] = "failed"
            records.append(rec)
            if notify_each:
                notifications.send_teams(collection_summary.build_account_card(rec, i, total, skipped + done))
            continue
        rec["seconds"] = time.monotonic() - t0
        if is_complete(db, job["handle"], job["since"], job["until"]):
            done += 1
            rec["status"] = "complete"
        else:
            partial += 1
            rec["status"] = "partial"
            print(f"  ~ {tag} made progress but isn't fully covered yet — will resume next run")
        records.append(rec)
        if notify_each:
            notifications.send_teams(collection_summary.build_account_card(rec, i, total, skipped + done))

    session_secs = time.monotonic() - session_start
    if monitor:
        await monitor.stop()
    print(f"\nBatch summary: {len(records)} processed this run — {done} newly complete, "
          f"{partial} partial, {failed} failed; {skipped} already complete "
          f"(of {total} targets, {_daily_count()} done today).")
    if monitor:
        print(f"[errors] {monitor.final_summary()}")
    if records:  # one summary notification per run, only when something was processed
        card = collection_summary.build_session_card(records, done, partial, failed,
                                  skipped, total, _daily_count(), session_secs, monitor)
        if not notifications.send_teams(card):  # Teams first; email only as fallback
            subject, text, html = collection_summary.build_session_email(records, done, partial, failed,
                                                       skipped, total, _daily_count(), session_secs)
            notifications.send_email(subject, text, html=html)
    return 1 if failed else 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="just list the targets and exit")
    ap.add_argument("--all", action="store_true",
                    help="collect every incomplete target non-interactively (for cron/systemd)")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="with --all: collect at most N targets this run")
    ap.add_argument("--daily-limit", type=int, default=None, metavar="M",
                    help="with --all: collect at most M targets per calendar day (across runs)")
    ap.add_argument("--notify-each", action="store_true",
                    help="with --all: post a Teams card after every account, not just "
                         "the end-of-session summary")
    ap.add_argument("--only", metavar="HANDLES",
                    help="restrict the run to these comma-separated handles, collected in "
                         "the order given (cheapest first keeps the account pool's burst "
                         "capacity for the expensive targets). Unknown handles abort the "
                         "run rather than silently collection nothing.")
    ap.add_argument("--error-report-min", type=float, default=10.0, metavar="MIN",
                    help="with --all: post a Teams card every MIN minutes if X's backend "
                         "threw errors in that window (default 10; 0 disables). Those "
                         "queries return no tweets without raising, so nothing else "
                         "notices them.")
    args = ap.parse_args()

    jobs = build_jobs()
    if args.only:
        wanted = [h.lstrip("@").strip().lower() for h in args.only.split(",") if h.strip()]
        by_handle: dict[str, list[dict]] = {}
        for j in jobs:
            by_handle.setdefault(j["handle"].lower(), []).append(j)
        unknown = [h for h in wanted if h not in by_handle]
        if unknown:
            sys.exit(f"--only: no such target(s): {', '.join(unknown)}")
        # A handle with several tenures keeps all of them, adjacent and in order.
        jobs = [j for h in wanted for j in by_handle[h]]
    if args.dry_run:
        print_menu(jobs)
        return

    setup_logging()
    if args.all:
        code = await run_batch(jobs, limit=args.limit, daily_limit=args.daily_limit,
                               notify_each=args.notify_each,
                               error_report_min=args.error_report_min)
        sys.exit(code)

    print_menu(jobs)
    while True:
        raw = input("\nPick a number (or handle), or 'q' to quit: ").strip()
        if raw.lower() in {"q", "quit", "exit", ""}:
            print("Bye.")
            return
        job = None
        if raw.isdigit() and 1 <= int(raw) <= len(jobs):
            job = jobs[int(raw) - 1]
        else:
            matches = [j for j in jobs if j["handle"].lower() == raw.lstrip("@").lower()]
            if len(matches) == 1:
                job = matches[0]
            elif len(matches) > 1:
                print("  that handle has multiple tenures — pick it by number instead")
                continue
        if job is None:
            print("  no such target — try again")
            continue
        try:
            await run_one(job)
        except KeyboardInterrupt:
            print("\n  interrupted — progress checkpointed, pick it again to resume")
        if input("\nScrape another? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print("Bye.")
            return
        print_menu(jobs)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted — progress is checkpointed, just re-run to resume.")
