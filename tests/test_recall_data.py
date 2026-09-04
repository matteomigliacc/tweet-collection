"""Check comparison rules with small local files."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def tweet(tid, author, year=2020):
    return {"rest_id": str(tid), "legacy": {
        "created_at": f"Wed Jan 01 12:00:00 +0000 {year}",
        "user_id_str": author},
        "core": {"user_results": {"result": {"legacy": {"screen_name": author}}}}}


class ComparisonTests(unittest.TestCase):
    def run_report(self, empty=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reference, dataset = base / "reference", base / "dataset"
            reference.mkdir()
            (dataset / "Party").mkdir(parents=True)
            accounts = base / "accounts.csv"
            accounts.write_text("handle,party,start,end\nalice,Party,2020-01-01,2020-12-31\n")
            # Duplicate IDs across exports count once. Out-of-window other authors
            # belong only to the optional all-date section.
            rows = [] if empty else [tweet(1, "alice"), tweet(2, "alice"),
                                     tweet(3, "bob"), tweet(4, "bob", 2019)]
            for name in ("alice-first.ndjson", "alice-second.ndjson"):
                (reference / name).write_text("".join(json.dumps(t) + "\n" for t in rows))
            (dataset / "Party" / "alice.ndjson").write_text(
                "" if empty else json.dumps(tweet(1, "alice")) + "\n")
            output = base / "result.json"
            subprocess.run([sys.executable, str(ROOT / "analysis/recall_data.py"),
                            "--reference", str(reference), "--dataset", str(dataset),
                            "--accounts", str(accounts), "--output", str(output),
                            "--other-authors"], check=True, capture_output=True)
            page = base / "report.html"
            subprocess.run([sys.executable, str(ROOT / "analysis/comparison_report.py"),
                            str(page), "--input", str(output)], check=True, capture_output=True)
            return json.loads(output.read_text()), page.read_text()

    def test_dates_duplicates_and_missing_authors(self):
        report, page = self.run_report()
        row = report["rows"][0]
        self.assertEqual((row["ref"], row["shared"], row["own_missing"], row["foreign_missing"]),
                         (3, 1, 1, 1))
        self.assertEqual((row["recall"], row["own_recall"]), (33.3, 50.0))
        other = report["other_authors"]
        self.assertEqual((other["total"], other["total_other"]), (4, 2))
        self.assertEqual(other["rows"][0]["years"], {"2019": 1, "2020": 1})
        self.assertIn("Other authors — all reference dates", page)
        self.assertIn("Shared tweets can include other authors", page)

    def test_empty_report(self):
        report, page = self.run_report(empty=True)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["other_authors"]["total"], 0)
        self.assertIn("No reference tweets matched", page)


if __name__ == "__main__":
    unittest.main()
