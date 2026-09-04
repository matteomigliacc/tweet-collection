"""Shared 4CAT transport and Zeeschuimer conversion."""
from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

PLATFORM = "twitter.com"


def api(cfg: dict, path: str, data: bytes | None = None,
        headers: dict | None = None, query: dict | None = None) -> dict:
    """Make one authenticated 4CAT request and decode its JSON response."""
    url = f"{cfg['base_url']}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url, data=data,
        headers={"Authentication": cfg["api_token"], **(headers or {})})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def tweet_envelope(
    tweet: dict, nav_index: int, collected_ms: int,
    *, user_agent: str = "populism-scraper upload_combined_4cat_streaming.py",
) -> bytes:
    """Return one compact Zeeschuimer-compatible NDJSON line."""
    if "id" not in tweet and tweet.get("rest_id"):
        tweet["id"] = base64.b64encode(
            f"Tweet:{tweet['rest_id']}".encode()
        ).decode()
    tweet.setdefault("source", "")
    quoted = (tweet.get("quoted_status_result") or {}).get("result") or {}
    if (
        tweet.get("quoted_status_result")
        and "legacy" not in quoted
        and "tweet" not in quoted
    ):
        del tweet["quoted_status_result"]
    envelope = {
        "nav_index": nav_index,
        "item_id": tweet.get("rest_id", str(nav_index)),
        "timestamp_collected": collected_ms,
        "last_updated": collected_ms,
        "source_platform": PLATFORM,
        "source_platform_url": "https://x.com",
        "source_url": "https://x.com/search",
        "user_agent": user_agent,
        "data": tweet,
    }
    return (
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def stream_import(cfg: dict, import_file: Path) -> str:
    parsed = urllib.parse.urlsplit(cfg["base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("fourcat base_url must be an HTTP(S) URL")
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=3600)
    endpoint = parsed.path.rstrip("/") + "/api/import-dataset/"
    headers = {
        "Authentication": cfg["api_token"],
        "X-Zeeschuimer-Platform": PLATFORM,
        "Content-Type": "application/x-ndjson",
        "Content-Length": str(import_file.stat().st_size),
    }
    with import_file.open("rb") as body:
        connection.request("POST", endpoint, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
    connection.close()
    if not 200 <= response.status < 300:
        raise RuntimeError(f"4CAT import failed with HTTP {response.status}: {payload[:500]!r}")
    result = json.loads(payload)
    key = result.get("key")
    if not key:
        raise RuntimeError(f"4CAT returned no dataset key: {result}")
    return key


def wait_and_label(cfg: dict, key: str, label: str) -> dict:
    for attempt in range(360):
        status = api(cfg, "/api/check-query/", query={"key": key})
        if status.get("done"):
            body = urllib.parse.urlencode({"label": label}).encode()
            api(
                cfg,
                f"/api/edit-dataset-label/{key}/",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            status["key"] = key
            status["label"] = label
            return status
        if attempt and attempt % 12 == 0:
            print(f"still processing dataset {key} ...", flush=True)
        time.sleep(5)
    return {"key": key, "label": label, "done": False, "timed_out": True}
