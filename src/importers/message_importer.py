# importers/message_importer.py
import asyncio
import random
from dataclasses import dataclass
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UsernameNotOccupiedError,
    ChatAdminRequiredError,
    UsernameInvalidError,
    ChannelInvalidError,
    UserBannedInChannelError,
)
from telethon.tl.types import User, Channel
from db.models import upsert_sender, insert_message, insert_message_entities
from config.settings import (
    FIRST_IMPORT_SCAN_LIMIT,
    MIN_DELAY_BETWEEN_MESSAGES,
    MAX_DELAY_BETWEEN_MESSAGES,
    MAX_MESSAGES_PER_CHANNEL,
)
from config.job_types import JOB_BOOTSTRAP, JOB_TYPES, JOB_UPDATE
from utils.logger import get_logger, log_debug, log_error, log_info, log_warning
from utils.normalize_datetime import normalize_dt
from utils.msg_importer_helper import (
    get_last_message_id,
    detect_message_type,
    extract_entities,
    save_last_message_id,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class MessageImportResult:
    saved_count: int
    should_count_empty_cycle: bool
    status: str = "ok"
    reason: str | None = None


PERMANENT_MESSAGE_ERRORS = (
    ChannelPrivateError,
    UsernameNotOccupiedError,
    ChatAdminRequiredError,
    UsernameInvalidError,
    ChannelInvalidError,
    UserBannedInChannelError,
)


def _is_real_message(msg) -> bool:
    return bool(msg and (msg.message is not None or msg.media is not None or msg.fwd_from is not None))


def _permanent_message_failure(e: Exception) -> bool:
    if isinstance(e, PERMANENT_MESSAGE_ERRORS):
        return True

    text = str(e).lower()
    permanent_fragments = (
        "private",
        "invalid channel",
        "channel_invalid",
        "channel_private",
        "username_not_occupied",
        "username_invalid",
        "admin required",
        "banned",
        "restricted",
        "violat",
        "terms of service",
    )
    return any(fragment in text for fragment in permanent_fragments)


# ----------------- EXTRACT SENDER -----------------
def _extract_sender_from_peer(msg, peer_map: dict) -> dict | None:
    if not msg.sender_id:
        return None

    sender = peer_map.get(msg.sender_id)
    if sender is None:
        return None

    if isinstance(sender, User):
        return {
            "telegram_id":  str(sender.id),
            "username":     sender.username,
            "display_name": sender.first_name,
            "is_bot":       sender.bot or False,
            "photo_url":    None,
        }
    if isinstance(sender, Channel):
        return {
            "telegram_id":  str(sender.id),
            "username":     sender.username,
            "display_name": sender.title,
            "is_bot":       False,
            "photo_url":    None,
        }
    return None


async def _save_message(
    channel_username: str,
    channel_db_id: int,
    msg,
    peer_map: dict[int, object],
) -> int | None:
    sender_db_id = None
    if msg.sender_id:
        sender_info = _extract_sender_from_peer(msg, peer_map)

        if sender_info is None and hasattr(msg, "_sender") and msg._sender:
            s = msg._sender
            sender_info = {
                "telegram_id":  str(msg.sender_id),
                "username":     getattr(s, "username", None),
                "display_name": getattr(s, "first_name", None) or getattr(s, "title", None),
                "is_bot":       getattr(s, "bot", False),
                "photo_url":    None,
            }

        if sender_info:
            try:
                sender_db_id = await upsert_sender(sender_info)
                peer_map[msg.sender_id] = type("CachedSender", (), sender_info)()
            except Exception as e:
                log_debug(
                    logger,
                    "Sender upsert skipped",
                    "sender_upsert_skipped",
                    error=str(e),
                )

    message_db_id = await insert_message({
        "telegram_id":     str(msg.id),
        "channel_id":      channel_db_id,
        "sender_id":       sender_db_id,
        "message_type":    detect_message_type(msg),
        "content_text":    msg.message,
        "media_file_path": f"media/{channel_username}/{msg.id}" if msg.media else None,
        "views":           msg.views,
        "forwards":        msg.forwards,
        "replies_count":   getattr(msg.replies, "replies", None) if msg.replies else None,
        "posted_at":       normalize_dt(msg.date),
    })

    entities = extract_entities(msg)
    if entities and message_db_id:
        await insert_message_entities(message_db_id, entities)

    return message_db_id


async def _save_messages_oldest_first(
    channel_username: str,
    channel_db_id: int,
    messages,
    highest_id: int,
) -> tuple[int, int]:
    saved_count = 0
    peer_map: dict[int, object] = {}

    for msg in messages:
        if not _is_real_message(msg):
            continue

        await _save_message(channel_username, channel_db_id, msg, peer_map)
        saved_count += 1
        highest_id = max(highest_id, msg.id)

        await asyncio.sleep(random.uniform(MIN_DELAY_BETWEEN_MESSAGES, MAX_DELAY_BETWEEN_MESSAGES))

    return saved_count, highest_id


async def _bootstrap_from_oldest_real_messages(
    client: TelegramClient,
    target: str,
    channel_username: str,
    channel_db_id: int,
) -> MessageImportResult:
    scanned_count = 0
    saved_count = 0
    highest_id = 0
    first_real_id = None
    peer_map: dict[int, object] = {}

    try:
        async for msg in client.iter_messages(
            target,
            limit=FIRST_IMPORT_SCAN_LIMIT,
            reverse=True,
        ):
            scanned_count += 1

            if not _is_real_message(msg):
                continue

            if first_real_id is None:
                first_real_id = msg.id
                log_info(
                    logger,
                    "First real historical message found",
                    "message_bootstrap_first_real",
                    username=channel_username,
                    first_real_id=first_real_id,
                    scanned_count=scanned_count,
                )

            await _save_message(channel_username, channel_db_id, msg, peer_map)
            saved_count += 1
            highest_id = max(highest_id, msg.id)

            await asyncio.sleep(random.uniform(MIN_DELAY_BETWEEN_MESSAGES, MAX_DELAY_BETWEEN_MESSAGES))

            if saved_count >= MAX_MESSAGES_PER_CHANNEL:
                break
    except FloodWaitError:
        if highest_id > 0:
            await save_last_message_id(channel_username, highest_id)
        raise

    except Exception as e:
        if _permanent_message_failure(e):
            log_warning(
                logger,
                "Permanent bootstrap message access failure",
                "message_bootstrap_permanent_failure",
                username=channel_username,
                error_type=e.__class__.__name__,
                error=str(e),
            )
            return MessageImportResult(
                saved_count=0,
                should_count_empty_cycle=False,
                status="permanent_failure",
                reason=e.__class__.__name__,
            )
        raise

    if saved_count == 0:
        log_info(
            logger,
            "No real messages found during historical bootstrap",
            "message_bootstrap_no_real_messages",
            username=channel_username,
            scanned_count=scanned_count,
            scan_limit=FIRST_IMPORT_SCAN_LIMIT,
        )
        return MessageImportResult(
            saved_count=0,
            should_count_empty_cycle=False,
            status="no_real_messages",
        )

    await save_last_message_id(channel_username, highest_id)
    log_info(
        logger,
        "Historical bootstrap saved",
        "message_bootstrap_historical_saved",
        username=channel_username,
        first_real_id=first_real_id,
        highest_saved_id=highest_id,
        saved_count=saved_count,
        scanned_count=scanned_count,
    )
    log_info(
        logger,
        "Message cursor updated",
        "message_state_update",
        username=channel_username,
        last_id=highest_id,
    )

    return MessageImportResult(saved_count=saved_count, should_count_empty_cycle=False)


# --------------- FETCH & SAVE MESSAGES ---------------
async def fetch_and_save_messages(
    client: TelegramClient,
    channel_username: str,
    channel_db_id: int,
    failed_list: list,
    job_type: str,
) -> MessageImportResult:
    target = channel_username
    log_info(
        logger,
        "Message import target selected",
        "message_import_target",
        username=channel_username,
        target="username",
    )

    last_id = await get_last_message_id(channel_username)
    highest_id = last_id

    try:
        if job_type not in JOB_TYPES:
            raise ValueError(f"Invalid job_type={job_type}")

        if job_type == JOB_UPDATE and last_id == 0:
            log_warning(
                logger,
                "Update job received channel without last_id",
                "message_update_missing_cursor",
                username=channel_username,
                job_type=job_type,
            )
            return MessageImportResult(
                saved_count=0,
                should_count_empty_cycle=False,
                status="missing_cursor",
            )

        if job_type == JOB_BOOTSTRAP:
            if last_id > 0:
                log_info(
                    logger,
                    "Bootstrap job skipped because channel already has last_id",
                    "message_bootstrap_already_done",
                    username=channel_username,
                    last_id=last_id,
                )
                return MessageImportResult(saved_count=0, should_count_empty_cycle=False)

            return await _bootstrap_from_oldest_real_messages(
                client,
                target,
                channel_username,
                channel_db_id,
            )

        messages = []
        async for msg in client.iter_messages(
            target,
            min_id=last_id,
            limit=MAX_MESSAGES_PER_CHANNEL,
            reverse=True,
        ):
            messages.append(msg)

        saved_count, highest_id = await _save_messages_oldest_first(
            channel_username,
            channel_db_id,
            messages,
            highest_id,
        )

    except FloodWaitError:
        if highest_id > last_id:
            await save_last_message_id(channel_username, highest_id)
        raise

    except Exception as e:
        if job_type == JOB_BOOTSTRAP and _permanent_message_failure(e):
            log_warning(
                logger,
                "Permanent bootstrap message access failure",
                "message_bootstrap_permanent_failure",
                username=channel_username,
                error_type=e.__class__.__name__,
                error=str(e),
            )
            return MessageImportResult(
                saved_count=0,
                should_count_empty_cycle=False,
                status="permanent_failure",
                reason=e.__class__.__name__,
            )

        log_error(
            logger,
            "Error fetching messages",
            "message_fetch_error",
            username=channel_username,
            error=str(e),
        )
        failed_list.append(channel_username)
        return MessageImportResult(saved_count=0, should_count_empty_cycle=False)

    if highest_id > last_id:
        await save_last_message_id(channel_username, highest_id)
        log_info(
            logger,
            "Message cursor updated",
            "message_state_update",
            username=channel_username,
            last_id=highest_id,
        )

    return MessageImportResult(saved_count=saved_count, should_count_empty_cycle=True)
