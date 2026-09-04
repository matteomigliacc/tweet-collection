#!/usr/bin/env python3
"""Filter dataset NDJSON files and import them into 4CAT."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from client_4cat import PLATFORM, api, tweet_envelope

from read_account_csv import build_jobs

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets" / "fourcat.json"
POLL_SECS = 5
POLL_TRIES = 60
TWEET_FMT = "%a %b %d %H:%M:%S %z %Y"


def load_windows() -> dict:
    """Return the exact CSV windows for each (party, handle) pair."""
    windows: dict = {}
    for job in build_jobs(ROOT / "frame" / "accounts.csv"):
        windows.setdefault((job["party"], job["handle"].lower()), []).append(
            (job["since"], job["until"]))
    return windows


def to_zeeschuimer(nd: Path, windows: list) -> bytes:
    """Wrap bare GraphQL tweet objects in the Zeeschuimer item envelope."""
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
        # Older working stores can contain tweets outside the CSV window.
        ca = tweet.get("legacy", {}).get("created_at")
        if not ca:
            continue
        d = datetime.strptime(ca, TWEET_FMT).date()
        if not any(s <= d <= e for s, e in windows):
            continue
        out.append(tweet_envelope(
            tweet, i, now_ms, user_agent="populism-scraper upload_4cat.py",
        ))
    return b"".join(out)


def upload_one(cfg: dict, nd: Path, label: str, windows: list) -> dict:
    """Upload an account file, wait for processing, and apply its label."""
    raw = to_zeeschuimer(nd, windows)
    if not raw.strip():
        print(f"  -- {label}: no tweets inside its CSV window, skipping")
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
                    help="skip the rsync from the collection server before uploading")
    args = ap.parse_args()

    dataset = Path(args.dataset)
    if not args.dry_run and not args.no_sync and dataset == ROOT / "data" / "dataset_server":
        print("syncing dataset from server ...", flush=True)
        subprocess.run(
            ["rsync", "-rtz", "-e", "ssh -i " + str(Path.home() / ".ssh" / "id_ed25519"),
             "--include=*/", "--include=*.ndjson", "--exclude=*",
             "root@192.168.1.106:/opt/populism-scraping/data/dataset/", str(dataset) + "/"],
            check=True)
    files = sorted(dataset.glob("*/*.ndjson"))
    if args.handle:
        want = {h.lower() for h in args.handle}
        files = [f for f in files if f.stem.lower() in want]
    if not files:
        sys.exit(f"no ndjson files found under {dataset}")

    all_windows = load_windows()
    skipped = [f for f in files if (f.parent.name, f.stem.lower()) not in all_windows]
    files = [f for f in files if (f.parent.name, f.stem.lower()) in all_windows]
    for f in skipped:
        print(f"  -- @{f.stem} ({f.parent.name}): not in the CSV, skipping")
    if not files:
        sys.exit("no CSV-listed ndjson files selected")

    print(f"{len(files)} file(s) from {dataset}:")
    for f in files:
        print(f"  {f.parent.name}/{f.name}  ({f.stat().st_size/1e6:.1f} MB)")
    if args.dry_run:
        return

    cfg = json.loads(SECRETS.read_text())
    results = []
    for f in files:
        label = f"@{f.stem} ({f.parent.name})"
        key = (f.parent.name, f.stem.lower())
        wins = all_windows[key]
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
