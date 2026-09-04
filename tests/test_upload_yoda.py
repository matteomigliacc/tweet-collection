import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "upload_yoda.sh"


class YodaUploadTests(unittest.TestCase):
    def run_upload(self, dry_run=False, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dataset with spaces"
            for name in ("Party One/leader.sqlite", "Party One/leader.ndjson",
                         "Party One/private.log", "Party One/nested/old.sqlite",
                         "Party Two/second.ndjson", "backup_old/Party/old.sqlite"):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            log = root / "calls.jsonl"
            client = root / "mock-ibridges"
            client.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["MOCK_LOG"] + ".contact").touch()
if args[0] == "sync":
    files = sorted(str(p.relative_to(args[1])) for p in Path(args[1]).rglob("*") if p.is_file())
    with open(os.environ["MOCK_LOG"], "a") as out:
        out.write(json.dumps({"destination": args[2], "files": files}) + "\\n")
    if os.environ.get("MOCK_FAIL") == "1":
        sys.exit(7)
''')
            client.chmod(0o755)
            args = ["/bin/bash", str(SCRIPT), "--source", str(source) + "/",
                    "--dest", "irods:~/collection", "--ibridges", str(client)]
            if dry_run:
                args.append("--dry-run")
            result = subprocess.run(args, capture_output=True, text=True, env={
                **os.environ, "MOCK_LOG": str(log), "MOCK_FAIL": str(int(fail)),
                "YODA_LOCK_FILE": str(root / "upload.lock"),
            })
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            if dry_run:
                self.assertFalse(Path(str(log) + ".contact").exists())
            self.assertEqual(list(source.glob(".yoda-upload.*")), [])
            self.assertTrue((source / "Party One/private.log").exists())
            return result, calls

    def test_only_selected_files_are_synced(self):
        result, calls = self.run_upload()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            {"destination": "irods:~/collection/Party One", "files": ["leader.ndjson", "leader.sqlite"]},
            {"destination": "irods:~/collection/Party Two", "files": ["second.ndjson"]},
        ])

    def test_dry_run_does_not_contact_client(self):
        result, calls = self.run_upload(dry_run=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertNotIn("private.log", result.stdout)
        self.assertNotIn("old.sqlite", result.stdout)

    def test_failure_cleans_up_staging_and_preserves_exit_code(self):
        result, calls = self.run_upload(fail=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
