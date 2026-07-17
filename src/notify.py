"""Optional email notifications for the batch scraper.

Reads SMTP settings from secrets/smtp.json (git-ignored). If that file is missing
or unreadable, send_email() is a safe no-op (logs a warning) so a mail problem can
never abort scraping.

secrets/smtp.json schema (see secrets/smtp.example.json):
{
  "host": "smtp.gmail.com",
  "port": 587,
  "use_tls": true,          # STARTTLS on 587; set false + port 465 for SMTPS
  "username": "you@gmail.com",
  "password": "app-password-here",
  "from_addr": "you@gmail.com",
  "to_addr": "you@gmail.com"
}
"""
import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "secrets" / "smtp.json"


def _load_config() -> dict | None:
    if not CONFIG.exists():
        logger.warning(f"no {CONFIG.name} -> email notifications disabled")
        return None
    try:
        cfg = json.loads(CONFIG.read_text())
        for key in ("host", "port", "username", "password", "from_addr", "to_addr"):
            if not cfg.get(key):
                logger.warning(f"{CONFIG.name} missing '{key}' -> email disabled")
                return None
        return cfg
    except Exception as e:
        logger.warning(f"could not read {CONFIG.name} ({e!r}) -> email disabled")
        return None


def send_email(subject: str, body: str, html: str | None = None) -> bool:
    """Send one email (plaintext, plus an optional HTML alternative). Returns True on
    success, False on any failure (failures are logged, never raised)."""
    cfg = _load_config()
    if cfg is None:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = cfg["to_addr"]
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if cfg.get("use_tls", True):
            with smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(cfg["username"], cfg["password"])
                s.send_message(msg)
        else:  # implicit TLS (SMTPS, usually port 465)
            with smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]),
                                  context=ssl.create_default_context(), timeout=30) as s:
                s.login(cfg["username"], cfg["password"])
                s.send_message(msg)
        logger.info(f"email sent: {subject!r} -> {cfg['to_addr']}")
        return True
    except Exception as e:
        logger.warning(f"email send failed ({e!r}): {subject!r}")
        return False


if __name__ == "__main__":  # quick manual test: python src/notify.py
    ok = send_email("[scraper] test email",
                    "If you can read this, secrets/smtp.json is configured correctly.")
    print("sent" if ok else "not sent (see warnings above)")
