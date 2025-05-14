import redis.asyncio as redis
from typing import Optional
import logging

# Импортируем настройки для доступа к REDIS_URL
from app.core.config import settings

logger = logging.getLogger(__name__)

class RedisClientManager:
    _redis_client: Optional[redis.Redis] = None
    _redis_url_used: Optional[str] = None # Store the URL used for initialization

    @property
    async def redis_client(self) -> redis.Redis:
        """
        Предоставляет экземпляр клиента Redis.
        Вызовет RuntimeError, если клиент не был инициализирован через setup_redis_client.
        """
        if self._redis_client is None:
            logger.warning("Redis client was accessed before explicit setup. Attempting default setup.")
            try:
                # Use stored URL if available, otherwise default from settings
                await self.setup_redis_client(redis_url=self._redis_url_used or str(settings.REDIS_URL))
            except Exception as e:
                logger.error(f"Default setup for Redis client failed: {e}", exc_info=True)
                # _redis_client мог быть установлен в None методом setup_redis_client при ошибке, или не установлен вовсе.
                # Мы должны вызвать исключение здесь, чтобы предотвратить возврат None.
                raise RuntimeError(f"Redis client is not available after default setup attempt failed: {e}")

        if self._redis_client is None: # Эта проверка не должна срабатывать, если setup_redis_client вызывает исключение при ошибке
            logger.error("Redis client is None even after setup attempt. This indicates an issue in setup_redis_client logic if it didn't raise.")
            raise RuntimeError("Redis client is not available (None after setup).")
        return self._redis_client

    async def setup_redis_client(self, client: Optional[redis.Redis] = None, redis_url: Optional[str] = None):
        """
        Инициализирует клиент Redis.
        Может принять существующий клиент, URL Redis или использовать URL из настроек.
        """
        if self._redis_client and hasattr(self._redis_client, 'ping'): # Check for actual client, not str
            try:
                await self._redis_client.ping() # Verify connection if already initialized
                logger.debug("Redis client already initialized and connected.")
                return
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, AttributeError) as e:
                logger.warning(f"Existing Redis client ping failed or client was not a Redis instance: {e}. Re-initializing.")
                self._redis_client = None # Force re-initialization
            except Exception as e: # Catch other unexpected errors during ping
                logger.warning(f"Unexpected error with existing Redis client: {e}. Re-initializing.")
                self._redis_client = None # Force re-initialization

        if client and isinstance(client, redis.Redis): # Ensure client is a Redis instance
            self._redis_client = client
            self._redis_url_used = None # Clear URL if direct client is provided
            logger.info("Using provided Redis client instance.")
        elif redis_url:
            self._redis_client = redis.from_url(redis_url)
            self._redis_url_used = redis_url # Store the URL
            logger.info(f"Initialized Redis client from URL: {redis_url}")
        elif settings.REDIS_URL:
            self._redis_client = redis.from_url(str(settings.REDIS_URL))
            self._redis_url_used = str(settings.REDIS_URL) # Store the URL
            logger.info(f"Initialized Redis client from settings REDIS_URL.")
        else:
            logger.error("Cannot initialize Redis client: No client instance, redis_url, or settings.REDIS_URL provided.")
            raise ValueError("Redis client cannot be initialized without a client instance or Redis URL.")
        
        try:
            # Проверка соединения (опционально, т.к. клиент подключается лениво)
            # Но полезно для раннего обнаружения проблем конфигурации.
            if self._redis_client:
                await self._redis_client.ping()
                logger.info("Successfully connected to Redis and pinged.")
        except Exception as e:
            logger.error(f"Failed to connect to Redis after initialization: {e}", exc_info=True)
            current_client = self._redis_client
            self._redis_client = None 
            if current_client:
                try:
                    await current_client.close() # Attempt to close the faulty client
                except Exception as close_e:
                    logger.error(f"Error closing faulty Redis client: {close_e}", exc_info=True)
            raise 

    async def close_redis_resources(self):
        """
        Закрывает соединение с Redis, если оно было установлено.
        """
        if self._redis_client:
            logger.info("Closing Redis client connection.")
            try:
                await self._redis_client.close()
                # await self._redis_client.connection_pool.disconnect() # Для некоторых версий/конфигураций
            except Exception as e:
                logger.error(f"Error closing Redis client: {e}", exc_info=True)
            finally:
                self._redis_client = None
                self._redis_url_used = None # Clear stored URL on close

    async def is_redis_client_available(self) -> bool:
        """Checks if the Redis client is initialized and can be pinged."""
        if self._redis_client is None:
            return False
        try:
            await self._redis_client.ping()
            return True
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, AttributeError) as e:
            logger.warning(f"Redis client ping failed or client is not a valid Redis instance: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error pinging Redis: {e}", exc_info=True)
            return False

    # Эти методы setup/cleanup предназначены для использования в классах,
    # которые наследуют RedisClientManager и, возможно, RunnableComponent.
    async def setup(self):
        """
        Метод настройки, который может быть вызван в жизненном цикле компонента.
        Инициализирует клиент Redis, используя URL из настроек по умолчанию.
        """
        logger.debug(f"Executing RedisClientManager setup for {self.__class__.__name__}")
        # Use stored URL if available from a previous setup, otherwise default from settings
        await self.setup_redis_client(redis_url=self._redis_url_used or str(settings.REDIS_URL))
        # Если есть родительский setup, его нужно вызвать явно в наследующем классе,
        # так как здесь мы не знаем о структуре наследования выше RedisClientManager.
        # if hasattr(super(), 'setup'):
        #     await super().setup()

    async def cleanup(self):
        """
        Метод очистки, который может быть вызван в жизненном цикле компонента.
        Закрывает соединение с Redis.
        """
        logger.debug(f"Executing RedisClientManager cleanup for {self.__class__.__name__}")
        await self.close_redis_resources()
        # Аналогично setup, вызов super().cleanup() должен быть в наследующем классе.
        # if hasattr(super(), 'cleanup'):
        #     await super().cleanup()
