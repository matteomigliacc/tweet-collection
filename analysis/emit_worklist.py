#!/usr/bin/env python3
"""Build the by-id fetch worklist for src/fetch_missing.py --worklist.

The reference scrapes are 3 GB and live in ~/Raw Data on the Mac; the scraper
box has under 4 GB free and is the only machine with the account pool. So the
diff happens here and only the ids travel:

    python3 analysis/emit_worklist.py --max-missing 200 > /tmp/worklist.json
    scp /tmp/worklist.json root@192.168.1.106:/opt/populism-scraping/
    ssh ... '.venv/bin/python src/fetch_missing.py --worklist worklist.json'

Targets are the same unit run_all.py scrapes — one handle within one tenure or
seat spell — so each id is tagged with the window that owns it and nothing can
be written into a neighbouring spell's database. Deliberately stdlib-only: this
runs where twscrape and loguru are not installed.
"""
import argparse
import csv
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare_dataset import CEILING, FLOOR, OURS, load, merge, prof_files

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def targets() -> list[dict]:
    """One dict per scrape target, mirroring run_all.build_jobs()."""
    out = []
    for name, party_col, s, e in (("leaders.csv", "party", "leader_start", "leader_end"),
                                  ("parties.csv", "party", "seat_start", "seat_end")):
        month_align = name == "parties.csv"
        with open(os.path.join(ROOT, "frame", name), newline="") as fh:
            for row in csv.DictReader(fh):
                start = datetime.strptime(row[s], "%Y-%m-%d").date()
                # run_all.build_jobs() snaps party seat spells to the 1st of the
                # month; the worklist key must match it exactly or the target is
                # not found and the ids are silently skipped.
                if month_align:
                    start = start.replace(day=1)
                end = (date.today() if row[e] == "ongoing"
                       else datetime.strptime(row[e], "%Y-%m-%d").date())
                since, until = max(start, FLOOR), min(end, CEILING)
                if since > until:
                    continue
                out.append({"handle": row["handle"].lstrip("@").strip(),
                            "party": row[party_col], "since": since, "until": until})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-missing", type=int, default=None, metavar="N",
                    help="only include targets missing N or fewer tweets — the cheap "
                         "repairs, so a big re-scrape is not duplicated one id at a time")
    ap.add_argument("--handle", action="append", metavar="H", help="restrict to these handles")
    args = ap.parse_args()

    wanted = {h.lstrip("@").lower() for h in args.handle} if args.handle else None
    work, total = [], 0
    for job in targets():
        handle = job["handle"]
        if wanted and handle.lower() not in wanted:
            continue
        refs = prof_files(handle)
        mirror = os.path.join(OURS, job["party"], f"{handle}.ndjson")
        if not refs or not os.path.exists(mirror):
            continue
        ours = load(mirror)
        if not ours:
            continue
        # The replies tab embeds other authors; the handle's own id is the one
        # that dominates its own file.
        uid = max({v[1] for v in ours.values()},
                  key=lambda u: sum(1 for v in ours.values() if v[1] == u))
        have = {i for i, v in ours.items() if v[1] == uid}
        prof = merge(refs)
        want = {i for i, v in prof.items()
                if v[1] == uid and job["since"] <= v[0] <= job["until"]}
        todo = sorted(want - have)
        if not todo or (args.max_missing and len(todo) > args.max_missing):
            continue
        total += len(todo)
        work.append({"handle": handle, "party": job["party"],
                     "since": job["since"].isoformat(),
                     "until": job["until"].isoformat(), "ids": todo})
        print(f"{handle:17} {job['party']:16} {len(todo):>6} ids", file=sys.stderr)

    json.dump(work, sys.stdout)
    print(f"\n{len(work)} targets, {total:,} ids", file=sys.stderr)


if __name__ == "__main__":
    main()
