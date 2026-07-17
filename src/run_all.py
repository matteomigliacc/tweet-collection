"""Automatic batch scraper for the whole corpus.

Reads data/leaders.csv (party leaders + their tenure dates) and data/parties.csv
(party accounts), computes each target's date window, and scrapes each into its own
file under output/<party>/<handle>.{sqlite,csv}.

Rules:
  * Study floor: nothing before 2017-01-01.
  * Leaders: their tenure only, clipped to the 2017 floor. "ongoing" => today.
  * Party accounts: 2017-01-01 -> today.
  * A handle can appear twice with different parties (e.g. Klaver GroenLinks vs
    GroenLinks-PvdA) -> two separate files under the two party folders.

Resumable: each target has its own DB + checkpoint, so a target already covering
its full window is skipped, and an interrupted target resumes where it stopped.

Usage:
  python src/run_all.py --dry-run          # list every job and its window, scrape nothing
  python src/run_all.py                     # run the whole corpus (skips completed targets)
  python src/run_all.py --party CDA         # only targets in one party folder
  python src/run_all.py --handle Robjetten  # only one handle
"""
import argparse
import asyncio
import csv
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

from collect import run_collection, month_chunks
from flatten import export_ndjson

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "corpus"   # party-named subfolders live here: data/corpus/<party>/
LEADERS = ROOT / "data" / "leaders.csv"
PARTIES = ROOT / "data" / "parties.csv"
FLOOR = date(2017, 1, 1)


def parse_end(s: str) -> date:
    s = s.strip().lower()
    return date.today() if s == "ongoing" else datetime.strptime(s, "%Y-%m-%d").date()


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
        jobs.append({"handle": r["handle"].lstrip("@").strip(), "party": r["party"],
                     "since": FLOOR, "until": date.today(), "kind": "party"})
    return jobs


def job_paths(job: dict) -> tuple[Path, Path]:
    d = OUT / job["party"]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job['handle']}.sqlite", d / f"{job['handle']}.ndjson"


def expected_months(since: date, until: date) -> set[str]:
    return {s.strftime("%Y-%m") for s, _ in month_chunks(since, until)}


def is_complete(db: Path, handle: str, since: date, until: date) -> bool:
    if not db.exists():
        return False
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT months_done FROM checkpoint WHERE handle = ?",
                          (handle,)).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()
    if not row:
        return False
    return expected_months(since, until) <= set(json.loads(row[0] or "[]"))


async def run_one(job: dict) -> None:
    """Scrape a single target to its tenure window and export raw ndjson."""
    db, nd_path = job_paths(job)
    handle = job["handle"]
    tag = f"{job['party']}/{handle}"
    if is_complete(db, handle, job["since"], job["until"]):
        print(f"{tag} is already complete ({job['since']} -> {job['until']}). Re-exporting ndjson.")
        n = export_ndjson(db, nd_path)
        print(f"-> {nd_path.relative_to(ROOT)} ({n} tweets)")
        return
    print(f"\nScraping {tag}   window {job['since']} -> {job['until']} (tenure, floored at {FLOOR})\n")
    frame = [{"handle": handle, "name": "", "party": job["party"], "country": "NL"}]
    until_excl = job["until"] + timedelta(days=1)  # include the end date itself
    await run_collection(frame, job["since"], until_excl, db,
                         skip_recent=True, verbose=True, raw=True)
    n = export_ndjson(db, nd_path)
    print(f"\n-> {nd_path.relative_to(ROOT)} ({n} tweets)")


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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="just list the targets and exit")
    args = ap.parse_args()

    jobs = build_jobs()
    if not args.dry_run:
        setup_logging()
    print_menu(jobs)
    if args.dry_run:
        return

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
