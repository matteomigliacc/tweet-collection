#!/usr/bin/env python3
"""Upload dataset NDJSON files to 4CAT (https://4cat.cdh.uu.nl) as import datasets.

Replaces the manual Zeeschuimer -> filter-by-date -> merge workflow from the
professors' onboarding doc: our per-handle ndjsons are already merged,
deduplicated (tweet_id PK) and clipped to the tenure/seat window, so each file
maps 1:1 onto a finished 4CAT dataset.

Auth: secrets/fourcat.json {"base_url": ..., "api_token": ...}. The token is
passed as the `access_token` query parameter (4CAT's Zeeschuimer-compatible
import endpoint); the NDJSON goes in the POST body unchanged.

Usage:
  python src/upload_4cat.py --dry-run              # list what would upload
  python src/upload_4cat.py --handle Nvanvroonhoven
  python src/upload_4cat.py                        # upload everything
  python src/upload_4cat.py --dataset data/dataset   # explicit dataset root
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets" / "fourcat.json"
PLATFORM = "twitter.com"  # Zeeschuimer platform id for X/Twitter
POLL_SECS = 5
POLL_TRIES = 60
FLOOR = date(2017, 3, 23)      # study floor: 2017 Tweede Kamer installation (same as run_all)
CEILING = date(2025, 11, 12)   # 2025 TK election: study window ends here
TWEET_FMT = "%a %b %d %H:%M:%S %z %Y"


def load_windows() -> dict:
    """(party, handle_lower) -> [(start, end)], clipped to FLOOR..CEILING.

    Keyed by party too because e.g. JesseKlaver has separate dataset files
    (and tenure windows) under GroenLinks, GroenLinks-PvdA and PRO.
    """
    windows: dict = {}
    for fn, s_col, e_col in [("frame/leaders.csv", "leader_start", "leader_end"),
                             ("frame/parties.csv", "seat_start", "seat_end")]:
        with open(ROOT / fn) as f:
            for row in csv.DictReader(f):
                h = (row.get("handle") or "").strip()
                if not h:
                    continue
                s = max(date.fromisoformat(row[s_col]), FLOOR)
                e = CEILING if row[e_col].strip() == "ongoing" \
                    else min(date.fromisoformat(row[e_col]), CEILING)
                # a window entirely outside FLOOR..CEILING (e.g. PRO, formed
                # after the 2025 election) stays in the dict as an empty list:
                # the handle is in the frame but has nothing inside the study.
                windows.setdefault((row["party"], h.lower()), [])
                if s <= e:
                    windows[(row["party"], h.lower())].append((s, e))
    return windows


def api(cfg: dict, path: str, data: bytes | None = None,
        headers: dict | None = None, query: dict | None = None) -> dict:
    """One HTTP call to 4CAT, JSON response decoded. GET when data is None, else POST.

    Uses only the stdlib (urllib) so this script runs anywhere without
    installing anything. The token goes in the Authentication header —
    {**a, **b} merges two dicts, with the caller's headers winning.
    """
    url = f"{cfg['base_url']}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url, data=data,
        headers={"Authentication": cfg["api_token"], **(headers or {})})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def to_zeeschuimer(nd: Path, windows: list) -> bytes:
    """Wrap bare GraphQL tweet objects in the Zeeschuimer item envelope.

    4CAT's /api/import-dataset/ hands the file to the zeeschuimer-import
    worker, which expects one envelope per line with the tweet under "data"
    — bare tweets end up as an unlabeled generic upload instead.
    """
    now_ms = int(time.time() * 1000)
    out = []
    # NB: split on real newlines only — str.splitlines() also splits on
    # U+2028/U+2029, which occur unescaped inside tweet text and would
    # shear valid JSON lines in half.
    for i, line in enumerate(nd.read_text().split("\n")):
        line = line.strip()
        if not line:
            continue
        tweet = json.loads(line)
        # clip to the study window: the DB keeps whatever any pass encountered
        # (Replies tab reaches outside the tenure window in both directions)
        ca = tweet.get("legacy", {}).get("created_at")
        if not ca:
            continue
        d = datetime.strptime(ca, TWEET_FMT).date()
        if not any(s <= d <= e for s, e in windows):
            continue
        # 4CAT's map_item does tweet["id"] unguarded; twscrape output only has
        # rest_id. Synthesize the GraphQL global id Zeeschuimer would have kept.
        if "id" not in tweet and tweet.get("rest_id"):
            tweet["id"] = base64.b64encode(f"Tweet:{tweet['rest_id']}".encode()).decode()
        # A quoted tweet that was deleted arrives as {"result": {"__typename":
        # "TweetUnavailable"}}; 4CAT's mapper indexes ["result"]["legacy"]
        # unguarded and crashes on it, so drop the stub.
        qr = (tweet.get("quoted_status_result") or {}).get("result") or {}
        if tweet.get("quoted_status_result") and "legacy" not in qr and "tweet" not in qr:
            del tweet["quoted_status_result"]
        out.append(json.dumps({
            "nav_index": i,
            "item_id": tweet.get("rest_id", str(i)),
            "timestamp_collected": now_ms,
            "last_updated": now_ms,
            "source_platform": PLATFORM,
            "source_platform_url": "https://x.com",
            "source_url": "https://x.com/search",
            "user_agent": "populism-scraper upload_4cat.py",
            "data": tweet,
        }, ensure_ascii=False))
    return ("\n".join(out) + "\n").encode()


def upload_one(cfg: dict, nd: Path, label: str, windows: list) -> dict:
    """Upload one ndjson: POST the wrapped tweets, poll until 4CAT finishes
    processing (up to POLL_TRIES x POLL_SECS), then rename the dataset from
    the generic import label to '@handle (Party)'."""
    raw = to_zeeschuimer(nd, windows)
    if not raw.strip():
        print(f"  -- {label}: no tweets inside the study window, skipping")
        return {"label": label, "skipped": "empty window"}
    n_lines = raw.count(b"\n")
    print(f"  uploading {label}: {len(raw)/1e6:.1f} MB, {n_lines} tweets ...", flush=True)
    res = api(cfg, "/api/import-dataset/", data=raw,
              headers={"X-Zeeschuimer-Platform": PLATFORM,
                       "Content-Type": "application/x-ndjson"})
    key = res.get("key")
    if not key:
        raise RuntimeError(f"no dataset key in response: {res}")
    for _ in range(POLL_TRIES):
        st = api(cfg, "/api/check-query/", query={"key": key})
        if st.get("done"):
            # replace the generic "X/Twitter (via Zeeschuimer) Dataset" label
            body = urllib.parse.urlencode({"label": label}).encode()
            try:
                api(cfg, f"/api/edit-dataset-label/{key}/", data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
            except Exception as e:
                print(f"  (label change failed: {e})")
            print(f"  -> done: {st.get('rows')} rows, key {key}, label {label!r}")
            st["label"] = label
            return st
        time.sleep(POLL_SECS)
    print(f"  -> still processing after {POLL_TRIES * POLL_SECS}s, key {key} (check in 4CAT UI)")
    return {"key": key, "label": label, "done": False}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "data" / "dataset_server"),
                    help="dataset root with <Party>/<handle>.ndjson files")
    ap.add_argument("--handle", action="append", default=None,
                    help="only upload these handles (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip the rsync from the scraper server before uploading")
    args = ap.parse_args()

    cfg = json.loads(SECRETS.read_text())
    dataset = Path(args.dataset)
    if not args.no_sync and dataset == ROOT / "data" / "dataset_server":
        print("syncing dataset from server ...", flush=True)
        subprocess.run(
            ["rsync", "-rtz", "-e", "ssh -i " + str(Path.home() / ".ssh" / "id_ed25519_scraper"),
             "--include=*/", "--include=*.ndjson", "--exclude=*",
             "root@192.168.1.106:/opt/populism-scraping/data/dataset/", str(dataset) + "/"],
            check=True)
    files = sorted(dataset.glob("*/*.ndjson"))
    if args.handle:
        want = {h.lower() for h in args.handle}
        files = [f for f in files if f.stem.lower() in want]
    if not files:
        sys.exit(f"no ndjson files found under {dataset}")

    print(f"{len(files)} file(s) from {dataset}:")
    for f in files:
        print(f"  {f.parent.name}/{f.name}  ({f.stat().st_size/1e6:.1f} MB)")
    if args.dry_run:
        return

    all_windows = load_windows()
    results = []
    for f in files:
        label = f"@{f.stem} ({f.parent.name})"
        key = (f.parent.name, f.stem.lower())
        if key in all_windows:
            wins = all_windows[key]
        else:
            # not under this party in the frame: any window for the handle,
            # else (handle not in frame at all) the full study span
            by_handle = [(p, h) for (p, h) in all_windows if h == f.stem.lower()]
            wins = sum((all_windows[k] for k in by_handle), []) if by_handle \
                else [(FLOOR, CEILING)]
        if not wins:
            print(f"  -- {label}: window lies entirely outside the study span, skipping")
            results.append({"label": label, "skipped": "outside study span"})
            continue
        try:
            results.append(upload_one(cfg, f, label, wins))
        except Exception as e:
            print(f"  !! {label} failed: {e}")
            results.append({"label": label, "error": str(e)})
    ok = sum(1 for r in results if r.get("done"))
    print(f"\n{ok}/{len(results)} datasets imported. Keys:")
    for r in results:
        print(f"  {r.get('label')}: {r.get('key', 'FAILED')}"
              + (f" ({r.get('rows')} rows)" if r.get("rows") else ""))


if __name__ == "__main__":
    main()
