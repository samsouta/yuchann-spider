# tracker/progress_tracker.py
import json
import os
import asyncio
from config.settings import (
    FAILED_FILE,
    SUSPENDED_FILE,
    PROGRESS_REPORT_INTERVAL,
)
from cache.redis import get_redis
from utils.logger import get_logger, log_error, log_info, log_warning
from config.redis_keys import RedisKeys

logger = get_logger(__name__)

def _ensure_file(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump([], f)

_ensure_file(SUSPENDED_FILE)
_ensure_file(FAILED_FILE)


# ------------------- RECORD FAILED -------------------
async def record_failed(r=None):
    if r is None:
        r = await get_redis()

    failed = await r.smembers(RedisKeys.QUEUE_FAILED)

    asyncio.create_task(_backup_to_file(FAILED_FILE, list(failed)))


# ------------------- RECORD SUSPENDED -------------------
async def record_suspended(account_id: int):
    r = await get_redis()
    await r.sadd(RedisKeys.TRACKER_SUSPENDED, str(account_id))
    suspended = await r.smembers(RedisKeys.TRACKER_SUSPENDED)
    asyncio.create_task(_backup_to_file(SUSPENDED_FILE, list(suspended)))
    log_error(
        logger,
        "Account marked suspended",
        "account_suspended",
        account_id=account_id,
    )


# ------------------- GET SUSPENDED -------------------
async def get_suspended() -> list:
    r = await get_redis()
    members = await r.smembers(RedisKeys.TRACKER_SUSPENDED)
    return [int(m) for m in members]


# ------------------- BACKUP TO FILE -------------------
async def _backup_to_file(path: str, data: list):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: open(path, "w").write(json.dumps(data, indent=2))
        )
    except Exception as e:
        log_warning(
            logger,
            "Backup file write failed",
            "backup_file_write_failed",
            path=path,
            error=str(e),
        )


# ------------------- PROGRESS REPORTER -------------------
async def start_progress_reporter():
    from queues.channel_queue import queue_stats

    while True:
        await asyncio.sleep(PROGRESS_REPORT_INTERVAL)
        try:
            stats     = await queue_stats()
            suspended = await get_suspended()

            log_info(
                logger,
                "Worker progress report",
                "progress_report",
                cycle=stats["cycle"],
                pending=stats["pending"],
                pending_update=stats["pending_update"],
                pending_bootstrap=stats["pending_bootstrap"],
                processing=stats["processing"],
                processing_update=stats["processing_update"],
                processing_bootstrap=stats["processing_bootstrap"],
                current_update_shard=stats["current_update_shard"],
                current_update_file=stats["current_update_file"],
                update_cycle_shard=stats["update_cycle_shard"],
                update_cycle_file=stats["update_cycle_file"],
                update_cycle_remaining=stats["update_cycle_remaining"],
                update_idle=stats["update_idle"],
                update_shards=stats["update_shards"],
                update_shards_done=stats["update_shards_done"],
                update_files=stats["update_files"],
                update_files_done=stats["update_files_done"],
                done=stats["done"],
                failed=stats["failed"],
                suspended=len(suspended),
            )
        except Exception as e:
            log_warning(
                logger,
                "Progress reporter error",
                "progress_reporter_error",
                error=str(e),
            )
