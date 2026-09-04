"""Add cookie-authenticated X accounts to secrets/accounts.json."""
import asyncio
import json
from pathlib import Path

from cli_prompts import ask, ask_yes_no

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets" / "accounts.json"

REQUIRED_COOKIES = ("auth_token", "ct0")


def missing_tokens(cookie_str: str) -> list[str]:
    """Return the required cookie names that aren't present in the string."""
    return [name for name in REQUIRED_COOKIES if f"{name}=" not in cookie_str]


def load_logins() -> list[dict]:
    if not SECRETS.exists():
        return []
    try:
        data = json.loads(SECRETS.read_text())
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        print(f"  [!] {SECRETS.name} exists but isn't valid JSON — starting a fresh list.")
        return []


def save_accounts(accounts: list[dict]) -> None:
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    SECRETS.write_text(json.dumps(accounts, indent=2, ensure_ascii=False) + "\n")


def upsert(accounts: list[dict], username: str, cookies: str) -> str:
    """Add a new account or replace an existing one. Returns 'added' | 'updated'."""
    for acc in accounts:
        if acc.get("username", "").lower() == username.lower():
            acc["username"] = username
            acc["cookies"] = cookies
            return "updated"
    accounts.append({"username": username, "cookies": cookies})
    return "added"


async def main() -> None:
    print("=" * 60)
    print("  Add X accounts by cookie string  (Ctrl-C to quit)")
    print("=" * 60)

    accounts = load_logins()
    if accounts:
        names = ", ".join(a.get("username", "?") for a in accounts)
        print(f"\nAlready in {SECRETS.name}: {len(accounts)} account(s) — {names}")

    print(
        "\nFor each account paste the cookie string from your logged-in X browser\n"
        "session. It must contain at least  auth_token=...  and  ct0=...\n"
        "e.g.   auth_token=abc123...; ct0=def456...\n"
    )

    added = 0
    while True:
        username = ask("\nUsername (without @), or blank to finish", default="").lstrip("@").strip()
        if not username:
            break

        while True:
            cookies = ask("Cookie string")
            missing = missing_tokens(cookies)
            if not missing:
                break
            print(f"  that string is missing: {', '.join(missing)} — paste the full cookie string")

        action = upsert(accounts, username, cookies)
        save_accounts(accounts)
        added += 1
        print(f"  [{'+' if action == 'added' else '~'}] {action} @{username} "
              f"({len(accounts)} account(s) saved to {SECRETS.name})")

        if not ask_yes_no("Add another?", default=True):
            break

    if added == 0:
        print("\nNothing added. Bye.")
        return

    print(f"\nSaved {len(accounts)} account(s) to {SECRETS.relative_to(ROOT)}")

    if ask_yes_no("\nLoad and verify these accounts now?", default=True):
        import load_logins as loader
        await loader.main()
    else:
        print("Later, run:  python src/load_logins.py")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Anything already confirmed was saved.")
