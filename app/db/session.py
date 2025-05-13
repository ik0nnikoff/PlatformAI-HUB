import logging
from typing import AsyncGenerator # Added import
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.core.config import settings # Стало: импортируем settings

logger = logging.getLogger(__name__)

engine = None
SessionLocal = None # Для async_sessionmaker

if not settings.DATABASE_URL: # Стало: используем settings.DATABASE_URL
    logger.critical("DATABASE_URL is not set in the environment variables.")
    # В реальном приложении здесь может быть более строгая обработка.
else:
    # Логируем без учетных данных, если URL содержит их
    db_url_display = settings.DATABASE_URL # Стало: используем settings.DATABASE_URL
    if "@" in settings.DATABASE_URL: # Стало: используем settings.DATABASE_URL
        db_url_display = settings.DATABASE_URL.split("@")[-1] # Стало: используем settings.DATABASE_URL
    logger.info(f"Database URL configured for: {db_url_display}")
    engine = create_async_engine(settings.DATABASE_URL, echo=False) # Стало: используем settings.DATABASE_URL
    SessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False
    )

# Base будет использоваться моделями SQLAlchemy для их определения.
# Модели будут импортировать Base из этого файла.
Base = declarative_base()

def get_async_session_factory(): # Новая функция
    """Возвращает фабрику асинхронных сессий."""
    if not SessionLocal:
        logger.critical("SessionLocal is not initialized. Call init_db or check DATABASE_URL.")
        # Можно возбудить исключение, если это критично для старта приложения
        # raise RuntimeError("SessionLocal is not initialized.")
        return None # Или вернуть None, чтобы обработать выше
    return SessionLocal

async def init_db():
    """Инициализирует базу данных, создавая все таблицы (если они еще не существуют).
    Внимание: Для управления структурой БД в продакшене рекомендуется использовать Alembic.
    """
    if not engine:
        logger.error("Database engine is not initialized. Cannot create tables.")
        return
    try:
        async with engine.begin() as conn:
            # Раскомментируйте следующую строку, чтобы удалить все таблицы перед созданием (ОСТОРОЖНО!)
            # await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables checked/created (if not managed by Alembic).")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}", exc_info=True)
        # В зависимости от политики, здесь можно перевыбросить исключение или обработать иначе

async def close_db_engine():
    """Закрывает соединение с базой данных (engine)."""
    if engine:
        try:
            await engine.dispose()
            logger.info("Database engine closed successfully.")
        except Exception as e:
            logger.error(f"Error closing database engine: {e}", exc_info=True)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an asynchronous database session.
    """
    if not SessionLocal:
        logger.error("SessionLocal is not initialized. Database might not be available.")
        # Depending on the application's needs, you might want to raise an HTTPException here
        # For example: raise HTTPException(status_code=503, detail="Database not available")
        # For now, we'll let it proceed, and it will likely fail if a session is truly needed.
        # Or, more strictly, raise a RuntimeError if the application cannot function without a DB.
        raise RuntimeError("SessionLocal is not initialized. Ensure the database is configured and init_db has been called if necessary.")
    
    async with SessionLocal() as session:
        try:
            yield session
            # Note: Commits should ideally be handled at the end of a successful request/operation unit.
            # For FastAPI dependencies, it's common to commit in the endpoint or service layer
            # after all operations using the session are complete.
            # If you want to auto-commit after each dependency usage (less common for complex ops):
            # await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise # Re-raise the exception to be handled by FastAPI error handlers
        finally:
            await session.close()
