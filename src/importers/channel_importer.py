# importers/channel_importer.py
from telethon import TelegramClient
from utils.logger import get_logger
from utils.peer_resolver import resolve_peer, resolve_peer_status

logger = get_logger(__name__)


async def fetch_channel_info(
    client: TelegramClient,
    username: str,
    is_adults: bool,
    r,
    account_id: int,
) -> dict | None:
    return await resolve_peer(client, username, is_adults, r, account_id)


async def fetch_channel_info_status(
    client: TelegramClient,
    username: str,
    is_adults: bool,
    r,
    account_id: int,
    force_refresh: bool = False,
):
    return await resolve_peer_status(
        client,
        username,
        is_adults,
        r,
        account_id,
        force_refresh=force_refresh,
    )
