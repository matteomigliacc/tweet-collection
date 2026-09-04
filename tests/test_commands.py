import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scraper_commands", ROOT / "collection.py")
collection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collection)


class CommandTests(unittest.TestCase):
    def test_no_command_only_shows_help(self):
        with patch.object(collection.subprocess, "call") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(collection.main([]), 0)
        run.assert_not_called()

    def test_interactive_help_does_not_start_work(self):
        with patch.object(collection.subprocess, "call") as run, contextlib.redirect_stdout(io.StringIO()):
            for command in collection.INTERACTIVE:
                self.assertEqual(collection.main([command, "--help"]), 0)
        run.assert_not_called()

    def test_forwards_arguments_without_shell_and_preserves_exit_code(self):
        with patch.object(collection.subprocess, "call", return_value=7) as run:
            self.assertEqual(collection.main(["combine", "--dataset", "folder with spaces"]), 7)
        run.assert_called_once_with([
            sys.executable, str(ROOT / "analysis/combine_datasets.py"),
            "--dataset", "folder with spaces",
        ])

    def test_every_command_has_a_script(self):
        for script, _ in collection.COMMANDS.values():
            self.assertTrue((ROOT / script).is_file(), script)


if __name__ == "__main__":
    unittest.main()
