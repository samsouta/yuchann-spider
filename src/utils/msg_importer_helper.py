#utils/msg_importer_helper.py
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageEntityUrl,
    MessageEntityTextUrl,
)
from cache.redis import get_redis
from config.redis_keys import RedisKeys

# ------------------- GET / SAVE LAST ID -------------------
async def get_last_message_id(channel_username: str) -> int:
    r = await get_redis()
    val = await r.get(RedisKeys.last_id(channel_username))
    return int(val) if val else 0


async def save_last_message_id(channel_username: str, last_id: int):
    r = await get_redis()
    await r.set(RedisKeys.last_id(channel_username), str(last_id))


# ------------------- DETECT MESSAGE TYPE -------------------
def detect_message_type(msg) -> str:
    if msg.media:
        if isinstance(msg.media, MessageMediaPhoto):
            return "IMAGE"
        if isinstance(msg.media, MessageMediaDocument):
            mime = getattr(msg.media.document, "mime_type", "")
            if "video" in mime:
                return "VIDEO"
    if msg.entities:
        for e in msg.entities:
            if isinstance(e, (MessageEntityUrl, MessageEntityTextUrl)):
                return "LINK"
    return "TEXT"

# ------------------ EXTRACT ENTITIES ------------------
def extract_entities(msg) -> list:
    entities = []
    if not msg.entities:
        return entities

    text = msg.message or ""
    for entity in msg.entities:
        entity_type  = type(entity).__name__
        start        = entity.offset
        end          = entity.offset + entity.length
        entity_value = text[start:end]

        if entity_value:
            entities.append({
                "entity_type":  entity_type,
                "entity_value": entity_value,
            })
    return entities
