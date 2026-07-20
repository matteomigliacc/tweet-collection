"""Build the notification messages the batch scraper sends (Teams cards + email).

This module contains no scraping logic at all. It takes the numbers that
run_all.py collected during a batch run and turns them into three kinds of
message:

  * build_session_card()  -- one Microsoft Teams "Adaptive Card" summarising a
                             whole run (posted at the end of every session).
  * build_account_card()  -- a small Teams card for ONE finished account
                             (only with run_all.py --notify-each).
  * build_session_email() -- an email version of the session summary, used as
                             a fallback when the Teams webhook fails.

An "Adaptive Card" is just a JSON structure (nested Python dicts/lists) in a
schema Microsoft defines: https://adaptivecards.io. Teams renders it as a
formatted message. Nothing here sends anything — actual delivery lives in
notify.py; these functions only *compose* the payloads, which makes them easy
to tweak or test without spamming the channel.

Vocabulary used throughout (matches run_all.py):
  records   list of dicts, one per account processed this run, with keys
            party, handle, since, until, tweets (int or None), seconds,
            status ('complete' | 'partial' | 'failed').
  done      accounts that became fully complete this run.
  partial   accounts that made progress but aren't finished.
  failed    accounts whose scrape raised a Python exception.
  skipped   accounts that were already complete before the run started.
"""
from datetime import datetime


def fmt_dur(seconds: float) -> str:
    """Format a duration in seconds as '2h 05m 11s' / '5m 03s' / '42s'.

    divmod(a, b) returns (a // b, a % b) in one step: first split total
    seconds into hours + remainder, then the remainder into minutes + seconds.
    """
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
    """Compose (subject, plaintext, html) for one batch-run summary email.

    Emails carry two bodies: a plain-text version (always readable) and an
    HTML version with the same content styled as a small dashboard. The mail
    client picks whichever it can display.
    """
    # Sum tweets over the records that actually have a count. `if r.get("tweets")`
    # skips both missing keys and None (a failed account never got a count).
    total_tweets = sum(r["tweets"] for r in records if r.get("tweets"))
    complete_overall = skipped + done
    dur = fmt_dur(session_secs)
    subject = (f"[scraper] {done} new · {partial} partial · {failed} failed — "
               f"{total_tweets:,} tweets in {dur}")

    # ---- plaintext ----
    lines = ["Populism scraper — session summary",
             "=" * 40,
             f"Duration:  {dur}",
             f"Processed: {len(records)} account(s) this run",
             f"Result:    {done} newly complete, {partial} partial, {failed} failed",
             f"Tweets:    {total_tweets:,} collected this session",
             f"Overall:   {complete_overall}/{total} targets complete ({done_today} done today)",
             "",
             # {:22} pads to 22 chars (left-aligned); {:>8} right-aligns in 8.
             f"{'Account':22}{'Party':16}{'Tweets':>8}{'Time':>9}  Status"]
    for r in records:
        tw = f"{r['tweets']:,}" if r.get("tweets") is not None else "-"
        lines.append(f"@{r['handle']:21}{r['party']:16}{tw:>8}{fmt_dur(r['seconds']):>9}  {r['status']}")
    text = "\n".join(lines)

    # ---- html ----
    # status -> (text colour, background colour) for the little status pill
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
        """One big-number tile for the header row of the email."""
        return (f"<td style='padding:14px 16px;text-align:center'>"
                f"<div style='font-size:22px;font-weight:700;color:#111'>{value}</div>"
                f"<div style='font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.04em'>{label}</div></td>")

    html = f"""\
<div style="background:#f4f5f7;padding:24px 0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#222">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)">
    <div style="background:#1f2933;color:#fff;padding:18px 22px;font-size:16px;font-weight:600">
      🐦 Populism scraper &middot; session summary
    </div>
    <table style="width:100%;border-collapse:collapse;border-bottom:1px solid #eee">
      <tr>{stat('Tweets', f'{total_tweets:,}')}{stat('New datasets', done)}{stat('Duration', dur)}{stat('Complete', f'{complete_overall}/{total}')}</tr>
    </table>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead><tr style="text-align:left;color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.04em">
        <th style="padding:10px 12px">Account</th><th style="padding:10px 12px">Party</th>
        <th style="padding:10px 12px">Window</th><th style="padding:10px 12px;text-align:right">Tweets</th>
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
    """Compose an Adaptive Card (Teams) mirroring the session-summary email.

    `monitor` is the run's `errmon.ErrorMonitor`. Without it the card can only
    report `failed`, which counts Python exceptions — a run that lost thousands
    of tweets to backend errors still shows "0 failed".
    """
    total_tweets = sum(r["tweets"] for r in records if r.get("tweets"))
    complete_overall = skipped + done
    dur = fmt_dur(session_secs)

    def stat(value, label):
        """One big-number column for the card's header row."""
        return {"type": "Column", "width": "stretch", "items": [
            {"type": "TextBlock", "text": str(value), "size": "ExtraLarge",
             "weight": "Bolder", "horizontalAlignment": "Center", "spacing": "None"},
            {"type": "TextBlock", "text": label, "size": "Small", "isSubtle": True,
             "horizontalAlignment": "Center", "spacing": "None"}]}

    colour = {"complete": "Good", "partial": "Warning", "failed": "Attention"}
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
                 "text": f"{tw} tweets · {fmt_dur(r['seconds'])}"},
                {"type": "TextBlock", "spacing": "None", "size": "Small", "weight": "Bolder",
                 "horizontalAlignment": "Right",
                 "color": colour.get(r["status"], "Default"), "text": r["status"]}]}]})

    return {"type": "AdaptiveCard", "version": "1.4",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "msteams": {"width": "Full"},
            "body": [
                {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                 "text": "🐦 Populism scraper · session summary"},
                {"type": "ColumnSet", "columns": [
                    stat(f"{total_tweets:,}", "Tweets"), stat(done, "New datasets"),
                    stat(dur, "Duration"), stat(f"{complete_overall}/{total}", "Complete")]},
                {"type": "Container", "separator": True, "items": rows},
                # *list unpacks: zero elements when the run was clean, two when not
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
    """Compose a small Adaptive Card for ONE finished account (--notify-each)."""
    colour = {"complete": "Good", "partial": "Warning", "failed": "Attention"}
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
                    {"title": "Tweets", "value": tw},
                    {"title": "Window", "value": f"{rec['since']} → {rec['until']}"},
                    {"title": "Took", "value": fmt_dur(rec["seconds"])}]},
                {"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True,
                 "separator": True,
                 "color": colour.get(rec["status"], "Default"),
                 "text": (f"Target {index}/{total} · {done_overall}/{total} complete · "
                          f"{datetime.now():%H:%M}")}]}
