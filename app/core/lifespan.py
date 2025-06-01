import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.logging_config import setup_logging
from app.db.session import close_db_engine
from app.services.redis_service import init_redis_pool, close_redis_pool
from app.core.config import settings

from app.workers.history_saver_worker import HistorySaverWorker
from app.workers.token_usage_worker import TokenUsageWorker
from app.workers.inactivity_monitor_worker import InactivityMonitorWorker

from app.api.schemas.common_schemas import IntegrationType
from app.db.crud import agent_crud
from app.services.process_manager import ProcessManager
from app.db.session import get_async_session_factory

logger = logging.getLogger(__name__)

background_tasks = []

async def start_existing_agents_and_integrations():
    """
    Запускает все существующие агенты и их активные интеграции при старте приложения.

    Действия:
    1. Инициализирует `ProcessManager` и настраивает его соединение с Redis.
    2. Получает фабрику сессий БД.
    3. В рамках сессии БД:
        - Загружает все конфигурации агентов из базы данных.
        - Для каждого агента:
            - Проверяет текущий статус агента в Redis.
            - Если агент не запущен или не находится в процессе запуска, вызывает
              `pm.start_agent_process()` для его запуска.
            - Обрабатывает конфигурацию интеграций агента (из `agent_db.config_json`):
                - Для каждой включенной интеграции (`enabled: true`):
                    - Проверяет тип интеграции и наличие настроек.
                    - Проверяет текущий статус интеграции в Redis.
                    - Если интеграция не запущена, вызывает `pm.start_integration_process()`
                      с передачей `agent_id`, типа интеграции и ее настроек.
    4. Логирует все основные шаги, ошибки и пропущенные элементы.
    5. Гарантирует очистку ресурсов `ProcessManager` (`pm.cleanup_manager()`) в блоке finally.

    Примечание:
    - Ошибки при запуске отдельного агента или интеграции логируются, но не прерывают
      процесс запуска других агентов/интеграций.
    - Ошибки при настройке `ProcessManager` или подключении к БД прерывают выполнение функции.
    """
    logger.info("Attempting to start existing agents and their integrations...")
    db_session_factory = get_async_session_factory()
    pm = ProcessManager() # ADDED: Instantiate ProcessManager

    if not db_session_factory:
        logger.error("Database session factory not available. Cannot start agents.")
        return

    try:
        await pm.setup_manager() # ADDED: Setup ProcessManager
        logger.info("ProcessManager setup complete for startup.")

        async with db_session_factory() as session:
            try:
                agents = await agent_crud.db_get_all_agents(session, limit=1000)
                logger.info(f"Found {len(agents)} agents to potentially start.")
                for agent_db in agents:
                    agent_id = str(agent_db.id) # Ensure agent_id is string
                    logger.info(f"Attempting to start agent: {agent_id}")
                    try:
                        current_status_dict = await pm.get_agent_status(agent_id)
                        if current_status_dict and current_status_dict.get("status") in ["running", "running_pending_agent_confirm", "starting"]:
                            logger.info(f"Agent {agent_id} is already {current_status_dict.get('status')}.")
                        else:
                            # Attempt to start the agent process
                            # The start_agent_process method now returns a boolean
                            started_successfully = await pm.start_agent_process(agent_id)
                            if started_successfully:
                                logger.info(f"Successfully initiated start for agent: {agent_id}. Process manager will update status.")
                            else:
                                logger.warning(f"Failed to initiate start for agent: {agent_id}. Check ProcessManager logs for details.")
                    except Exception as e_agent_start:
                        logger.error(f"Error managing agent {agent_id} during startup: {e_agent_start}", exc_info=True)

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
                                        if isinstance(integration_item, dict) and integration_item.get("settings", {}).get("enabled", True):
                                            integration_type_str = integration_item.get("type")
                                            integration_actual_settings = integration_item.get("settings")

                                            if not integration_type_str:
                                                logger.warning(f"Integration item for agent {agent_id} is missing 'type'. Skipping.")
                                                continue
                                            
                                            if integration_actual_settings is not None and not isinstance(integration_actual_settings, dict):
                                                logger.warning(f"Integration item '{integration_type_str}' for agent {agent_id} has 'settings' but it's not a dictionary. Found type: {type(integration_actual_settings)}. Skipping.")
                                                continue

                                            try:
                                                integration_type_enum_val = IntegrationType(integration_type_str).value # Get the string value
                                                logger.info(f"Attempting to start {integration_type_enum_val} integration for agent: {agent_id} (settings provided: {integration_actual_settings is not None})")
                                                
                                                current_integration_status_dict = await pm.get_integration_status(
                                                    agent_id, integration_type_enum_val
                                                )
                                                if current_integration_status_dict and current_integration_status_dict.get("status") in ["running", "running_pending_agent_confirm", "starting"]:
                                                    logger.info(f"{integration_type_enum_val} integration for agent {agent_id} is already {current_integration_status_dict.get('status')}.")
                                                else:
                                                    integration_started_successfully = await pm.start_integration_process(
                                                        agent_id, 
                                                        integration_type_enum_val,
                                                        integration_actual_settings
                                                    )
                                                    if integration_started_successfully:
                                                        logger.info(f"Successfully initiated start for {integration_type_enum_val} integration for agent: {agent_id}. Process manager will update status.")
                                                    else:
                                                        logger.warning(f"Failed to initiate start for {integration_type_enum_val} integration for agent: {agent_id}. Check ProcessManager logs.")
                                            except ValueError:
                                                logger.error(f"Invalid integration type string '{integration_type_str}' in config for agent {agent_id}. Skipping.")
                                            except Exception as e_integration_start:
                                                logger.error(f"Error managing {integration_type_str} integration for agent {agent_id}: {e_integration_start}", exc_info=True)
                                        elif isinstance(integration_item, dict) and not integration_item.get("settings").get("enabled"):
                                            logger.info(f"Integration '{integration_item.get('type', 'Unknown type')}' for agent {agent_id} is disabled. Skipping.")
                                        else:
                                            logger.warning(f"Skipping invalid integration item for agent {agent_id}: {integration_item}")
                                else:
                                    if integrations_list is not None:
                                        logger.warning(f"'integrations' field in agent {agent_id} config (simple.settings.integrations) is not a list. Found: {type(integrations_list)}. Skipping integrations.")
                            else:
                                if settings_config is not None:
                                     logger.warning(f"'settings' field in agent {agent_id} config (simple.settings) is not a dictionary. Found: {type(settings_config)}. Skipping integrations.")
                        else:
                            if simple_config is not None:
                                logger.warning(f"'simple' field in agent {agent_id} config is not a dictionary. Found: {type(simple_config)}. Skipping integrations.")
                    elif agent_db.config_json is None:
                        logger.info(f"Agent {agent_id} has no config_json. Skipping integrations.")
                    else:
                        logger.warning(f"agent_db.config_json for agent {agent_id} is not a dictionary. Found: {type(agent_db.config_json)}. Skipping integrations.")
            except Exception as e:
                logger.error(f"Failed to fetch or process agents for startup: {e}", exc_info=True)
    except Exception as e_pm_setup: # Catch errors from pm.setup_manager()
        logger.error(f"Failed to setup ProcessManager or connect to Redis: {e_pm_setup}", exc_info=True)
    finally:
        await pm.cleanup_manager() # ADDED: Cleanup ProcessManager
        logger.info("ProcessManager cleanup complete for startup function.")

async def start_background_tasks(app: FastAPI):
    """
    Запускает все необходимые фоновые задачи (воркеры) для приложения.

    Для каждого типа воркера (HistorySaverWorker, TokenUsageWorker, InactivityMonitorWorker):
    1. Создает экземпляр воркера.
    2. Создает задачу asyncio для выполнения метода `run()` воркера.
    3. Добавляет задачу в список `background_tasks` для отслеживания.
    4. Логирует запуск воркера или ошибку при запуске.

    После инициализации всех воркеров, вызывает `start_existing_agents_and_integrations()`
    для запуска ранее существовавших агентов и их интеграций.

    Args:
        app (FastAPI): Экземпляр FastAPI приложения (в данный момент не используется напрямую
                       в теле функции, но может быть полезен для передачи зависимостей в будущем).
    """
    logger.info("Starting background tasks...")

    # History Saver Worker
    try:
        history_worker = HistorySaverWorker()
        history_task = asyncio.create_task(
            history_worker.run(), 
            name="HistorySaverWorker"
        )
        background_tasks.append(history_task)
        logger.info(f"History saver worker started, listening to queue: {settings.REDIS_HISTORY_QUEUE_NAME}")
    except Exception as e:
        logger.error(f"Failed to start history saver worker: {e}", exc_info=True)

    # Token Usage Worker
    try:
        token_worker = TokenUsageWorker()
        token_task = asyncio.create_task(
            token_worker.run(),
            name="TokenUsageWorker"
        )
        background_tasks.append(token_task)
        logger.info(f"Token usage saver worker started, listening to queue: {settings.REDIS_TOKEN_USAGE_QUEUE_NAME}")
    except Exception as e:
        logger.error(f"Failed to start token usage saver worker: {e}", exc_info=True)

    # Inactivity Monitor Worker
    try:
        inactivity_worker = InactivityMonitorWorker()
        inactivity_task = asyncio.create_task(
            inactivity_worker.run(),
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
    """
    Останавливает все фоновые задачи, запущенные при старте приложения.

    Итерируется по списку `background_tasks`:
    - Если задача еще не завершена (`task.done()` is False):
        - Вызывает `task.cancel()` для запроса отмены задачи.
        - Ожидает завершения задачи с таймаутом (10 секунд).
        - Логирует результат отмены (успешно отменена, таймаут или другая ошибка).
    - Если задача уже была завершена, логирует этот факт.
    """
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
    """
    Асинхронный контекстный менеджер для управления жизненным циклом FastAPI приложения.

    Выполняет действия при старте и остановке приложения.

    При старте (до `yield`):
    1. Настраивает логирование (`setup_logging()`).
    2. Инициализирует пул соединений Redis (`init_redis_pool()`).
    3. Запускает фоновые задачи (`start_background_tasks(app)`), включая воркеры
       и инициализацию существующих агентов/интеграций.
    4. Логирует завершение последовательности запуска.

    При остановке (после `yield`):
    1. Логирует начало последовательности остановки.
    2. Останавливает фоновые задачи (`stop_background_tasks()`).
    3. Закрывает пул соединений Redis (`close_redis_pool()`).
    4. Закрывает движок базы данных (`close_db_engine()`).
    5. Логирует завершение последовательности остановки.

    Args:
        app (FastAPI): Экземпляр FastAPI приложения. Передается в `start_background_tasks`.
    """
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
