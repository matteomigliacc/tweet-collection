import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import client_4cat
import upload_4cat


class FourcatTests(unittest.TestCase):
    def test_streaming_envelope_keeps_resume_hash_bytes(self):
        tweet = {"rest_id": "42", "legacy": {"full_text": "café"}}
        expected = {
            "nav_index": 3,
            "item_id": "42",
            "timestamp_collected": 123,
            "last_updated": 123,
            "source_platform": "twitter.com",
            "source_platform_url": "https://x.com",
            "source_url": "https://x.com/search",
            "user_agent": "populism-scraper upload_combined_4cat_streaming.py",
            "data": {**tweet, "id": "VHdlZXQ6NDI=", "source": ""},
        }
        expected_bytes = (json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.assertEqual(client_4cat.tweet_envelope(copy.deepcopy(tweet), 3, 123), expected_bytes)

    def test_normalization_preserves_text_and_available_quotes(self):
        tweet = {
            "rest_id": "42",
            "legacy": {"full_text": "line\u2028separator\u2029and café"},
            "quoted_status_result": {"result": {"legacy": {"full_text": "quote"}}},
        }
        result = json.loads(client_4cat.tweet_envelope(tweet, 3, 123))
        self.assertEqual(result["data"]["id"], "VHdlZXQ6NDI=")
        self.assertEqual(result["data"]["source"], "")
        self.assertEqual(result["data"]["legacy"]["full_text"], tweet["legacy"]["full_text"])
        self.assertIn("quoted_status_result", result["data"])
        self.assertEqual(result["nav_index"], 3)

    def test_account_export_filters_inclusive_window_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tweets.ndjson"
            rows = [
                {"rest_id": str(day), "legacy": {
                    "created_at": f"Wed Jan {day:02d} 23:59:59 +0000 2020",
                    "full_text": "a\u2028b\u2029c",
                }} for day in (1, 2, 3, 4)
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
            result = upload_4cat.to_zeeschuimer(path, [(date(2020, 1, 2), date(2020, 1, 3))])
            parsed = [json.loads(line) for line in result.split(b"\n") if line]
            self.assertEqual([row["item_id"] for row in parsed], ["2", "3"])
            self.assertEqual(parsed[0]["data"]["legacy"]["full_text"], "a\u2028b\u2029c")

    def test_dry_run_does_not_sync_or_upload_or_read_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "data" / "dataset_server" / "Party"
            dataset.mkdir(parents=True)
            (dataset / "Alice.ndjson").write_text("{}\n")
            with (
                patch.object(upload_4cat, "ROOT", root),
                patch.object(upload_4cat, "load_windows", return_value={("Party", "alice"): []}),
                patch.object(upload_4cat.subprocess, "run", side_effect=AssertionError("sync attempted")),
                patch.object(upload_4cat, "upload_one", side_effect=AssertionError("upload attempted")),
                patch.object(Path, "read_text", side_effect=AssertionError("secret read attempted")),
                patch.object(sys, "argv", ["upload_4cat.py", "--dry-run"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                upload_4cat.main()

    def test_empty_report_renders_without_invented_missing_explanations(self):
        spec = importlib.util.spec_from_file_location("render_report_test", ROOT / "analysis/comparison_report.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            output = Path(temporary) / "report.html"
            report.write_text(json.dumps({"generated": "2026-01-01", "rows": []}))
            with (
                patch.object(module, "RECALL", str(report)),
                patch.object(sys, "argv", ["comparison_report.py", str(output)]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                module.main()
            rendered = output.read_text()
            self.assertIn("0 accounts", rendered)
            self.assertNotIn("524 locked", rendered)


if __name__ == "__main__":
    unittest.main()
