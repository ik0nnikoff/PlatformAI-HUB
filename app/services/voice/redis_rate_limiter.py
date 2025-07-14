"""
Redis-based Rate Limiter для голосовых сервисов
"""

import time
import logging
from typing import Optional
from app.services.redis_wrapper import RedisService


class RedisRateLimiter:
    """
    Redis-based rate limiter с sliding window алгоритмом
    """
    
    def __init__(self, 
                 redis_service: RedisService,
                 max_requests: int, 
                 window_seconds: int = 60,
                 key_prefix: str = "voice_rate_limit:",
                 logger: Optional[logging.Logger] = None):
        """
        Args:
            redis_service: Redis service wrapper
            max_requests: Максимальное количество запросов в окне
            window_seconds: Размер окна в секундах
            key_prefix: Префикс для ключей Redis
            logger: Логгер
        """
        self.redis_service = redis_service
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.logger = logger or logging.getLogger("redis_rate_limiter")
        
    async def is_allowed(self, user_id: str) -> bool:
        """
        Проверяет, разрешен ли запрос для пользователя
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            True если запрос разрешен
        """
        try:
            key = f"{self.key_prefix}{user_id}"
            now = time.time()
            pipeline = self.redis_service.pipeline()
            
            # Удаляем старые записи (outside window)
            pipeline.zremrangebyscore(key, 0, now - self.window_seconds)
            
            # Получаем количество текущих запросов
            pipeline.zcard(key)
            
            # Добавляем текущий запрос
            pipeline.zadd(key, {str(now): now})
            
            # Устанавливаем TTL
            pipeline.expire(key, self.window_seconds + 1)
            
            results = await pipeline.execute()
            current_count = results[1]  # Результат zcard
            
            if current_count >= self.max_requests:
                # Удаляем добавленный запрос, так как лимит превышен
                await self.redis_service.zrem(key, str(now))
                self.logger.warning(f"Rate limit exceeded for user {user_id}: {current_count}/{self.max_requests}")
                return False
                
            self.logger.debug(f"Rate limit OK for user {user_id}: {current_count + 1}/{self.max_requests}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error checking rate limit for user {user_id}: {e}", exc_info=True)
            # В случае ошибки Redis разрешаем запрос
            return True
    
    async def get_remaining_requests(self, user_id: str) -> int:
        """
        Получает количество оставшихся запросов для пользователя
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            Количество оставшихся запросов
        """
        try:
            key = f"{self.key_prefix}{user_id}"
            now = time.time()
            
            # Очищаем старые записи и получаем текущее количество
            pipeline = self.redis_service.pipeline()
            pipeline.zremrangebyscore(key, 0, now - self.window_seconds)
            pipeline.zcard(key)
            
            results = await pipeline.execute()
            current_count = results[1]
            
            return max(0, self.max_requests - current_count)
            
        except Exception as e:
            self.logger.error(f"Error getting remaining requests for user {user_id}: {e}")
            return self.max_requests  # В случае ошибки возвращаем максимум
    
    async def get_reset_time(self, user_id: str) -> float:
        """
        Получает время до сброса лимита для пользователя
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            Время в секундах до сброса
        """
        try:
            key = f"{self.key_prefix}{user_id}"
            now = time.time()
            
            # Получаем самую старую запись в окне
            oldest_entries = await self.redis_service.zrange(
                key, 0, 0, withscores=True
            )
            
            if not oldest_entries:
                return 0.0
                
            oldest_time = oldest_entries[0][1]  # Score (timestamp)
            reset_time = oldest_time + self.window_seconds - now
            
            return max(0.0, reset_time)
            
        except Exception as e:
            self.logger.error(f"Error getting reset time for user {user_id}: {e}")
            return 0.0
    
    async def clear_user_limit(self, user_id: str) -> bool:
        """
        Очищает лимит для пользователя (админская функция)
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            True если успешно
        """
        try:
            key = f"{self.key_prefix}{user_id}"
            await self.redis_service.delete(key)
            self.logger.info(f"Cleared rate limit for user {user_id}")
            return True
        except Exception as e:
            self.logger.error(f"Error clearing rate limit for user {user_id}: {e}")
            return False

    async def get_user_stats(self, user_id: str) -> dict:
        """
        Получает статистику использования для пользователя
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            Словарь со статистикой
        """
        try:
            key = f"{self.key_prefix}{user_id}"
            now = time.time()
            
            # Очищаем старые записи
            await self.redis_service.zremrangebyscore(key, 0, now - self.window_seconds)
            
            # Получаем текущее количество и время последнего запроса
            pipeline = self.redis_service.pipeline()
            pipeline.zcard(key)
            pipeline.zrange(key, -1, -1, withscores=True)  # Последний элемент
            
            results = await pipeline.execute()
            current_count = results[0]
            last_request_data = results[1]
            
            last_request_time = None
            if last_request_data:
                last_request_time = last_request_data[0][1]  # Score (timestamp)
            
            return {
                "user_id": user_id,
                "current_requests": current_count,
                "max_requests": self.max_requests,
                "remaining_requests": max(0, self.max_requests - current_count),
                "window_seconds": self.window_seconds,
                "last_request_time": last_request_time,
                "reset_time_seconds": await self.get_reset_time(user_id)
            }
            
        except Exception as e:
            self.logger.error(f"Error getting stats for user {user_id}: {e}")
            return {
                "user_id": user_id,
                "error": str(e)
            }
