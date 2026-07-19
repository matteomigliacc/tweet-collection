#!/usr/bin/env python3
"""Parse the Rijksarchief OCR'd Twitter-archive PDFs into NDJSON.

The archive (Rijksorganisatie voor Informatiehuishouding) is a screenshot dump
of @MinPres' timeline with an OCR text layer. Layout is rigidly column-anchored,
which we exploit via `pdftotext -bbox-layout` word coordinates:

  x ~ 691   header lines ("Mark Rutte & @MinPres - 19 dec. 2022") and body text
  x  715+   text OCR'd out of embedded media cards -> not anchored, dropped
  x ~ 692 / 810 / 931 / 1052   engagement row: replies / retweets / likes / views

A tweet = header row, then body rows anchored at the left margin, until the
engagement row (or the next header). Engagement counts are read per column;
values the OCR mangled beyond recognition become null rather than wrong.

The screenshot pages overlap, so most tweets appear twice with slightly
different OCR: exact-hash dedup misses them. We therefore fuzzy-dedup within
each calendar day (SequenceMatcher >= 0.75) and merge the copies — longest
text wins, engagement fields fill in from whichever copy kept its counter row
(page breaks often cut it off one of the two).

No tweet IDs exist in the source: ids are a (date, normalised text) hash.
Dates are day-precision only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET

XHTML = "{http://www.w3.org/1999/xhtml}"

# Layout constants (PDF points), measured off the 2022/2023 dumps.
# Screenshots come in two flavours (cropped column / full browser window with
# sidebars), but the tweet column itself sits at the same x in both.
COL_MIN, COL_MAX = 625, 1235   # x-band of the tweet column; sidebars fall outside
MARGIN_X = 691.5        # left margin body/header text is anchored to
ANCHOR_TOL = 9          # how far a row's first word may stray from the margin
ROW_TOL = 6             # words within this y-distance form one visual row
ENG_COLS = {"replies": 692, "retweets": 810, "likes": 931, "views": 1052}
ENG_TOL = 35

# Dutch abbreviated month names, plus OCR variants seen in the dumps
MONTHS = {
    "jan": 1, "feb": 2, "mrt": 3, "maa": 3, "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "ep": 9, "sept": 9, "okt": 10, "oki": 10,
    "nov": 11, "dec": 12,
}
HEADER = re.compile(
    r"""Rutte \s* .{0,3}? \s* @(?P<handle>\w+) \s*
        [-–—•·] \s* (?P<day>\d{1,2}) \s+ (?P<mon>[a-z]{2,4})\.? \s+ (?P<yr>\d{2,4})""",
    re.IGNORECASE | re.VERBOSE,
)
REPLY_TO = re.compile(r"^\s*Als antwoord op\s+(@\S+)", re.IGNORECASE)
COUNT = re.compile(r"(\d[\d.,]*)\s*([KkMm])?")


def rows_from_pdf(pdf: Path):
    """Yield visual rows as (min_x, text, words) in reading order, all pages."""
    out = subprocess.run(
        ["pdftotext", "-bbox-layout", str(pdf), "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    for page in ET.fromstring(out).iter(f"{XHTML}page"):
        words = []
        for w in page.iter(f"{XHTML}word"):
            x = float(w.get("xMin"))
            if w.text and w.text.strip() and COL_MIN <= x <= COL_MAX:
                words.append((float(w.get("yMin")), x, w.text.strip()))
        words.sort()
        row: list[tuple[float, float, str]] = []
        for y, x, t in words:
            if row and y - row[0][0] > ROW_TOL:
                yield finish_row(row)
                row = []
            row.append((y, x, t))
        if row:
            yield finish_row(row)


def finish_row(row):
    row.sort(key=lambda w: w[1])                       # left-to-right
    text = " ".join(t for _, _, t in row)
    return (row[0][1], text, row)


def parse_count(tokens: list[str]) -> int | None:
    """Best-effort numeric value of one engagement cell; None when OCR ate it."""
    s = " ".join(tokens)
    best = None
    for m in COUNT.finditer(s):
        num, suf = m.group(1), m.group(2)
        # lone digits with no K/M suffix are usually OCR'd icon fragments,
        # unless the cell is exactly that number and nothing else
        if len(num.replace(".", "").replace(",", "")) < 2 and not suf \
                and s.strip() != num:
            continue
        best = (num, suf)
    if not best:
        return None
    num, suf = best
    try:
        val = float(num.replace(",", "."))
    except ValueError:
        return None
    return int(val * {"k": 1e3, "m": 1e6}.get((suf or "").lower(), 1))


def engagement(row) -> dict | None:
    """Interpret a row as the counter strip; None if it doesn't look like one."""
    _, text, words = row
    letters = sum(c.isalpha() for c in text)
    if letters > 14 or len(words) > 9:                 # prose, not counters
        return None
    cells: dict[str, list[str]] = {k: [] for k in ENG_COLS}
    hit = 0
    for _, x, t in words:
        for name, cx in ENG_COLS.items():
            if abs(x - cx) <= ENG_TOL:
                cells[name].append(t)
                hit += 1
                break
    if hit < 2 or not cells["replies"]:
        return None
    counts = {k: parse_count(v) if v else None for k, v in cells.items()}
    # 2 column hits with nothing parseable is icon garbage drifting through the
    # bands ("wig G"); >=3 hits is the counter strip even when OCR ate every
    # number — treat it as the tweet boundary so it can't leak into the body.
    if all(v is None for v in counts.values()) and hit < 3:
        return None
    return counts


def parse_date(m) -> date | None:
    mon = MONTHS.get(m["mon"].lower().rstrip("."))
    if not mon:
        return None
    y = int(m["yr"])
    if y < 100:
        y += 2000
    try:
        return date(y, mon, int(m["day"]))
    except ValueError:
        return None


def parse(pdf: Path) -> list[dict]:
    tweets, cur, body = [], None, []

    def flush():
        nonlocal cur
        if cur is None:
            return
        txt = re.sub(r"\s+", " ", " ".join(body)).strip()
        # the phone/loudspeaker emoji OCRs to a stray letter-comma prefix
        txt = re.sub(r"^[A-Za-z][,.]{1,2}:?\s*", "", txt).strip()
        # OCR reads a standalone capital I as a pipe ("| spoke with...")
        txt = re.sub(r"(?:(?<=^)|(?<=\s))\|(?=\s|$)", "I", txt)
        if len(txt) >= 8 or txt.startswith("#"):
            cur["text"] = txt
            cur["id"] = hashlib.sha1(
                f"{cur['date']}|{txt.lower()}".encode()).hexdigest()[:16]
            tweets.append(cur)
        cur = None

    for x0, text, words in rows_from_pdf(pdf):
        if (m := HEADER.search(text)) and abs(x0 - MARGIN_X) < 80:
            d = parse_date(m)
            if d:
                flush()
                body = []
                cur = {"id": None, "handle": m["handle"], "date": d.isoformat(),
                       "text": None, "reply_to": None, "source": pdf.name,
                       "replies": None, "retweets": None,
                       "likes": None, "views": None}
                continue
        if cur is None:
            continue
        if (eng := engagement((x0, text, words))) is not None:
            cur.update(eng)
            flush()
            continue
        if abs(x0 - MARGIN_X) > ANCHOR_TOL:
            continue                                   # media-card OCR, skip
        if (r := REPLY_TO.match(text)):
            cur["reply_to"] = r.group(1)
            continue
        if not re.search(r"[A-Za-zÀ-ÿ]{3}", text):
            continue                                   # icon fragments ("T—")
        if re.fullmatch(r"(Meer weergeven|Show more|Show this thread|"
                        r"Deze thread weergeven)\W*", text, re.IGNORECASE):
            continue                                   # truncation button
        body.append(text)
    flush()
    return tweets


ENG_FIELDS = ("replies", "retweets", "likes", "views")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9@#]", "", text.lower())


def merge(a: dict, b: dict) -> dict:
    """Fold duplicate OCR copies of one tweet into a single record."""
    keep, other = (a, b) if len(a["text"]) >= len(b["text"]) else (b, a)
    for f in ENG_FIELDS + ("reply_to",):
        if keep[f] is None:
            keep[f] = other[f]
    return keep


def dedup(rows: list[dict]) -> list[dict]:
    """Fuzzy-dedup within each day: overlapping screenshots yield near-copies."""
    by_day: dict[str, list[dict]] = {}
    for t in rows:
        day = by_day.setdefault(t["date"], [])
        for i, o in enumerate(day):
            if SequenceMatcher(None, norm(t["text"]), norm(o["text"])).ratio() >= 0.75:
                day[i] = merge(o, t)
                break
        else:
            day.append(t)
    return [t for day in by_day.values() for t in day]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()

    rows = []
    for pdf in args.pdfs:
        got = parse(pdf)
        rows.extend(got)
        print(f"{pdf.name}: {len(got)} parsed", file=sys.stderr)

    n_raw = len(rows)
    rows = dedup(rows)
    print(f"dedup: {n_raw} -> {len(rows)} unique", file=sys.stderr)

    rows.sort(key=lambda t: t["date"])
    with args.out.open("w") as f:
        for t in rows:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    if rows:
        n_eng = sum(1 for t in rows if t["likes"] is not None)
        print(f"-> {args.out}  {len(rows)} tweets  "
              f"{rows[0]['date']}..{rows[-1]['date']}  "
              f"({n_eng} with like-counts)", file=sys.stderr)


if __name__ == "__main__":
    main()
