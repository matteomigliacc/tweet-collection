"""Build Teams cards and fallback email summaries for dataset runs."""
from datetime import datetime


TEAMS_STATUS_COLOURS = {
    "complete": "Good", "partial": "Warning", "failed": "Attention",
}


def _totals(records: list[dict], skipped: int, done: int,
            session_secs: float) -> tuple[int, int, str]:
    """Values shared by the email and Teams session summaries."""
    total_records = sum(r["tweets"] for r in records if r.get("tweets"))
    complete_overall = skipped + done
    return total_records, complete_overall, fmt_dur(session_secs)


def fmt_dur(seconds: float) -> str:
    """Format seconds as a compact duration."""
    s = int(round(seconds))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def build_session_email(records: list[dict], done: int, partial: int, failed: int,
                        skipped: int, total: int, done_today: int,
                        session_secs: float) -> tuple[str, str, str]:
    """Compose the plaintext and HTML versions of a run summary."""
    total_records, complete_overall, dur = _totals(
        records, skipped, done, session_secs)
    subject = (f"[collection] {done} new · {partial} partial · {failed} failed — "
               f"{total_records:,} records in {dur}")

    lines = ["Populism collection — session summary",
             "=" * 40,
             f"Duration:  {dur}",
             f"Processed: {len(records)} account(s) this run",
             f"Result:    {done} newly complete, {partial} partial, {failed} failed",
             f"Records:   {total_records:,} across processed datasets",
             f"Overall:   {complete_overall}/{total} targets complete ({done_today} done today)",
             "",
             f"{'Account':22}{'Party':16}{'Records':>8}{'Time':>9}  Status"]
    for r in records:
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "-"
        lines.append(f"@{r['handle']:21}{r['party']:16}{tw:>8}{fmt_dur(r['seconds']):>9}  {r['status']}")
    text = "\n".join(lines)

    badge = {"complete": ("#137333", "#e6f4ea"), "partial": ("#a56300", "#fef7e0"),
             "failed": ("#c5221f", "#fce8e6")}
    rows = ""
    for r in records:
        col, bg = badge.get(r["status"], ("#444", "#eee"))
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "—"
        td = "padding:9px 12px;border-bottom:1px solid #eee;"
        rows += (
            f"<tr>"
            f"<td style='{td}font-family:ui-monospace,Menlo,monospace'>@{r['handle']}</td>"
            f"<td style='{td}'>{r['party']}</td>"
            f"<td style='{td}color:#888;font-size:12px'>{r['since']} → {r['until']}</td>"
            f"<td style='{td}text-align:right;font-variant-numeric:tabular-nums'>{tw}</td>"
            f"<td style='{td}text-align:right;color:#888'>{fmt_dur(r['seconds'])}</td>"
            f"<td style='{td}'><span style='background:{bg};color:{col};padding:2px 9px;"
            f"border-radius:11px;font-size:12px;font-weight:600'>{r['status']}</span></td>"
            f"</tr>")

    def stat(label, value):
        return (f"<td style='padding:14px 16px;text-align:center'>"
                f"<div style='font-size:22px;font-weight:700;color:#111'>{value}</div>"
                f"<div style='font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.04em'>{label}</div></td>")

    html = f"""\
<div style="background:#f4f5f7;padding:24px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)">
    <div style="background:#1f2933;color:#fff;padding:18px 22px;font-size:16px;font-weight:600">
      🐦 Populism collection &middot; session summary
    </div>
    <table style="width:100%;border-collapse:collapse;border-bottom:1px solid #eee">
      <tr>{stat('Records', f'{total_records:,}')}{stat('New datasets', done)}{stat('Duration', dur)}{stat('Complete', f'{complete_overall}/{total}')}</tr>
    </table>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead><tr style="text-align:left;color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.04em">
        <th style="padding:10px 12px">Account</th><th style="padding:10px 12px">Party</th>
        <th style="padding:10px 12px">Window</th><th style="padding:10px 12px;text-align:right">Records</th>
        <th style="padding:10px 12px;text-align:right">Time</th><th style="padding:10px 12px">Status</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div style="padding:14px 22px;color:#999;font-size:12px;background:#fafafa">
      {partial} partial · {failed} failed · {skipped} already complete · {done_today} done today ·
      finished {datetime.now():%Y-%m-%d %H:%M}
    </div>
  </div>
</div>"""
    return subject, text, html


def build_session_card(records: list[dict], done: int, partial: int, failed: int,
                       skipped: int, total: int, done_today: int,
                       session_secs: float, monitor=None) -> dict:
    """Compose the Teams summary for a batch run."""
    total_records, complete_overall, dur = _totals(
        records, skipped, done, session_secs)

    def stat(value, label):
        return {"type": "Column", "width": "stretch", "items": [
            {"type": "TextBlock", "text": str(value), "size": "ExtraLarge",
             "weight": "Bolder", "horizontalAlignment": "Center", "spacing": "None"},
            {"type": "TextBlock", "text": label, "size": "Small", "isSubtle": True,
             "horizontalAlignment": "Center", "spacing": "None"}]}

    rows = []
    for r in records:
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "—"
        rows.append({"type": "ColumnSet", "spacing": "Small", "columns": [
            {"type": "Column", "width": "stretch", "items": [
                {"type": "TextBlock", "spacing": "None", "wrap": True,
                 "text": f"**@{r['handle']}** · {r['party']}"},
                {"type": "TextBlock", "spacing": "None", "size": "Small", "isSubtle": True,
                 "text": f"{r['since']} → {r['until']}"}]},
            {"type": "Column", "width": "auto", "items": [
                {"type": "TextBlock", "spacing": "None", "horizontalAlignment": "Right",
                 "text": f"{tw} records · {fmt_dur(r['seconds'])}"},
                {"type": "TextBlock", "spacing": "None", "size": "Small", "weight": "Bolder",
                 "horizontalAlignment": "Right",
                 "color": TEAMS_STATUS_COLOURS.get(r["status"], "Default"),
                 "text": r["status"]}]}]})

    return {"type": "AdaptiveCard", "version": "1.4",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "msteams": {"width": "Full"},
            "body": [
                {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                 "text": "🐦 Populism collection · session summary"},
                {"type": "ColumnSet", "columns": [
                    stat(f"{total_records:,}", "Records"), stat(done, "New datasets"),
                    stat(dur, "Duration"), stat(f"{complete_overall}/{total}", "Complete")]},
                {"type": "Container", "separator": True, "items": rows},
                *_error_block(monitor),
                {"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True,
                 "separator": True,
                 "text": (f"{partial} partial · {failed} failed · {skipped} already "
                          f"complete · {done_today} done today · "
                          f"finished {datetime.now():%Y-%m-%d %H:%M}")}]}


def _error_block(monitor) -> list[dict]:
    """The backend-error banner for the session card — empty when the run was clean."""
    if monitor is None or not monitor.errors_total:
        return []
    lost = monitor.data_loss_total
    return [{"type": "TextBlock", "separator": True, "wrap": True, "weight": "Bolder",
             "color": "Attention" if lost else "Warning",
             "text": (f"🛑 {monitor.errors_total} backend errors — months may have been "
                      f"checkpointed empty. Verify recall before trusting this run."
                      if lost else
                      f"⚠️ {monitor.errors_total} backend errors during this run.")},
            {"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True,
             "spacing": "None", "text": monitor.final_summary()}]


def build_account_card(rec: dict, index: int, total: int, done_overall: int) -> dict:
    """Compose the optional per-account Teams card."""
    icon = {"complete": "✅", "partial": "🟡", "failed": "❌"}
    tw = f"{rec['tweets']:,}" if rec.get("tweets") is not None else "—"
    return {"type": "AdaptiveCard", "version": "1.4",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "msteams": {"width": "Full"},
            "body": [
                {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True,
                 "text": f"{icon.get(rec['status'], '•')} @{rec['handle']} · {rec['party']}"},
                {"type": "FactSet", "facts": [
                    {"title": "Status", "value": rec["status"]},
                    {"title": "Records", "value": tw},
                    {"title": "Window", "value": f"{rec['since']} → {rec['until']}"},
                    {"title": "Took", "value": fmt_dur(rec["seconds"])}]},
                {"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True,
                 "separator": True,
                 "color": TEAMS_STATUS_COLOURS.get(rec["status"], "Default"),
                 "text": (f"Target {index}/{total} · {done_overall}/{total} complete · "
                          f"{datetime.now():%H:%M}")}]}
