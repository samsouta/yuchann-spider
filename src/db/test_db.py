# test_db.py
import asyncio
from connection import get_pool, close_pool

async def test():
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT COUNT(*) FROM \"Channel\"")
        print(f"✅ Connected! Channels in DB: {result}")
        print(__name__)
    await close_pool()

asyncio.run(test())