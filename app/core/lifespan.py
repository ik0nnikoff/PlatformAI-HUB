import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.logging_config import setup_logging
from app.db.session import close_db_engine
from app.services.redis_service import init_redis_pool, close_redis_pool
from app.core.config import settings

# Импорты для воркеров
from app.workers.history_saver_worker import main_loop as history_saver_main_loop
from app.workers.token_usage_worker import main_loop as token_usage_main_loop
from app.workers.inactivity_monitor_worker import main_loop as inactivity_monitor_main_loop

logger = logging.getLogger(__name__)

background_tasks = []

async def start_background_tasks(app: FastAPI):
    """Запускает все необходимые фоновые задачи."""
    logger.info("Starting background tasks...")

    # History Saver Worker
    try:
        history_task = asyncio.create_task(
            history_saver_main_loop(), 
            name="HistorySaverWorker"
        )
        background_tasks.append(history_task)
        logger.info(f"History saver worker started, listening to queue: {settings.REDIS_HISTORY_QUEUE_NAME}")
    except Exception as e:
        logger.error(f"Failed to start history saver worker: {e}", exc_info=True)

    # Token Usage Worker
    try:
        token_task = asyncio.create_task(
            token_usage_main_loop(),
            name="TokenUsageWorker"
        )
        background_tasks.append(token_task)
        logger.info(f"Token usage saver worker started, listening to queue: {settings.REDIS_TOKEN_USAGE_QUEUE_NAME}")
    except Exception as e:
        logger.error(f"Failed to start token usage saver worker: {e}", exc_info=True)

    # Inactivity Monitor Worker
    try:
        inactivity_task = asyncio.create_task(
            inactivity_monitor_main_loop(),
            name="InactivityMonitorWorker"
        )
        background_tasks.append(inactivity_task)
        logger.info(f"Inactivity monitor worker started. Check interval: {settings.AGENT_INACTIVITY_CHECK_INTERVAL}s, Timeout: {settings.AGENT_INACTIVITY_TIMEOUT}s.")
    except Exception as e:
        logger.error(f"Failed to start inactivity monitor worker: {e}", exc_info=True)

    logger.info(f"{len(background_tasks)} background tasks initiated.")

async def stop_background_tasks():
    """Останавливает все фоновые задачи."""
    logger.info(f"Stopping {len(background_tasks)} background tasks...")

    for task in background_tasks:
        if not task.done():
            logger.info(f"Attempting to cancel task {task.get_name()}...")
            try:
                task.cancel()
                # Даем задаче шанс завершиться после отмены
                # Ожидание с таймаутом, чтобы не блокировать процесс выключения надолго
                await asyncio.wait_for(task, timeout=10.0) 
                logger.info(f"Task {task.get_name()} finished after cancellation signal.")
            except asyncio.CancelledError:
                logger.info(f"Task {task.get_name()} was cancelled successfully.")
            except asyncio.TimeoutError:
                logger.warning(f"Task {task.get_name()} did not finish within 10s after cancellation. It might be stuck.")
            except Exception as e:
                logger.error(f"Error stopping task {task.get_name()}: {e}", exc_info=True)
        else:
            logger.info(f"Task {task.get_name()} was already done.")
    logger.info("All background tasks signaled to stop.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Контекстный менеджер для управления жизненным циклом FastAPI приложения."""
    # --- Startup ---
    setup_logging() 
    logger.info("Application startup sequence initiated.")

    await init_redis_pool()
    # await init_db() # Раскомментировать, если таблицы должны создаваться при старте (Alembic обычно управляет этим)

    await start_background_tasks(app)

    logger.info("Application startup sequence completed.")
    yield
    # --- Shutdown ---
    logger.info("Application shutdown sequence initiated.")

    await stop_background_tasks()
    await close_redis_pool()
    await close_db_engine()

    logger.info("Application shutdown sequence completed.")
