#!/usr/bin/env python3
"""Commands for collecting, checking, exporting, and uploading tweets."""

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COMMANDS = {
    "collect": ("src/collect_dataset.py", "Collect accounts listed in the account CSV"),
    "account": ("src/collect_account.py", "Collect one account interactively"),
    "add-logins": ("src/add_logins.py", "Add login accounts interactively"),
    "load-logins": ("src/load_logins.py", "Load and verify login accounts"),
    "export": ("src/export_dataset.py", "Export a tweet database as CSV"),
    "combine": ("analysis/combine_datasets.py", "Filter, sort, and deduplicate the dataset"),
    "upload": ("src/upload_4cat.py", "Upload separate account datasets to 4CAT"),
    "upload-combined": ("analysis/upload_combined_dataset.py", "Upload a combined dataset to 4CAT with resume support"),
    "compare": ("analysis/recall_data.py", "Compare the dataset with reference exports"),
    "report": ("analysis/comparison_report.py", "Render the comparison as HTML"),
    "collect-missing": ("src/collect_missing.py", "Collect missing tweets by ID"),
}
INTERACTIVE = {"account", "add-logins", "load-logins"}


def main(argv=None):
    listing = "\n".join(f"  {name:18} {description}" for name, (_, description) in COMMANDS.items())
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=listing + "\n\nUse COMMAND --help for options. Running without a command does nothing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", nargs="?", choices=COMMANDS, metavar="COMMAND")
    parser.add_argument("args", nargs=argparse.REMAINDER, metavar="OPTIONS", help="options passed to the command")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    script, description = COMMANDS[args.command]
    if args.command in INTERACTIVE and args.args:
        if args.args in (["--help"], ["-h"]):
            print(f"{description}. Run without options.")
            return 0
        parser.error(f"{args.command} does not accept options")
    return subprocess.call([sys.executable, str(ROOT / script), *args.args])


if __name__ == "__main__":
    raise SystemExit(main())
