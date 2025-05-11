import os
import logging
from fastapi import FastAPI, Request, status, Depends # Добавил Depends, если его нет
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field as PydanticField
from typing import Dict, Any, Optional
import redis.asyncio as redis
import asyncio
from contextlib import asynccontextmanager
from sqlalchemy.exc import SQLAlchemyError

# Импортируем зависимости и модули
# --- ИСПРАВЛЕНИЕ: Удаляем импорт redis_pool ---
from .redis_client import init_redis_pool, close_redis_pool, get_redis # Убрали redis_pool
# --- Конец исправления ---
# --- ИЗМЕНЕНИЕ: Убедимся, что SessionLocal импортирован ---
from .db import init_db, close_db_engine, get_db, SessionLocal # Импортируем SessionLocal (или AsyncSessionLocal, если имя другое)
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
from .process_manager import check_inactive_agents # Импортируем фоновую задачу
from .api import agents as agents_api # Импортируем новый роутер для агентов
from .api import websocket as websocket_api # Импортируем роутер для WebSocket
# --- НОВОЕ: Импортируем роутер для пользователей ---
from .api import users as users_api
# --- КОНЕЦ НОВОГО ---
from . import crud, process_manager, models # Добавляем импорты
# --- ИЗМЕНЕНИЕ: Импортируем супервизор ---
from .history_saver import supervise_history_saver, REDIS_URL as HISTORY_REDIS_URL # Импортируем супервизора и URL
# --- НОВОЕ: Импортируем супервизор для token_usage_saver ---
from .token_usage_saver import supervise_token_usage_saver, REDIS_URL as TOKEN_SAVER_REDIS_URL
# --- КОНЕЦ НОВОГО ---
# --- КОНЕЦ ИЗМЕНЕНИЯ ---


# --- Configuration & Globals ---
# Load .env - adjust path as needed
from dotenv import load_dotenv
# Construct the path to the .env file relative to the current script's directory
# Assuming .env is in the parent directory of agent_manager
current_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(current_dir, '..', '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    print(f"Warning: .env file not found at {dotenv_path}")


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# TODO: Add PostgreSQL setup (SQLAlchemy models, database URL, session management)
# from . import crud, models, schemas
# from .database import SessionLocal, engine
# models.Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client: Optional[redis.Redis] = None # Уточняем тип
inactivity_check_task: Optional[asyncio.Task] = None
# --- ИЗМЕНЕНИЕ: Добавляем переменную для задачи history_saver ---
history_saver_task: Optional[asyncio.Task] = None
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
# --- НОВОЕ: Переменные для token_usage_saver ---
token_usage_saver_task: Optional[asyncio.Task] = None
token_usage_saver_shutdown_event: Optional[asyncio.Event] = None # Отдельное событие для token_usage_saver
# --- КОНЕЦ НОВОГО ---
# --- ИЗМЕНЕНИЕ: Добавляем глобальное событие ---
shutdown_event: Optional[asyncio.Event] = None # Это событие для history_saver
# --- КОНЕЦ ИЗМЕНЕНИЯ ---


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

# --- FastAPI App Initialization ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events."""
    # --- ИЗМЕНЕНИЕ: Добавляем history_saver_task в global ---
    global redis_client, inactivity_check_task, history_saver_task, shutdown_event
    # --- НОВОЕ: Добавляем переменные token_usage_saver в global ---
    global token_usage_saver_task, token_usage_saver_shutdown_event
    # --- КОНЕЦ НОВОГО ---
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    # Startup
    logger.info("Agent Manager Service starting up...")
    # --- ИСПРАВЛЕНИЕ: Используем возвращаемое значение init_redis_pool ---
    # Вызываем init_redis_pool и сохраняем результат
    initialized_pool = await init_redis_pool()
    if (initialized_pool): # Проверяем возвращенное значение
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
                                if (bot_token):
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


    # Start the background task for inactivity check
    # Проверяем redis_client
    if redis_client: # Запускаем задачу только если Redis доступен
        # Передаем redis_client в фоновую задачу
        inactivity_check_task = asyncio.create_task(check_inactive_agents(redis_client))
    else:
        # Уточняем лог
        logger.warning("Redis client instance not available, inactivity check task will not start.")


    # --- ИЗМЕНЕНИЕ: Запуск супервизора history_saver ---
    logger.info("Attempting to start History Saver Supervisor...")
    try:
        # --- ИЗМЕНЕНИЕ: Создаем событие и запускаем супервизора с ним ---
        shutdown_event = asyncio.Event() # Создаем событие
        history_saver_task = asyncio.create_task(
            supervise_history_saver(
                redis_url=HISTORY_REDIS_URL,
                db_session_factory=SessionLocal,
                shutdown_event=shutdown_event # Передаем событие
            )
        )
        logger.info(f"History Saver Supervisor task created successfully (ID: {id(history_saver_task)}).")
    except Exception as e:
        logger.error(f"Failed to create History Saver Supervisor task: {e}", exc_info=True)
        history_saver_task = None # Убедимся, что None при ошибке
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- НОВОЕ: Запуск супервизора token_usage_saver ---
    logger.info("Attempting to start Token Usage Saver Supervisor...")
    try:
        token_usage_saver_shutdown_event = asyncio.Event()
        token_usage_saver_task = asyncio.create_task(
            supervise_token_usage_saver(
                redis_url=TOKEN_SAVER_REDIS_URL, # Используем URL из token_usage_saver
                db_session_factory=SessionLocal, # Та же фабрика сессий
                shutdown_event=token_usage_saver_shutdown_event
            )
        )
        logger.info(f"Token Usage Saver Supervisor task created successfully (ID: {id(token_usage_saver_task)}).")
    except Exception as e:
        logger.error(f"Failed to create Token Usage Saver Supervisor task: {e}", exc_info=True)
        token_usage_saver_task = None
    # --- КОНЕЦ НОВОГО ---

    yield
    # Shutdown
    logger.info("Agent Manager Service shutting down...")

    # --- ИЗМЕНЕНИЕ: Остановка супервизора history_saver ---
    if shutdown_event:
        logger.info("Signaling shutdown to History Saver Supervisor...")
        shutdown_event.set() # Сигнализируем о завершении
    if history_saver_task and not history_saver_task.done():
        logger.info("Attempting to cancel History Saver Supervisor task...")
        history_saver_task.cancel()
        try:
            await history_saver_task
        except asyncio.CancelledError:
            logger.info("History Saver Supervisor task successfully cancelled.")
        except Exception as e:
            logger.error(f"Error during History Saver Supervisor shutdown: {e}", exc_info=True)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- НОВОЕ: Остановка супервизора token_usage_saver ---
    if token_usage_saver_shutdown_event:
        logger.info("Signaling shutdown to Token Usage Saver Supervisor...")
        token_usage_saver_shutdown_event.set()
    if token_usage_saver_task and not token_usage_saver_task.done():
        logger.info("Attempting to cancel Token Usage Saver Supervisor task...")
        token_usage_saver_task.cancel()
        try:
            await token_usage_saver_task
        except asyncio.CancelledError:
            logger.info("Token Usage Saver Supervisor task successfully cancelled.")
        except Exception as e:
            logger.error(f"Error during Token Usage Saver Supervisor shutdown: {e}", exc_info=True)
    # --- КОНЕЦ НОВОГО ---

    # Cancel the inactivity check task
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
    title="Agent Manager API",
    description="API for managing and interacting with AI agents.",
    version="0.1.0",
    lifespan=lifespan,
    # Добавляем документацию для тегов
    openapi_tags=[
        {"name": "Agents", "description": "Manage agent configurations and processes."},
        {"name": "Integrations", "description": "Manage agent integrations (e.g., Telegram)."},
        {"name": "Status", "description": "Service status endpoints."},
        # --- НОВОЕ: Добавляем тег для пользователей ---
        {"name": "Users", "description": "Manage users."},
        # --- КОНЕЦ НОВОГО ---
        {"name": "Chats", "description": "Access chat history."}, # Добавляем тег Chats
        {"name": "WebSocket", "description": "WebSocket communication."}, # Добавляем тег WebSocket
        {"name": "Internal", "description": "Internal endpoints used by other services (e.g., agent runner)."}
    ]
)

# --- Добавление CORSMiddleware ---
# Настройте origins в соответствии с вашими потребностями.
# ["*"] разрешает все источники (менее безопасно, подходит для разработки).
# Укажите конкретные источники для production, например ["http://localhost:3000", "https://your-frontend.com"]
origins = [
    "http://localhost:3000", # Разрешаем ваш фронтенд
    "http://127.0.0.1:3000", # Можно добавить и 127.0.0.1 на всякий случай
    # Добавьте другие источники, если необходимо
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True, # Разрешает куки и заголовки авторизации
    allow_methods=["*"],    # Разрешает все методы (GET, POST, PUT, DELETE и т.д.)
    allow_headers=["*"],    # Разрешает все заголовки
)
# --- Конец добавления ---


# --- Include Routers ---
# Используем роутер из agents.py
app.include_router(agents_api.router, prefix="/agents", tags=["Agents"])
app.include_router(websocket_api.router, tags=["WebSocket"]) # Добавляем роутер WebSocket
# --- НОВОЕ: Подключаем роутер для пользователей ---
app.include_router(users_api.router, prefix="/users", tags=["Users"])
# --- КОНЕЦ НОВОГО ---

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

# --- Exception Handlers ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Log the detailed validation errors
    logger.warning(f"Request validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )

@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    # Log the database error
    logger.error(f"Database error during request: {exc}", exc_info=True) # Log traceback
    # В реальном приложении можно скрыть детали ошибки от клиента
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"An internal database error occurred: {type(exc).__name__}"},
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Log any other unexpected errors
    logger.error(f"Unhandled exception during request: {exc}", exc_info=True) # Log traceback
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"An unexpected internal server error occurred: {type(exc).__name__}"},
    )

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
