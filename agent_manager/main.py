import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field as PydanticField
from typing import Dict, Any, Optional
import redis.asyncio as redis
import asyncio
from contextlib import asynccontextmanager

# Импортируем зависимости и модули
# --- ИСПРАВЛЕНИЕ: Удаляем импорт redis_pool ---
from .redis_client import init_redis_pool, close_redis_pool, get_redis # Убрали redis_pool
# --- Конец исправления ---
from .db import init_db, close_db_engine, get_db, SessionLocal # Импортируем SessionLocal
from .process_manager import check_inactive_agents # Импортируем фоновую задачу
from .api import agents as agents_api # Импортируем новый роутер для агентов
from .api import websocket as websocket_api # Импортируем роутер для WebSocket
from . import crud, process_manager, models # Добавляем импорты

# --- Configuration & Globals ---
# Load .env - adjust path as needed
from dotenv import load_dotenv
# Construct the path to the .env file relative to the current script's directory
# Assuming .env is in the parent directory of agent_manager
current_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(current_dir, '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    print(f"Warning: .env file not found at {dotenv_path}")


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# TODO: Add PostgreSQL setup (SQLAlchemy models, database URL, session management)
# from . import crud, models, schemas
# from .database import SessionLocal, engine
# models.Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client: Optional[redis.Redis] = None # Уточняем тип
inactivity_check_task: Optional[asyncio.Task] = None

# --- Pydantic Models for API ---
class AgentConfigInput(BaseModel):
    # Define structure matching the input JSON config more closely
    name: str
    description: str
    userId: str # Assuming this relates to the owner
    private: Optional[str] = None # Or bool?
    type: str # e.g., "simple"
    config: Dict[str, Any] # Keep flexible for now

class AgentConfigOutput(AgentConfigInput):
     id: str = PydanticField(..., description="Unique Agent ID assigned by the service")

class AgentStatus(BaseModel):
    agent_id: str
    status: str = PydanticField(..., description="e.g., 'stopped', 'running', 'starting', 'error', 'error_config', 'error_app_create'")
    pid: Optional[int] = None
    last_active: Optional[float] = None

# --- FastAPI Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events."""
    global redis_client, inactivity_check_task # Объявляем глобальные переменные
    # Startup
    logger.info("Agent Manager Service starting up...")
    # --- ИСПРАВЛЕНИЕ: Используем возвращаемое значение init_redis_pool ---
    # Вызываем init_redis_pool и сохраняем результат
    initialized_pool = await init_redis_pool()
    if initialized_pool: # Проверяем возвращенное значение
        try:
            # Создаем клиент из возвращенного пула
            redis_client = redis.Redis.from_pool(initialized_pool)
            await redis_client.ping() # Проверяем соединение
            logger.info("Successfully created Redis client from pool for lifespan.")
        except Exception as e:
            logger.error(f"Failed to create Redis client from pool or ping failed: {e}")
            redis_client = None # Убедимся, что клиент None при ошибке
    else:
        # Эта ветка выполняется, если init_redis_pool вернула None
        logger.warning("Redis pool initialization failed (init_redis_pool returned None), redis_client will be None.")
        redis_client = None
    # --- Конец исправления ---

    await init_db() # Initialize DB connection

    # --- Auto-start agents and integrations ---
    # Проверяем redis_client (который теперь должен быть экземпляром клиента, если пул инициализирован)
    if redis_client:
        logger.info("Attempting to auto-start agents and integrations from database...")
        start_tasks = []
        try:
            async with SessionLocal() as db_session: # Создаем сессию для автозапуска
                all_agents = await crud.db_get_all_agents(db_session, limit=1000) # Получаем всех агентов
                logger.info(f"Found {len(all_agents)} agents in database.")
                for agent in all_agents:
                    logger.info(f"Initiating auto-start for agent: {agent.id}")
                    # Создаем задачу для запуска агента
                    start_tasks.append(
                        # Передаем redis_client
                        asyncio.create_task(process_manager.start_agent_process(agent.id, redis_client))
                    )

                    # --- ИСПРАВЛЕНИЕ: Проверяем integrations вместо tools ---
                    try:
                        config_data = agent.config_json or {}
                        simple_config = config_data.get("simple", {})
                        settings_data = simple_config.get("settings", {})
                        # Получаем список интеграций
                        integrations_config = settings_data.get("integrations", [])

                        if not integrations_config:
                            logger.debug(f"Agent {agent.id}: No integrations found in config['simple']['settings']['integrations'].")
                        else:
                            logger.debug(f"Agent {agent.id}: Found integrations in config: {integrations_config}")

                        # Итерируем по списку интеграций
                        for integration in integrations_config:
                            integration_type = integration.get("type") # Получаем тип интеграции
                            logger.debug(f"Agent {agent.id}: Checking integration with type: {integration_type}")

                            # Проверяем тип "telegram"
                            if integration_type == "telegram":
                                # --- ИСПРАВЛЕНИЕ: Извлекаем токен и передаем его ---
                                integration_settings = integration.get("settings", {})
                                bot_token = integration_settings.get("botToken")
                                if bot_token:
                                    logger.info(f"Initiating auto-start for Telegram integration for agent: {agent.id}")
                                    # Создаем задачу для запуска интеграции Telegram, передавая настройки
                                    start_tasks.append(
                                        asyncio.create_task(
                                            process_manager.start_integration_process(
                                                agent_id=agent.id,
                                                integration_type=models.IntegrationType.TELEGRAM,
                                                r=redis_client,
                                                integration_settings=integration_settings # Передаем настройки
                                            )
                                        )
                                    )
                                else:
                                    logger.warning(f"Agent {agent.id}: Telegram integration found but 'botToken' is missing in settings.")
                                # --- Конец исправления ---
                                # Добавьте другие типы интеграций здесь, если необходимо

                    except Exception as config_err:
                        logger.error(f"Error parsing config for agent {agent.id} during auto-start check: {config_err}")
                    # --- Конец исправления ---

            # Запускаем все задачи параллельно и ждем завершения
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Error during auto-start task {i}: {result}", exc_info=result if not isinstance(result, asyncio.CancelledError) else None)
            logger.info("Finished auto-start attempts.")

        except Exception as auto_start_err:
            logger.error(f"Failed to perform auto-start: {auto_start_err}", exc_info=True)
    else:
        # Уточняем лог
        logger.warning("Redis client instance not available, skipping auto-start of agents.")
    # --- End auto-start ---


    # Start the background task
    # Проверяем redis_client
    if redis_client: # Запускаем задачу только если Redis доступен
        # Передаем redis_client в фоновую задачу
        inactivity_check_task = asyncio.create_task(check_inactive_agents(redis_client))
    else:
        # Уточняем лог
        logger.warning("Redis client instance not available, inactivity check task will not start.")


    yield
    # Shutdown
    logger.info("Agent Manager Service shutting down...")

    # Cancel the background task
    if inactivity_check_task and not inactivity_check_task.done(): # Проверяем, что задача существует и не завершена
        logger.info("Cancelling inactivity check task...")
        inactivity_check_task.cancel()
        try:
            await inactivity_check_task
        except asyncio.CancelledError:
            logger.info("Inactivity check task successfully cancelled.")
        except Exception as e:
             logger.error(f"Error during inactivity check task shutdown: {e}")

    # Закрываем глобальный клиент, если он был создан
    if redis_client:
        try:
            await redis_client.aclose()
            logger.info("Closed lifespan Redis client.")
        except Exception as e:
            logger.error(f"Error closing lifespan Redis client: {e}")

    await close_redis_pool() # Закрываем пул (если это необходимо)
    await close_db_engine() # Close DB connection pool


app = FastAPI(
    title="Agent Management Service",
    description="API for creating, managing, and interacting with configurable agents.",
    version="0.1.0",
    lifespan=lifespan
)

# --- Middleware (Optional) ---
# Example: Add timing middleware
# @app.middleware("http")
# async def add_process_time_header(request: Request, call_next):
#     start_time = time.time()
#     response = await call_next(request)
#     process_time = time.time() - start_time
#     response.headers["X-Process-Time"] = str(process_time)
#     return response

# --- Exception Handlers (Optional) ---
# Example: Generic exception handler
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception for request {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "An internal server error occurred."},
    )

# --- Include Routers ---
# Используем роутер из agents.py
app.include_router(agents_api.router, prefix="/agents", tags=["Agents"])
app.include_router(websocket_api.router, tags=["WebSocket"]) # Добавляем роутер WebSocket

# --- Root Endpoint ---
@app.get("/", tags=["Status"])
async def read_root():
    """Root endpoint providing basic service status."""
    redis_status = "disconnected"
    db_status = "disconnected"
    # Проверяем redis_client для статуса
    if redis_client:
        try:
            await redis_client.ping()
            redis_status = "connected"
        except Exception:
            pass # Status remains disconnected

    try:
        # Check DB using SessionLocal directly for health check
        if SessionLocal: # Check if SessionLocal was initialized
            async with SessionLocal() as session: # Create a session directly
                # Perform a simple query like SELECT 1
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
                db_status = "connected"
        else:
             db_status = "disabled" # If SessionLocal is None
    except Exception as e:
        logger.warning(f"DB connection check failed: {e}")
        pass # Status remains disconnected

    return {
        "service": "Agent Management Service",
        "status": "running",
        "redis_status": redis_status,
        "db_status": db_status # Add DB status
    }

# --- Main execution (for running with uvicorn directly) ---
# This part is usually not needed if using `make manager` target
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(
#         "agent_manager.main:app",
#         host=config.MANAGER_HOST,
#         port=config.MANAGER_PORT,
#         reload=True # Enable reload for development
#     )
