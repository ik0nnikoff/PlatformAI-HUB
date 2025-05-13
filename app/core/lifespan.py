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

from app.api.schemas.common_schemas import IntegrationType # Добавлено для start_existing_agents_and_integrations
from app.db.crud import agent_crud # Добавлено для получения агентов
from app.services import process_manager_service # Добавлено для запуска агентов/интеграций
from app.db.session import get_async_session_factory # Для получения сессии БД
from app.services.redis_service import get_redis_client

logger = logging.getLogger(__name__)

background_tasks = []

async def start_existing_agents_and_integrations():
    """
    Запускает всех существующих агентов и их активные интеграции.
    Эта функция будет вызываться при старте приложения.
    """
    logger.info("Attempting to start existing agents and their integrations...")
    db_session_factory = get_async_session_factory()
    redis_client = None # Инициализируем redis_client

    if not db_session_factory:
        logger.error("Database session factory not available. Cannot start agents.")
        return

    try:
        redis_client = await get_redis_client() # ИЗМЕНЕНО: Добавлен await
        if not redis_client:
            logger.error("Redis client not available. Cannot start agents.")
            return

        async with db_session_factory() as session:
            try:
                agents = await agent_crud.db_get_all_agents(session, limit=1000) # Получаем всех агентов
                logger.info(f"Found {len(agents)} agents to potentially start.")
                for agent_db in agents:
                    agent_id = agent_db.id
                    logger.info(f"Attempting to start agent: {agent_id}")
                    try:
                        # Проверяем, не запущен ли уже агент (на случай перезапуска менеджера)
                        current_status = await process_manager_service.get_agent_status(agent_id, redis_client) # ИЗМЕНЕНО: передаем async redis_client
                        if current_status.status == "running":
                            logger.info(f"Agent {agent_id} is already running.")
                        else:
                            started = await process_manager_service.start_agent_process(agent_id, redis_client) # ИЗМЕНЕНО: передаем async redis_client
                            if started:
                                logger.info(f"Successfully initiated start for agent: {agent_id}")
                            else:
                                logger.warning(f"Failed to initiate start for agent: {agent_id}")
                    except Exception as e_agent_start:
                        logger.error(f"Error starting agent {agent_id}: {e_agent_start}", exc_info=True)

                    # Запуск интеграций для агента
                    if isinstance(agent_db.config_json, dict):
                        simple_config = agent_db.config_json.get("simple")
                        if isinstance(simple_config, dict):
                            settings_config = simple_config.get("settings")
                            if isinstance(settings_config, dict):
                                integrations_list = settings_config.get("integrations")
                                if isinstance(integrations_list, list):
                                    logger.info(f"Found {len(integrations_list)} integrations in config for agent {agent_id}.")
                                    for integration_item in integrations_list:
                                        if isinstance(integration_item, dict) and integration_item.get("enabled"):
                                            integration_type_str = integration_item.get("type")
                                            integration_actual_settings = integration_item.get("settings") # Это должен быть словарь

                                            if not integration_type_str:
                                                logger.warning(f"Integration item for agent {agent_id} is missing 'type'. Skipping.")
                                                continue
                                            
                                            # integration_actual_settings может быть None, если ключ "settings" отсутствует,
                                            # или это должен быть словарь. start_integration_process принимает Optional[Dict].
                                            if integration_actual_settings is not None and not isinstance(integration_actual_settings, dict):
                                                logger.warning(f"Integration item '{integration_type_str}' for agent {agent_id} has 'settings' but it's not a dictionary. Found type: {type(integration_actual_settings)}. Skipping.")
                                                continue

                                            try:
                                                integration_type_enum = IntegrationType(integration_type_str)
                                                logger.info(f"Attempting to start {integration_type_enum.value} integration for agent: {agent_id} (settings provided: {integration_actual_settings is not None})")
                                                
                                                current_integration_status = await process_manager_service.get_integration_status(
                                                    agent_id, integration_type_enum, redis_client
                                                )
                                                if current_integration_status.status == "running":
                                                    logger.info(f"{integration_type_enum.value} integration for agent {agent_id} is already running.")
                                                else:
                                                    integration_started = await process_manager_service.start_integration_process(
                                                        agent_id, 
                                                        integration_type_enum, 
                                                        redis_client,
                                                        integration_actual_settings # Передаем извлеченные настройки
                                                    )
                                                    if integration_started:
                                                        logger.info(f"Successfully initiated start for {integration_type_enum.value} integration for agent: {agent_id}")
                                                    else:
                                                        logger.warning(f"Failed to initiate start for {integration_type_enum.value} integration for agent: {agent_id}")
                                            except ValueError: # Ошибка преобразования строки в IntegrationType Enum
                                                logger.error(f"Invalid integration type string '{integration_type_str}' in config for agent {agent_id}. Skipping.")
                                            except AttributeError as e_attr:
                                                logger.error(f"Missing function (e.g., start_integration_process or get_integration_status) in process_manager_service: {e_attr}")
                                            except Exception as e_integration_start:
                                                logger.error(f"Error starting {integration_type_str} integration for agent {agent_id}: {e_integration_start}", exc_info=True)
                                        elif isinstance(integration_item, dict) and not integration_item.get("enabled"):
                                            logger.info(f"Integration '{integration_item.get('type', 'Unknown type')}' for agent {agent_id} is disabled. Skipping.")
                                        else:
                                            logger.warning(f"Skipping invalid integration item for agent {agent_id}: {integration_item}")
                                else: # integrations_list не является списком
                                    if integrations_list is not None: # Ключ есть, но значение не список
                                        logger.warning(f"'integrations' field in agent {agent_id} config (simple.settings.integrations) is not a list. Found: {type(integrations_list)}. Skipping integrations.")
                                    # Если integrations_list is None, значит ключ "integrations" отсутствует - это нормально, нет интеграций.
                            else: # settings_config не словарь или None
                                if settings_config is not None:
                                     logger.warning(f"'settings' field in agent {agent_id} config (simple.settings) is not a dictionary. Found: {type(settings_config)}. Skipping integrations.")
                        else: # simple_config не словарь или None
                            if simple_config is not None:
                                logger.warning(f"'simple' field in agent {agent_id} config is not a dictionary. Found: {type(simple_config)}. Skipping integrations.")
                    elif agent_db.config_json is None:
                        logger.info(f"Agent {agent_id} has no config_json. Skipping integrations.")
                    else: # agent_db.config_json не словарь
                        logger.warning(f"agent_db.config_json for agent {agent_id} is not a dictionary. Found: {type(agent_db.config_json)}. Skipping integrations.")
            except Exception as e:
                logger.error(f"Failed to fetch or process agents for startup: {e}", exc_info=True)
    except Exception as e_redis: # Обработка ошибки получения redis_client
        logger.error(f"Failed to get Redis client: {e_redis}", exc_info=True)
    finally:
        if redis_client:
            await redis_client.close() # ИЗМЕНЕНО: Добавлен await
            logger.info("Redis client closed in start_existing_agents_and_integrations.")


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
    
    # Запускаем существующих агентов после инициализации основных фоновых задач
    try:
        await start_existing_agents_and_integrations()
    except Exception as e:
        logger.error(f"Error during initial startup of existing agents and integrations: {e}", exc_info=True)


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
