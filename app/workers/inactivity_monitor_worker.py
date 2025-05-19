import asyncio
import logging
import time # Keep for time.time()
from datetime import datetime, timezone # Added for datetime.now(timezone.utc)
import redis.exceptions # Added for RedisError
import os # Added import os
# Removed signal, os, json, redis.asyncio, RedisConnectionError as they are handled by base or ProcessManager

from app.core.config import settings
from app.api.schemas.common_schemas import IntegrationType # Keep for type checking
from app.services.process_manager import ProcessManager
from app.workers.base_worker import ScheduledTaskWorker
import time # Ensure time is imported
import redis.exceptions # Ensure redis.exceptions is imported
import logging # Ensure logging is imported
import asyncio # Ensure asyncio is imported
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import crud # This should give access to submodules like agent_crud
from app.db.crud import agent_crud # Explicit import for agent_crud
from app.db.session import SessionLocal

# logger is inherited from BaseWorker/ScheduledTaskWorker

class InactivityMonitorWorker(ScheduledTaskWorker):
    """
    Периодический воркер, который отслеживает активность и состояние
    процессов агентов и их интеграций.

    Основные задачи:
    - Проверка неактивных агентов: останавливает агентов, которые не проявляли
      активность дольше заданного таймаута (`settings.AGENT_INACTIVITY_TIMEOUT`).
    - Проверка "упавших" или некорректно остановленных процессов: пытается
      перезапустить процессы агентов или интеграций, которые должны быть запущены,
      но неактивны (PID не существует) или находятся в ошибочном состоянии.

    Наследует от `ScheduledTaskWorker` для выполнения периодических задач.
    Использует `ProcessManager` для взаимодействия с процессами и их статусами в Redis.

    Атрибуты:
        process_manager (ProcessManager): Экземпляр менеджера процессов.

    Методы:
        __init__(): Инициализирует воркер.
        setup(): Настраивает воркер, включая `ProcessManager` и начальную проверку процессов.
        perform_task(): Основная задача, выполняемая периодически (проверка неактивности и падений).
        _check_inactive_agents(): Проверяет и останавливает неактивные процессы агентов.
        _check_and_restart_crashed_processes(): Проверяет и перезапускает "упавшие" или некорректно
                                                 остановленные процессы агентов и интеграций.
        cleanup(): Выполняет очистку ресурсов при завершении работы воркера.
    """
    def __init__(self):
        """
        Инициализирует InactivityMonitorWorker.

        Устанавливает `component_id`, интервал выполнения задачи из
        `settings.AGENT_INACTIVITY_CHECK_INTERVAL` и префикс ключа статуса в Redis.
        Создает экземпляр `ProcessManager`.
        """
        super().__init__(
            component_id="inactivity_monitor_worker", # Unique ID for this worker instance
            interval_seconds=settings.AGENT_INACTIVITY_CHECK_INTERVAL,
            status_key_prefix="worker_status:inactivity_monitor:" # Specific status key prefix
        )
        self.process_manager = ProcessManager()
        self.logger.debug(f"[{self._component_id}] Initialized with check interval: {self.interval_seconds}s.")

    async def setup(self):
        """
        Выполняет настройку InactivityMonitorWorker перед запуском основного цикла.

        Действия:
        1. Вызывает `super().setup()` для инициализации `StatusUpdater` и клиента Redis для статуса воркера.
        2. Если воркер уже находится в процессе остановки (`self._running` is False), прерывает настройку.
        3. Инициализирует `self.process_manager` вызовом `setup_manager()`.
           В случае ошибки инициализации `ProcessManager`, инициирует остановку воркера.
        4. После небольшой задержки (10 секунд, чтобы дать другим компонентам время на запуск),
           выполняет начальную проверку `_check_and_restart_crashed_processes()` для
           перезапуска процессов, которые могли "упасть" до старта этого воркера.
        """
        await super().setup() # Sets up StatusUpdater, Redis client for worker status
        if not self._running:
            self.logger.info(f"[{self._component_id}] Shutdown initiated during setup. Aborting further setup.")
            return
        try:
            await self.process_manager.setup_manager() # Ensure PM is setup
            self.logger.info(f"[{self._component_id}] ProcessManager initialized for InactivityMonitor.")
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Failed to initialize ProcessManager: {e}", exc_info=True)
            self.initiate_shutdown() # Stop the worker if PM setup fails
            return

        # Initial check for crashed/stopped processes shortly after startup
        if self._running: # Check _running before performing tasks
            self.logger.info(f"[{self._component_id}] Delaying initial check for 10 seconds to allow lifespan to complete agent setups...")
            await asyncio.sleep(10) # Added delay
            self.logger.info(f"[{self._component_id}] Performing initial check for crashed/stopped processes...")
            await self._check_and_restart_crashed_processes()
            self.logger.info(f"[{self._component_id}] Initial check for crashed/stopped processes completed.")

    async def perform_task(self) -> None:
        """
        Основная периодическая задача, выполняемая `ScheduledTaskWorker`.

        Проверяет доступность клиента Redis в `ProcessManager` и пытается его
        переустановить в случае недоступности.
        Если клиент Redis доступен и воркер активен (`self._running`):
        - Вызывает `_check_inactive_agents()` для проверки и остановки неактивных агентов.
        - Вызывает `_check_and_restart_crashed_processes()` для проверки и перезапуска
          "упавших" или некорректно остановленных процессов агентов и интеграций.
        Логирует ошибки, возникающие во время выполнения задачи.
        """
        if not self._running:
            self.logger.info(f"[{self._component_id}] Shutdown in progress. Skipping periodic checks.")
            return

        if not await self.process_manager.is_redis_client_available():
            self.logger.warning(f"[{self._component_id}] ProcessManager Redis client not available. Skipping checks.")
            try:
                await self.process_manager.setup_redis_client() # Attempt to re-establish
                if not await self.process_manager.is_redis_client_available():
                    self.logger.error(f"[{self._component_id}] Failed to re-establish ProcessManager Redis client. Checks will be skipped.")
                    return
                self.logger.info(f"[{self._component_id}] ProcessManager Redis client re-established.")
            except Exception as e_redis_setup:
                self.logger.error(f"[{self._component_id}] Error re-establishing ProcessManager Redis client: {e_redis_setup}. Checks skipped.")
                return

        self.logger.debug(f"[{self._component_id}] Running periodic checks (inactivity, crashes)...")
        try:
            if self._running: # Check _running again before potentially long operations
                await self._check_inactive_agents()
            
            if self._running: # Check _running again before potentially starting new processes
                await self._check_and_restart_crashed_processes()
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Error during perform_task: {e}", exc_info=True)

    async def _check_inactive_agents(self):
        """
        Проверяет неактивные процессы агентов и останавливает их.

        Если воркер находится в процессе остановки, проверка прерывается.
        Получает список ключей статусов агентов из Redis (`agent_status:*`).
        Для каждого агента со статусом "running" и имеющего `last_active_time`:
        - Рассчитывает время неактивности.
        - Если время неактивности превышает `settings.AGENT_INACTIVITY_TIMEOUT`,
          пытается остановить процесс агента с помощью `self.process_manager.stop_agent_process()`.
        Логирует обнаружение неактивных агентов и ошибки во время проверки.
        """
        if not self._running:
            self.logger.info(f"[{self._component_id}] Shutdown in progress. Skipping inactivity check.")
            return

        self.logger.debug(f"[{self._component_id}] Running inactivity check for agents...")
        current_time = time.time()
        agent_inactivity_timeout = settings.AGENT_INACTIVITY_TIMEOUT
        
        pm_redis_client = await self.process_manager.redis_client # Get actual client
        if not pm_redis_client:
            self.logger.warning(f"[{self._component_id}] ProcessManager Redis client not available for inactivity check.")
            return

        try:
            agent_keys = await pm_redis_client.keys("agent_status:*")
            for key_bytes in agent_keys:
                if not self._running: # Check frequently during loops
                    self.logger.info(f"[{self._component_id}] Shutdown in progress during agent scan. Aborting.")
                    break
                key = key_bytes.decode('utf-8')
                agent_id = key.split(":", 1)[1]
                status = await pm_redis_client.hgetall(key)
                
                # Decode status values
                status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status.items()}

                last_active_str = status.get("last_active_time") # Assuming this key from StatusUpdater
                current_status = status.get("status")

                if current_status == "running" and last_active_str:
                    try:
                        last_active = float(last_active_str)
                        if (current_time - last_active) > agent_inactivity_timeout:
                            self.logger.warning(f"[{self._component_id}] Agent {agent_id} is inactive (last active {last_active:.0f}, current {current_time:.0f}, timeout {agent_inactivity_timeout}s). Attempting to stop.")
                            if self._running: # Check before stopping
                                await self.process_manager.stop_agent_process(agent_id, force=False) # Attempt graceful stop
                        else:
                            self.logger.debug(f"[{self._component_id}] Agent {agent_id} is active (last active {last_active:.0f}).")
                    except ValueError:
                        self.logger.error(f"[{self._component_id}] Could not parse last_active_time '{last_active_str}' for agent {agent_id}.")
                elif current_status != "running" and current_status != "stopped" and current_status != "error" and current_status != "initializing":
                     self.logger.debug(f"[{self._component_id}] Agent {agent_id} has status '{current_status}', not checking for inactivity timeout.")


        except redis.exceptions.RedisError as e_redis:
            self.logger.error(f"[{self._component_id}] Redis error during agent inactivity check: {e_redis}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Unexpected error during agent inactivity check: {e}", exc_info=True)


    async def _check_and_restart_crashed_processes(self):
        """
        Проверяет "упавшие" или некорректно остановленные процессы агентов и интеграций
        и пытается их перезапустить или запустить.

        Если воркер находится в процессе остановки, проверка прерывается.

        Для агентов:
        - Получает ключи статусов агентов из Redis (`agent_status:*`).
        - Для каждого агента:
            - Проверяет, существует ли процесс с указанным PID (если PID есть).
            - Условия для перезапуска:
                1. Статус "running" или "initializing", но процесс с PID не существует.
                2. Поле `process_should_be_running` установлено в `true`, но процесс не жив
                   (независимо от текущего статуса, например, "stopped" или "error").
            - Если требуется перезапуск, вызывает `self.process_manager.restart_agent_process()`.

        Для интеграций:
        - Получает ключи статусов интеграций из Redis (`integration_status:*`).
        - Парсит `agent_id` и `integration_type` из ключа.
        - Для каждой интеграции:
            - Проверяет существование процесса по PID (если есть).
            - Определяет действие ("restart" или "start") на основе статуса, наличия PID,
              живучести процесса и флага `process_should_be_running`.
            - Если действие определено:
                - Загружает конфигурацию агента из БД для получения настроек интеграции.
                - Если настройки найдены, вызывает соответствующий метод `ProcessManager`
                  (`restart_integration_process` или `start_integration_process`).
        Логирует обнаружение "упавших" процессов, попытки перезапуска и ошибки.
        """
        if not self._running:
            self.logger.info(f"[{self._component_id}] Shutdown in progress. Skipping crash check and restarts.")
            return

        self.logger.debug(f"[{self._component_id}] Checking for crashed or stopped agents and integrations to restart...")

        pm_redis_client = await self.process_manager.redis_client # Get actual client
        if not pm_redis_client:
            self.logger.warning(f"[{self._component_id}] ProcessManager Redis client not available for crash check.")
            return
            
        # Check Agents
        try:
            agent_keys = await pm_redis_client.keys("agent_status:*")
            for key_bytes in agent_keys:
                if not self._running:
                    self.logger.info(f"[{self._component_id}] Shutdown in progress during agent crash check. Aborting.")
                    break
                key = key_bytes.decode('utf-8')
                agent_id = key.split(":", 1)[1]
                status_data = await pm_redis_client.hgetall(key)
                status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status_data.items()}
                
                current_status = status.get("status")
                pid_str = status.get("pid")
                process_should_be_running = status.get("process_should_be_running", "false").lower() == "true"

                # Check if process is actually running using os.kill(pid, 0) if PID exists
                process_alive = False
                if pid_str:
                    try:
                        pid = int(pid_str)
                        os.kill(pid, 0) # Check if process exists
                        process_alive = True
                    except (ProcessLookupError, ValueError): # PID not found or invalid
                        process_alive = False
                    except OSError as ose: # Other OS errors, e.g. permission denied
                        self.logger.warning(f"[{self._component_id}] OSError checking PID {pid_str} for agent {agent_id}: {ose}. Assuming not alive for safety.")
                        process_alive = False

                # Condition for restart:
                # 1. Status is 'running' or 'initializing', but process is not alive (crash).
                # 2. OR, status is 'stopped' or 'error', but 'process_should_be_running' is true (e.g. was manually stopped but should be auto-restarted)
                #    AND the process is not alive.
                
                needs_restart = False
                if current_status in ["running", "initializing"] and pid_str and not process_alive:
                    self.logger.warning(f"[{self._component_id}] Agent {agent_id} has status '{current_status}' with PID {pid_str} but process is not alive. Flagging for restart.")
                    needs_restart = True
                elif process_should_be_running and not process_alive : # Covers cases where it was stopped/errored but should be running
                    self.logger.warning(f"[{self._component_id}] Agent {agent_id} is marked as 'should_be_running' but process (PID: {pid_str or 'N/A'}) is not alive. Status: '{current_status}'. Flagging for restart.")
                    needs_restart = True

                if needs_restart and self._running:
                    self.logger.info(f"[{self._component_id}] Attempting to restart agent {agent_id}...")
                    await self.process_manager.restart_agent_process(agent_id)
                elif current_status in ["stopped", "error"] and not process_should_be_running:
                     self.logger.debug(f"[{self._component_id}] Agent {agent_id} is in status '{current_status}' and not marked 'process_should_be_running'. No restart action.")

        except redis.exceptions.RedisError as e_redis_agent:
            self.logger.error(f"[{self._component_id}] Redis error during agent crash check: {e_redis_agent}", exc_info=True)
        except Exception as e_agent_scan:
            self.logger.error(f"[{self._component_id}] Error scanning agent statuses for crash check: {e_agent_scan}", exc_info=True)

        if not self._running: 
            self.logger.info(f"[{self._component_id}] Shutdown in progress after agent checks. Skipping integration checks.")
            return

        # Check Integrations
        try:
            integration_keys = await pm_redis_client.keys("integration_status:*")
            valid_integration_type_values = [it.value for it in IntegrationType] # Get all valid enum values

            for key_bytes in integration_keys:
                if not self._running:
                    self.logger.info(f"[{self._component_id}] Shutdown in progress during integration crash check. Aborting.")
                    break
                key = key_bytes.decode('utf-8') # e.g., integration_status:agent_id:telegram
                parts = key.split(":")
                
                agent_id = None
                integration_type_str = None

                # New parsing logic:
                # Expected new format: "integration_status:<agent_id>:<integration_type_value>"
                if len(parts) == 3 and parts[0] == "integration_status":
                    potential_agent_id = parts[1]
                    potential_integration_type = parts[2].lower() # Convert to lowercase for case-insensitive comparison
                    if potential_integration_type in valid_integration_type_values: # valid_integration_type_values are already lowercase
                        agent_id = potential_agent_id
                        integration_type_str = potential_integration_type
                        self.logger.debug(f"[{self._component_id}] Parsed new format key: {key} -> agent_id={agent_id}, type={integration_type_str}")
                    else:
                        # Could be old format: "integration_status:<integration_type_value>:<agent_id>"
                        # where parts[1] is the type and parts[2] is the agent_id
                        # Ensure parts[1] is also lowercased if it's a type for comparison
                        if parts[1].lower() in valid_integration_type_values: # Check if parts[1] is a type
                             self.logger.warning(f"[{self._component_id}] Detected potentially old integration status key format: {key}. Skipping restart for this key during transition.")
                             continue # Skip old format for now
                        else:
                            self.logger.warning(f"[{self._component_id}] Malformed integration status key (3 parts, but type '{parts[2]}' not recognized or parts[1] not a type if old format): {key}")
                            continue
                elif len(parts) == 4 and parts[0] == "integration_status" and parts[1] == "telegram_bot": # Specific old format
                    self.logger.warning(f"[{self._component_id}] Detected old specific integration status key format (telegram_bot): {key}. Skipping.")
                    continue
                else:
                    self.logger.warning(f"[{self._component_id}] Malformed integration status key (unexpected number of parts or prefix): {key}. Parts: {parts}")
                    continue

                if not agent_id or not integration_type_str:
                    # This should ideally not be reached if continue statements work, but as a safeguard:
                    self.logger.error(f"[{self._component_id}] Failed to extract agent_id or integration_type_str from key {key} after parsing. Skipping.")
                    continue
                
                # ... (rest of the logic for status_data, process_alive, needs_restart is the same as before) ...
                # Ensure this part uses the correctly parsed agent_id and integration_type_str
                status_data = await pm_redis_client.hgetall(key)
                status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status_data.items()}
                current_status = status.get("status")
                pid_str = status.get("pid")
                process_should_be_running = status.get("process_should_be_running", "false").lower() == "true"

                process_alive = False
                if pid_str:
                    try:
                        pid = int(pid_str)
                        os.kill(pid, 0)
                        process_alive = True
                    except (ProcessLookupError, ValueError):
                        process_alive = False
                    except OSError as ose:
                        self.logger.warning(f"[{self._component_id}] OSError checking PID {pid_str} for integration {agent_id}/{integration_type_str}: {ose}. Assuming not alive.")
                        process_alive = False

                action_to_take = None # Can be "restart", "start", or None
                if current_status in ["running", "initializing"] and pid_str and not process_alive:
                    self.logger.warning(f"[{self._component_id}] Integration {agent_id}/{integration_type_str} has status '{current_status}' with PID {pid_str} but process is not alive. Flagging for restart.")
                    action_to_take = "restart"
                elif process_should_be_running and not process_alive: # Covers cases where it was stopped/errored but should be running
                     self.logger.warning(f"[{self._component_id}] Integration {agent_id}/{integration_type_str} is marked 'process_should_be_running' but process (PID: {pid_str or 'N/A'}) is not alive. Status: '{current_status}'. Flagging for restart.")
                     action_to_take = "restart"
                elif not pid_str and status.get("status") not in ["stopped", "not_found", "error_start_failed", "error_script_not_found"] and process_should_be_running:
                    self.logger.info(f"[{self._component_id}] Integration {agent_id}/{integration_type_str} should be running but has no PID and status is '{status.get('status', 'unknown')}'. Flagging for start.")
                    action_to_take = "start"

                if action_to_take and self._running:
                    self.logger.info(f"[{self._component_id}] Action '{action_to_take}' identified for integration {agent_id}/{integration_type_str}.")
                    
                    retrieved_integration_settings = None
                    async with SessionLocal() as db_session:
                        agent_config_db = None
                        try:
                            agent_config_db = await agent_crud.db_get_agent_config(db_session, agent_id=agent_id)
                            self.logger.debug(f"[{self._component_id}] Fetched agent_config_db for {agent_id} (action: {action_to_take} integration {integration_type_str}): {agent_config_db}")
                        except Exception as e:
                            self.logger.error(f"[{self._component_id}] DB error fetching agent config for {agent_id} to {action_to_take} integration {integration_type_str}: {e}", exc_info=True)
                            continue # Skip to next integration key on DB error

                        if not agent_config_db:
                            self.logger.info(f"[{self._component_id}] Agent {agent_id} not found in DB. Skipping {action_to_take} of its integration {integration_type_str}.")
                            continue # Skip to next integration key

                        # Agent config exists, now check for specific integration settings
                        if agent_config_db.config_json and agent_config_db.config_json.get("integrations"):
                            for integ_conf in agent_config_db.config_json["integrations"]:
                                if integ_conf.get("integration_type", "").upper() == integration_type_str.upper():
                                    retrieved_integration_settings = integ_conf.get("settings")
                                    self.logger.info(f"[{self._component_id}] Found settings for {agent_id}/{integration_type_str} for {action_to_take}.")
                                    break
                        
                        if retrieved_integration_settings is None:
                            self.logger.info(f"[{self._component_id}] Agent {agent_id} found, but integration type {integration_type_str} is not configured or has no settings in DB. Skipping {action_to_take}.")
                            continue # Skip to next integration key
                    
                    # Perform the action if settings were successfully retrieved (retrieved_integration_settings is not None)
                    if action_to_take == "restart":
                        self.logger.info(f"[{self._component_id}] Attempting to restart integration {agent_id}/{integration_type_str} with retrieved settings...")
                        restarted = await self.process_manager.restart_integration_process(agent_id, integration_type_str, integration_settings=retrieved_integration_settings)
                        if restarted:
                            self.logger.info(f"[{self._component_id}] Successfully initiated restart for integration {agent_id}/{integration_type_str}.")
                        else:
                            self.logger.error(f"[{self._component_id}] Failed to initiate restart for integration {agent_id}/{integration_type_str}.")
                    elif action_to_take == "start":
                        self.logger.info(f"[{self._component_id}] Attempting to start integration {agent_id}/{integration_type_str} with retrieved settings...")
                        # Assuming start_integration_process returns a boolean or similar indication of initiation
                        started = await self.process_manager.start_integration_process(agent_id, integration_type_str, integration_settings=retrieved_integration_settings)
                        if started: # Modify this check if start_integration_process has a different return signature
                            self.logger.info(f"[{self._component_id}] Successfully initiated start for integration {agent_id}/{integration_type_str}.")
                        else:
                            self.logger.error(f"[{self._component_id}] Failed to initiate start for integration {agent_id}/{integration_type_str}.")
                elif self._running: # No action_to_take but still running (e.g. process is alive and status is fine)
                     self.logger.debug(f"[{self._component_id}] No action needed for integration {agent_id}/{integration_type_str} (Status: '{current_status}', PID: {pid_str or 'N/A'}, Alive: {process_alive}, ShouldRun: {process_should_be_running}).")


        except redis.exceptions.RedisError as e_redis_int:
            self.logger.error(f"[{self._component_id}] Redis error during integration crash check: {e_redis_int}", exc_info=True)
        except Exception as e_int_scan:
            self.logger.error(f"[{self._component_id}] Error scanning integration statuses for crash check: {e_int_scan}", exc_info=True)

    async def cleanup(self):
        """
        Выполняет очистку ресурсов при завершении работы InactivityMonitorWorker.

        - Вызывает `cleanup_manager()` для `self.process_manager` для закрытия его соединений с Redis.
        - Вызывает `super().cleanup()` для очистки ресурсов `ScheduledTaskWorker` (например, `StatusUpdater`).
        Логирует процесс очистки.
        """
        self.logger.info(f"[{self._component_id}] Cleaning up InactivityMonitorWorker...")
        if self.process_manager:
            try:
                await self.process_manager.cleanup_manager()
                self.logger.info(f"[{self._component_id}] ProcessManager cleaned up.")
            except Exception as e_pm_cleanup:
                self.logger.error(f"[{self._component_id}] Error cleaning up ProcessManager: {e_pm_cleanup}", exc_info=True)
        await super().cleanup() # Cleans up StatusUpdater and Redis client for worker status
        self.logger.info(f"[{self._component_id}] InactivityMonitorWorker cleanup finished.")

# Removed old signal_handler and main_loop functions.

if __name__ == "__main__":
    # Basic logging setup for direct script execution.
    # If part of a larger app, central logging config should handle this.
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(name)s - [%(component_id)s] - %(message)s'
    )
    # Add a simple StreamHandler if no handlers are configured by basicConfig by default (e.g. in some environments)
    if not logging.getLogger().hasHandlers():
        logging.getLogger().addHandler(logging.StreamHandler())

    main_logger = logging.getLogger("inactivity_monitor_main")
     # Add a dummy component_id to the logger's extra, so the format string doesn't break
    main_logger = logging.LoggerAdapter(main_logger, {"component_id": "main"})
   
    main_logger.info("Initializing InactivityMonitorWorker...")

    worker = InactivityMonitorWorker()

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        main_logger.info("InactivityMonitorWorker interrupted by user (KeyboardInterrupt).")
        # worker.run() handles graceful shutdown via signals.
    except Exception as e:
        main_logger.critical(f"InactivityMonitorWorker failed to start or run: {e}", exc_info=True)
    finally:
        main_logger.info("InactivityMonitorWorker application finished.")

