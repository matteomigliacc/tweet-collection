"""Sampling-frame jobs with inclusive CSV date windows."""
import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "dataset"
FRAME = ROOT / "frame" / "accounts.csv"


def build_jobs(frame: Path = FRAME) -> list[dict]:
    """One job per account and inclusive date window."""
    jobs = []
    with frame.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"handle", "party", "start", "end"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f"{frame}: required columns are {', '.join(sorted(required))}")
        for number, row in enumerate(reader, 2):
            handle = (row.get("handle") or "").strip().lstrip("@").strip()
            party = (row.get("party") or "").strip()
            if not handle or not party:
                raise ValueError(f"{frame}:{number}: handle and party must not be empty")
            since = date.fromisoformat(row["start"].strip())
            until = date.fromisoformat(row["end"].strip())
            if since > until:
                raise ValueError(f"invalid window for {handle}: {since} > {until}")
            jobs.append({"handle": handle, "party": party, "since": since,
                         "until": until, "kind": (row.get("kind") or "").strip()})
    return jobs


def job_paths(job: dict, output: Path = OUT) -> tuple[Path, Path]:
    d = output / job["party"]
    return d / f"{job['handle']}.sqlite", d / f"{job['handle']}.ndjson"



def month_chunks(since: date, until: date):
    """Yield full calendar months overlapping the half-open interval."""
    cur = date(since.year, since.month, 1)
    while cur < until:
        # first day of the next month (December rolls over to January 1st)
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        yield cur, nxt
        cur = nxt
