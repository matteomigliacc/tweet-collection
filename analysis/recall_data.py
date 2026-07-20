#!/usr/bin/env python3
"""Compare data/dataset_server against the professors' scrapes in ~/Raw Data.

    python3 analysis/recall_data.py > analysis/dataset_recall.json

A professor tweet counts as held when our dataset has the same rest_id. Ids that
are absent are then attributed to their real author: the professors' exports
embed parent and quoted tweets by other accounts, which are not ours to hold.
So each handle gets two numbers — raw recall, and recall over the tweets the
handle actually wrote.

Windows are each target's tenure or seat spell clipped to the study window,
unioned when a handle has several. A handle with several spells also has
several dataset files (one per party folder); they are unioned too, or the
extra spells read as missing.

Stdlib only, and no import from the scraper package: this is the independent
check on it.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = Path.home() / "Raw Data"
MIRROR = ROOT / "data" / "dataset_server"

FLOOR = date(2017, 3, 23)     # TK installation
CEILING = date(2025, 11, 12)  # TK election


def objects(path):
    """Yield each line's raw GraphQL tweet object."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            # The professors' files sometimes nest the tweet one level down
            # (Zeeschuimer's "data" envelope, or a "tweet"/"result" wrapper);
            # unwrap until we're holding the object with rest_id itself.
            if "rest_id" not in obj:
                for key in ("tweet", "data", "result"):
                    if isinstance(obj.get(key), dict) and "rest_id" in obj[key]:
                        obj = obj[key]
                        break
            if "rest_id" in obj and obj.get("legacy"):
                yield obj


def tweet_date(obj):
    try:
        return datetime.strptime(obj["legacy"]["created_at"],
                                 "%a %b %d %H:%M:%S %z %Y").date()
    except Exception:
        return None


def screen_name(obj):
    """The author's handle, from whichever of the two shapes X used."""
    user = ((obj.get("core") or {}).get("user_results") or {}).get("result") or {}
    for src in (user.get("core") or {}, user.get("legacy") or {}):
        if src.get("screen_name"):
            return src["screen_name"]
    return ""


def windows():
    """handle(lower) -> ([(start, end)], party) clipped to the study window."""
    spells, party = defaultdict(list), {}
    for name, start_col, end_col in (("leaders.csv", "leader_start", "leader_end"),
                                     ("parties.csv", "seat_start", "seat_end")):
        with open(ROOT / "frame" / name, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                start = date.fromisoformat(row[start_col])
                end = (CEILING if row[end_col].strip() in ("ongoing", "")
                       else date.fromisoformat(row[end_col]))
                start, end = max(start, FLOOR), min(end, CEILING)
                if start > end:
                    continue  # spell falls entirely outside the study window
                key = row["handle"].lower()
                spells[key].append((start, end))
                party.setdefault(key, row["party"])
    return spells, party


def main():
    """For each handle with a reference file: recall = shared ids / reference ids.

    Steps: (1) index the professors' files and our mirror files by handle;
    (2) per handle, collect our ids and the reference ids inside the tenure
    window; (3) whatever the reference has that we lack is 'missing' — split
    into tweets the handle actually wrote ('own', the real gap) vs other
    authors' tweets riding along in their exports ('foreign', never ours to
    hold). Emits one JSON blob on stdout for render_report.py.
    """
    spells, party = windows()
    notes = []

    reference = defaultdict(list)
    for path in sorted(RAW.glob("*.ndjson")) + sorted(RAW.glob("*/*.ndjson")):
        match = re.match(r"@?([A-Za-z0-9_]+)", path.name)
        if match:
            reference[match.group(1).lower()].append(path)
        else:
            notes.append(f"{path.name}: no handle in filename, skipped")

    ours = defaultdict(list)
    for path in MIRROR.glob("*/*.ndjson"):
        ours[path.stem.lower()].append(path)

    rows = []
    for handle in sorted(reference):
        if handle not in spells:
            notes.append(f"{handle}: reference file but no frame row, skipped")
            continue
        spell = spells[handle]
        in_window = lambda d: d is not None and any(s <= d <= e for s, e in spell)

        held_ids, dataset_ids = set(), set()
        for path in ours.get(handle, []):
            for obj in objects(path):
                held_ids.add(obj["rest_id"])
                if in_window(tweet_date(obj)):
                    dataset_ids.add(obj["rest_id"])
        dataset = len(dataset_ids)

        ref, missing = {}, {}
        for path in reference[handle]:
            for obj in objects(path):
                if in_window(tweet_date(obj)):
                    ref[obj["rest_id"]] = obj
        for tid, obj in ref.items():
            if tid not in held_ids:
                missing[tid] = obj
        if not ref:
            notes.append(f"{handle}: no reference tweets in window")
            continue

        # Split the gap: tweets the handle wrote vs other people's tweets that
        # ride along in the professors' exports as thread parents and quotes.
        own = {t: o for t, o in missing.items()
               if screen_name(o).lower() == handle}
        foreign = len(missing) - len(own)

        shared = len(ref) - len(missing)
        extra = len(dataset_ids - set(ref))  # in window, ours, not in their files
        rows.append({
            "extra": extra,
            "handle": handle,
            "party": party.get(handle, "?"),
            "dataset": dataset,
            "ref": len(ref),
            "shared": shared,
            "missing": len(missing),
            "own_missing": len(own),
            "foreign_missing": foreign,
            "recall": round(100.0 * shared / len(ref), 1),
            "own_recall": round(100.0 * shared / (shared + len(own)), 1)
            if shared + len(own) else 100.0,
            "miss_years": dict(sorted(Counter(
                str(tweet_date(o).year) for o in own.values()).items())),
            "miss_kind": dict(Counter(
                "reply" if (o.get("legacy") or {}).get("in_reply_to_status_id_str")
                else "standalone" for o in own.values())),
            "window": [[s.isoformat(), e.isoformat()] for s, e in sorted(spell)],
        })

    for row in rows:
        row["cat"] = ("ok" if row["own_missing"] == 0
                      else "minor" if row["own_missing"] <= 25 else "gap")
    rows.sort(key=lambda r: (r["own_recall"], r["recall"], -r["ref"]))

    json.dump({"generated": datetime.now().isoformat(timespec="seconds"),
               "rows": rows, "notes": notes}, sys.stdout, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
