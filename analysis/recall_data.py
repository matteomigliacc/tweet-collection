#!/usr/bin/env python3
"""Compare data/dataset_server against the professors' exports in ~/Raw Data."""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

from pathlib import Path

from read_tweets import RAW, MIRROR, ROOT, mirror_paths, objects, parse_created_at, raw_paths, screen_name

def tweet_date(obj):
    created_at = parse_created_at(obj)
    return created_at.date() if created_at else None


def windows(accounts=ROOT / "frame" / "accounts.csv"):
    """Return exact CSV windows plus the listed (party, handle) file keys."""
    spells, party = defaultdict(list), {}
    frame_keys = set()
    name = "accounts.csv"
    with open(accounts, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            start = date.fromisoformat(row["start"].strip())
            end = date.fromisoformat(row["end"].strip())
            if start > end:
                raise ValueError(f"{name}: {row['handle']} starts after it ends")
            key = row["handle"].strip().lstrip("@").strip().lower()
            spells[key].append((start, end))
            party.setdefault(key, row["party"])
            frame_keys.add((row["party"], key))
    return spells, party, frame_keys


def tweet_year(obj):
    created_at = parse_created_at(obj)
    return created_at.year if created_at else None


def kind(obj):
    """Describe the tweet type; this does not establish why it was included."""
    leg = obj.get("legacy") or {}
    if leg.get("retweeted_status_result"):
        return "retweet"
    if leg.get("in_reply_to_status_id_str"):
        return "reply"
    if leg.get("is_quote_status"):
        return "quote"
    return "standalone"


def author_uid(objs, handle):
    """The handle's own numeric user id, taken from the tweets that name it."""
    ids = Counter((o.get("legacy") or {}).get("user_id_str")
                  for o in objs if screen_name(o).lower() == handle)
    ids.pop(None, None)
    return ids.most_common(1)[0][0] if ids else None


def other_authors(reference, dataset_root):
    """Count unique IDs per reference handle, across all dates and dataset files."""
    ours = defaultdict(list)
    for path in mirror_paths(dataset_root):
        ours[path.stem.lower()].append(path)
    rows, authors_total, kinds = [], Counter(), Counter()
    for handle, paths in sorted(reference.items()):
        # Deduplicate before classifying so one ID cannot count in both groups.
        tweets = {o["rest_id"]: o for path in paths for o in objects(path)}
        uid = author_uid(tweets.values(), handle)
        others = {tid: o for tid, o in tweets.items()
                  if not (screen_name(o).lower() == handle or
                          (uid and (o.get("legacy") or {}).get("user_id_str") == uid))}
        held = {o["rest_id"] for path in ours.get(handle, []) for o in objects(path)}
        authors = Counter(screen_name(o) or "?" for o in others.values())
        authors_total.update(authors)
        kinds.update(kind(o) for o in others.values())
        rows.append({"handle": handle, "total": len(tweets),
                     "own": len(tweets) - len(others), "other": len(others),
                     "pct": round(100 * len(others) / len(tweets), 1) if tweets else 0,
                     "distinct_authors": len(authors),
                     "other_in_dataset": len(set(others) & held),
                     "top": authors.most_common(3),
                     "years": dict(sorted(Counter(str(y) for o in others.values()
                                                  if (y := tweet_year(o))).items()))})
    rows.sort(key=lambda row: -row["other"])
    return {"date_scope": "All reference dates; CSV windows are not applied.",
            "counting": "Unique tweet IDs per handle; shared IDs across handles count again.",
            "rows": rows, "top_authors": authors_total.most_common(200),
            "kinds": dict(kinds), "total_other": sum(r["other"] for r in rows),
            "total": sum(r["total"] for r in rows),
            "other_in_dataset": sum(r["other_in_dataset"] for r in rows)}


def main():
    """For each handle with a reference file: recall = shared ids / reference ids."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=RAW, help="folder containing reference NDJSON files")
    parser.add_argument("--dataset", type=Path, default=MIRROR, help="folder containing party/handle.ndjson files")
    parser.add_argument("--accounts", type=Path, default=ROOT / "frame" / "accounts.csv")
    parser.add_argument("--output", type=Path, help="write JSON here instead of standard output")
    parser.add_argument("--other-authors", action="store_true", help="also count other authors across all reference dates")
    args = parser.parse_args()
    spells, party, frame_keys = windows(args.accounts)
    notes = []

    reference = defaultdict(list)
    for path in raw_paths(args.reference):
        match = re.match(r"@?([A-Za-z0-9_]+)", path.name)
        if match:
            reference[match.group(1).lower()].append(path)
        else:
            notes.append(f"{path.name}: no handle in filename, skipped")

    ours = defaultdict(list)
    for path in mirror_paths(args.dataset):
        key = (path.parent.name, path.stem.lower())
        if key not in frame_keys:
            notes.append(f"{path.parent.name}/{path.name}: no exact frame row, skipped")
            continue
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

    report = {"generated": datetime.now().isoformat(timespec="seconds"),
              "rows": rows, "notes": notes}
    if args.other_authors:
        report["other_authors"] = other_authors(reference, args.dataset)
    result = json.dumps(report, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
