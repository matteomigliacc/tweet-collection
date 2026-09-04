import csv
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "merge_ndjson", ROOT / "analysis" / "combine_datasets.py"
)
combine_datasets = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(combine_datasets)


def tweet(tweet_id: str, timestamp: str, text: str = "") -> dict:
    dt = datetime.fromisoformat(timestamp).astimezone(timezone.utc)
    return {
        "rest_id": tweet_id,
        "legacy": {
            "id_str": tweet_id,
            "created_at": dt.strftime(combine_datasets.TWEET_FMT),
            "full_text": text,
        },
    }


class MergeNdjsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "frame").mkdir()
        for party in ("PartyA", "PartyB", "Orphan"):
            (self.root / "data" / party).mkdir(parents=True)

        with (self.root / "frame" / "accounts.csv").open(
            "w", newline="", encoding="utf-8"
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=["party", "handle", "start", "end"])
            writer.writeheader()
            writer.writerows([
                {"party": "PartyA", "handle": "Alice", "start": "2020-01-02", "end": "2020-01-03"},
                {"party": "PartyB", "handle": "Bob", "start": "2020-01-01", "end": "2020-01-04"},
            ])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_rows(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def test_filters_sorts_deduplicates_and_reports(self) -> None:
        alice = self.root / "data" / "PartyA" / "Alice.ndjson"
        bob = self.root / "data" / "PartyB" / "Bob.ndjson"
        orphan = self.root / "data" / "Orphan" / "Nobody.ndjson"
        self.write_rows(alice, [
            tweet("1", "2020-01-01T23:59:59+00:00", "outside"),
            tweet("3", "2020-01-02T12:00:00+00:00", "alice"),
            tweet("5", "2020-01-03T23:59:59+00:00", "inclusive end"),
        ])
        self.write_rows(bob, [
            tweet("2", "2020-01-01T00:00:00+00:00", "inclusive start"),
            tweet("3", "2020-01-02T12:00:00+00:00", "duplicate"),
            tweet("4", "2020-01-02T12:00:00+00:00", "tie"),
        ])
        self.write_rows(orphan, [tweet("99", "2020-01-02T00:00:00+00:00")])

        windows = combine_datasets.load_windows(self.root)
        inputs, skipped = combine_datasets.select_inputs(self.root / "data", windows)
        self.assertEqual([path.name for path in inputs], ["Alice.ndjson", "Bob.ndjson"])
        self.assertEqual([path.name for path in skipped], ["Nobody.ndjson"])

        output = self.root / "combined.ndjson"
        report = combine_datasets.merge(
            inputs,
            output,
            self.root / "data",
            [windows[(path.parent.name, path.stem.lower())] for path in inputs],
        )
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual([row["rest_id"] for row in rows], ["2", "3", "4", "5"])
        self.assertEqual(report["input_rows"], 6)
        self.assertEqual(report["excluded_outside_windows"], 1)
        self.assertEqual(report["duplicate_rows"], 1)
        self.assertEqual(report["output_rows"], 4)
        self.assertEqual(report["first_timestamp"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(report["last_timestamp"], "2020-01-03T23:59:59+00:00")

    def test_unsorted_input_does_not_publish_output(self) -> None:
        alice = self.root / "data" / "PartyA" / "Alice.ndjson"
        self.write_rows(alice, [
            tweet("2", "2020-01-03T00:00:00+00:00"),
            tweet("1", "2020-01-02T00:00:00+00:00"),
        ])
        windows = combine_datasets.load_windows(self.root)
        output = self.root / "combined.ndjson"
        with self.assertRaisesRegex(ValueError, "input is not sorted"):
            combine_datasets.merge(
                [alice], output, self.root / "data", [windows[("PartyA", "alice")]]
            )
        self.assertFalse(output.exists())

    def test_missing_timestamp_fails(self) -> None:
        alice = self.root / "data" / "PartyA" / "Alice.ndjson"
        self.write_rows(alice, [{"rest_id": "1", "legacy": {}}])
        windows = combine_datasets.load_windows(self.root)
        with self.assertRaisesRegex(ValueError, "missing legacy.created_at"):
            combine_datasets.merge(
                [alice], self.root / "combined.ndjson", self.root / "data",
                [windows[("PartyA", "alice")]],
            )


if __name__ == "__main__":
    unittest.main()
