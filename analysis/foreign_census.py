#!/usr/bin/env python3
"""Census of tweets in ~/Raw Data written by someone other than the file's handle.

    python3 analysis/foreign_census.py [--json out.json]

The professors' exports come from Zeeschuimer, which captures whatever the
timeline rendered — so a reply carries its parent, a quote carries what it
quotes, and a retweet carries the original. Those authors are not in the frame.
This counts them per file and names who they are, so the decision to keep or
drop them is made on numbers rather than assumption.

No date-window filter: the question is what the files contain, not what falls
in scope. Stdlib only.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = Path.home() / "Raw Data"
MIRROR = ROOT / "data" / "dataset_server"


def objects(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if "rest_id" not in obj:
                for key in ("tweet", "data", "result"):
                    if isinstance(obj.get(key), dict) and "rest_id" in obj[key]:
                        obj = obj[key]
                        break
            if "rest_id" in obj and obj.get("legacy"):
                yield obj


def screen_name(obj):
    user = ((obj.get("core") or {}).get("user_results") or {}).get("result") or {}
    for src in (user.get("core") or {}, user.get("legacy") or {}):
        if src.get("screen_name"):
            return src["screen_name"]
    return ""


def tweet_year(obj):
    try:
        return datetime.strptime(obj["legacy"]["created_at"],
                                 "%a %b %d %H:%M:%S %z %Y").year
    except Exception:
        return None


def kind(obj):
    """Why this tweet is probably in the file at all."""
    leg = obj.get("legacy") or {}
    if leg.get("retweeted_status_result"):
        return "retweet"
    if leg.get("in_reply_to_status_id_str"):
        return "reply"
    if leg.get("is_quote_status"):
        return "quote"
    return "standalone"


def author_uid(objs, handle):
    """The handle's own numeric user id, taken from the tweets that name it.

    Some exports carry a tweet with no user object at all; those still have
    legacy.user_id_str, so matching on the id rather than the screen name keeps
    them with their real author instead of counting them as somebody else's.
    """
    ids = Counter((o.get("legacy") or {}).get("user_id_str")
                  for o in objs if screen_name(o).lower() == handle)
    ids.pop(None, None)
    return ids.most_common(1)[0][0] if ids else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="PATH", help="also write the full census as JSON")
    args = ap.parse_args()

    ours = defaultdict(list)
    for path in MIRROR.glob("*/*.ndjson"):
        ours[path.stem.lower()].append(path)

    files = defaultdict(list)
    for path in sorted(RAW.glob("*.ndjson")) + sorted(RAW.glob("*/*.ndjson")):
        match = re.match(r"@?([A-Za-z0-9_]+)", path.name)
        if match:
            files[match.group(1).lower()].append(path)

    rows, all_foreign = [], Counter()
    foreign_kinds, held_foreign, total_foreign = Counter(), 0, 0

    for handle in sorted(files):
        loaded = [o for path in files[handle] for o in objects(path)]
        uid = author_uid(loaded, handle)
        own, foreign = {}, {}
        for obj in loaded:
            mine = (screen_name(obj).lower() == handle
                    or (uid and (obj.get("legacy") or {}).get("user_id_str") == uid))
            (own if mine else foreign)[obj["rest_id"]] = obj

        held = set()
        for path in ours.get(handle, []):
            held |= {o["rest_id"] for o in objects(path)}

        authors = Counter(screen_name(o) or "?" for o in foreign.values())
        all_foreign.update(authors)
        foreign_kinds.update(kind(o) for o in foreign.values())
        held_here = sum(1 for t in foreign if t in held)
        held_foreign += held_here
        total_foreign += len(foreign)

        total = len(own) + len(foreign)
        rows.append({
            "handle": handle,
            "total": total,
            "own": len(own),
            "foreign": len(foreign),
            "pct": round(100.0 * len(foreign) / total, 1) if total else 0.0,
            "distinct_authors": len(authors),
            "foreign_held_by_us": held_here,
            "top": authors.most_common(3),
            "years": dict(sorted(Counter(
                y for o in foreign.values() if (y := tweet_year(o))).items())),
        })

    rows.sort(key=lambda r: -r["foreign"])
    grand = sum(r["total"] for r in rows)

    w = f"{'handle':<20}{'total':>9}{'own':>9}{'foreign':>9}{'%':>7}{'authors':>9}{'we hold':>9}"
    print(w)
    print("-" * len(w))
    for r in rows:
        print(f"{r['handle']:<20}{r['total']:>9,}{r['own']:>9,}{r['foreign']:>9,}"
              f"{r['pct']:>6.1f}%{r['distinct_authors']:>9,}{r['foreign_held_by_us']:>9,}")
    print("-" * len(w))
    print(f"{'TOTAL':<20}{grand:>9,}{grand - total_foreign:>9,}{total_foreign:>9,}"
          f"{100.0 * total_foreign / grand:>6.1f}%{len(all_foreign):>9,}{held_foreign:>9,}")

    print(f"\nWhy the foreign tweets are there: "
          + ", ".join(f"{k} {n:,}" for k, n in foreign_kinds.most_common()))
    print(f"\nMost frequent outside authors across all {len(rows)} files:")
    for name, n in all_foreign.most_common(15):
        in_frame = " (in frame)" if name.lower() in files else ""
        print(f"  {n:>6,}  @{name}{in_frame}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"rows": rows, "top_authors": all_foreign.most_common(200),
             "kinds": dict(foreign_kinds), "total_foreign": total_foreign,
             "grand_total": grand, "foreign_held_by_us": held_foreign},
            ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
