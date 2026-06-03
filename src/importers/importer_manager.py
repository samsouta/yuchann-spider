# importers/importer_manager.py
import asyncio
import random
from dataclasses import dataclass
from telethon import TelegramClient
from utils.logger import get_logger, log_error, log_info, log_warning
from db.models import upsert_channel
from importers.channel_importer import fetch_channel_info, fetch_channel_info_status
from importers.message_importer import fetch_and_save_messages
from config.settings import (
    BOOTSTRAP_NO_REAL_THRESHOLD,
    MIN_DELAY_BETWEEN_CHANNELS,
    MAX_DELAY_BETWEEN_CHANNELS,
)
from config.job_types import JOB_BOOTSTRAP, JOB_UPDATE
from config.redis_keys import RedisKeys
from tracker.channel_stats import record_empty_cycle, record_messages_found
from utils.channel_registry import move_bootstrap_to_update, move_update_to_bootstrap

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChannelImportResult:
    success: bool
    permanent_failure: bool = False
    reason: str | None = None

    def __bool__(self):
        return self.success


async def import_channel(
    client: TelegramClient,
    channel: dict,
    failed_list: list,
    no_messages_list: list,
    r,
    account_id,
    all_channels: list,
    job_type: str = JOB_UPDATE,
) -> ChannelImportResult:
    username  = channel.get("username")
    is_adults = channel.get("is_adults", False)

    # 1. resolve channel info
    if job_type == JOB_BOOTSTRAP:
        peer_result = await fetch_channel_info_status(
            client,
            username,
            is_adults,
            r,
            account_id,
            force_refresh=True,
        )
        if peer_result.status == "permanent_failure":
            log_warning(
                logger,
                "Bootstrap channel has permanent access failure",
                "bootstrap_channel_permanent_failure",
                worker_id=account_id,
                username=username,
                reason=peer_result.reason,
                error_type=peer_result.error_type,
                error=peer_result.error,
            )
            return ChannelImportResult(
                success=False,
                permanent_failure=True,
                reason=peer_result.reason or peer_result.error_type,
            )
        channel_info = peer_result.info
    else:
        channel_info = await fetch_channel_info(client, username, is_adults, r, account_id)

    if not channel_info:
        log_warning(
            logger,
            "Channel info fetch failed",
            "channel_info_fetch_failed",
            worker_id=account_id,
            username=username,
        )
        failed_list.append(username)
        return ChannelImportResult(success=False)

    # 2. save to DB
    channel_db_id = await upsert_channel(channel_info)
    if not channel_db_id:
        log_error(
            logger,
            "Channel upsert failed",
            "channel_upsert_failed",
            worker_id=account_id,
            username=username,
        )
        raise Exception(f"DB upsert failed for @{username}")

    # 3. fetch and save messages
    result = await fetch_and_save_messages(
        client,
        username,
        channel_db_id,
        failed_list,
        job_type,
    )

    if username in failed_list:
        return ChannelImportResult(success=False)

    if result.status == "permanent_failure":
        log_warning(
            logger,
            "Bootstrap channel has permanent message failure",
            "bootstrap_channel_permanent_failure",
            worker_id=account_id,
            username=username,
            reason=result.reason,
        )
        return ChannelImportResult(
            success=False,
            permanent_failure=True,
            reason=result.reason,
        )

    if result.status == "missing_cursor":
        try:
            await move_update_to_bootstrap(r, channel, str(account_id))
            channel["_moved_to_bootstrap"] = True
            log_warning(
                logger,
                "Update channel missing cursor moved to bootstrap",
                "update_channel_missing_cursor_moved",
                worker_id=account_id,
                username=username,
                job_type=job_type,
            )
        except Exception as e:
            log_error(
                logger,
                "Update-to-bootstrap registry move failed",
                "channel_registry_move_to_bootstrap_error",
                worker_id=account_id,
                username=username,
                error=str(e),
            )
            return ChannelImportResult(success=False)

        return ChannelImportResult(success=True)

    if job_type == JOB_BOOTSTRAP and result.status == "no_real_messages":
        key = RedisKeys.bootstrap_no_real(username)
        attempts = await r.incr(key)
        if attempts >= BOOTSTRAP_NO_REAL_THRESHOLD:
            await r.delete(key)
            log_warning(
                logger,
                "Bootstrap channel exceeded no-real-message threshold",
                "bootstrap_no_real_messages_threshold",
                worker_id=account_id,
                username=username,
                attempts=attempts,
                threshold=BOOTSTRAP_NO_REAL_THRESHOLD,
            )
            return ChannelImportResult(
                success=False,
                permanent_failure=True,
                reason="bootstrap_no_real_messages_threshold",
            )

        log_info(
            logger,
            "No real messages found during bootstrap",
            "bootstrap_no_real_messages_retry",
            worker_id=account_id,
            username=username,
            attempts=attempts,
            threshold=BOOTSTRAP_NO_REAL_THRESHOLD,
        )
        await asyncio.sleep(random.uniform(MIN_DELAY_BETWEEN_CHANNELS, MAX_DELAY_BETWEEN_CHANNELS))
        return ChannelImportResult(success=True, reason="no_real_messages")

    if result.saved_count == 0 and result.should_count_empty_cycle:
        no_messages_list.append(username)
        cycles = await record_empty_cycle(username, is_adults, all_channels)
        log_info(
            logger,
            "No new messages found",
            "channel_no_new_messages",
            worker_id=account_id,
            username=username,
            empty_cycles=cycles,
        )
    
    elif result.saved_count == 0:
        log_info(
            logger,
            "No messages saved without empty-cycle penalty",
            "channel_no_messages_no_penalty",
            worker_id=account_id,
            username=username,
            job_type=job_type,
        )

    else:
        await r.delete(RedisKeys.bootstrap_no_real(username))
        await record_messages_found(username)
        log_info(
            logger,
            "Messages saved",
            "channel_messages_saved",
            worker_id=account_id,
            username=username,
            saved_count=result.saved_count,
        )

    if job_type == JOB_BOOTSTRAP and await r.get(RedisKeys.last_id(username)):
        try:
            await move_bootstrap_to_update(r, channel, str(account_id))
        except Exception as e:
            log_error(
                logger,
                "Bootstrap registry move failed",
                "channel_registry_move_error",
                worker_id=account_id,
                username=username,
                error=str(e),
            )

    await asyncio.sleep(random.uniform(MIN_DELAY_BETWEEN_CHANNELS, MAX_DELAY_BETWEEN_CHANNELS))

    return ChannelImportResult(success=True)
