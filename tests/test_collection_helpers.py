import asyncio
import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cli_prompts
import tweet_collection
import collect_missing
import export_dataset
import read_account_csv


class CollectionHelpersTests(unittest.TestCase):
    def test_frame_preserves_separate_spells_and_rejects_reversed_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / "accounts.csv"
            frame.write_text("party,handle,start,end,kind\nA,@Alice,2020-01-01,2020-01-02,leader\nB,Alice,2020-03-01,2020-03-02,leader\nA,PartyA,2020-01-01,2020-01-01,party\n")
            jobs = read_account_csv.build_jobs(frame)
            self.assertEqual([(j["party"], j["handle"], j["kind"]) for j in jobs], [
                ("A", "Alice", "leader"), ("B", "Alice", "leader"), ("A", "PartyA", "party")])
            self.assertEqual(jobs[-1]["since"], jobs[-1]["until"])
            frame.write_text("party,handle,start,end\nA,PartyA,2020-01-02,2020-01-01\n")
            with self.assertRaises(ValueError):
                read_account_csv.build_jobs(frame)

    def test_frame_needs_no_leader_or_party_type(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "accounts.csv"
            frame.write_text("handle,party,start,end\nAlice,A,2020-01-01,2020-01-01\n")
            self.assertEqual(read_account_csv.build_jobs(frame)[0]["handle"], "Alice")
            frame.write_text("handle\nAlice\n")
            with self.assertRaisesRegex(ValueError, "required columns"):
                read_account_csv.build_jobs(frame)

    def test_months_cover_half_open_window_and_year_boundary(self):
        self.assertEqual(list(read_account_csv.month_chunks(date(2020, 12, 31), date(2021, 2, 1))), [
            (date(2020, 12, 1), date(2021, 1, 1)),
            (date(2021, 1, 1), date(2021, 2, 1)),
        ])

    def test_paths_do_not_create_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing"
            db, ndjson = read_account_csv.job_paths({"party": "A", "handle": "Alice"}, root)
            self.assertEqual(db, root / "A/Alice.sqlite")
            self.assertEqual(ndjson, root / "A/Alice.ndjson")
            self.assertFalse(root.exists())

    def test_blank_prompt_can_finish(self):
        with patch("builtins.input", return_value=""):
            self.assertEqual(cli_prompts.ask("Username", default=""), "")

    def test_exports_preserve_formats_order_and_source_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "tweets.sqlite"
            con = sqlite3.connect(db)
            tweet_collection.init_db(con)
            objects = [
                {"rest_id": "2", "legacy": {"created_at": "Thu Jan 02 12:00:00 +0000 2020", "user_id_str": "7", "full_text": "raw\u2028text"}},
                {"id": 1, "date": "2020-01-01T12:00:00+00:00", "rawContent": "parsed"},
            ]
            for i, obj in enumerate(objects):
                con.execute("INSERT INTO tweets VALUES (?, ?, ?, ?, ?, ?, ?)", (2-i, "Alice", 7, f"2020-01-0{2-i}", "search", json.dumps(obj, ensure_ascii=False), "today"))
            con.commit()
            con.close()
            before = db.read_bytes()
            self.assertEqual(export_dataset.export_ndjson(db, root / "out.ndjson"), 2)
            rows = [json.loads(line) for line in (root / "out.ndjson").read_text().split("\n") if line]
            self.assertEqual(rows, list(reversed(objects)))
            self.assertEqual(export_dataset.flatten_db(db, root / "out.csv"), 2)
            with (root / "out.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual({r['text'] for r in rows}, {"parsed", "raw\u2028text"})
            self.assertEqual(db.read_bytes(), before)

    def test_repair_dry_run_does_not_create_gone_table(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {"party": "A", "handle": "Alice", "since": date(2020, 1, 1), "until": date(2020, 1, 2)}
            db = root / "tweets.sqlite"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE tweets (tweet_id INTEGER, user_id INTEGER)")
            con.execute("INSERT INTO tweets VALUES (1, 7)")
            con.commit()
            con.close()
            before = db.read_bytes()
            with patch.object(collect_missing, "job_paths", return_value=(db, root / "out.ndjson")), patch.object(collect_missing, "reference_files", return_value=[root / "reference.ndjson"]), patch.object(collect_missing, "reference_ids", return_value={"1", "2"}):
                result = asyncio.run(collect_missing.fetch_target(None, job, str(root), None, True, False))
            self.assertEqual(result["ids"], ["2"])
            self.assertEqual(db.read_bytes(), before)
            self.assertFalse((root / "out.ndjson").exists())

    def test_author_and_inclusive_end_conversion_remain_exact(self):
        obj = {"legacy": {"user_id_str": "7", "created_at": "Thu Jan 02 23:59:59 +0000 2020"}}
        self.assertTrue(tweet_collection.raw_tweet_is_eligible(obj, 7, date(2020, 1, 1), date(2020, 1, 3)))
        self.assertFalse(tweet_collection.raw_tweet_is_eligible(obj, 8, date(2020, 1, 1), date(2020, 1, 3)))
        self.assertFalse(tweet_collection.raw_tweet_is_eligible(obj, 7, date(2020, 1, 1), date(2020, 1, 2)))


if __name__ == "__main__":
    unittest.main()
