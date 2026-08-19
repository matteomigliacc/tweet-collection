"""Optional notifications for the batch scraper (Teams webhook and/or email).

Each channel reads its settings from a git-ignored file under secrets/ and is a
safe no-op (logs a warning) when that file is missing or unreadable, so a
notification problem can never abort scraping.

secrets/teams.json schema (see secrets/teams.example.json):
{
  "webhook_url": "https://prod-..logic.azure.com:443/workflows/..."
}
Get the URL from Teams: channel > Workflows > "Post to a channel when a webhook
request is received" (Power Automate). The card is posted as an Adaptive Card.

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
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "secrets" / "smtp.json"
TEAMS_CONFIG = ROOT / "secrets" / "teams.json"


def _load_json_secret(path: Path, required_keys: tuple[str, ...],
                      channel: str) -> dict | None:
    """Load and validate one notification channel's JSON secret."""
    if not path.exists():
        logger.warning(f"no {path.name} -> {channel} notifications disabled")
        return None
    try:
        cfg = json.loads(path.read_text())
        for key in required_keys:
            if not cfg.get(key):
                logger.warning(f"{path.name} missing '{key}' -> {channel} disabled")
                return None
        return cfg
    except Exception as e:
        logger.warning(f"could not read {path.name} ({e!r}) -> {channel} disabled")
        return None


def _load_config() -> dict | None:
    # Email needs both the SMTP login and the envelope addresses before it can
    # construct and deliver a message.
    return _load_json_secret(
        CONFIG, ("host", "port", "username", "password", "from_addr", "to_addr"),
        "email")


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


def _load_teams_config() -> dict | None:
    # Teams uses a Workflow webhook, so its URL is the only required secret.
    return _load_json_secret(TEAMS_CONFIG, ("webhook_url",), "Teams")


def send_teams(card: dict) -> bool:
    """Post one Adaptive Card to the Teams Workflows webhook. Returns True on
    success, False on any failure (failures are logged, never raised)."""
    cfg = _load_teams_config()
    if cfg is None:
        return False
    payload = {"type": "message",
               "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive",
                                "contentUrl": None, "content": card}]}
    req = urllib.request.Request(
        cfg["webhook_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 202):
                logger.info(f"Teams card posted (HTTP {resp.status})")
                return True
            logger.warning(f"Teams webhook returned HTTP {resp.status}")
            return False
    except Exception as e:
        logger.warning(f"Teams post failed ({e!r})")
        return False


if __name__ == "__main__":  # quick manual test: python src/notify.py
    test_card = {"type": "AdaptiveCard", "version": "1.4",
                 "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                 "body": [{"type": "TextBlock", "weight": "Bolder",
                           "text": "🐦 Scraper test card"},
                          {"type": "TextBlock", "wrap": True, "text":
                           "If you can read this, secrets/teams.json is configured correctly."}]}
    ok_teams = send_teams(test_card)
    print("teams: sent" if ok_teams else "teams: not sent (see warnings above)")
    ok = send_email("[scraper] test email",
                    "If you can read this, secrets/smtp.json is configured correctly.")
    print("email: sent" if ok else "email: not sent (see warnings above)")
