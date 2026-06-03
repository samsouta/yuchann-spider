# login_acc.py
# Run this ONCE before main.py to authenticate all accounts.
# Usage: python login_acc.py

import asyncio
import json
from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    RPCError,
)
from dotenv import load_dotenv
from utils.logger import get_logger, log_error, log_info
from config.settings import ACCOUNTS_FILE

load_dotenv()
logger = get_logger(__name__)


def load_accounts_for_login() -> list:

    with open(ACCOUNTS_FILE, "r") as f:
        raw = json.load(f)

    accounts = []
    for acc in raw:
        acc["_display"] = acc.get("name") or f"worker{acc['id']}"

        if not acc.get("enable", True):
            log_info(
                logger,
                "Account skipped because it is disabled",
                "login_account_skipped",
                account=acc["_display"],
            )
            continue

        accounts.append(acc)

    return accounts


# -----------------------------------------------------------------------
# Login a single account interactively
# -----------------------------------------------------------------------
async def login_account(account: dict) -> bool:
    label    = account["_display"]  
    api_id   = account["api_id"]
    api_hash = account["api_hash"]
    phone    = account["phone"]
    session  = account["session"]

    client = TelegramClient(session, api_id, api_hash)

    try:
        await client.connect()

        # ---- Already logged in ----
        if await client.is_user_authorized():
            me = await client.get_me()
            log_info(
                logger,
                "Account already logged in",
                "login_account_already_authorized",
                account=label,
                username=me.username or me.first_name,
                telegram_id=me.id,
            )
            return True

        # ---- Need to login ----
        log_info(
            logger,
            "Sending login code",
            "login_code_send",
            account=label,
            phone=phone,
        )
        await client.send_code_request(phone)

        # Retry loop for wrong / expired code
        while True:
            code = input(f"\n[{label}] 🔑 Enter the Telegram code for {phone}: ").strip()

            try:
                await client.sign_in(phone, code)
                break  # success

            except PhoneCodeInvalidError:
                print(f"[{label}] ❌ Wrong code. Try again.")
                continue

            except PhoneCodeExpiredError:
                print(f"[{label}] ⏰ Code expired — requesting a new one...")
                await client.send_code_request(phone)
                continue

            except SessionPasswordNeededError:
                # 2FA — inner retry loop
                while True:
                    password = input(
                        f"[{label}] 🔐 2FA password for {phone}: "
                    ).strip()
                    try:
                        await client.sign_in(password=password)
                        break
                    except Exception as pw_err:
                        print(f"[{label}] ❌ Wrong password ({pw_err}). Try again.")
                break  # exit outer code loop after 2FA handled

        me = await client.get_me()
        log_info(
            logger,
            "Account logged in successfully",
            "login_account_success",
            account=label,
            username=me.username or me.first_name,
            telegram_id=me.id,
        )
        return True

    except (AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
        log_error(
            logger,
            "Account banned or revoked",
            "login_account_banned",
            account=label,
            error=str(e),
        )
        return False

    except RPCError as e:
        log_error(
            logger,
            "Login RPC error",
            "login_rpc_error",
            account=label,
            phone=phone,
            error=str(e),
        )
        return False

    except Exception as e:
        log_error(
            logger,
            "Unexpected login error",
            "login_unexpected_error",
            account=label,
            phone=phone,
            error=str(e),
        )
        return False

    finally:
        await client.disconnect()


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
async def main():
    print("=" * 52)
    print("  🔐  Telegram Account Login Tool")
    print("=" * 52)

    accounts = load_accounts_for_login()

    if not accounts:
        print("❌  No enabled accounts found in accounts.json.")
        print("    Set \"enable\": true for the accounts you want to use.")
        return

    print(f"  Found {len(accounts)} enabled account(s):\n")
    for acc in accounts:
        print(f"    • [{acc['_display']}]  {acc['phone']}")
    print()

    results = {"ok": [], "failed": []}

    # Login one by one — must be sequential (interactive terminal input)
    for account in accounts:
        print(f"\n{'─' * 52}")
        print(f"  Account : {account['_display']}")
        print(f"  Phone   : {account['phone']}")
        print(f"{'─' * 52}")

        success = await login_account(account)

        entry = {"label": account["_display"], "phone": account["phone"]}
        if success:
            results["ok"].append(entry)
        else:
            results["failed"].append(entry)

    # ---- Summary ----
    print(f"\n{'=' * 52}")
    print(f"  📊  Login Summary")
    print(f"{'=' * 52}")
    print(f"  ✅  Success : {len(results['ok'])}")
    print(f"  ❌  Failed  : {len(results['failed'])}")

    if results["ok"]:
        print(f"\n  Ready accounts:")
        for e in results["ok"]:
            print(f"    ✅  [{e['label']}]  {e['phone']}")

    if results["failed"]:
        print(f"\n  Failed accounts (fix before running main.py):")
        for e in results["failed"]:
            print(f"    ❌  [{e['label']}]  {e['phone']}")
        print("\n⚠️   Some accounts failed. Fix them before running main.py.")
    else:
        print("\n🚀  All accounts ready! You can now run:  python main.py")

    print("=" * 52)


if __name__ == "__main__":
    asyncio.run(main())
