# workers/worker.py
import asyncio
import random
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
)
from queues.channel_queue import (
    pull_channel, mark_done, mark_failed,
    mark_skipped,
    is_update_cycle_done, is_update_idle, reset_update_cycle, requeue_channel,
    sync_bootstrap_queue,
)
from config.job_types import JOB_BOOTSTRAP, JOB_UPDATE
from importers.importer_manager import import_channel
from tracker.progress_tracker import record_failed, record_suspended
from config.redis_keys import RedisKeys
from config.settings import (
    MAX_ATTEMPTS,
    BOOTSTRAP_IDLE_SLEEP_SECONDS,
    UPDATE_IDLE_SLEEP_SECONDS,
    MIN_DELAY_BETWEEN_CHANNELS,
    MAX_DELAY_BETWEEN_CHANNELS,
)
from utils.channel_registry import remove_bootstrap_channel
from utils.logger import get_logger, log_error, log_info, log_warning

logger = get_logger(__name__)

IDLE_POLL_INTERVAL = 10


async def run_worker(account: dict, r, worker_role: str):
    if worker_role not in (JOB_UPDATE, JOB_BOOTSTRAP):
        raise ValueError(f"Invalid worker_role={worker_role}")

    worker_name = account.get("name", f"account_{account.get('id', '?')}")
    worker_id   = f"worker_{account['id']}"
    session     = account["session"]
    api_id      = account["api_id"]
    api_hash    = account["api_hash"]
    phone       = account["phone"]

    log_info(
        logger,
        "Worker starting",
        "worker_start",
        worker_id=worker_id,
        worker_name=worker_name,
        worker_role=worker_role,
    )
    client = TelegramClient(session, api_id, api_hash)

    try:
        await client.start(phone=phone)
        log_info(
            logger,
            "Worker connected",
            "worker_connected",
            worker_id=worker_id,
            worker_role=worker_role,
        )

        while True:
            item = await pull_channel(worker_id, r, worker_role)

            # ── queue empty ──
            if not item:
                if worker_role == JOB_BOOTSTRAP:
                    added = await sync_bootstrap_queue(r, worker_id)
                    if added > 0:
                        log_info(
                            logger,
                            "Bootstrap queue refilled",
                            "bootstrap_queue_refilled",
                            worker_id=worker_id,
                            worker_role=worker_role,
                            added=added,
                        )
                        continue

                    log_info(
                        logger,
                        "Bootstrap queue empty. Sleeping",
                        "bootstrap_queue_empty",
                        worker_id=worker_id,
                        worker_role=worker_role,
                        added=added,
                        sleep_seconds=BOOTSTRAP_IDLE_SLEEP_SECONDS,
                    )
                    await asyncio.sleep(BOOTSTRAP_IDLE_SLEEP_SECONDS)
                    continue

                if await is_update_cycle_done(r):
                    did_reset = await reset_update_cycle(worker_id, r)
                    if did_reset:
                        log_info(
                            logger,
                            "Cycle reset completed",
                            "cycle_reset_done",
                            worker_id=worker_id,
                            worker_role=worker_role,
                        )
                    else:
                        if await is_update_idle(r):
                            log_info(
                                logger,
                                "Update lane idle. Sleeping",
                                "update_lane_idle",
                                worker_id=worker_id,
                                worker_role=worker_role,
                                sleep_seconds=UPDATE_IDLE_SLEEP_SECONDS,
                            )
                            await asyncio.sleep(UPDATE_IDLE_SLEEP_SECONDS)
                            continue

                        log_info(
                            logger,
                            "Waiting for cycle reset by another worker",
                            "cycle_wait_other_worker",
                            worker_id=worker_id,
                            worker_role=worker_role,
                        )
                else:
                    log_info(
                        logger,
                        "Queue empty. Polling",
                        "queue_empty",
                        worker_id=worker_id,
                        worker_role=worker_role,
                        poll_interval=IDLE_POLL_INTERVAL,
                    )

                await asyncio.sleep(IDLE_POLL_INTERVAL)
                continue

            # ── process channel ──
            username  = item.get("username")
            is_adults = item.get("is_adults", False)
            cycle_channels = []
            log_info(
                logger,
                "Processing channel",
                "channel_processing",
                worker_id=worker_id,
                worker_name=worker_name,
                worker_role=worker_role,
                username=username,
                is_adults=is_adults,
            )

            success          = False
            permanent_failure_reason = None
            attempt          = 1
            failed_list      = []
            no_messages_list = []

            while attempt <= MAX_ATTEMPTS:
                try:
                    result = await import_channel(
                        client,
                        item,
                        failed_list,
                        no_messages_list,
                        r,
                        worker_id,
                        all_channels=cycle_channels,
                        job_type=worker_role,
                    )

                    if result.permanent_failure:
                        permanent_failure_reason = result.reason
                        log_warning(
                            logger,
                            "Permanent channel failure detected",
                            "channel_permanent_failure",
                            worker_id=worker_id,
                            worker_role=worker_role,
                            username=username,
                            reason=permanent_failure_reason,
                        )
                        break

                    if not result.success:
                        log_warning(
                            logger,
                            "Import returned no result",
                            "channel_import_retry",
                            worker_id=worker_id,
                            worker_role=worker_role,
                            username=username,
                            attempt=attempt,
                            max_attempts=MAX_ATTEMPTS,
                        )
                        await asyncio.sleep(random.uniform(MIN_DELAY_BETWEEN_CHANNELS, MAX_DELAY_BETWEEN_CHANNELS))
                        attempt += 1
                        continue

                    success = True
                    break

                except FloodWaitError as e:
                    wait = e.seconds + 10
                    log_warning(
                        logger,
                        "Flood wait encountered",
                        "flood_wait",
                        worker_id=worker_id,
                        worker_role=worker_role,
                        username=username,
                        wait_seconds=wait,
                        wait_hours=round(wait / 3600, 2),
                    )

                    if e.seconds > 3600:
                        # long flood — give channel to another worker
                        await requeue_channel(r, username, item)
                        resume_at = asyncio.get_running_loop().time() + wait
                        await r.set(RedisKeys.worker_cooldown(worker_id), resume_at, ex=wait + 60)
                        log_error(
                            logger,
                            "Worker entering cooldown",
                            "worker_cooldown_start",
                            worker_id=worker_id,
                            worker_role=worker_role,
                            cooldown_seconds=wait,
                            cooldown_hours=round(wait / 3600, 2),
                        )
                        await asyncio.sleep(wait)
                        await r.delete(RedisKeys.worker_cooldown(worker_id))
                        log_info(
                            logger,
                            "Worker cooldown finished",
                            "worker_cooldown_end",
                            worker_id=worker_id,
                            worker_role=worker_role,
                        )
                        success = False  
                        break
                    else:
                        await asyncio.sleep(wait)

                except Exception as e:
                    log_error(
                        logger,
                        "Channel processing error",
                        "channel_error",
                        worker_id=worker_id,
                        worker_role=worker_role,
                        username=username,
                        attempt=attempt,
                        max_attempts=MAX_ATTEMPTS,
                        error=str(e),
                    )
                    await asyncio.sleep(3 * attempt + random.uniform(1, 5))
                    attempt += 1

            if success:
                if item.get("_moved_to_bootstrap"):
                    await mark_skipped(
                        worker_id,
                        username,
                        r,
                        worker_role,
                        reason="moved_to_bootstrap",
                    )
                    continue

                await mark_done(worker_id, username, r, worker_role)
                log_info(
                    logger,
                    "Channel processed successfully",
                    "channel_success",
                    worker_id=worker_id,
                    worker_name=worker_name,
                    worker_role=worker_role,
                    username=username,
                )
            else:
                await mark_failed(worker_id, username, r, worker_role)
                await record_failed(r)
                if worker_role == JOB_BOOTSTRAP:
                    await remove_bootstrap_channel(r, item, worker_id)
                log_error(
                    logger,
                    "Channel permanently failed",
                    "channel_failed_permanent",
                    worker_id=worker_id,
                    worker_name=worker_name,
                    worker_role=worker_role,
                    username=username,
                    reason=permanent_failure_reason or "max_attempts_exceeded",
                )

    except (AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
        log_error(
            logger,
            "Worker account suspended",
            "worker_suspended",
            worker_id=worker_id,
            worker_role=worker_role,
            error=str(e),
        )
        await record_suspended(account["id"])

    except Exception as e:
        log_error(
            logger,
            "Worker crashed",
            "worker_crash",
            worker_id=worker_id,
            worker_role=worker_role,
            error=str(e),
        )

    finally:
        await client.disconnect()
        log_info(
            logger,
            "Worker disconnected",
            "worker_disconnected",
            worker_id=worker_id,
            worker_role=worker_role,
        )
