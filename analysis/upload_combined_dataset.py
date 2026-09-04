#!/usr/bin/env python3
"""Upload a validated chronological dataset to 4CAT in resumable chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from client_4cat import api, stream_import, wait_and_label, tweet_envelope  # noqa: E402


def iter_prepared_parts(
    source: Path,
    scratch_dir: Path,
    max_bytes: int,
    collected_ms: int,
) -> Iterator[dict[str, Any]]:
    """Yield metadata for one prepared part at a time, never splitting a row."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    out = None
    path = None
    part_index = rows = size = 0
    digest = hashlib.sha256()
    try:
        with source.open(encoding="utf-8") as src:
            for nav_index, raw in enumerate(src):
                raw = raw.rstrip("\r\n")
                if not raw:
                    raise ValueError(f"blank source line at {nav_index + 1}")
                payload = tweet_envelope(json.loads(raw), nav_index, collected_ms)
                if len(payload) > max_bytes:
                    raise ValueError(
                        f"tweet at source line {nav_index + 1} exceeds chunk limit"
                    )
                if out is not None and size + len(payload) > max_bytes:
                    out.close()
                    out = None
                    yield {
                        "index": part_index,
                        "path": str(path),
                        "rows": rows,
                        "bytes": size,
                        "sha256": digest.hexdigest(),
                    }
                    rows = size = 0
                    digest = hashlib.sha256()
                if out is None:
                    part_index += 1
                    path = scratch_dir / f"part_{part_index:03d}.ndjson"
                    out = path.open("wb")
                out.write(payload)
                digest.update(payload)
                rows += 1
                size += len(payload)
        if out is not None:
            out.close()
            out = None
            yield {
                "index": part_index,
                "path": str(path),
                "rows": rows,
                "bytes": size,
                "sha256": digest.hexdigest(),
            }
    finally:
        if out is not None:
            out.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def initialise_state(
    state_path: Path,
    source: Path,
    report: dict[str, Any],
    label: str,
    max_part_bytes: int,
) -> dict[str, Any]:
    source_sha = sha256_file(source)
    expected_sha = report.get("sha256")
    if expected_sha != source_sha:
        raise ValueError(
            f"source SHA-256 does not match validation report: {source_sha} != {expected_sha}"
        )
    if source.stat().st_size != report.get("output_bytes"):
        raise ValueError("source size does not match validation report")

    identity = {
        "source": str(source.resolve()),
        "source_bytes": source.stat().st_size,
        "source_sha256": source_sha,
        "source_rows": report["output_rows"],
        "label": label,
        "max_part_bytes": max_part_bytes,
    }
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for key, value in identity.items():
            if state.get(key) != value:
                raise ValueError(f"upload state mismatch for {key}")
        return state

    state = {
        **identity,
        "collected_ms": int(time.time() * 1000),
        "parts": [],
    }
    save_state(state_path, state)
    return state


def queue_merge(cfg: dict, keys: list[str], label: str) -> str:
    base = cfg["base_url"].rstrip("/")
    body = urllib.parse.urlencode({
        "key": keys[0],
        "processor": "merge-datasets",
        "source": "\n".join(f"{base}/results/{key}/" for key in keys[1:]),
        "merge": "keep",
        "label": label,
    }).encode()
    result = api(
        cfg,
        "/api/queue-processor/",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if result.get("status") != "success" or not result.get("key"):
        raise RuntimeError(f"could not queue 4CAT merge: {result}")
    return result["key"]


def wait_for_merge(
    cfg: dict,
    analysis_key: str,
    expected_rows: int,
    expected_label: str,
) -> tuple[str, dict[str, Any]]:
    """Wait for merge analysis and extract its copied standalone dataset key."""
    for attempt in range(720):
        status = api(
            cfg,
            "/api/check-processors/",
            query={"subqueries": json.dumps([analysis_key])},
        )
        if status and status[0].get("finished"):
            html = status[0].get("html", "")
            keys = dict.fromkeys(re.findall(r"/results/([0-9a-f]{32})/?", html))
            candidates = []
            for key in keys:
                if key == analysis_key:
                    continue
                candidate = api(cfg, "/api/check-query/", query={"key": key})
                candidates.append((key, candidate))
                if (
                    candidate.get("done")
                    and candidate.get("rows") == expected_rows
                    and candidate.get("label") == expected_label
                ):
                    return key, candidate
            if not candidates:
                raise RuntimeError(
                    "merge finished but its standalone dataset key was not returned"
                )
            raise RuntimeError(
                "merge finished but no standalone candidate matched the expected "
                f"label and {expected_rows:,} rows"
            )
        if attempt and attempt % 12 == 0:
            progress = status[0].get("progress") if status else "unknown"
            print(f"merge {analysis_key}: {progress}% ...", flush=True)
        time.sleep(5)
    raise TimeoutError(f"4CAT merge {analysis_key} did not finish within one hour")


def upload_parts(
    cfg: dict,
    source: Path,
    scratch_dir: Path,
    state_path: Path,
    state: dict[str, Any],
) -> None:
    for generated in iter_prepared_parts(
        source,
        scratch_dir,
        state["max_part_bytes"],
        state["collected_ms"],
    ):
        index = generated["index"]
        if index <= len(state["parts"]):
            part = state["parts"][index - 1]
            for field in ("index", "rows", "bytes", "sha256"):
                if part.get(field) != generated[field]:
                    raise RuntimeError(f"regenerated part {index} differs in {field}")
        else:
            part = {key: generated[key] for key in ("index", "rows", "bytes", "sha256")}
            state["parts"].append(part)
            save_state(state_path, state)

        part_path = Path(generated["path"])
        try:
            part_label = f"TEMP combined {index:03d}"
            if part.get("key") and not part.get("done"):
                result = wait_and_label(cfg, part["key"], part_label)
                part["done"] = bool(result.get("done"))
                part["fourcat_rows"] = result.get("rows")
                save_state(state_path, state)
            if part.get("done"):
                if part.get("fourcat_rows") not in (None, part["rows"]):
                    raise RuntimeError(f"4CAT row mismatch for completed part {index}")
                print(f"part {index:03d} already complete: {part['key']}", flush=True)
                continue

            print(
                f"uploading part {index:03d}: {part['bytes'] / 1_000_000:.1f} MB, "
                f"{part['rows']:,} tweets",
                flush=True,
            )
            part["key"] = stream_import(cfg, part_path)
            save_state(state_path, state)
            result = wait_and_label(cfg, part["key"], part_label)
            part["done"] = bool(result.get("done"))
            part["fourcat_rows"] = result.get("rows")
            save_state(state_path, state)
            if not part["done"]:
                raise RuntimeError(f"part {index} did not finish: {result}")
            if part["fourcat_rows"] != part["rows"]:
                raise RuntimeError(
                    f"part {index} row mismatch: prepared {part['rows']}, "
                    f"4CAT {part['fourcat_rows']}"
                )
        finally:
            part_path.unlink(missing_ok=True)

    total = sum(part["rows"] for part in state["parts"])
    if total != state["source_rows"]:
        raise RuntimeError(
            f"chunk rows do not reconcile: {total} != {state['source_rows']}"
        )
    state["parts_complete"] = True
    save_state(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--max-part-mb", type=int, default=120)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report.get("status") != "valid":
        parser.error("merge validation report is not valid")
    cfg = json.loads((ROOT / "secrets" / "fourcat.json").read_text(encoding="utf-8"))
    state = initialise_state(
        args.state,
        args.source,
        report,
        args.label,
        args.max_part_mb * 1_000_000,
    )
    if state.get("parts_complete"):
        print(
            f"all {len(state['parts'])} upload parts already complete; "
            "resuming final merge verification",
            flush=True,
        )
    else:
        upload_parts(cfg, args.source, args.scratch_dir, args.state, state)

    if len(state["parts"]) == 1:
        final_key = state["parts"][0]["key"]
        body = urllib.parse.urlencode({"label": args.label}).encode()
        api(
            cfg,
            f"/api/edit-dataset-label/{final_key}/",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        final_status = api(cfg, "/api/check-query/", query={"key": final_key})
    else:
        if not state.get("merge_analysis_key"):
            state["merge_analysis_key"] = queue_merge(
                cfg, [part["key"] for part in state["parts"]], args.label
            )
            save_state(args.state, state)
            print(f"queued merge {state['merge_analysis_key']}", flush=True)
        final_key, final_status = wait_for_merge(
            cfg,
            state["merge_analysis_key"],
            state["source_rows"],
            args.label,
        )

    if not final_status.get("done"):
        raise RuntimeError(f"final 4CAT dataset is not finished: {final_status}")
    if final_status.get("rows") != state["source_rows"]:
        raise RuntimeError(
            f"final 4CAT row mismatch: {final_status.get('rows')} != "
            f"{state['source_rows']}"
        )
    state["final_key"] = final_key
    state["final_rows"] = final_status["rows"]
    state["final_url"] = final_status.get("url")
    state["complete"] = True
    save_state(args.state, state)
    print(json.dumps({
        "key": final_key,
        "rows": final_status["rows"],
        "label": args.label,
        "url": final_status.get("url"),
    }), flush=True)


if __name__ == "__main__":
    main()
