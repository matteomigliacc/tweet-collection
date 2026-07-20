"""Flatten collected raw tweet JSON (tweets.sqlite) into a tidy analysis table.

Reads the raw_json column and emits one row per tweet with the fields most
studies need. The raw store stays the source of truth; this is a derived view,
so it never scrapes and can be re-run freely.

Usage:
  python src/flatten.py                 # -> data/tweets_flat.csv
  python src/flatten.py --out foo.csv
"""
import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tweets.sqlite"
DEFAULT_OUT = ROOT / "data" / "tweets_flat.csv"

COLUMNS = [
    "tweet_id", "handle", "created_at", "text", "lang",
    "like_count", "retweet_count", "reply_count", "quote_count", "view_count",
    "is_retweet", "is_reply", "is_quote",
    "conversation_id", "hashtags", "mentioned_users", "collection_pass",
]


def _flatten_raw(handle: str, collection_pass: str, d: dict) -> dict:
    """Flatten a raw GraphQL Tweet object (raw-mode collection / .ndjson format)."""
    leg = d.get("legacy") or {}
    ents = leg.get("entities") or {}
    created = leg.get("created_at")
    if created:  # 'Wed Aug 14 09:15:00 +0000 2023' -> ISO
        created = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").isoformat()
    views = (d.get("views") or {}).get("count")
    return {
        "tweet_id": d.get("rest_id") or leg.get("id_str"),
        "handle": handle,
        "created_at": created,
        "text": (leg.get("full_text") or "").replace("\n", " ").strip(),
        "lang": leg.get("lang"),
        "like_count": leg.get("favorite_count"),
        "retweet_count": leg.get("retweet_count"),
        "reply_count": leg.get("reply_count"),
        "quote_count": leg.get("quote_count"),
        "view_count": int(views) if views else None,
        "is_retweet": "retweeted_status_result" in leg,
        "is_reply": leg.get("in_reply_to_status_id_str") is not None,
        "is_quote": bool(leg.get("is_quote_status")),
        "conversation_id": leg.get("conversation_id_str"),
        "hashtags": "|".join(h.get("text", "") for h in ents.get("hashtags") or []),
        "mentioned_users": "|".join(
            u.get("screen_name", "") for u in ents.get("user_mentions") or []
        ),
        "collection_pass": collection_pass,
    }


def flatten_row(handle: str, collection_pass: str, d: dict) -> dict:
    """Flatten one stored tweet, whichever of the two storage formats it uses.

    Dataset runs store the raw GraphQL object (recognisable by its 'legacy'
    key); ad-hoc non-raw runs store twscrape's parsed form with different
    field names (rawContent vs full_text, likeCount vs favorite_count, ...).
    Both come out as the same tidy row.
    """
    if "legacy" in d:  # raw GraphQL object (raw-mode / dataset format)
        return _flatten_raw(handle, collection_pass, d)
    users = d.get("mentionedUsers") or []
    return {
        "tweet_id": d.get("id"),
        "handle": handle,
        "created_at": d.get("date"),
        "text": (d.get("rawContent") or "").replace("\n", " ").strip(),
        "lang": d.get("lang"),
        "like_count": d.get("likeCount"),
        "retweet_count": d.get("retweetCount"),
        "reply_count": d.get("replyCount"),
        "quote_count": d.get("quoteCount"),
        "view_count": d.get("viewCount"),
        "is_retweet": d.get("retweetedTweet") is not None,
        "is_reply": d.get("inReplyToTweetId") is not None,
        "is_quote": bool(d.get("isQuoteStatus")),
        "conversation_id": d.get("conversationId"),
        "hashtags": "|".join(d.get("hashtags") or []),
        "mentioned_users": "|".join(
            u.get("username", "") for u in users if isinstance(u, dict)
        ),
        "collection_pass": collection_pass,
    }


def export_ndjson(db_path: Path, out_path: Path) -> int:
    """Write every stored raw tweet object to .ndjson (one JSON object per line).

    Use with raw-mode collection, where raw_json holds the raw GraphQL Tweet
    object — the output matches the archive .ndjson format field-for-field.
    """
    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT raw_json FROM tweets ORDER BY created_at").fetchall()
    n = 0
    with Path(out_path).open("w", encoding="utf-8") as f:
        # each fetched row is a 1-element tuple; `for (raw,) in rows` unpacks it.
        # The JSON is written back verbatim — no re-serialisation, no data loss.
        for (raw,) in rows:
            f.write(raw.rstrip("\n"))
            f.write("\n")
            n += 1
    con.close()
    return n


def flatten_db(db_path: Path, out_path: Path) -> int:
    """Flatten every tweet in `db_path` to a tidy CSV at `out_path`. Returns row count."""
    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT handle, source, raw_json FROM tweets").fetchall()
    n = 0
    with Path(out_path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for handle, source, raw in rows:
            w.writerow(flatten_row(handle, source, json.loads(raw)))
            n += 1
    con.close()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    n = flatten_db(args.db, args.out)
    print(f"Wrote {n} rows -> {args.out}")


if __name__ == "__main__":
    main()
