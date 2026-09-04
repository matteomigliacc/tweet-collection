#!/usr/bin/env bash
# Back up datasets to SURFdrive, retaining replaced files in a dated archive.
set -uo pipefail
cd "$(dirname "$0")/.."

DEST="surfdrive:populism-backup"
LOG=/tmp/backup_rclone.log

if rclone sync data/dataset "$DEST/dataset" \
      --backup-dir "$DEST/archive/$(date +%F)" \
      --exclude ".quota.json" --exclude "*.sqlite-journal" \
      --transfers 4 --retries 3 --timeout 5m \
      --log-level NOTICE --log-file "$LOG"; then
  SIZE=$(rclone size "$DEST/dataset" | tr '\n' ' ')
  echo "backup ok: $SIZE"
  .venv/bin/python - "$SIZE" <<'PY'
import sys
from datetime import datetime
sys.path.insert(0, "src")
import notifications
card = {"type": "AdaptiveCard", "version": "1.4",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [{"type": "TextBlock", "weight": "Bolder",
                  "color": "Good", "text": "✅ SURFdrive backup OK"},
                 {"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small",
                  "text": f"{sys.argv[1].strip()} · {datetime.now():%Y-%m-%d %H:%M}"}]}
notifications.send_teams(card)
PY
else
  rc=$?
  echo "backup FAILED (rclone exit $rc) — see $LOG"
  .venv/bin/python - "$LOG" <<'PY'
import sys
sys.path.insert(0, "src")
import notifications
tail = open(sys.argv[1], errors="replace").read()[-900:] or "(no rclone log)"
card = {"type": "AdaptiveCard", "version": "1.4",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [{"type": "TextBlock", "weight": "Bolder", "size": "Large",
                  "color": "Attention", "text": "⚠️ SURFdrive backup FAILED"},
                 {"type": "TextBlock", "wrap": True, "isSubtle": True,
                  "fontType": "Monospace", "text": tail}]}
notifications.send_teams(card)   # failure goes to BOTH channels
notifications.send_email("[collection] SURFdrive backup FAILED", tail)
PY
  exit "$rc"
fi
