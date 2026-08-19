"""Interactive tweet-scraper front-end.

Run it and answer the prompts — no flags to remember:

    python src/scrape_account.py

It asks for a handle, a date window, and a couple of options, shows a summary
to confirm, then runs the same engine as collector.py and optionally flattens
the result to a tidy CSV.
"""
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

from collector import run_collection
from cli_prompts import ask, ask_yes_no
from flatten import flatten_db

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def ask_date(prompt: str, default: str | None = None, allow_today=False) -> date:
    while True:
        raw = ask(prompt, default)
        if allow_today and raw.lower() in {"today", "now"}:
            return date.today()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            print("  format must be YYYY-MM-DD (e.g. 2020-03-15)"
                  + (" or 'today'" if allow_today else ""))


async def main() -> None:
    print("=" * 60)
    print("  Tweet scraper — answer the prompts (Ctrl-C to quit)")
    print("=" * 60)

    handle = ask("\nHandle to scrape (with or without @)").lstrip("@").strip()

    since = ask_date("Start date (YYYY-MM-DD)")
    while True:
        until = ask_date("End date (YYYY-MM-DD, or 'today')",
                         default="today", allow_today=True)
        if until > since:
            break
        print("  end date must be after the start date")
    # search window is [since, until); +1 day so an inclusive 'today' captures today
    until_exclusive = until + timedelta(days=1)

    include_recent = ask_yes_no(
        "Also check the latest timeline (up to ~3200 items, date-filtered)?",
        default=False,
    )
    verbose = ask_yes_no("Verbose progress output?", default=True)

    DATA.mkdir(exist_ok=True)
    default_db = DATA / f"{handle}.sqlite"
    db_path = Path(ask("Output database file", str(default_db)))

    frame = [{"handle": handle, "name": "", "party": "", "country": ""}]

    print("\n" + "-" * 60)
    print(f"  Handle:   @{handle}")
    print(f"  Window:   {since}  ->  {until}  (inclusive)")
    print(f"  Mode:     {'timeline + search' if include_recent else 'search only'}")
    print(f"  Database: {db_path}")
    print("-" * 60)
    if not ask_yes_no("Start scraping with these settings?", default=True):
        print("Aborted.")
        return

    total = await run_collection(
        frame, since, until_exclusive, db_path,
        skip_recent=not include_recent, verbose=verbose,
    )

    if total and ask_yes_no("\nFlatten to a tidy CSV now?", default=True):
        out = db_path.with_suffix(".csv")
        n = flatten_db(db_path, out)
        print(f"Wrote {n} rows -> {out}")

    print("\nAll done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted — progress is checkpointed, just re-run to resume.")
