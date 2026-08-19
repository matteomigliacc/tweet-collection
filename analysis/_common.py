"""Shared, stdlib-only readers for the independent analysis scripts.

This module deliberately never imports from ``src``: the analyses are an
independent check on the scraper rather than another view through its code.
"""
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RAW = Path.home() / "Raw Data"
MIRROR = ROOT / "data" / "dataset_server"


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


def parse_created_at(obj):
    """Return a tweet's creation datetime, or None when it is malformed."""
    try:
        return datetime.strptime(obj["legacy"]["created_at"],
                                 "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        return None


def screen_name(obj):
    """The author's handle, from whichever of the two shapes X used."""
    user = ((obj.get("core") or {}).get("user_results") or {}).get("result") or {}
    for src in (user.get("core") or {}, user.get("legacy") or {}):
        if src.get("screen_name"):
            return src["screen_name"]
    return ""


def raw_paths():
    """All professors' exports, both top-level and in party folders."""
    return sorted(RAW.glob("*.ndjson")) + sorted(RAW.glob("*/*.ndjson"))


def mirror_paths():
    """All NDJSON files in the local mirror of the server dataset."""
    return MIRROR.glob("*/*.ndjson")
