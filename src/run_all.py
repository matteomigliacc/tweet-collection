"""Automatic batch scraper for the whole dataset — the main entry point.

Reads frame/leaders.csv (party leaders + their tenure dates) and frame/parties.csv
(party accounts), computes each target's date window, and scrapes each into its
own pair of files under data/dataset/<Party>/<handle>.{sqlite,ndjson}. The
actual scraping is done by collect.py; this script decides WHO gets scraped
WHEN, and wraps that in logging, quotas, and notifications.

Dataset rules (one "target" = one handle within one date window):
  * Study window: 2017-03-23 (Tweede Kamer installation, FLOOR) to
    2025-11-12 (TK election, CEILING). Nothing outside it is requested.
  * Leaders: their tenure only, clipped to the study window. "ongoing" in the
    CSV means "until the ceiling".
  * Party accounts: only while the party held Tweede Kamer seats — one
    seat_start/seat_end spell per CSV row, month-aligned. A party that left and
    later returned (e.g. 50PLUS) has two rows, both scraped into the one
    account file (the out-of-parliament gap is skipped).
  * A handle can appear under two parties (e.g. Klaver: GroenLinks, then
    GroenLinks-PvdA) -> two separate files under the two party folders.

Resumable: each target has its own SQLite DB with a checkpoint table, so a
target already covering its full window is skipped entirely, and an interrupted
target resumes where it stopped (see collect.py for how checkpoints work).

Usage:
  python src/run_all.py --dry-run              # list every job + window, scrape nothing
  python src/run_all.py                        # interactive menu, one target at a time
  python src/run_all.py --all --limit 3 --daily-limit 15   # what the server timer runs
  python src/run_all.py --only handle1,handle2 # just these handles, in this order
"""
import argparse
import asyncio
import csv
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

from collect import run_collection, month_chunks
from flatten import export_ndjson
import errmon
import notify
import reports

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "dataset"     # scraped output: party-named subfolders under data/dataset/<party>/
LEADERS = ROOT / "frame" / "leaders.csv"   # committed sampling-frame inputs
PARTIES = ROOT / "frame" / "parties.csv"
FLOOR = date(2017, 3, 23)      # 2017 Tweede Kamer installation: the study window starts here
CEILING = date(2025, 11, 12)   # 2025 Tweede Kamer election: the study window ends here


def parse_end(s: str) -> date:
    """Turn an end-date CSV cell into a real date, capped at the study ceiling.

    The frame CSVs write "ongoing" for a leader/party still in place; that
    means "no end yet", so it becomes today — and min() then clips everything
    to the 2025-11-12 ceiling, since the study stops there regardless.
    """
    s = s.strip().lower()
    end = date.today() if s == "ongoing" else datetime.strptime(s, "%Y-%m-%d").date()
    return min(end, CEILING)


def read_csv(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def build_jobs() -> list[dict]:
    """One dict per scrape target: handle, party, since, until (inclusive), kind."""
    jobs = []
    for r in read_csv(LEADERS):
        start = datetime.strptime(r["leader_start"], "%Y-%m-%d").date()
        since = max(start, FLOOR)
        until = parse_end(r["leader_end"])
        if since > until:
            continue  # tenure entirely before the 2017 floor -> nothing to fetch
        jobs.append({"handle": r["handle"].lstrip("@").strip(), "party": r["party"],
                     "since": since, "until": until, "kind": "leader"})
    for r in read_csv(PARTIES):
        start = datetime.strptime(r["seat_start"], "%Y-%m-%d").date().replace(day=1)
        since = max(start, FLOOR)          # month-aligned; nothing before the floor
        until = parse_end(r["seat_end"])
        if since > until:
            continue  # seat spell entirely before the 2017 floor -> nothing to fetch
        jobs.append({"handle": r["handle"].lstrip("@").strip(), "party": r["party"],
                     "since": since, "until": until, "kind": "party"})
    return jobs


def job_paths(job: dict) -> tuple[Path, Path]:
    d = OUT / job["party"]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job['handle']}.sqlite", d / f"{job['handle']}.ndjson"


def expected_months(since: date, until: date) -> set[str]:
    return {s.strftime("%Y-%m") for s, _ in month_chunks(since, until)}


def is_complete(db: Path, handle: str, since: date, until: date) -> bool:
    """True when this target needs no more scraping.

    Complete means: the replies-tab pass (Pass C) ran, AND every calendar month
    of the target's window appears in the checkpoint's months_done list. The
    `<=` on the last line is Python's subset test for sets.
    """
    if not db.exists():
        return False
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT months_done, replies_done FROM checkpoint WHERE handle = ?",
            (handle,)).fetchone()
    except sqlite3.OperationalError:
        return False  # old schema (no replies_done yet) -> needs a Pass C run
    finally:
        con.close()
    if not row or not row[1]:  # Pass C (replies tab) not done yet
        return False
    return expected_months(since, until) <= set(json.loads(row[0] or "[]"))


async def run_one(job: dict) -> int:
    """Scrape a single target to its tenure window and export raw ndjson.

    Returns the number of tweets in the exported ndjson."""
    db, nd_path = job_paths(job)
    handle = job["handle"]
    tag = f"{job['party']}/{handle}"
    if is_complete(db, handle, job["since"], job["until"]):
        print(f"{tag} is already complete ({job['since']} -> {job['until']}). Re-exporting ndjson.")
        n = export_ndjson(db, nd_path)
        print(f"-> {nd_path.relative_to(ROOT)} ({n} tweets)")
        return n
    print(f"\nScraping {tag}   window {job['since']} -> {job['until']} (tenure, clipped to {FLOOR}..{CEILING})\n")
    frame = [{"handle": handle, "name": "", "party": job["party"], "country": "NL"}]
    until_excl = job["until"] + timedelta(days=1)  # include the end date itself
    await run_collection(frame, job["since"], until_excl, db,
                         skip_recent=True, verbose=True, raw=True)
    n = export_ndjson(db, nd_path)
    print(f"\n-> {nd_path.relative_to(ROOT)} ({n} tweets)")
    return n


def print_menu(jobs: list[dict]) -> None:
    print(f"\nWhich leader do you want to tackle?  (floor {FLOOR}, tenure-only)\n")
    for i, j in enumerate(jobs, 1):
        db, _ = job_paths(j)
        done = "✓" if is_complete(db, j["handle"], j["since"], j["until"]) else " "
        kind = "" if j["kind"] == "leader" else "  [party acct]"
        print(f"  [{done}] {i:2}. {j['party']:16} @{j['handle']:18} "
              f"{j['since']} -> {j['until']}{kind}")


class _Tee:
    """Duplicate console output to a log file as well."""
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
    log_path = OUT / f"scrape_{datetime.now():%Y%m%d_%H%M%S}.log"
    fh = open(log_path, "a", encoding="utf-8")
    # console prints (progress) -> console + file
    sys.stdout = _Tee(sys.__stdout__, fh)
    # twscrape's own loguru messages (rate limits, warnings) -> same file
    logger.add(str(log_path), level="INFO", enqueue=True)
    print(f"[log] writing this session to {log_path.relative_to(ROOT)}")
    return log_path


QUOTA_FILE = OUT / ".quota.json"   # tracks targets processed per calendar day


def _daily_count() -> int:
    """Targets already processed *today* (0 if the state file is old/absent)."""
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
    """Non-interactive: scrape incomplete targets once, isolating failures.

    Built for cron/systemd. `limit` caps targets processed this run; `daily_limit`
    caps targets processed per calendar day across all runs (persisted in
    data/dataset/.quota.json) so a timer firing N times still won't exceed it.
    Sends ONE summary email per run (if any account was processed).
    Returns exit code 0 if nothing failed, 1 if any target errored.

    Every `error_report_min` minutes an `errmon.ErrorMonitor` posts a Teams card
    if X's backend threw any errors in that window — those queries return zero
    tweets without raising, so nothing else in this function would notice. Set to
    0 to disable.
    """
    done = skipped = partial = failed = 0
    records: list[dict] = []
    total = len(jobs)
    session_start = time.monotonic()
    monitor = errmon.ErrorMonitor(window_minutes=error_report_min) if error_report_min else None
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
                notify.send_teams(reports.build_account_card(rec, i, total, skipped + done))
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
            notify.send_teams(reports.build_account_card(rec, i, total, skipped + done))

    session_secs = time.monotonic() - session_start
    if monitor:
        await monitor.stop()
    print(f"\nBatch summary: {len(records)} processed this run — {done} newly complete, "
          f"{partial} partial, {failed} failed; {skipped} already complete "
          f"(of {total} targets, {_daily_count()} done today).")
    if monitor:
        print(f"[errors] {monitor.final_summary()}")
    if records:  # one summary notification per run, only when something was processed
        card = reports.build_session_card(records, done, partial, failed,
                                  skipped, total, _daily_count(), session_secs, monitor)
        if not notify.send_teams(card):  # Teams first; email only as fallback
            subject, text, html = reports.build_session_email(records, done, partial, failed,
                                                       skipped, total, _daily_count(), session_secs)
            notify.send_email(subject, text, html=html)
    return 1 if failed else 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="just list the targets and exit")
    ap.add_argument("--all", action="store_true",
                    help="scrape every incomplete target non-interactively (for cron/systemd)")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="with --all: scrape at most N targets this run")
    ap.add_argument("--daily-limit", type=int, default=None, metavar="M",
                    help="with --all: scrape at most M targets per calendar day (across runs)")
    ap.add_argument("--notify-each", action="store_true",
                    help="with --all: post a Teams card after every account, not just "
                         "the end-of-session summary")
    ap.add_argument("--only", metavar="HANDLES",
                    help="restrict the run to these comma-separated handles, scraped in "
                         "the order given (cheapest first keeps the account pool's burst "
                         "capacity for the expensive targets). Unknown handles abort the "
                         "run rather than silently scraping nothing.")
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
