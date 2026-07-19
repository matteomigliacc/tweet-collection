#!/usr/bin/env python3
"""Upload corpus NDJSON files to 4CAT (https://4cat.cdh.uu.nl) as import datasets.

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
  python src/upload_4cat.py --corpus data/corpus   # explicit corpus root
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets" / "fourcat.json"
PLATFORM = "twitter.com"  # Zeeschuimer platform id for X/Twitter
POLL_SECS = 5
POLL_TRIES = 60


def api(cfg: dict, path: str, data: bytes | None = None,
        headers: dict | None = None, query: dict | None = None) -> dict:
    url = f"{cfg['base_url']}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url, data=data,
        headers={"Authentication": cfg["api_token"], **(headers or {})})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def to_zeeschuimer(nd: Path) -> bytes:
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


def upload_one(cfg: dict, nd: Path, label: str) -> dict:
    raw = to_zeeschuimer(nd)
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
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpus_server"),
                    help="corpus root with <Party>/<handle>.ndjson files")
    ap.add_argument("--handle", action="append", default=None,
                    help="only upload these handles (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip the rsync from the scraper server before uploading")
    args = ap.parse_args()

    cfg = json.loads(SECRETS.read_text())
    corpus = Path(args.corpus)
    if not args.no_sync and corpus == ROOT / "data" / "corpus_server":
        print("syncing corpus from server ...", flush=True)
        subprocess.run(
            ["rsync", "-rtz", "-e", "ssh -i " + str(Path.home() / ".ssh" / "id_ed25519_scraper"),
             "--include=*/", "--include=*.ndjson", "--exclude=*",
             "root@192.168.1.106:/opt/populism-scraping/data/corpus/", str(corpus) + "/"],
            check=True)
    files = sorted(corpus.glob("*/*.ndjson"))
    if args.handle:
        want = {h.lower() for h in args.handle}
        files = [f for f in files if f.stem.lower() in want]
    if not files:
        sys.exit(f"no ndjson files found under {corpus}")

    print(f"{len(files)} file(s) from {corpus}:")
    for f in files:
        print(f"  {f.parent.name}/{f.name}  ({f.stat().st_size/1e6:.1f} MB)")
    if args.dry_run:
        return

    results = []
    for f in files:
        label = f"@{f.stem} ({f.parent.name})"
        try:
            results.append(upload_one(cfg, f, label))
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
