#worker/db/models.py
from db.connection import get_pool
from utils.normalize_datetime import normalize_dt


# ================= CHANNEL =================
async def upsert_channel(data: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO "Channel" (
                channel_id, access_hash, peer_type, title, username, type,
                is_private, is_adults, description,
                members_count, photo_url, created_at, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW(),NOW())
            ON CONFLICT (channel_id) DO UPDATE SET
                access_hash   = EXCLUDED.access_hash,
                peer_type     = EXCLUDED.peer_type,
                title         = COALESCE(EXCLUDED.title, "Channel".title),
                username      = COALESCE(EXCLUDED.username, "Channel".username),
                members_count = COALESCE(EXCLUDED.members_count, "Channel".members_count),
                photo_url     = COALESCE(EXCLUDED.photo_url, "Channel".photo_url),
                description   = COALESCE(EXCLUDED.description, "Channel".description),
                updated_at    = NOW()
            RETURNING id
        """,
            str(data["channel_id"]),
            int(data["access_hash"]) if data.get("access_hash") else None,
            data.get("peer_type", "CHANNEL"),
            data.get("title", ""),
            data.get("username"),
            data.get("type", "channel"),
            data.get("is_private", False),
            data.get("is_adults", False),
            data.get("description"),
            data.get("members_count"),
            data.get("photo_url"),
        )
        if row is None:
            raise Exception(f"upsert_channel failed for {data['channel_id']}")
        return row["id"]
    

async def get_channel_by_username(username: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id, channel_id, access_hash, peer_type FROM "Channel" WHERE username = $1',
            username
        )
        if row:
            return {
                "id":          row["id"],
                "channel_id":  row["channel_id"],
                "access_hash": str(row["access_hash"]) if row["access_hash"] else None,
                "peer_type":   row["peer_type"],
            }
        return None

# ================= SENDER =================
async def upsert_sender(data: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO "Sender" (
                telegram_id, username, display_name,
                is_bot, photo_url, created_at, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,NOW(),NOW())
            ON CONFLICT (telegram_id) DO UPDATE SET
                username     = EXCLUDED.username,
                display_name = EXCLUDED.display_name,
                updated_at   = NOW()
        """,
            str(data["telegram_id"]),
            data.get("username"),
            data.get("display_name"),
            data.get("is_bot", False),
            data.get("photo_url"),
        )

        row = await conn.fetchrow(
            'SELECT id FROM "Sender" WHERE telegram_id = $1',
            str(data["telegram_id"])
        )
        return row["id"]

# ================= MESSAGE =================
async def insert_message(data: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO "Message" (
                telegram_id, channel_id, sender_id,
                message_type, content_text, media_file_path,
                views, forwards, replies_count,
                posted_at, created_at, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW(),NOW())
            ON CONFLICT (telegram_id, channel_id) DO NOTHING
            RETURNING id
        """,
            str(data["telegram_id"]),
            data["channel_id"],
            data.get("sender_id"),
            data.get("message_type", "TEXT"),
            data.get("content_text"),
            data.get("media_file_path"),
            data.get("views"),
            data.get("forwards"),
            data.get("replies_count"),
            normalize_dt(data["posted_at"]),
        )

        if row is None:
            row = await conn.fetchrow(
                'SELECT id FROM "Message" WHERE telegram_id = $1 AND channel_id = $2',
                str(data["telegram_id"]), data["channel_id"]
            )

        return row["id"]

# ================= MESSAGE ENTITY =================
async def insert_message_entities(message_id: int, entities: list):
    if not entities:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO "MessageEntity" (
                message_id, entity_type, entity_value, created_at, updated_at
            )
            VALUES ($1, $2, $3, NOW(), NOW())
        """, [
            (message_id, e["entity_type"], e["entity_value"])
            for e in entities
        ])