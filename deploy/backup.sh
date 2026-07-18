#!/usr/bin/env bash
#
# Nightly corpus backup to SURFdrive over WebDAV (rclone remote "surfdrive").
#
# Mirrors data/corpus/ to populism-backup/corpus/ on SURFdrive. Files that a
# sync would overwrite or delete are moved into a dated archive/ folder first,
# so no backup run can ever destroy the previous good copy. On failure, posts
# a Teams card via src/notify.py (falls back to email, then to just logging).
#
# One-time setup (already done on the production LXC):
#   apt-get install rclone
#   rclone config create surfdrive webdav \
#       url "https://surfdrive.surf.nl/remote.php/dav/files/<user>" \
#       vendor owncloud user "<user>" pass "<webdav-app-password>"
set -uo pipefail
cd "$(dirname "$0")/.."

DEST="surfdrive:populism-backup"
LOG=/tmp/backup_rclone.log

if rclone sync data/corpus "$DEST/corpus" \
      --backup-dir "$DEST/archive/$(date +%F)" \
      --exclude ".quota.json" \
      --transfers 4 --retries 3 --timeout 5m \
      --log-level NOTICE --log-file "$LOG"; then
  echo "backup ok: $(rclone size "$DEST/corpus" | tr '\n' ';')"
else
  rc=$?
  echo "backup FAILED (rclone exit $rc) — see $LOG"
  .venv/bin/python - "$LOG" <<'PY'
import sys
sys.path.insert(0, "src")
import notify
tail = open(sys.argv[1], errors="replace").read()[-900:] or "(no rclone log)"
card = {"type": "AdaptiveCard", "version": "1.4",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [{"type": "TextBlock", "weight": "Bolder", "size": "Large",
                  "color": "Attention", "text": "⚠️ SURFdrive backup FAILED"},
                 {"type": "TextBlock", "wrap": True, "isSubtle": True,
                  "fontType": "Monospace", "text": tail}]}
if not notify.send_teams(card):
    notify.send_email("[scraper] SURFdrive backup FAILED", tail)
PY
  exit "$rc"
fi
