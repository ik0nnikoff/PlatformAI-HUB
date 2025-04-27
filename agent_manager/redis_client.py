import logging
from typing import AsyncGenerator, Optional
import redis.asyncio as redis
from fastapi import HTTPException, status

from .config import REDIS_URL

logger = logging.getLogger(__name__)

redis_pool: Optional[redis.ConnectionPool] = None # Уточняем тип

async def init_redis_pool() -> Optional[redis.ConnectionPool]:
    """Initializes the Redis connection pool and returns it."""
    global redis_pool
    try:
        # Создаем пул и присваиваем его глобальной переменной
        redis_pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
        # Тестируем соединение
        client = redis.Redis.from_pool(redis_pool)
        await client.ping()
        await client.close() # Закрываем тестовый клиент, пул остается
        logger.info("Redis connection pool initialized successfully.")
        return redis_pool # Возвращаем созданный пул
    except Exception as e:
        logger.error(f"Failed to initialize Redis connection pool: {e}")
        redis_pool = None # Убедимся, что пул None при ошибке
        return None # Возвращаем None при ошибке

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
    client = None # Инициализируем client как None
    try:
        client = redis.Redis.from_pool(redis_pool)
        yield client
    finally:
        # --- НАЧАЛО ИЗМЕНЕНИЯ: Убираем явный close ---
        # При использовании пула соединений, явный вызов close() обычно не нужен,
        # так как пул управляет жизненным циклом соединений.
        # await client.close() # <--- Удаляем или комментируем эту строку
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        # Дополнительная проверка, если client был успешно создан
        if client:
             # Можно добавить логирование, если нужно отследить возврат в пул
             # logger.debug("Redis client yielded by dependency is being released.")
             pass # Ничего не делаем, пул сам обработает
