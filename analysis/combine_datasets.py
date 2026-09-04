#!/usr/bin/env python3
"""Build one validated, globally chronological raw-tweet NDJSON dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import os
import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any


TWEET_FMT = "%a %b %d %H:%M:%S %z %Y"


def load_windows(root: Path) -> dict[tuple[str, str], list[tuple[date, date]]]:
    """Load inclusive frame windows keyed by exact party and lowercase handle."""
    windows: dict[tuple[str, str], list[tuple[date, date]]] = {}
    filename = "frame/accounts.csv"
    with (root / filename).open(newline="", encoding="utf-8") as fh:
        for row_number, row in enumerate(csv.DictReader(fh), 2):
            handle = (row.get("handle") or "").strip().lstrip("@").strip().lower()
            if not handle:
                continue
            party = (row.get("party") or "").strip()
            if not party:
                raise ValueError(f"{filename}:{row_number}: missing party")
            start = date.fromisoformat(row["start"].strip())
            end = date.fromisoformat(row["end"].strip())
            if start > end:
                raise ValueError(
                    f"{filename}:{row_number}: {row['handle']} starts after it ends"
                )
            windows.setdefault((party, handle), []).append((start, end))
    return windows


def select_inputs(
    dataset: Path,
    windows: dict[tuple[str, str], list[tuple[date, date]]],
) -> tuple[list[Path], list[Path]]:
    found = sorted(dataset.glob("*/*.ndjson"))
    selected = [
        path for path in found
        if (path.parent.name, path.stem.lower()) in windows
    ]
    selected_set = set(selected)
    return selected, [path for path in found if path not in selected_set]


def _file_stats(path: Path, dataset: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(dataset)),
        "bytes": path.stat().st_size,
        "input_rows": 0,
        "excluded_outside_windows": 0,
        "eligible_rows": 0,
        "duplicate_rows": 0,
        "output_rows": 0,
    }


def iter_tweets(
    path: Path,
    source_index: int,
    windows: list[tuple[date, date]],
    stats: dict[str, Any],
) -> Iterator[tuple[datetime, str, int, int, str]]:
    """Yield eligible rows and verify that this account file is sorted."""
    previous: datetime | None = None
    with path.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            stats["input_rows"] += 1
            raw = line.rstrip("\r\n")
            if not raw:
                raise ValueError(f"blank line in {path} at line {line_number}")
            try:
                tweet = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid JSON in {path} at line {line_number}: {error}"
                ) from error
            created_raw = (tweet.get("legacy") or {}).get("created_at")
            if not created_raw:
                raise ValueError(
                    f"missing legacy.created_at in {path} at line {line_number}"
                )
            try:
                created_at = datetime.strptime(created_raw, TWEET_FMT)
            except ValueError as error:
                raise ValueError(
                    f"invalid legacy.created_at in {path} at line {line_number}: "
                    f"{created_raw!r}"
                ) from error
            if previous is not None and created_at < previous:
                raise ValueError(f"input is not sorted: {path} line {line_number}")
            previous = created_at
            if not any(start <= created_at.date() <= end for start, end in windows):
                stats["excluded_outside_windows"] += 1
                continue
            tweet_id = str(
                tweet.get("rest_id")
                or (tweet.get("legacy") or {}).get("id_str")
                or ""
            )
            if not tweet_id:
                raise ValueError(f"missing tweet ID in {path} at line {line_number}")
            stats["eligible_rows"] += 1
            yield created_at, tweet_id, source_index, line_number, raw


def merge(
    inputs: list[Path],
    output: Path,
    dataset: Path,
    per_file_windows: list[list[tuple[date, date]]],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Atomically write a sorted, globally deduplicated merge and its stats."""
    if output.exists() and not overwrite:
        raise FileExistsError(f"output already exists (use --overwrite): {output}")
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError("output must not overwrite an input file")
    output.parent.mkdir(parents=True, exist_ok=True)

    files = [_file_stats(path, dataset) for path in inputs]
    iterators: list[Iterator[tuple[datetime, str, int, int, str]]] = []
    heap: list[tuple[datetime, str, int, int, str]] = []
    seen: dict[str, tuple[str, int]] = {}
    duplicate_examples: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    output_rows = 0
    first: datetime | None = None
    last: datetime | None = None
    temporary_name: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=output.name + ".", suffix=".tmp", delete=False,
        ) as out:
            temporary_name = out.name
            for index, path in enumerate(inputs):
                iterator = iter_tweets(path, index, per_file_windows[index], files[index])
                iterators.append(iterator)
                try:
                    heapq.heappush(heap, next(iterator))
                except StopIteration:
                    pass

            while heap:
                created_at, tweet_id, index, line_number, raw = heapq.heappop(heap)
                if tweet_id in seen:
                    files[index]["duplicate_rows"] += 1
                    if len(duplicate_examples) < 20:
                        kept_path, kept_line = seen[tweet_id]
                        duplicate_examples.append({
                            "tweet_id": tweet_id,
                            "kept_from": kept_path,
                            "kept_line": kept_line,
                            "removed_from": files[index]["path"],
                            "removed_line": line_number,
                        })
                else:
                    if last is not None and created_at < last:
                        raise RuntimeError("merged output is not chronologically ordered")
                    seen[tweet_id] = (files[index]["path"], line_number)
                    encoded = (raw + "\n").encode("utf-8")
                    out.write(raw + "\n")
                    digest.update(encoded)
                    files[index]["output_rows"] += 1
                    output_rows += 1
                    first = first or created_at
                    last = created_at
                try:
                    heapq.heappush(heap, next(iterators[index]))
                except StopIteration:
                    pass
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary_name, output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    input_rows = sum(item["input_rows"] for item in files)
    excluded = sum(item["excluded_outside_windows"] for item in files)
    eligible = sum(item["eligible_rows"] for item in files)
    duplicates = sum(item["duplicate_rows"] for item in files)
    if input_rows != excluded + eligible or eligible != duplicates + output_rows:
        raise RuntimeError("validation failed: row counts do not reconcile")
    return {
        "status": "valid",
        "output": str(output),
        "output_bytes": output.stat().st_size,
        "sha256": digest.hexdigest(),
        "selected_files": len(inputs),
        "input_rows": input_rows,
        "excluded_outside_windows": excluded,
        "eligible_rows": eligible,
        "duplicate_rows": duplicates,
        "output_rows": output_rows,
        "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
        "duplicate_examples": duplicate_examples,
        "files": files,
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/dataset_server"))
    parser.add_argument(
        "--out", type=Path, default=Path("data/combined/all_tweets_sorted.ndjson")
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    all_windows = load_windows(root)
    inputs, skipped = select_inputs(args.dataset, all_windows)
    if not inputs:
        parser.error(f"no frame-listed NDJSON files found under {args.dataset}")
    print(f"selected {len(inputs)} frame-listed files from {args.dataset}")
    for path in skipped:
        print(f"skipping {path.parent.name}/{path.name}: not in the CSV")
    if args.inventory_only:
        return

    report = merge(
        inputs,
        args.out,
        args.dataset,
        [all_windows[(path.parent.name, path.stem.lower())] for path in inputs],
        overwrite=args.overwrite,
    )
    report["skipped_unlisted_files"] = [
        str(path.relative_to(args.dataset)) for path in skipped
    ]
    report_path = args.report or args.out.with_suffix(args.out.suffix + ".validation.json")
    write_json_atomic(report_path, report)
    print(
        f"wrote {report['output_rows']:,} unique tweets to {args.out} "
        f"({report['excluded_outside_windows']:,} date-excluded; "
        f"{report['duplicate_rows']:,} duplicates)"
    )
    print(
        f"range {report['first_timestamp']} through {report['last_timestamp']}; "
        f"validation report: {report_path}"
    )


if __name__ == "__main__":
    main()
