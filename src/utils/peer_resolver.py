# utils/peer_resolver.py
import asyncio
from dataclasses import dataclass
from telethon import TelegramClient
from telethon.tl.types import (
    Channel, Chat,
    InputPeerChannel,
    InputPeerChat,
)
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UsernameNotOccupiedError,
    ChatAdminRequiredError,
    UsernameInvalidError,
    ChannelInvalidError,
    UserBannedInChannelError,
)
from db.models import get_channel_by_username
from utils.logger import get_logger, log_error, log_info, log_warning
from config.settings import RESOLVE_MIN_GAP
from config.redis_keys import RedisKeys
logger = get_logger(__name__)


@dataclass(frozen=True)
class PeerResolveResult:
    status: str
    info: dict | None = None
    reason: str | None = None
    error_type: str | None = None
    error: str | None = None


PERMANENT_PEER_ERRORS = (
    ChannelPrivateError,
    UsernameNotOccupiedError,
    ChatAdminRequiredError,
    UsernameInvalidError,
    ChannelInvalidError,
    UserBannedInChannelError,
)


# ------------------- DETECT PEER TYPE -------------------
def detect_peer_type(entity) -> str:
    """Detect if entity is channel, supergroup or group."""
    if isinstance(entity, Channel):
        if entity.megagroup:
            return "SUPERGROUP"
        return "CHANNEL"
    if isinstance(entity, Chat):
        return "GROUP"
    return "CHANNEL"  


# ----------------------- BUILD TARGET PEER -----------------------
def build_peer(peer_type: str, channel_id: int, access_hash: int):
    """Build correct InputPeer based on type."""
    if peer_type in ("CHANNEL", "SUPERGROUP"):
        return InputPeerChannel(
            channel_id=channel_id,
            access_hash=access_hash,
        )
    if peer_type == "GROUP":
        return InputPeerChat(chat_id=channel_id)
    return None


def _restriction_reason(entity) -> str | None:
    restricted = getattr(entity, "restricted", False)
    reasons = getattr(entity, "restriction_reason", None) or []

    if restricted:
        return "restricted"
    if reasons:
        platform_reasons = []
        for reason in reasons:
            text = getattr(reason, "reason", None) or getattr(reason, "text", None)
            if text:
                platform_reasons.append(str(text))
        return ",".join(platform_reasons) or "restriction_reason"

    return None


# ----------------------- THROTTLE RESOLVE -----------------------
async def throttle_resolve(r, account_id: int):
    """Global per-account resolve throttle via Redis."""
    while True:
        now = asyncio.get_running_loop().time()
        key = RedisKeys.last_resolve_time(account_id)
        last = float(await r.get(key) or 0)
        elapsed = now - last
        if elapsed >= RESOLVE_MIN_GAP:
            await r.set(key, now, ex=60)
            break
        await asyncio.sleep(RESOLVE_MIN_GAP - elapsed)


# ----------------------- RESOLVE PEER STATUS -----------------------
async def resolve_peer_status(
    client: TelegramClient,
    username: str,
    is_adults: bool,
    r,
    account_id: int,
    force_refresh: bool = False,
) -> PeerResolveResult:
    """
    Universal peer resolver.
    1. Check DB cache first
    2. If valid cache → return immediately (no API call)
    3. If no cache or force_refresh → resolve via get_entity
    4. Save to DB and return
    """

    # 1. check cache
    if not force_refresh:
        cached = await get_channel_by_username(username)
        if cached and cached.get("access_hash") and cached.get("peer_type"):
            log_info(
                logger,
                "Using cached peer",
                "peer_cache_hit",
                username=username,
                peer_type=cached["peer_type"],
            )
            return PeerResolveResult(status="ok", info=cached)
        log_info(
            logger,
            "Peer cache miss. Resolving via API",
            "peer_cache_miss",
            username=username,
        )

    # 2. resolve via API
    try:
        await throttle_resolve(r, account_id)
        entity = await client.get_entity(username)
        restriction_reason = _restriction_reason(entity)
        if restriction_reason:
            log_warning(
                logger,
                "Peer is restricted",
                "peer_restricted",
                username=username,
                reason=restriction_reason,
            )
            return PeerResolveResult(
                status="permanent_failure",
                reason=restriction_reason,
                error_type="RestrictedPeer",
            )

        peer_type = detect_peer_type(entity)

        info = {
            "channel_id":    str(entity.id),
            "access_hash":   str(getattr(entity, "access_hash", 0)),
            "peer_type":     peer_type,
            "title":         getattr(entity, "title", username),
            "username":      getattr(entity, "username", None),
            "type":          "channel",
            "is_private":    getattr(entity, "username", None) is None,
            "is_adults":     is_adults,
            "description":   getattr(entity, "about", None),
            "members_count": getattr(entity, "participants_count", None),
            "photo_url":     None,
        }

        log_info(
            logger,
            "Peer resolved",
            "peer_resolved",
            username=username,
            peer_type=peer_type,
            telegram_id=entity.id,
        )
        return PeerResolveResult(status="ok", info=info)

    except FloodWaitError:
        raise

    except PERMANENT_PEER_ERRORS as e:
        log_warning(
            logger,
            "Cannot access peer",
            "peer_access_denied",
            username=username,
            error=str(e),
        )
        return PeerResolveResult(
            status="permanent_failure",
            reason=e.__class__.__name__,
            error_type=e.__class__.__name__,
            error=str(e),
        )

    except Exception as e:
        log_error(
            logger,
            "Peer resolve failed",
            "peer_resolve_failed",
            username=username,
            error=str(e),
        )
        return PeerResolveResult(
            status="transient_failure",
            reason="peer_resolve_failed",
            error_type=e.__class__.__name__,
            error=str(e),
        )


# ----------------------- RESOLVE PEER -----------------------
async def resolve_peer(
    client: TelegramClient,
    username: str,
    is_adults: bool,
    r,
    account_id: int,
    force_refresh: bool = False,
) -> dict | None:
    result = await resolve_peer_status(
        client,
        username,
        is_adults,
        r,
        account_id,
        force_refresh=force_refresh,
    )
    return result.info if result.status == "ok" else None
