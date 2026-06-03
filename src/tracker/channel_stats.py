# tracker/channel_stats.py
import json
import os
import asyncio
import aiohttp
from datetime import datetime, timezone
from cache.redis import get_redis
from utils.logger import get_logger, log_debug, log_error, log_info, log_warning
from config.redis_keys import RedisKeys

logger = get_logger(__name__)

_notify_lock        = asyncio.Lock()
_batch_exhausted    = False                       


# ------------------ RECORD MESSAGES FOUND ------------------
async def record_messages_found(username: str):
    """Reset empty cycle count when messages found."""
    r = await get_redis()
    await r.set(f"{RedisKeys.empty_cycle(username)}", "0")


# ------------------ RECORD EMPTY CYCLE ------------------
async def record_empty_cycle(
    username: str,
    is_adults: bool,
    all_channels: list,
) -> int:
    """
    Increment empty cycle count.
    If >= threshold → retire channel.
    If ALL channels retired → notify admin.
    Returns current empty_cycles count.
    """
    from config.settings import EMPTY_CYCLE_THRESHOLD

    r = await get_redis()
    key = RedisKeys.empty_cycle(username)
    count = await r.incr(key)

    log_debug(
        logger,
        "Empty cycle recorded",
        "empty_cycle_recorded",
        username=username,
        empty_cycles=count,
    )

    if count >= EMPTY_CYCLE_THRESHOLD:
        await _retire_channel(username, is_adults, count)
        await _check_batch_exhausted(all_channels)

    return count


# ------------------ RETIRE CHANNEL ------------------
async def _retire_channel(username: str, is_adults: bool, empty_cycles: int):
    """Append channel to retired.json and add to Redis retired set."""
    from config.settings import RETIRED_FILE, FILE_NUMBER

    r = await get_redis()

    # skip if already retired
    already = await r.sismember(RedisKeys.retired_key(), username)
    if already:
        return

    await r.sadd(RedisKeys.retired_key(), username)

    # build retired entry
    entry = {
        "username":     username,
        "is_adults":    is_adults,
        "retired_at":   datetime.now(timezone.utc).isoformat(),
        "reason":       f"{empty_cycles} empty cycles",
        "empty_cycles": empty_cycles,
        "batch_file":   f"batch_{FILE_NUMBER}.json",
    }

    # load existing retired list
    retired = []
    os.makedirs(os.path.dirname(RETIRED_FILE), exist_ok=True)
    if os.path.exists(RETIRED_FILE):
        try:
            with open(RETIRED_FILE, "r") as f:
                retired = json.load(f)
        except (json.JSONDecodeError, Exception):
            retired = []

    # append if not already there
    existing_usernames = {ch.get("username") for ch in retired}
    if username not in existing_usernames:
        retired.append(entry)
        with open(RETIRED_FILE, "w") as f:
            json.dump(retired, f, indent=2)

    log_warning(
        logger,
        "Channel retired after empty cycles",
        "channel_retired",
        username=username,
        empty_cycles=empty_cycles,
        batch_file=f"batch_{FILE_NUMBER}.json",
    )


# ------------------ CHECK BATCH EXHAUSTED ------------------
async def _check_batch_exhausted(all_channels: list):
    """Check if ALL channels in current batch are retired."""
    global _batch_exhausted

    if _batch_exhausted:
        return

    r = await get_redis()
    retired = await r.smembers(RedisKeys.retired_key())

    all_usernames = {ch.get("username") for ch in all_channels if ch.get("username")}

    if all_usernames and all_usernames.issubset(retired):
        async with _notify_lock:
            if _batch_exhausted:
                return  
            _batch_exhausted = True

        log_error(
            logger,
            "All channels in current batch are exhausted",
            "batch_exhausted",
        )
        await _notify_admin_telegram()


# ------------------ NOTIFY ADMIN ------------------
async def _notify_admin_telegram():
    """Send Telegram message to admin via bot."""
    from config.settings import BOT_TOKEN, ADMIN_ID, FILE_NUMBER

    if not BOT_TOKEN or not ADMIN_ID:
        log_warning(
            logger,
            "Bot token or admin ID missing. Cannot notify admin",
            "admin_notify_not_configured",
        )
        return

    message = (
        f"Hello , Dear*\n\n"
        f"🚨 *Batch Exhausted*\n\n"
        f"All channels in `batch_{FILE_NUMBER}.json` have no new messages.\n\n"
        f"Please update `.env`:\n"
        f"`FILE_NUMBER=XXX`\n\n"
        f"Then restart the worker."
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "chat_id":    ADMIN_ID,
                "text":       message,
                "parse_mode": "Markdown",
            }) as resp:
                if resp.status == 200:
                    log_info(
                        logger,
                        "Admin notified via Telegram",
                        "admin_notified",
                        admin_id=ADMIN_ID,
                    )
                else:
                    text = await resp.text()
                    log_error(
                        logger,
                        "Telegram admin notification failed",
                        "admin_notify_failed",
                        status=resp.status,
                        response=text,
                    )
    except Exception as e:
        log_error(
            logger,
            "Telegram admin notification error",
            "admin_notify_error",
            error=str(e),
        )


# ------------------ GET EMPTY CYCLES ------------------
async def get_empty_cycles(username: str) -> int:
    r = await get_redis()
    val = await r.get(RedisKeys.empty_cycle(username))
    return int(val) if val else 0


# ------------------ IS RETIRED ------------------
async def is_retired(username: str) -> bool:
    r = await get_redis()
    return bool(await r.sismember(RedisKeys.retired_key(), username))


# ------------------ RESET RETIRED FLAG ------------------
async def reset_batch_exhausted_flag():
    """ clears retired list if FILE_NUMBER changed."""
    global _batch_exhausted
    _batch_exhausted = False

    from config.settings import FILE_NUMBER
    r = await get_redis()

    stored_batch = await r.get(RedisKeys.QUEUE_CURRENT_BATCH)
    if stored_batch != FILE_NUMBER:
        await _delete_empty_cycle_keys(r)
        await r.delete(
            RedisKeys.retired_key(),
            RedisKeys.QUEUE_DONE,
            RedisKeys.QUEUE_CYCLE_START,
        )

        await r.set(RedisKeys.QUEUE_CYCLE, "1")
        await r.set(RedisKeys.QUEUE_CYCLE_START, asyncio.get_running_loop().time())
        await r.set(RedisKeys.QUEUE_CURRENT_BATCH, FILE_NUMBER)

        log_info(
            logger,
            "Batch changed. retired_set cleared",
            "batch_reset",
            old_batch=stored_batch,
            new_batch=FILE_NUMBER,
        )

    else:
        count = await r.scard(RedisKeys.retired_key())
        log_info(
            logger,
            "Batch unchanged. keeping retired set",
            "batch_unchanged",
            batch=FILE_NUMBER,
            retired_count=count,
        )


# ----- HELPER ------------------
async def _delete_empty_cycle_keys(r):
    cursor = 0
    pattern = f"{RedisKeys.CHANNEL_PREFIX}:empty_cycles:*"

    while True:
        cursor, keys = await r.scan(cursor=cursor, match=pattern, count=500)
        if keys:
            await r.delete(*keys)

        if cursor == 0:
            break
