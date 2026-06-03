import asyncio
from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    RPCError,
)
from tracker.progress_tracker import record_suspended
from utils.logger import get_logger, log_error, log_info
logger = get_logger(__name__)

# -----------------------------------------------------------------------
# Pre-flight: try connecting every account before touch the queue.
# -----------------------------------------------------------------------

async def _check_account(account: dict) -> tuple[dict, str | None]:
    worker_name = account.get('name', f"account_{account.get('id', '?')}")
    worker_id = f"worker_{account['id']}"
    client = TelegramClient(
        account["session"],
        account["api_id"],
        account["api_hash"],
    )
    try:
        await client.connect()

        if not await client.is_user_authorized():
            return account, "NOT_AUTHORIZED"

        me = await client.get_me()
        log_info(
            logger,
            "Account ready",
            "account_ready",
            worker_id=worker_id,
            worker_name=worker_name,
            username=me.username or me.first_name,
            telegram_id=me.id,
        )
        return account, None

    except (AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
        return account, f"BANNED: {e}"

    except RPCError as e:
        return account, f"RPC_ERROR: {e}"

    except Exception as e:
        return account, f"UNKNOWN: {e}"

    finally:
        await client.disconnect()


async def preflight_check(accounts: list) -> list:

    log_info(
        logger,
        "Preflight check starting",
        "preflight_start",
        account_count=len(accounts),
    )

    results = await asyncio.gather(
        *[_check_account(acc) for acc in accounts],
        return_exceptions=True,
    )

    ready   = []
    failed  = []
    for result in results:
        if isinstance(result, Exception):
            log_error(
                logger,
                "Preflight account task crashed",
                "preflight_task_crashed",
                error=str(result),
            )
            continue

    for account, error in results:
        worker_id = f"worker_{account['id']}"
        worker_name = f"worker_{account['name']}"
        if error is None:
            ready.append(account)
        else:
            failed.append(account)
            log_error(
                logger,
                "Account not ready",
                "account_not_ready",
                worker_id=worker_id,
                worker_name=worker_name,
                error=error,
            )
            # Mark banned/suspended accounts so the tracker knows
            if "BANNED" in error or "NOT_AUTHORIZED" in error:
                await record_suspended(account["id"])

    log_info(
        logger,
        "Preflight check complete",
        "preflight_complete",
        ready_accounts=len(ready),
        failed_accounts=len(failed),
    )

    if not ready:
        raise RuntimeError(
            "❌ No accounts passed pre-flight check. "
            "Please fix your sessions before running."
        )

    return ready
