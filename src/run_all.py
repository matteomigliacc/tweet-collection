"""Automatic batch scraper for the whole corpus.

Reads frame/leaders.csv (party leaders + their tenure dates) and frame/parties.csv
(party accounts), computes each target's date window, and scrapes each into its own
file under output/<party>/<handle>.{sqlite,csv}.

Rules:
  * Study floor: nothing before 2017-01-01.
  * Leaders: their tenure only, clipped to the 2017 floor. "ongoing" => today.
  * Party accounts: only while the party held Tweede Kamer seats -- one
    seat_start/seat_end spell per CSV row, clipped to the 2017 floor, month-aligned,
    "ongoing" => today. A party that left and later returned (e.g. 50PLUS) has two
    rows, both scraped into the one account file (the out-of-parliament gap is skipped).
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
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

from collect import run_collection, month_chunks
from flatten import export_ndjson
import notify

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "corpus"     # scraped output: party-named subfolders under data/corpus/<party>/
LEADERS = ROOT / "frame" / "leaders.csv"   # committed sampling-frame inputs
PARTIES = ROOT / "frame" / "parties.csv"
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
    print(f"\nScraping {tag}   window {job['since']} -> {job['until']} (tenure, floored at {FLOOR})\n")
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


def _fmt_dur(seconds: float) -> str:
    s = int(round(seconds))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def build_session_email(records: list[dict], done: int, partial: int, failed: int,
                        skipped: int, total: int, done_today: int,
                        session_secs: float) -> tuple[str, str, str]:
    """Compose (subject, plaintext, html) for one batch-run summary email.

    `records`: one dict per account processed this run, with keys party, handle,
    since, until, tweets (int|None), seconds, status ('complete'|'partial'|'failed')."""
    total_tweets = sum(r["tweets"] for r in records if r.get("tweets"))
    complete_overall = skipped + done
    dur = _fmt_dur(session_secs)
    subject = (f"[scraper] {done} new · {partial} partial · {failed} failed — "
               f"{total_tweets:,} tweets in {dur}")

    # ---- plaintext ----
    lines = ["Populism scraper — session summary",
             "=" * 40,
             f"Duration:  {dur}",
             f"Processed: {len(records)} account(s) this run",
             f"Result:    {done} newly complete, {partial} partial, {failed} failed",
             f"Tweets:    {total_tweets:,} collected this session",
             f"Overall:   {complete_overall}/{total} targets complete ({done_today} done today)",
             "",
             f"{'Account':22}{'Party':16}{'Tweets':>8}{'Time':>9}  Status"]
    for r in records:
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "-"
        lines.append(f"@{r['handle']:21}{r['party']:16}{tw:>8}{_fmt_dur(r['seconds']):>9}  {r['status']}")
    text = "\n".join(lines)

    # ---- html ----
    badge = {"complete": ("#137333", "#e6f4ea"), "partial": ("#a56300", "#fef7e0"),
             "failed": ("#c5221f", "#fce8e6")}
    rows = ""
    for r in records:
        col, bg = badge.get(r["status"], ("#444", "#eee"))
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "—"
        td = "padding:9px 12px;border-bottom:1px solid #eee;"
        rows += (
            f"<tr>"
            f"<td style='{td}font-family:ui-monospace,Menlo,monospace'>@{r['handle']}</td>"
            f"<td style='{td}'>{r['party']}</td>"
            f"<td style='{td}color:#888;font-size:12px'>{r['since']} → {r['until']}</td>"
            f"<td style='{td}text-align:right;font-variant-numeric:tabular-nums'>{tw}</td>"
            f"<td style='{td}text-align:right;color:#888'>{_fmt_dur(r['seconds'])}</td>"
            f"<td style='{td}'><span style='background:{bg};color:{col};padding:2px 9px;"
            f"border-radius:11px;font-size:12px;font-weight:600'>{r['status']}</span></td>"
            f"</tr>")

    def stat(label, value):
        return (f"<td style='padding:14px 16px;text-align:center'>"
                f"<div style='font-size:22px;font-weight:700;color:#111'>{value}</div>"
                f"<div style='font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.04em'>{label}</div></td>")

    html = f"""\
<div style="background:#f4f5f7;padding:24px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)">
    <div style="background:#1f2933;color:#fff;padding:18px 22px;font-size:16px;font-weight:600">
      🐦 Populism scraper &middot; session summary
    </div>
    <table style="width:100%;border-collapse:collapse;border-bottom:1px solid #eee">
      <tr>{stat('Tweets', f'{total_tweets:,}')}{stat('New datasets', done)}{stat('Duration', dur)}{stat('Complete', f'{complete_overall}/{total}')}</tr>
    </table>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead><tr style="text-align:left;color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.04em">
        <th style="padding:10px 12px">Account</th><th style="padding:10px 12px">Party</th>
        <th style="padding:10px 12px">Window</th><th style="padding:10px 12px;text-align:right">Tweets</th>
        <th style="padding:10px 12px;text-align:right">Time</th><th style="padding:10px 12px">Status</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="padding:14px 22px;color:#999;font-size:12px;background:#fafafa">
      {partial} partial · {failed} failed · {skipped} already complete · {done_today} done today ·
      finished {datetime.now():%Y-%m-%d %H:%M}
    </div>
  </div>
</div>"""
    return subject, text, html


def build_session_card(records: list[dict], done: int, partial: int, failed: int,
                       skipped: int, total: int, done_today: int,
                       session_secs: float) -> dict:
    """Compose an Adaptive Card (Teams) mirroring the session-summary email."""
    total_tweets = sum(r["tweets"] for r in records if r.get("tweets"))
    complete_overall = skipped + done
    dur = _fmt_dur(session_secs)

    def stat(value, label):
        return {"type": "Column", "width": "stretch", "items": [
            {"type": "TextBlock", "text": str(value), "size": "ExtraLarge",
             "weight": "Bolder", "horizontalAlignment": "Center", "spacing": "None"},
            {"type": "TextBlock", "text": label, "size": "Small", "isSubtle": True,
             "horizontalAlignment": "Center", "spacing": "None"}]}

    colour = {"complete": "Good", "partial": "Warning", "failed": "Attention"}
    rows = []
    for r in records:
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "—"
        rows.append({"type": "ColumnSet", "spacing": "Small", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "spacing": "None", "wrap": True,
                 "text": f"**@{r['handle']}** · {r['party']}"},
                {"type": "TextBlock", "spacing": "None", "size": "Small", "isSubtle": True,
                 "text": f"{r['since']} → {r['until']}"}]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "spacing": "None", "horizontalAlignment": "Right",
                 "text": f"{tw} tweets · {_fmt_dur(r['seconds'])}"},
                {"type": "TextBlock", "spacing": "None", "size": "Small", "weight": "Bolder",
                 "horizontalAlignment": "Right",
                 "color": colour.get(r["status"], "Default"), "text": r["status"]}]}]})

    return {"type": "AdaptiveCard", "version": "1.4",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "msteams": {"width": "Full"},
            "body": [
                {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                 "text": "🐦 Populism scraper · session summary"},
                {"type": "ColumnSet", "columns": [
                    stat(f"{total_tweets:,}", "Tweets"), stat(done, "New datasets"),
                    stat(dur, "Duration"), stat(f"{complete_overall}/{total}", "Complete")]},
                {"type": "Container", "separator": True, "items": rows},
                {"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True,
                 "separator": True,
                 "text": (f"{partial} partial · {failed} failed · {skipped} already "
                          f"complete · {done_today} done today · "
                          f"finished {datetime.now():%Y-%m-%d %H:%M}")}]}


async def run_batch(jobs: list[dict], limit: int | None = None,
                    daily_limit: int | None = None) -> int:
    """Non-interactive: scrape incomplete targets once, isolating failures.

    Built for cron/systemd. `limit` caps targets processed this run; `daily_limit`
    caps targets processed per calendar day across all runs (persisted in
    data/corpus/.quota.json) so a timer firing N times still won't exceed it.
    Sends ONE summary email per run (if any account was processed).
    Returns exit code 0 if nothing failed, 1 if any target errored.
    """
    done = skipped = partial = failed = 0
    records: list[dict] = []
    total = len(jobs)
    session_start = time.monotonic()
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
        _bump_daily_count()   # count the attempt up-front (a crash still consumes a slot)
        rec = {"party": job["party"], "handle": job["handle"],
               "since": job["since"], "until": job["until"], "tweets": None}
        t0 = time.monotonic()
        try:
            rec["tweets"] = await run_one(job)
        except KeyboardInterrupt:
            print("\n  interrupted — progress checkpointed; re-run to resume")
            raise
        except Exception as e:  # one bad handle must not abort the whole batch
            failed += 1
            logger.error(f"{tag} failed: {e!r}")
            print(f"  !! {tag} failed: {e!r} — continuing")
            rec["seconds"] = time.monotonic() - t0
            rec["status"] = "failed"
            records.append(rec)
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

    session_secs = time.monotonic() - session_start
    print(f"\nBatch summary: {len(records)} processed this run — {done} newly complete, "
          f"{partial} partial, {failed} failed; {skipped} already complete "
          f"(of {total} targets, {_daily_count()} done today).")
    if records:  # one summary notification per run, only when something was processed
        card = build_session_card(records, done, partial, failed,
                                  skipped, total, _daily_count(), session_secs)
        if not notify.send_teams(card):  # Teams first; email only as fallback
            subject, text, html = build_session_email(records, done, partial, failed,
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
    args = ap.parse_args()

    jobs = build_jobs()
    if args.dry_run:
        print_menu(jobs)
        return

    setup_logging()
    if args.all:
        code = await run_batch(jobs, limit=args.limit, daily_limit=args.daily_limit)
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
