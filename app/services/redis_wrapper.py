"""
Простая обертка для Redis сервиса для совместимости
"""

import logging
from typing import Optional, Any
import redis.asyncio as redis
from app.services.redis_service import init_redis_pool, close_redis_pool, _redis_pool

class RedisService:
    """Простая обертка Redis сервиса для совместимости с голосовыми сервисами"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.client: Optional[redis.Redis] = None
        self._pool: Optional[redis.ConnectionPool] = None

    async def initialize(self) -> None:
        """Инициализация Redis клиента"""
        try:
            self._pool = await init_redis_pool()
            if self._pool:
                self.client = redis.Redis.from_pool(self._pool)
                await self.client.ping()
                self.logger.info("Redis service initialized")
            else:
                raise RuntimeError("Failed to initialize Redis pool")
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis service: {e}")
            raise

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.client:
            await self.client.close()
            self.client = None
        if self._pool:
            await close_redis_pool()
            self._pool = None
        self.logger.info("Redis service cleaned up")

    async def get(self, key: str) -> Optional[str]:
        """Получить значение по ключу"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Установить значение"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.set(key, value, ex=ex)

    async def setex(self, key: str, time: int, value: str) -> bool:
        """Установить значение с TTL"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.setex(key, time, value)

    async def delete(self, key: str) -> int:
        """Удалить ключ"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.delete(key)

    async def publish(self, channel: str, message: str) -> int:
        """Опубликовать сообщение в канал"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.publish(channel, message)

    def pipeline(self):
        """Создать pipeline для батчевых операций"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return self.client.pipeline()

    async def zadd(self, key: str, mapping: dict) -> int:
        """Добавить элементы в sorted set"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.zadd(key, mapping)

    async def zcard(self, key: str) -> int:
        """Получить количество элементов в sorted set"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.zcard(key)

    async def zremrangebyrank(self, key: str, start: int, end: int) -> int:
        """Удалить элементы из sorted set по рангу"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.zremrangebyrank(key, start, end)

    async def expire(self, key: str, time: int) -> bool:
        """Установить TTL для ключа"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.expire(key, time)

    async def lpush(self, key: str, *values) -> int:
        """Добавить элементы в начало списка"""
        if not self.client:
            raise RuntimeError("Redis service not initialized")
        return await self.client.lpush(key, *values)
