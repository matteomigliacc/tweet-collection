#!/usr/bin/env python3
"""Recall of the server corpus against the professors' reference scrapes.

Emits one JSON row per handle — the exact shape the missing-tweet artifact's
DATA array expects — so the report can be regenerated from fresh server data:

    rsync the corpus into data/corpus_server, then
    python3 analysis/compare_corpus.py > analysis/corpus_recall.json

Metric (per CLAUDE.md): clip both sides to the target's tenure/seat window,
author-filter to the handle's own user id, then
recall = |shared ids| / |professor ids in window|.

A tweet id is not stable. Editing a tweet mints a new id for the same post, and
a few reference files predate edits our scrape captured afterwards, so a pure id
join reports edited tweets as missing on one side and extra on the other. Every
id-unmatched tweet therefore gets a second pass keyed on (date, normalised text)
against the other side's leftovers — same words on the same day by the same
author is the same tweet. Those land in `text_matched` and count as held, not
missing; `missing` is what survives both passes.

The professors' folder holds first *and* second runs for some handles; every
file matching the handle is merged by tweet id, which is their most complete
picture. `cat` is a standing editorial judgement about *why* a handle is short
(see CATEGORIES) and is not derived from the numbers.
"""
import csv
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.expanduser("~/Raw Data")
OURS = os.path.join(ROOT, "data", "corpus_server")
FMT = "%a %b %d %H:%M:%S %z %Y"

FLOOR = date(2017, 3, 23)     # TK installation — the study floor run_all.py scrapes from
CEILING = date(2025, 11, 12)  # TK election — the study ceiling
TODAY = date.today()

# Why each short handle is short. Anything absent defaults to "ok".
CATEGORIES = {
    "PartijvdDieren": "rescrape", "cdavandaag": "rescrape",
    # fvdemocratie was "rescrape" until 2026-07-20: the sweep took it 21.3% -> 99.8%,
    # recovering 6,634 of its 6,651 missing tweets.
    # BoerBurgerB was "rescrape" until 2026-07-20: the sweep took it 7.4% -> 100%,
    # recovering all 6,657 missing tweets. The single biggest error-spike casualty.
    # 50pluspartij was "rescrape" until 2026-07-20: the sweep took it 9.2% -> 100%
    # inside its seat spells. Most of its apparent gap was never a gap — the old
    # comparison spanned 2021-2025, when 50PLUS held no seats at all.
    # denknl was "rescrape" until 2026-07-20: the ascending sweep took it
    # 68.3% -> 97.8%, recovering 1,007 of its 1,081 missing tweets.
    # SGPnieuws was "rescrape" until 2026-07-20: the ascending sweep re-ran it and
    # took 17.4% -> 99.3%, recovering 1,978 of its 1,994 missing tweets. Proof the
    # error-spike diagnosis was right — the tweets were on X the whole time.
    # hugodejonge was in "inject" until 2026-07-20: a clean re-scrape recovered 196
    # of his 197 missing tweets (190 of them one silently-empty month, 2020-06), so
    # his gap was a checkpointed error, not a search-index gap.
    # Gertjansegers left "inject" on 2026-07-20, and the category's premise went
    # with him. The claim was that X's search index permanently hides these
    # tweets, so they could only be injected from the professors' JSON. Half
    # right: search cannot reach them, but tweet_details can. fetch_missing.py
    # asked X for all 1,834 ids directly and got 1,687 back plus 157
    # thread-mates, with *zero* deleted — 75.6% -> 100%. Nothing had to be
    # injected; the tweets were live on X the whole time.
    # lientje1967 left "inject" on 2026-07-20. Her 286-tweet hole sat in Jan-Mar
    # 2021, months the search cap truncated at exactly 1,000 — but the Onboarding
    # doc starts BBB at 31 Mar 2021, so none of it was ever in scope. Correcting
    # the frame took her to 100% without fetching anything.
    "ChristenUnie": "minor", "NwSocContract": "minor", "VoltNederland": "minor",
    "thierrybaudet": "minor",
    # geertwilderspvv left "minor" on 2026-07-20: fetch_missing.py pulled all 53
    # of his missing quote tweets by id -> 100%. Search under-returns quotes; a
    # direct tweet_details call does not care how a tweet is classified.
}

# Handles with no usable comparison — reported separately in the artifact.
SKIP = {"Estherouwehand", "markrutte", "JuisteAntwoord", "LianedenHaan", "progressiefned"}


def windows() -> dict[str, list[tuple[date, date]]]:
    """handle -> list of (start, end) spells from the frame CSVs, clipped.

    A handle can appear on several rows, and the rows are not always adjacent:
    50PLUS held seats 2017-2021, lost them, and returned in 2025. Spanning the
    outer edges would count the four years they were out of parliament, so each
    spell is kept separate and a tweet counts if it falls in *any* of them.
    """
    out: dict[str, list[tuple[date, date]]] = {}
    for name, s, e in (("leaders.csv", "leader_start", "leader_end"),
                       ("parties.csv", "seat_start", "seat_end")):
        path = os.path.join(ROOT, "frame", name)
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                start = datetime.strptime(row[s], "%Y-%m-%d").date()
                end = TODAY if row[e] == "ongoing" else datetime.strptime(row[e], "%Y-%m-%d").date()
                start, end = max(start, FLOOR), min(end, CEILING, TODAY)
                if start <= end:
                    out.setdefault(row["handle"], []).append((start, end))
    return {h: sorted(v) for h, v in out.items()}


def in_window(d: date, spells: list[tuple[date, date]]) -> bool:
    return any(lo <= d <= hi for lo, hi in spells)


def prof_files(handle: str) -> list[str]:
    """Every reference file for this handle, top level or party subfolder."""
    pat = re.compile(r"^@?" + re.escape(handle) + r"[ _.]", re.I)
    hits = []
    for path in glob.glob(os.path.join(RAW, "*.ndjson")) + glob.glob(os.path.join(RAW, "*", "*.ndjson")):
        if pat.match(os.path.basename(path)):
            hits.append(path)
    return hits


def norm(text: str) -> str:
    """Text reduced to what survives an edit round-trip: no t.co links, no case,
    no runs of whitespace, no entity escaping."""
    text = re.sub(r"https?://t\.co/\w+", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(text.lower().split())


def load(path: str) -> dict[str, tuple[date, str, str, str]]:
    """-> {tweet_id: (date, author_user_id, kind, normalised_text)}.

    Tolerates Zeeschuimer envelopes."""
    out = {}
    with open(path, errors="replace") as fh:
        # split on \n only: splitlines() shears JSON at U+2028 inside tweet text
        for line in fh.read().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o.get("data"), dict) and "rest_id" not in o:
                o = o["data"]
            leg = o.get("legacy") or {}
            tid = o.get("rest_id") or leg.get("id_str")
            created = leg.get("created_at")
            if not tid or not created:
                continue
            try:
                d = datetime.strptime(created, FMT).date()
            except Exception:
                continue
            kind = ("reply" if leg.get("in_reply_to_status_id_str")
                    else "quote" if leg.get("is_quote_status") else "standalone")
            out[str(tid)] = (d, leg.get("user_id_str") or "?", kind,
                             norm(leg.get("full_text") or ""))
    return out


def merge(paths: list[str]) -> dict:
    out = {}
    for p in paths:
        out.update(load(p))
    return out


def pair_by_text(left: dict, right: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """Match id-unmatched tweets on (date, text). -> ([(left_id, right_id)], unmatched_left).

    Empty text can't identify anything (image-only posts, and the odd record that
    arrives without full_text), so those are never paired. Matching is one-to-one:
    a handle that tweets "Dankjewel!" four times in a day pairs four, not sixteen.
    """
    pool: dict[tuple[date, str], list[str]] = {}
    for rid, v in right.items():
        if v[3]:
            pool.setdefault((v[0], v[3]), []).append(rid)
    pairs, unmatched = [], []
    for lid, v in left.items():
        candidates = pool.get((v[0], v[3])) if v[3] else None
        if candidates:
            pairs.append((lid, candidates.pop()))
        else:
            unmatched.append(lid)
    return pairs, unmatched


def main() -> None:
    win = windows()
    rows = []
    notes = []
    # A handle can hold seats under several party labels (JesseKlaver has a file
    # under GroenLinks, PRO and GroenLinks-PvdA), which would otherwise produce
    # one row per party and count the same tweets several times in the totals.
    # Merge every file for a handle into one comparison, keyed by tweet id.
    by_handle: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(OURS, "*", "*.ndjson"))):
        by_handle.setdefault(os.path.basename(path)[:-len(".ndjson")], []).append(path)

    for handle, paths in sorted(by_handle.items()):
        path = paths[0]
        party = "/".join(sorted(os.path.basename(os.path.dirname(p)) for p in paths))
        if handle in SKIP:
            continue
        refs = prof_files(handle)
        if not refs:
            notes.append(f"{handle}: no reference file")
            continue
        if handle not in win:
            notes.append(f"{handle}: no window in frame/")
            continue
        spells = win[handle]
        ours, prof = merge(paths), merge(refs)
        if not ours:
            notes.append(f"{handle}: corpus file is empty")
            continue

        # The replies tab embeds other authors; keep only the handle's own tweets.
        uid = Counter(v[1] for v in ours.values()).most_common(1)[0][0]
        o = {i: v for i, v in ours.items() if v[1] == uid and in_window(v[0], spells)}
        p = {i: v for i, v in prof.items() if v[1] == uid and in_window(v[0], spells)}
        if not p:
            notes.append(f"{handle}: reference holds nothing in {spells}")
            continue

        by_id = set(p) & set(o)
        by_text, still_missing = pair_by_text(
            {i: p[i] for i in set(p) - by_id},
            {i: o[i] for i in set(o) - by_id})
        held = len(by_id) + len(by_text)
        missing = [p[i] for i in still_missing]
        rows.append({
            "handle": handle, "party": party,
            "corpus": len(o), "ref": len(p),
            "shared": len(by_id), "text_matched": len(by_text), "held": held,
            "missing": len(missing), "extra": len(o) - held,
            "recall": round(100 * held / len(p), 1),
            "miss_years": dict(sorted(Counter(str(v[0].year) for v in missing).items())),
            "miss_kind": dict(Counter(v[2] for v in missing).most_common()),
            "cat": CATEGORIES.get(handle, "ok"),
            "window": [[a.isoformat(), b.isoformat()] for a, b in spells],
        })

    rows.sort(key=lambda r: (r["recall"], r["handle"].lower()))
    json.dump({"generated": datetime.now().isoformat(timespec="seconds"),
               "rows": rows, "notes": notes}, sys.stdout, indent=1)
    print()
    for n in notes:
        print("note:", n, file=sys.stderr)
    tot_ref = sum(r["ref"] for r in rows)
    print(f"\n{len(rows)} handles | ref {tot_ref:,} | missing {sum(r['missing'] for r in rows):,} "
          f"| added {sum(r['extra'] for r in rows):,} "
          f"| recovered by text {sum(r['text_matched'] for r in rows):,} "
          f"| recall {100*sum(r['held'] for r in rows)/tot_ref:.1f}%", file=sys.stderr)


if __name__ == "__main__":
    main()
