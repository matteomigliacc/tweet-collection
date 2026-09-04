#!/usr/bin/env python3
"""Render the dataset comparison page from analysis/dataset_recall.json."""
import argparse
import html
import json
import os
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECALL = os.path.join(ROOT, "analysis", "dataset_recall.json")

CSS = """
:root{
  --paper:#f4f6f7; --card:#fff; --ink:#151a1e; --ink-2:#4d5761; --ink-3:#7b858e;
  --rule:#dde2e6; --rule-2:#eaeef0;
  --held:#2f6f8f; --extra:#3f8f6a; --miss:#b8503f; --dead:#a7b0b7;
  --held-s:#e6eff4; --extra-s:#e5f1eb; --miss-s:#f7e6e3;
}
@media (prefers-color-scheme:dark){:root{
  --paper:#101519; --card:#161c21; --ink:#e7ebee; --ink-2:#a6b1ba; --ink-3:#78838c;
  --rule:#28323a; --rule-2:#202930;
  --held:#68aecd; --extra:#69bd95; --miss:#e08573; --dead:#4c565e;
  --held-s:#182a34; --extra-s:#162b23; --miss-s:#32201c;
}}
:root[data-theme="dark"]{
  --paper:#101519; --card:#161c21; --ink:#e7ebee; --ink-2:#a6b1ba; --ink-3:#78838c;
  --rule:#28323a; --rule-2:#202930;
  --held:#68aecd; --extra:#69bd95; --miss:#e08573; --dead:#4c565e;
  --held-s:#182a34; --extra-s:#162b23; --miss-s:#32201c;
}
:root[data-theme="light"]{
  --paper:#f4f6f7; --card:#fff; --ink:#151a1e; --ink-2:#4d5761; --ink-3:#7b858e;
  --rule:#dde2e6; --rule-2:#eaeef0;
  --held:#2f6f8f; --extra:#3f8f6a; --miss:#b8503f; --dead:#a7b0b7;
  --held-s:#e6eff4; --extra-s:#e5f1eb; --miss-s:#f7e6e3;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:15px;line-height:1.5}
.wrap{max-width:1000px;margin:0 auto;padding:40px 20px 72px;display:flex;flex-direction:column;gap:34px}
.num{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
h1{margin:0;font-size:22px;font-weight:600;letter-spacing:-.01em}
h2{margin:0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);font-weight:600}
p{margin:0}
header{display:flex;flex-direction:column;gap:5px}
.stamp{color:var(--ink-3);font-size:12.5px}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule);border-radius:3px;overflow:hidden}
.stat{background:var(--card);padding:16px 18px;display:flex;flex-direction:column;gap:3px}
.stat .k{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.stat .v{font-size:27px;line-height:1.1;letter-spacing:-.02em}
.stat .n{font-size:12.5px;color:var(--ink-2)}

.key{display:flex;flex-wrap:wrap;gap:16px;font-size:12.5px;color:var(--ink-2)}
.key span{display:flex;align-items:center;gap:6px}
.key i{width:11px;height:11px;border-radius:2px;display:block}

section{display:flex;flex-direction:column;gap:12px}
.chart{background:var(--card);border:1px solid var(--rule);border-radius:3px;
  padding:14px 16px;display:flex;flex-direction:column;gap:0}
.row{display:grid;grid-template-columns:132px 1fr 118px;gap:12px;align-items:center;
  padding:4px 0;border-bottom:1px solid var(--rule-2)}
.row:last-child{border-bottom:0}
.row .nm{font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .fig{font-size:12px;text-align:right;color:var(--ink-2);white-space:nowrap}
.bar{display:flex;height:13px;border-radius:2px;overflow:hidden;background:transparent}
.bar i{display:block;height:100%}
.bar .h{background:var(--held)}
.bar .e{background:var(--extra)}
.bar .m{background:var(--miss)}
.bar .d{background:var(--dead)}

.tblwrap{overflow-x:auto;border:1px solid var(--rule);border-radius:3px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:640px;font-size:13.5px}
thead th{text-align:left;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink-3);font-weight:600;padding:11px 12px;border-bottom:1px solid var(--rule);white-space:nowrap}
th.r,td.r{text-align:right}
tbody td{padding:8px 12px;border-bottom:1px solid var(--rule-2);white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
td.hd{font-weight:600}
td.pt{color:var(--ink-2)}
.pos{color:var(--extra)}
.neg{color:var(--miss)}
.nil{color:var(--ink-3)}
.notes{color:var(--ink-2);font-size:13px;display:flex;flex-direction:column;gap:6px}
@media (max-width:600px){
  .row{grid-template-columns:100px 1fr;gap:8px}
  .row .fig{grid-column:1/-1;text-align:left}
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default=os.path.join(ROOT, "analysis", "dataset-report.html"))
    parser.add_argument("--input", default=RECALL, help="recall JSON to render")
    args = parser.parse_args()
    out = args.output
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = sorted(report["rows"], key=lambda r: -max(r["dataset"], r["ref"]))

    ref = sum(r["ref"] for r in rows)
    shared = sum(r["shared"] for r in rows)
    dataset = sum(r["dataset"] for r in rows)
    extra = sum(r["extra"] for r in rows)
    own_missing = sum(r["own_missing"] for r in rows)
    foreign = sum(r["foreign_missing"] for r in rows)
    scale = max((max(r["dataset"], r["ref"]) + r["own_missing"] for r in rows), default=1) or 1

    bars = []
    for r in rows:
        # One bar per account: what both sides hold, what only we hold, what only
        # they hold. Widths share a single scale so account size stays comparable.
        seg = []
        for cls, n in (("h", r["shared"]), ("e", r["extra"]),
                       ("m", r["own_missing"]), ("d", r["foreign_missing"])):
            if n:
                seg.append(f'<i class="{cls}" style="width:{100 * n / scale:.3f}%"></i>')
        gain = (f'<span class="pos">+{r["extra"]:,}</span>' if r["extra"]
                else '<span class="nil">+0</span>')
        loss = (f' <span class="neg">−{r["own_missing"]:,}</span>'
                if r["own_missing"] else "")
        bars.append(
            '<div class="row">'
            f'<div class="nm">@{html.escape(r["handle"])}</div>'
            f'<div class="bar">{"".join(seg)}</div>'
            f'<div class="fig num">{r["dataset"]:,} {gain}{loss}</div>'
            "</div>"
        )

    trs = []
    for r in sorted(rows, key=lambda r: -r["extra"]):
        gain = (f'<span class="pos">+{r["extra"]:,}</span>' if r["extra"]
                else '<span class="nil">—</span>')
        miss = (f'<span class="neg">{r["own_missing"]:,}</span>' if r["own_missing"]
                else '<span class="nil">—</span>')
        trs.append(
            "<tr>"
            f'<td class="hd">@{html.escape(r["handle"])}</td>'
            f'<td class="pt">{html.escape(r["party"])}</td>'
            f'<td class="r num">{r["ref"]:,}</td>'
            f'<td class="r num">{r["dataset"]:,}</td>'
            f'<td class="r num">{gain}</td>'
            f'<td class="r num">{miss}</td>'
            f'<td class="r num">{r["own_recall"]:.1f}%</td>'
            "</tr>"
        )

    other_section = ""
    if "other_authors" in report:
        other = report["other_authors"]
        other_rows = "".join(
            f'<tr><td>@{html.escape(row["handle"])}</td>'
            f'<td class="r num">{row["total"]:,}</td>'
            f'<td class="r num">{row["other"]:,}</td>'
            f'<td class="r num">{row["other_in_dataset"]:,}</td></tr>'
            for row in other["rows"])
        other_section = f"""<section><h2>Other authors — all reference dates</h2>
<p>This section includes dates outside the CSV windows and handles outside the account list.
It counts each tweet ID once per handle. A tweet found under several handles counts again.</p>
<p>Tweet types describe the tweets themselves; they do not tell us why the reference export included them.</p>
<div class="tblwrap"><table><thead><tr><th>Handle</th><th>Reference tweets</th>
<th>Other authors' tweets</th><th>Also in dataset</th></tr></thead><tbody>{other_rows}</tbody></table></div></section>"""
    notes = "".join(f"<p>{html.escape(note)}</p>" for note in report.get("notes", []))
    empty = "<p>No reference tweets matched the account dates.</p>" if not rows else ""
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tweet dataset compared with reference exports</title>
<style>{CSS}</style>
<div class="wrap">

<header>
  <h1>Collected dataset compared with reference exports</h1>
  <p class="stamp num">{len(rows)} accounts · dates from the account CSV · generated {html.escape(report["generated"].replace("T", " "))}</p>
</header>

{empty}
<div class="stats">
  <div class="stat"><span class="k">Reference tweets</span>
    <span class="v num">{ref:,}</span><span class="n">within the account dates</span></div>
  <div class="stat"><span class="k">Our dataset</span>
    <span class="v num">{dataset:,}</span><span class="n">+{100 * extra / ref if ref else 0:.1f}% over reference</span></div>
  <div class="stat"><span class="k">Tweets we added</span>
    <span class="v num" style="color:var(--extra)">+{extra:,}</span><span class="n">not in their files</span></div>
  <div class="stat"><span class="k">Theirs we lack</span>
    <span class="v num" style="color:var(--miss)">{own_missing:,}</span><span class="n">own tweets missing from our dataset</span></div>
</div>

<div class="key">
  <span><i style="background:var(--held)"></i>both ({shared:,})</span>
  <span><i style="background:var(--extra)"></i>ours only ({extra:,})</span>
  <span><i style="background:var(--miss)"></i>theirs only ({own_missing:,})</span>
  <span><i style="background:var(--dead)"></i>missing tweets by other authors ({foreign:,})</span>
</div>

<section>
  <h2>Per account, shared scale</h2>
  <div class="chart">
{chr(10).join(bars)}
  </div>
</section>

<section>
  <h2>By tweets added</h2>
  <div class="tblwrap"><table>
    <thead><tr>
      <th>Handle</th><th>Party</th><th class="r">Reference</th><th class="r">Ours</th>
      <th class="r">Added</th><th class="r">Missing</th><th class="r">Adjusted recall</th>
    </tr></thead>
    <tbody>
{chr(10).join(trs)}
    </tbody>
  </table></div>
</section>

<section>
  <h2>Method</h2>
  <div class="notes">
    <p>Matched on exact <span class="num">rest_id</span>, each account clipped to its own tenure or seat window.</p>
    <p>Missing counts tweets written by the named account. Adjusted recall is shared tweets divided by shared tweets plus missing tweets by that account. Shared tweets can include other authors. Raw recall in the JSON is shared tweets divided by all reference tweets within the dates.</p>
    <p>These percentages measure overlap with the reference exports, not whether every tweet on X was collected.</p>
    {notes}
  </div>
</section>

{other_section}
</div></html>
"""
    Path(out).write_text(page, encoding="utf-8")
    print(f"{out}: {len(rows)} handles, dataset {dataset:,} vs reference {ref:,} (+{extra:,})")


if __name__ == "__main__":
    main()
