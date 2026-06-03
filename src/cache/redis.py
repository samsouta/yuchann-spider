#cache.redis.py
import os
from dotenv import load_dotenv
import redis.asyncio as aioredis
load_dotenv()

_redis = None

async def get_redis():
    global _redis
    try:
        if _redis is None:
            _redis = await aioredis.from_url(
                os.getenv("REDIS_URL"),
                decode_responses=True,
                socket_keepalive=True,         
                socket_connect_timeout=10,
                retry_on_timeout=True,          
                health_check_interval=30,       
            )
        await _redis.ping()
        return _redis
    except Exception:
        _redis = None
        _redis = await aioredis.from_url(
            os.getenv("REDIS_URL"),
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None