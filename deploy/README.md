# Deploying the scraper on a Proxmox LXC

The scraper runs unattended in a dedicated Debian 13 LXC on the home Proxmox box,
driven by a systemd timer. This is the reproducible record of that setup.

## Container

| | |
|---|---|
| CTID / hostname | `106` / `populism-scraper` |
| IP | `192.168.1.106/24`, gw `192.168.1.1` (static) |
| Base | `debian-13-standard`, unprivileged, `nesting=1` (needed for systemd 257) |
| Resources | 2 cores, 1 GB RAM, 512 MB swap, 8 GB rootfs on `local-lvm` |
| Timezone | `Europe/Amsterdam` |
| Install path | `/opt/populism-scraping` |

Create it from the Proxmox host with `pct create 106 local:vztmpl/debian-13-standard_*.tar.zst
--unprivileged 1 --features nesting=1 --net0 name=eth0,bridge=vmbr0,ip=192.168.1.106/24,gw=192.168.1.1
--rootfs local-lvm:8 --onboot 1 --ssh-public-keys <key>` then `pct start 106`.

## Provisioning

```bash
apt-get install -y python3-venv python3-pip git rsync ca-certificates
# copy the repo to /opt/populism-scraping (rsync from the source machine),
# then, inside it:
bash setup.sh                       # venv + twscrape
.venv/bin/python src/load_accounts.py   # build data/accounts.db, verify cookies
.venv/bin/python src/run_all.py --dry-run   # confirm [✓] on already-scraped targets
```

Secrets are **not** in git: copy `secrets/accounts.json` (X cookies) to the container
separately, and — for notifications — create `secrets/teams.json` (Teams webhook, see
`secrets/teams.example.json`) and/or `secrets/smtp.json` (see `secrets/smtp.example.json`).
Existing scraped data is migrated once by rsync-ing `data/dataset/` over; the per-target
checkpoints then make every later run skip finished months (see the repo README).

## Schedule

Install the units from this folder:

```bash
cp deploy/scrape.service deploy/scrape.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now scrape.timer
```

The timer fires 5×/day (01/06/11/16/21h + up to 2h jitter). Each run does
`run_all.py --all --limit 3 --daily-limit 15`, so the first pass ramps up ~15
accounts/day, account-by-account, over several days; afterwards runs only fetch new
months. Each run sends one summary notification via `src/notify.py` — an Adaptive
Card to a Microsoft Teams channel (session scrape time, per-account table, overall
progress) through a Teams *Workflows* incoming webhook (`secrets/teams.json`); if
Teams isn't configured or the post fails it falls back to the HTML summary email
(`secrets/smtp.json`). With neither file present notifications are a silent no-op;
nothing is sent on an empty run.

To create the Teams webhook: in the target channel choose **Workflows → "Post to a
channel when a webhook request is received"**, copy the resulting HTTPS URL into
`secrets/teams.json` as `webhook_url`, then test with `.venv/bin/python src/notify.py`.

## Backup

`backup.timer` mirrors `data/dataset/` nightly (04:30 ± 30m) to SURFdrive — the
university's research cloud — over WebDAV, via `deploy/backup.sh` and an rclone
remote named `surfdrive`. Overwritten/deleted files are moved to a dated
`populism-backup/archive/<date>/` folder first, so a bad sync can never destroy
the previous copy; a failed run posts a Teams alert. The rclone remote is
configured once on the container with a SURFdrive *app password* (Settings →
Security on surfdrive.surf.nl — SSO logins don't work over WebDAV):

```bash
apt-get install rclone
rclone config create surfdrive webdav \
    url "https://surfdrive.surf.nl/remote.php/dav/files/<user>" \
    vendor owncloud user "<user>" pass "<app-password>"
cp deploy/backup.service deploy/backup.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now backup.timer
```

Secrets (X cookies, `accounts.db`) are deliberately **not** uploaded to
SURFdrive; cover those with a Proxmox vzdump of the container instead.

## Operating it

```bash
systemctl list-timers scrape.timer        # when it next runs
journalctl -u scrape.service -e           # last run's output
systemctl start scrape.service            # run now (respects the daily cap)
tail -f data/dataset/scrape_*.log          # live scrape progress
```

The daily cap is tracked in `data/dataset/.quota.json` (resets each calendar day).
