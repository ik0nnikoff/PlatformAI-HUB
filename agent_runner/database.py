import logging
import asyncio
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, func, Integer, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

# Импортируем DATABASE_URL из config.py
from .config import DATABASE_URL

logger = logging.getLogger("agent_runner.database") # Иерархическое имя логгера

db_engine = None
AsyncSessionLocal = None
Base = declarative_base()

# Определяем модель здесь. В идеале, она должна быть в общем пакете.
class ChatMessageDB(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, nullable=False, index=True)
    thread_id = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    sender_type = Column(String, nullable=False) # 'user', 'agent'
    content = Column(Text, nullable=False)

async def init_db():
    """Initializes the database engine and session maker."""
    global db_engine, AsyncSessionLocal
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set. Chat history saving is disabled.")
        return

    if db_engine: # Avoid re-initialization
        logger.debug("Database engine already initialized.")
        return

    try:
        db_engine = create_async_engine(DATABASE_URL, echo=False, future=True, pool_pre_ping=True)
        # Test connection
        async with db_engine.connect() as conn:
            await conn.run_sync(lambda sync_conn: None)
        AsyncSessionLocal = async_sessionmaker(
            bind=db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        logger.info("Database engine initialized successfully for runner.")
    except Exception as e:
        logger.error(f"Failed to initialize database engine: {e}", exc_info=True)
        db_engine = None
        AsyncSessionLocal = None

async def close_db():
    """Closes the database engine."""
    global db_engine, AsyncSessionLocal
    if db_engine:
        logger.info("Closing database engine.")
        await db_engine.dispose()
        db_engine = None
        AsyncSessionLocal = None

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[Optional[AsyncSession], None]:
    """Provides a transactional scope around a series of operations."""
    if not AsyncSessionLocal:
        logger.error("Database session factory not available (AsyncSessionLocal is None).")
        yield None
        return

    session: AsyncSession = AsyncSessionLocal()
    try:
        yield session
    except SQLAlchemyError as e:
        logger.error(f"Database session error: {e}", exc_info=True)
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type((SQLAlchemyError, asyncio.TimeoutError)),
    reraise=True
)
async def save_chat_message(
    agent_id: str,
    thread_id: str,
    channel: Optional[str],
    sender_type: str,
    content: str,
    log_adapter: logging.LoggerAdapter # Передаем адаптер для корректного agent_id в логах
):
    """Saves a chat message to the database with retry logic."""
    if not AsyncSessionLocal:
        log_adapter.warning("Database not configured. Skipping chat message saving.")
        return

    log_adapter.debug(f"Attempting to save message: Thread={thread_id}, Sender={sender_type}")
    try:
        async with get_db_session() as session:
            if session is None:
                log_adapter.error("Failed to get DB session. Skipping message saving.")
                return

            message_db = ChatMessageDB(
                agent_id=agent_id,
                thread_id=thread_id,
                channel=channel,
                sender_type=sender_type,
                content=content
            )
            session.add(message_db)
            await session.commit()
            log_adapter.info(f"Successfully saved message from '{sender_type}' to DB (Thread: {thread_id})")
    except Exception as e:
        log_adapter.error(f"Failed to save message to DB (attempt info in retry log): {e}", exc_info=True)
        raise # Re-raise for tenacity