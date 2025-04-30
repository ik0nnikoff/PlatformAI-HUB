import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
import sys # Добавляем импорт sys

from .config import DATABASE_URL # Import from config

logger = logging.getLogger(__name__)

# --- ИЗМЕНЕНИЕ: Явная настройка логгера для этого модуля ---
# Устанавливаем уровень
logger.setLevel(logging.INFO)
# Создаем обработчик (вывод в stdout)
handler = logging.StreamHandler(sys.stdout)
# Создаем простой форматтер, не использующий agent_id или другие специфичные поля
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
# Удаляем существующие обработчики, чтобы избежать дублирования
if logger.hasHandlers():
    logger.handlers.clear()
# Добавляем наш обработчик
logger.addHandler(handler)
# Предотвращаем передачу сообщений корневому логгеру
logger.propagate = False
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

if not DATABASE_URL:
    logger.warning("DATABASE_URL not set in environment variables. Database features will be disabled.")
    engine = None
    SessionLocal = None
    Base = declarative_base() # Still need Base for models
else:
    try:
        # echo=True is useful for debugging SQL queries
        engine = create_async_engine(DATABASE_URL, echo=False, future=True)
        SessionLocal = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False, # Recommended for FastAPI background tasks
            autoflush=False,
            autocommit=False
        )
        Base = declarative_base()
        logger.info("SQLAlchemy async engine and session maker configured.")
    except Exception as e:
        logger.error(f"Failed to configure SQLAlchemy: {e}", exc_info=True)
        engine = None
        SessionLocal = None
        Base = declarative_base()

async def init_db():
    """Initializes the database connection (called on startup)."""
    if not engine:
        logger.warning("Database engine not initialized. Skipping DB init.")
        return
    # In a real application, you might run migrations here or ensure connection
    logger.info("Database connection pool initialized.")
    # --- ИЗМЕНЕНИЕ: Создание таблиц ---
    try:
        async with engine.begin() as conn:
            # This will create tables based on Base.metadata if they don't exist
            # Ensure all models inheriting from Base are imported before this runs
            # (usually happens when importing models in main.py or crud.py)
            from .models import Base # Импортируем Base здесь, чтобы все модели были загружены
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables checked/created successfully.")
    except Exception as e:
        logger.error(f"Error during table creation/check: {e}", exc_info=True)
        # Consider if the application should stop if table creation fails
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # Example check: Try connecting
    try:
        async with engine.connect() as conn:
            logger.info("Successfully connected to the database.")
    except Exception as e:
        logger.error(f"Failed to connect to the database during init: {e}")

async def close_db_engine():
    """Closes the database engine (called on shutdown)."""
    if engine:
        await engine.dispose()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency to get a database session."""
    if not SessionLocal:
        logger.error("Database SessionLocal not configured.")
        logger.error("Database SessionLocal not configured.")
        # Depending on strictness, could raise HTTPException here
        yield None # Or raise? For now, yield None to avoid breaking things if DB is optional
        return

    async with SessionLocal() as session:
        try:
            yield session
            # Note: Commit/rollback logic should be handled within the CRUD operations or endpoint logic
            # await session.commit() # Generally avoid auto-commit in dependency
        except Exception:
            # logger.error("Database session error", exc_info=True) # Logged by generic handler
            await session.rollback()
            raise
        finally:
            # Session is automatically closed by the context manager
            pass

