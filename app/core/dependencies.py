import logging
from typing import AsyncGenerator

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.services.redis_service import get_redis_pool

logger = logging.getLogger(__name__)

# Global instance cache for ImageOrchestrator
_image_orchestrator_instance = None

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI зависимость для получения сессии базы данных."""
    if not SessionLocal:
        logger.error("SessionLocal is not initialized. Check db.session.py.")
        # В реальном приложении здесь может быть HTTPException
        # или другая обработка ошибки конфигурации.
        # Для простоты пока просто вернем None, но это приведет к ошибкам дальше.
        yield None # Это плохая практика, но для демонстрации этапа
        return

    async with SessionLocal() as session:
        try:
            yield session
        except Exception as e:
            logger.error(f"Error in DB session: {e}", exc_info=True)
            # В зависимости от политики, можно сделать await session.rollback()
            # или перевыбросить исключение.
            # Важно не коммитить сессию здесь, если она не должна быть закомичена вызывающим кодом.
            raise # Перевыбрасываем исключение, чтобы FastAPI мог его обработать
        finally:
            # Обычно закрытие сессии происходит автоматически при выходе из контекстного менеджера
            # await session.close() # Не обязательно, если SessionLocal настроен правильно
            pass

async def get_redis_client() -> AsyncGenerator[redis.Redis, None]:
    """FastAPI зависимость для получения Redis клиента из пула."""
    redis_pool = get_redis_pool() # Получаем пул из redis_service
    if not redis_pool:
        logger.error("Redis pool is not initialized. Check services.redis_service.py.")
        # Аналогично get_db, здесь нужна корректная обработка ошибки конфигурации
        yield None
        return

    client: redis.Redis = None
    try:
        client = redis.Redis.from_pool(redis_pool)
        yield client
    except Exception as e:
        logger.error(f"Error getting Redis client: {e}", exc_info=True)
        raise
    finally:
        if client:
            await client.close() # Закрываем соединение, возвращая его в пул


async def get_image_orchestrator():
    """
    FastAPI зависимость для получения экземпляра ImageOrchestrator.
    Использует глобальный экземпляр (singleton pattern) для эффективности.
    """
    global _image_orchestrator_instance
    
    if _image_orchestrator_instance is None:
        try:
            from app.services.media.image_orchestrator import ImageOrchestrator
            _image_orchestrator_instance = ImageOrchestrator()
            await _image_orchestrator_instance.initialize()
            logger.info("ImageOrchestrator initialized in dependencies")
        except Exception as e:
            logger.error(f"Failed to initialize ImageOrchestrator: {e}", exc_info=True)
            raise
    
    return _image_orchestrator_instance
