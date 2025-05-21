import logging
from typing import Optional
import redis.asyncio as redis # Используем redis.asyncio
from redis.exceptions import ConnectionError as RedisConnectionError # Добавлено

from app.core.config import settings # Стало: импортируем settings

logger = logging.getLogger(__name__)

_redis_pool: Optional[redis.ConnectionPool] = None

async def init_redis_pool() -> Optional[redis.ConnectionPool]:
    """Инициализирует пул соединений Redis и возвращает его."""
    global _redis_pool
    if (_redis_pool):
        logger.info("Redis connection pool already initialized.")
        return _redis_pool
    try:
        _redis_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True) # Стало: используем settings.REDIS_URL
        # Тестируем соединение
        client = redis.Redis.from_pool(_redis_pool)
        await client.ping()
        await client.close() # Закрываем тестовый клиент, пул остается
        logger.info("Redis connection pool initialized successfully.")
        return _redis_pool
    except RedisConnectionError as e: # Изменено: используется импортированный RedisConnectionError
        logger.error(f"Failed to connect to Redis: {e}", exc_info=True)
        _redis_pool = None # Сбрасываем пул в случае ошибки
        # В реальном приложении здесь может быть более строгая обработка,
        # например, FastAPI может не стартовать.
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during Redis pool initialization: {e}", exc_info=True)
        _redis_pool = None
        return None

async def close_redis_pool():
    """Закрывает пул соединений Redis."""
    global _redis_pool
    if (_redis_pool):
        try:
            await _redis_pool.disconnect()
            logger.info("Redis connection pool closed successfully.")
        except Exception as e:
            logger.error(f"Error closing Redis connection pool: {e}", exc_info=True)
        finally:
            _redis_pool = None
    else:
        logger.info("Redis connection pool was not initialized or already closed.")

def get_redis_pool() -> Optional[redis.ConnectionPool]:
    """Возвращает существующий пул соединений Redis."""
    if not _redis_pool:
        logger.warning("Redis pool accessed before initialization or after closure.")
    return _redis_pool

async def get_redis_client() -> redis.Redis: # Changed redis.asyncio.Redis to redis.Redis
    """
    FastAPI dependency that provides a Redis client instance configured with the global pool.
    The pool (redis_pool) must be initialized beforehand (e.g., during app startup).
    """
    pool = get_redis_pool() # ИЗМЕНЕНО: убран await, так как get_redis_pool() синхронная
    # Create a new client instance using the connection pool.
    # Connections are managed by the client/pool automatically.
    return redis.Redis(connection_pool=pool)

# Можно добавить другие функции для работы с Redis, если это необходимо,
# например, для публикации сообщений, работы с очередями и т.д.,
# чтобы инкапсулировать эту логику здесь.
# Пример:
# async def publish_message(channel: str, message: str):
#     if not _redis_pool:
#         logger.error("Cannot publish message: Redis pool not available.")
#         return
#     try:
#         client = redis.Redis.from_pool(_redis_pool)
#         await client.publish(channel, message)
#         await client.close()
#     except Exception as e:
#         logger.error(f"Error publishing Redis message to {channel}: {e}", exc_info=True)
