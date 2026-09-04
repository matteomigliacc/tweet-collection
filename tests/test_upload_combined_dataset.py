import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upload_streaming",
    ROOT / "analysis" / "upload_combined_dataset.py",
)
upload_streaming = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(upload_streaming)


class StreamingUploadTests(unittest.TestCase):
    def test_parts_are_complete_lines_and_preserve_order(self) -> None:
        tweets = [
            {
                "rest_id": str(index),
                "legacy": {"created_at": "Wed Jan 01 00:00:00 +0000 2020"},
            }
            for index in range(1, 4)
        ]
        tweets[1]["quoted_status_result"] = {
            "result": {"__typename": "TweetUnavailable"}
        }
        collected_ms = 123456789
        first = upload_streaming.tweet_envelope(
            copy.deepcopy(tweets[0]), 0, collected_ms
        )
        second = upload_streaming.tweet_envelope(
            copy.deepcopy(tweets[1]), 1, collected_ms
        )
        limit = len(first) + len(second)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.ndjson"
            source.write_text(
                "".join(json.dumps(tweet) + "\n" for tweet in tweets),
                encoding="utf-8",
            )
            metadata = []
            parsed = []
            for part in upload_streaming.iter_prepared_parts(
                source, root / "parts", limit, collected_ms
            ):
                path = Path(part["path"])
                raw = path.read_bytes()
                self.assertEqual(len(raw), part["bytes"])
                self.assertTrue(raw.endswith(b"\n"))
                lines = raw.decode().splitlines()
                self.assertEqual(len(lines), part["rows"])
                parsed.extend(json.loads(line) for line in lines)
                metadata.append(part)
                path.unlink()

        self.assertEqual(len(metadata), 2)
        self.assertEqual([item["item_id"] for item in parsed], ["1", "2", "3"])
        self.assertEqual([item["nav_index"] for item in parsed], [0, 1, 2])
        self.assertIn("id", parsed[0]["data"])
        self.assertEqual(parsed[0]["data"]["source"], "")
        self.assertNotIn("quoted_status_result", parsed[1]["data"])

    def test_oversized_single_tweet_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.ndjson"
            source.write_text(
                json.dumps({"rest_id": "1", "legacy": {}, "large": "x" * 1000}) + "\n"
            )
            with self.assertRaisesRegex(ValueError, "exceeds chunk limit"):
                list(upload_streaming.iter_prepared_parts(source, root / "parts", 10, 1))


if __name__ == "__main__":
    unittest.main()
