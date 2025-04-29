import logging
import os
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
import sys # Добавляем импорт sys

# Настраиваем базовый логгер для этого модуля, чтобы избежать KeyError: 'agent_id'
# при импорте до основной настройки в runner.py
module_logger = logging.getLogger(__name__)
# --- ИЗМЕНЕНИЕ: Явная настройка module_logger ---
# Устанавливаем уровень
module_logger.setLevel(logging.INFO)
# Создаем обработчик (вывод в stdout)
handler = logging.StreamHandler(sys.stdout)
# Создаем простой форматтер, не использующий agent_id
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
# Удаляем существующие обработчики, чтобы избежать дублирования, если они есть
if module_logger.hasHandlers():
    module_logger.handlers.clear()
# Добавляем наш обработчик
module_logger.addHandler(handler)
# Предотвращаем передачу сообщений корневому логгеру, чтобы избежать двойного вывода
module_logger.propagate = False
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# Загружаем DATABASE_URL из .env
# Путь к .env относительно этого файла
current_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(current_dir, '..', '.env')
if os.path.exists(dotenv_path):
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=dotenv_path)
    module_logger.info(f"Agent Runner DB: Loaded environment variables from {dotenv_path}")
else:
    module_logger.warning(f"Agent Runner DB: .env file not found at {dotenv_path}")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    module_logger.warning("Agent Runner DB: DATABASE_URL not set. Database features requiring direct access will be disabled in runner.")
    engine = None
    SessionLocal = None
    Base = declarative_base() # Base все равно нужен для моделей, если они будут здесь
else:
    try:
        engine = create_async_engine(DATABASE_URL, echo=False, future=True)
        SessionLocal = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False
        )
        Base = declarative_base()
        module_logger.info("Agent Runner DB: SQLAlchemy async engine and session maker configured.")
    except Exception as e:
        module_logger.error(f"Agent Runner DB: Failed to configure SQLAlchemy: {e}", exc_info=True)
        engine = None
        SessionLocal = None
        Base = declarative_base()

# Функция для получения фабрики сессий (если SessionLocal успешно создан)
def get_db_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    """Returns the SQLAlchemy session factory if configured."""
    if not SessionLocal:
         module_logger.warning("Agent Runner DB: SessionLocal is not available.")
    return SessionLocal

# Функция для закрытия движка (если понадобится)
async def close_db_engine():
    """Closes the database engine."""
    if engine:
        await engine.dispose()
        module_logger.info("Agent Runner DB: Database engine disposed.")

