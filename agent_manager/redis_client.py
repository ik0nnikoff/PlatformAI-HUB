import logging
from typing import AsyncGenerator
import redis.asyncio as redis
from fastapi import HTTPException, status

from .config import REDIS_URL

logger = logging.getLogger(__name__)

redis_pool = None

async def init_redis_pool():
    """Initializes the Redis connection pool."""
    global redis_pool
    try:
        redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
        # Test connection
        client = redis.Redis.from_pool(redis_pool)
        await client.ping()
        await client.close() # Close the test client, pool remains
        logger.info("Redis connection pool initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Redis connection pool: {e}")
        redis_pool = None # Ensure pool is None if init fails

async def close_redis_pool():
    """Closes the Redis connection pool."""
    global redis_pool
    if redis_pool:
        logger.info("Closing Redis connection pool.")
        # redis-py's pool doesn't have an explicit close, rely on connections closing
        # Forcing disconnect might be needed if connections linger
        # await redis_pool.disconnect() # Check if available in your version
        redis_pool = None

async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    """FastAPI dependency to get a Redis connection from the pool."""
    if not redis_pool:
        logger.error("Redis pool is not available.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis service not available.",
        )
    try:
        client = redis.Redis.from_pool(redis_pool)
        yield client
    finally:
        # Connections borrowed from the pool are typically returned automatically.
        # Explicit close might be needed depending on usage pattern, but usually not required here.
        await client.close() # Ensure connection is closed/returned after use
