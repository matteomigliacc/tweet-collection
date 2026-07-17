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
separately, and — for email — create `secrets/smtp.json` (see `secrets/smtp.example.json`).
Existing scraped data is migrated once by rsync-ing `data/corpus/` over; the per-target
checkpoints then make every later run skip finished months (see the repo README).

## Schedule

Install the units from this folder:

```bash
cp deploy/scrape.service deploy/scrape.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now scrape.timer
```

The timer fires 4×/day (jittered ±2h). Each run does `run_all.py --all --limit 3
--daily-limit 10`, so the first pass ramps up ~10 accounts/day, account-by-account,
over several days; afterwards runs only fetch new months. On each newly-completed
dataset the runner emails via `src/notify.py` (no-op until `secrets/smtp.json` exists).

## Operating it

```bash
systemctl list-timers scrape.timer        # when it next runs
journalctl -u scrape.service -e           # last run's output
systemctl start scrape.service            # run now (respects the daily cap)
tail -f data/corpus/scrape_*.log          # live scrape progress
```

The daily cap is tracked in `data/corpus/.quota.json` (resets each calendar day).
