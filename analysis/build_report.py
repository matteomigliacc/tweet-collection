#!/usr/bin/env python3
"""Rebuild the corpus-vs-professors report page from fresh comparison output.

    rsync ... root@192.168.1.106:/opt/populism-scraping/data/corpus/ data/corpus_server/
    python3 analysis/compare_corpus.py > analysis/corpus_recall.json
    python3 analysis/build_report.py <template.html> [out.html]

The template carries the whole design; this only swaps the two generated lines
(`const DATA = [...]` and the `__GENERATED__` timestamp), so editing the page by
hand and rebuilding it are safe in either order. Writes in place when `out` is
omitted, which is what the artifact republish expects.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECALL = os.path.join(ROOT, "analysis", "corpus_recall.json")

KEEP = ("handle", "party", "corpus", "ref", "shared", "missing", "extra",
        "recall", "miss_years", "miss_kind", "cat")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    template = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else template

    report = json.load(open(RECALL))
    rows = [{k: r[k] for k in KEEP} for r in report["rows"]]
    html = open(template).read()

    data_line = "const DATA = " + json.dumps(rows, ensure_ascii=False) + ";"
    html, n = re.subn(r"^const DATA = .*?;$", lambda _: data_line, html,
                      count=1, flags=re.M | re.S)
    if not n:
        sys.exit(f"{template}: no `const DATA = ...;` line to replace")
    html = re.sub(r'"(?:__GENERATED__|[\d]{4}-[\d]{2}-[\d]{2}T[\d:]+)"',
                  '"' + report["generated"] + '"', html, count=1)

    open(out, "w").write(html)
    print(f"{out}: {len(rows)} handles, generated {report['generated']}")
    for note in report["notes"]:
        print("  note:", note)


if __name__ == "__main__":
    main()
