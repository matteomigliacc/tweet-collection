"""Load X accounts (via browser cookies) into twscrape's pool and verify them.

Reads secrets/accounts.json (git-ignored), adds each account using its
auth_token/ct0 cookies, then does a live fetch to prove the cookies authenticate.
"""
import asyncio
import json
from pathlib import Path

from twscrape import API

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets" / "accounts.json"
DB = ROOT / "data" / "accounts.db"
VERIFY_HANDLE = "nasa"  # any well-known public account, used only as a smoke test


async def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    accounts = json.loads(SECRETS.read_text())
    api = API(str(DB))

    for acc in accounts:
        username = acc["username"]
        try:
            await api.pool.add_account(
                username=username,
                password="unused-cookie-auth",
                email=f"{username}@example.invalid",
                email_password="unused",
                cookies=acc["cookies"],
            )
            print(f"[+] added {username}")
        except Exception as e:  # already-exists or validation issues shouldn't abort
            print(f"[=] {username}: {e}")

    try:
        info = await api.pool.accounts_info()
        print("\nPool state:")
        for a in info:
            print("  ", a)
    except Exception as e:
        print(f"(could not read pool state: {e})")

    print(f"\nVerifying cookies via @{VERIFY_HANDLE} ...")
    user = await api.user_by_login(VERIFY_HANDLE)
    if user:
        print(f"[OK] fetched @{user.username} (id={user.id}, followers={user.followersCount})")
    else:
        print("[FAIL] fetch returned nothing — cookies may be invalid, expired, or the account is locked")


if __name__ == "__main__":
    asyncio.run(main())
