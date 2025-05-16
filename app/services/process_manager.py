import asyncio
import json
import logging
import os
import signal
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pathlib import Path
from redis import exceptions as redis_exceptions # Added import

from app.core.config import settings
from app.core.base.redis_manager import RedisClientManager
from app.core.base.process_launcher import ProcessLauncher

# Placeholder for actual Pydantic schemas if needed later. For now, using Dicts.
AgentStatusInfo = Dict[str, Any]
IntegrationStatusInfo = Dict[str, Any]

# Fallback for IntegrationType if the enum is not directly used/imported
# This allows using string keys like "TELEGRAM"
IntegrationTypeStr = str 

logger = logging.getLogger(__name__)

# Define a default for graceful shutdown if not in settings
DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT = 10.0

class ProcessManager(RedisClientManager):
    """
    Управляет жизненным циклом дочерних процессов для агентов и интеграций.

    Отвечает за запуск, остановку, перезапуск и мониторинг состояния
    процессов агентов (локально или в Docker) и процессов интеграций (локально).
    Использует Redis для хранения и обновления информации о состоянии процессов.
    Наследует от `RedisClientManager` для управления соединением с Redis.

    Атрибуты:
        logger (logging.Logger): Логгер для журналирования событий.
        launcher (ProcessLauncher): Экземпляр для запуска дочерних процессов.
        agent_status_key_template (str): Шаблон ключа Redis для статуса агента.
        integration_status_key_template (str): Шаблон ключа Redis для статуса интеграции.
        project_root (str): Абсолютный путь к корневому каталогу проекта.
        python_executable (str): Путь к исполняемому файлу Python.
        agent_runner_module_path (str): Путь к модулю запуска агента.
        agent_runner_script_full_path (str): Полный путь к скрипту запуска агента.
        integration_module_paths (Dict[IntegrationTypeStr, str]): Словарь путей к модулям интеграций.
        integration_script_full_paths (Dict[IntegrationTypeStr, str]): Словарь полных путей к скриптам интеграций.
        process_env (Dict[str, str]): Переменные окружения для дочерних процессов.

    Методы:
        __init__(): Инициализирует менеджер процессов.
        setup_manager(): Инициализирует соединение с Redis.
        cleanup_manager(): Закрывает соединение с Redis.
        _update_status_in_redis(...): Обновляет статус процесса в Redis.
        _get_status_from_redis(...): Получает статус процесса из Redis.
        _delete_fields_from_redis_status(...): Удаляет поля из статуса в Redis.
        _delete_status_key_from_redis(...): Удаляет ключ статуса из Redis.
        delete_agent_status_completely(...): Полностью удаляет статус агента из Redis.
        delete_integration_status_completely(...): Полностью удаляет статус интеграции из Redis.
        start_agent_process(...): Запускает процесс агента.
        stop_agent_process(...): Останавливает процесс агента.
        restart_agent_process(...): Перезапускает процесс агента.
        get_agent_status(...): Получает статус агента.
        get_all_agent_statuses(...): Получает статусы всех агентов.
        start_integration_process(...): Запускает процесс интеграции.
        stop_integration_process(...): Останавливает процесс интеграции.
        get_integration_status(...): Получает статус интеграции.
        get_all_integration_statuses_for_agent(...): Получает статусы всех интеграций для агента.
        get_all_integration_statuses(...): Получает статусы всех интеграций.
        _get_docker_container_id(...): Получает ID Docker-контейнера по его имени.
        _get_docker_container_status(...): Получает статус Docker-контейнера.
        _validate_agent_process(...): Проверяет существование локального процесса агента.
        _validate_integration_process(...): Проверяет существование локального процесса интеграции.
    """
    def __init__(self):
        """
        Инициализирует ProcessManager.

        Выполняет следующие действия:
        - Инициализирует родительский класс `RedisClientManager`.
        - Инициализирует логгер и `ProcessLauncher`.
        - Определяет шаблоны ключей Redis для статусов агентов и интеграций.
        - Определяет корневой каталог проекта и пути к исполняемым файлам и скриптам.
        - Настраивает переменные окружения для дочерних процессов, включая PYTHONPATH.
        """
        super().__init__()  # Initialize RedisClientManager
        self.logger = logger # Initialize instance logger
        self.launcher = ProcessLauncher()

        self.agent_status_key_template = "agent_status:{}"  # agent_id
        self.integration_status_key_template = "integration_status:{}:{}"  # agent_id, integration_type_str

        # Determine Project Root with pathlib and detailed logging
        try:
            # Assuming this file is in app/services/process_manager.py
            # Path(__file__) -> /path/to/app/services/process_manager.py
            # .resolve() -> resolves symlinks, makes absolute
            # .parent -> /path/to/app/services
            # .parent -> /path/to/app
            # .parent -> /path/to/ (project_root)
            resolved_file_path = Path(__file__).resolve()
            self.logger.info(f"ProcessManager.__init__: Path(__file__).resolve() = {resolved_file_path}")
            
            project_root_path = resolved_file_path.parent.parent.parent
            self.project_root = str(project_root_path) # Convert Path object to string for os.path.join later
            
            self.logger.info(f"ProcessManager.__init__: Calculated PROJECT_ROOT_PATH with pathlib = {self.project_root}")
            
            # Validate the calculated project root
            if not os.path.isdir(self.project_root) or not os.path.exists(os.path.join(self.project_root, "app")):
                 self.logger.error(f"Calculated project root with pathlib does not seem to be a valid project directory: {self.project_root}")
                 # Fallback logic (consider removing or making more robust if pathlib fails)
                 dev_path = "/Users/jb/Projects/PlatformAI/PlatformAI-HUB" # User's known correct path
                 if os.path.isdir(dev_path) and os.path.exists(os.path.join(dev_path, "app")):
                    self.project_root = dev_path
                    self.logger.warning(f"Falling back to hardcoded development project_root: {self.project_root}")
                 else:
                    # Fallback to os.getcwd() as a last resort if hardcoded path is also invalid
                    self.project_root = os.getcwd() 
                    self.logger.error(f"CRITICAL: Project root detection failed with pathlib and fallback, using CWD: {self.project_root}. This will likely cause issues.")
            else:
                self.logger.info(f"PROJECT_ROOT_PATH {self.project_root} (from pathlib) seems valid.")

        except Exception as e:
            self.logger.error(f"Error calculating PROJECT_ROOT_PATH with pathlib: {e}", exc_info=True)
            # Fallback to os.getcwd() if any exception occurs during path calculation
            self.project_root = os.getcwd() 
            self.logger.warning(f"Fell back to using current working directory as project_root due to exception: {self.project_root}")

        self.python_executable = settings.PYTHON_EXECUTABLE

        # Agent runner paths
        self.agent_runner_module_path = settings.AGENT_RUNNER_MODULE_PATH
        self.agent_runner_script_full_path = settings.AGENT_RUNNER_SCRIPT_FULL_PATH

        # Integration paths (using string keys for integration types)
        self.integration_module_paths: Dict[IntegrationTypeStr, str] = {
            "TELEGRAM": "app.integrations.telegram.telegram_bot_main"
            # Add other integration types here
        }
        self.integration_script_full_paths: Dict[IntegrationTypeStr, str] = {
            k: os.path.join(self.project_root, v.replace('.', os.sep) + ".py")
            for k, v in self.integration_module_paths.items()
        }
        
        # Ensure PYTHONPATH is correctly set for subprocesses
        self.process_env = os.environ.copy()
        self.process_env["PYTHONPATH"] = self.project_root + \
            (os.pathsep + self.process_env.get("PYTHONPATH", "") if self.process_env.get("PYTHONPATH") else "")
        self.process_env["PYTHONUNBUFFERED"] = "1" # Common practice for scripts

    async def setup_manager(self):
        """
        Инициализирует соединение с Redis для ProcessManager.

        Вызывает `setup_redis_client` из родительского класса `RedisClientManager`,
        используя `settings.REDIS_URL`.
        Логирует успешную инициализацию.
        """
        await self.setup_redis_client(redis_url=str(settings.REDIS_URL))
        logger.info(f"ProcessManager initialized with Redis connection to {settings.REDIS_URL}")

    async def cleanup_manager(self):
        """
        Закрывает соединение с Redis, используемое ProcessManager.

        Вызывает `close_redis_resources` из родительского класса `RedisClientManager`.
        Логирует завершение очистки.
        """
        await self.close_redis_resources()
        logger.info("ProcessManager cleaned up Redis connection.")

    # --- Helper methods for Redis Hash operations (compatible with original service) ---
    async def _update_status_in_redis(self, key: str, status_dict: Dict[str, Any]):
        """
        Обновляет или создает запись о статусе в Redis (HSET).

        Автоматически добавляет/обновляет поле `last_updated_utc` текущим временем UTC.
        Гарантирует наличие поля `last_active`, устанавливая его в текущее время (timestamp),
        если оно отсутствует. Преобразует все значения словаря в строки.
        Пустые или None значения не записываются.

        Args:
            key (str): Ключ Redis, по которому будет сохранена информация (хеш).
            status_dict (Dict[str, Any]): Словарь с данными статуса для сохранения.
        """
        status_dict["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
        # last_active is often set specifically at point of activity or start/stop
        # Let's ensure it's present if not already.
        if "last_active" not in status_dict:
            status_dict["last_active"] = str(time.time())

        mapping = {k: str(v) for k, v in status_dict.items() if v is not None}
        if not mapping:
            logger.warning(f"Attempted to update status for key {key} with an empty mapping. Original dict: {status_dict}")
            return

        redis_cli = await self.redis_client
        await redis_cli.hset(key, mapping=mapping)
        logger.debug(f"Updated status for key {key} with mapping: {mapping}")

    async def _get_status_from_redis(self, key: str) -> Dict[str, str]:
        """
        Извлекает статус (хеш) из Redis по ключу.

        Если клиент Redis недоступен или ключ не существует, возвращает пустой словарь.
        Декодирует ключи и значения из байтов в строки UTF-8.
        В случае ошибок Redis или декодирования логирует проблему и возвращает пустой словарь.

        Args:
            key (str): Ключ Redis для извлечения хеша.

        Returns:
            Dict[str, str]: Словарь со статусной информацией, где ключи и значения являются строками.
                            Возвращает пустой словарь, если ключ не найден или произошла ошибка.
        """
        if not await self.is_redis_client_available():
            logger.warning(f"Redis client not available when trying to get status for key: {key}")
            return {}
        try:
            # self.redis_client is from RedisClientManager, an instance of redis.asyncio.Redis
            # decode_responses=False was used during client creation in RedisClientManager,
            # so hgetall returns Dict[bytes, bytes].
            redis_cli = await self.redis_client # Get the actual client instance
            raw_status_data: Dict[bytes, bytes] = await redis_cli.hgetall(key)
            
            if not raw_status_data:
                return {}
            
            # Decode keys and values from bytes to str
            decoded_status_data: Dict[str, str] = {}
            for k_bytes, v_bytes in raw_status_data.items():
                try:
                    k_str = k_bytes.decode('utf-8')
                    v_str = v_bytes.decode('utf-8')
                    decoded_status_data[k_str] = v_str
                except UnicodeDecodeError:
                    logger.warning(f"Could not decode UTF-8 for key or value in Redis hash for key {key}. Key bytes: {k_bytes!r}. Value bytes: {v_bytes!r}. Skipping problematic item.")
            return decoded_status_data
            
        except redis_exceptions.RedisError as e:
            logger.error(f"RedisError getting status from Redis for key {key}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error getting status from Redis for key {key}: {e.__class__.__name__} - {e}")
            return {}

    async def _delete_fields_from_redis_status(self, key: str, fields: List[str]):
        """
        Удаляет указанные поля из хеша статуса в Redis.

        Использует команду HDEL.

        Args:
            key (str): Ключ Redis, в котором находится хеш.
            fields (List[str]): Список имен полей для удаления.
        """
        if not fields: return
        redis_cli = await self.redis_client
        await redis_cli.hdel(key, *fields)
        logger.debug(f"Deleted fields {fields} from key {key}")
    
    async def _delete_status_key_from_redis(self, key: str):
        """
        Полностью удаляет ключ статуса из Redis.

        Использует команду DEL.

        Args:
            key (str): Ключ Redis для удаления.
        """
        redis_cli = await self.redis_client
        await redis_cli.delete(key)
        logger.debug(f"Deleted status key {key}")

    # --- Public methods for complete status deletion ---
    async def delete_agent_status_completely(self, agent_id: str):
        """
        Полностью удаляет всю информацию о статусе для указанного agent_id из Redis.

        Формирует ключ статуса агента и вызывает `_delete_status_key_from_redis`.

        Args:
            agent_id (str): Идентификатор агента.
        """
        status_key = self.agent_status_key_template.format(agent_id)
        await self._delete_status_key_from_redis(status_key)
        logger.info(f"Completely deleted status for agent {agent_id} from Redis.")

    async def delete_integration_status_completely(self, agent_id: str, integration_type: IntegrationTypeStr):
        """
        Полностью удаляет всю информацию о статусе для указанной интеграции агента из Redis.

        Формирует ключ статуса интеграции и вызывает `_delete_status_key_from_redis`.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").
        """
        integration_type_for_redis_key = integration_type.lower()

        status_key = self.integration_status_key_template.format(agent_id, integration_type_for_redis_key)
        await self._delete_status_key_from_redis(status_key)
        logger.info(f"Completely deleted status for integration {integration_type} of agent {agent_id} from Redis.")

    async def stop_agent_process(self, agent_id: str, force: bool = False) -> bool:
        """
        Останавливает процесс агента (локальный или Docker).

        Получает текущий статус агента. Если агент уже остановлен, ничего не делает.
        Обновляет статус в Redis на "stopping".
        Для Docker-агентов использует `docker stop` (и `docker kill` при `force=True`).
        Для локальных агентов отправляет SIGTERM, затем SIGKILL (при `force=True`), если процесс не завершился.
        После успешной остановки обновляет статус в Redis на "stopped" и удаляет динамические поля (pid, container_name и т.д.).

        Args:
            agent_id (str): Идентификатор агента.
            force (bool): Если True, применяет принудительные методы остановки (SIGKILL для локальных, docker kill для Docker).
                          По умолчанию False.

        Returns:
            bool: True, если процесс успешно остановлен (или уже был остановлен), иначе False.
        """
        status_key = self.agent_status_key_template.format(agent_id)
        status_info = await self.get_agent_status(agent_id)

        current_status = status_info.get("status", "unknown")
        pid_to_stop = status_info.get("pid") 
        runtime = status_info.get("runtime", "local")
        container_name = status_info.get("container_name")

        if current_status == "stopped" or current_status == "not_found":
            logger.info(f"Agent {agent_id} is already stopped or not found.")
            # Ensure Redis reflects this, cleaning up any lingering dynamic fields
            redis_cli = await self.redis_client # Get the actual client instance
            if await redis_cli.exists(status_key): # Now call exists on the instance
                 await self._update_status_in_redis(status_key, {"status": "stopped", "agent_id": agent_id})
                 await self._delete_fields_from_redis_status(status_key, ["pid", "container_name", "runtime", "last_active", "error_detail", "actual_container_id", "start_attempt_utc"])
            return True

        await self._update_status_in_redis(status_key, {"status": "stopping"})
        stopped_successfully = False

        try:
            if runtime == "docker" and container_name:
                logger.info(f"Stopping Docker agent {agent_id} (container: {container_name}). Force: {force}")
                stop_cmd = ["docker", "stop", container_name]
                rc_stop, out_stop, err_stop = await self.launcher.run_command_and_wait(stop_cmd, f"docker_stop_{container_name}")

                if rc_stop == 0:
                    logger.info(f"Docker container {container_name} stopped successfully.")
                    stopped_successfully = True
                else:
                    logger.warning(f"docker stop {container_name} failed. RC:{rc_stop}, stdout:'{out_stop}', stderr:'{err_stop}'.")
                    if force:
                        logger.warning(f"Forcing stop with docker kill for container {container_name}.")
                        kill_cmd = ["docker", "kill", container_name]
                        rc_kill, out_kill, err_kill = await self.launcher.run_command_and_wait(kill_cmd, f"docker_kill_{container_name}")
                        if rc_kill == 0:
                            logger.info(f"Docker container {container_name} killed successfully.")
                            stopped_successfully = True
                        else:
                            logger.error(f"docker kill {container_name} failed. RC:{rc_kill}, stdout:'{out_kill}', stderr:'{err_kill}'.")
            
            elif runtime == "local" and pid_to_stop:
                logger.info(f"Stopping local agent process {agent_id} (PID: {pid_to_stop}). Force: {force}")
                try:
                    os.kill(pid_to_stop, signal.SIGTERM)
                    logger.info(f"SIGTERM sent to agent {agent_id} (PID: {pid_to_stop})")
                    
                    wait_time = getattr(settings, 'PROCESS_GRACEFUL_SHUTDOWN_TIMEOUT', DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT)
                    for _ in range(int(wait_time / 0.1)): 
                        await asyncio.sleep(0.1)
                        try:
                            os.kill(pid_to_stop, 0) # Check if process exists
                        except ProcessLookupError:
                            stopped_successfully = True 
                            logger.info(f"Agent {agent_id} (PID: {pid_to_stop}) terminated gracefully after SIGTERM.")
                            break 
                    else: # Loop completed without break
                        logger.warning(f"Agent {agent_id} (PID: {pid_to_stop}) did not terminate after SIGTERM within {wait_time}s.")
                        if force:
                            logger.info(f"Forcing kill (SIGKILL) for agent {agent_id} (PID: {pid_to_stop}).")
                            try:
                                os.kill(pid_to_stop, signal.SIGKILL)
                                await asyncio.sleep(0.1) # Brief moment for SIGKILL to take effect
                                try:
                                    os.kill(pid_to_stop, 0) # Check if truly gone
                                    logger.warning(f"Agent {agent_id} (PID: {pid_to_stop}) still exists after SIGKILL attempt.")
                                    # stopped_successfully remains False
                                except ProcessLookupError:
                                    logger.info(f"Agent {agent_id} (PID: {pid_to_stop}) confirmed terminated after SIGKILL.")
                                    stopped_successfully = True
                            except ProcessLookupError: 
                                logger.info(f"Agent {agent_id} (PID: {pid_to_stop}) was already gone before SIGKILL could be sent or checked.")
                                stopped_successfully = True
                            except Exception as e_sigkill:
                                logger.error(f"Error sending SIGKILL to agent {agent_id} (PID: {pid_to_stop}): {e_sigkill}", exc_info=True)
                        # else (not force): stopped_successfully remains False if SIGTERM failed
                
                except ProcessLookupError:
                    logger.warning(f"Local agent process {agent_id} (PID: {pid_to_stop}) not found (already stopped?).")
                    stopped_successfully = True # Effectively stopped
                except Exception as e_kill: 
                    logger.error(f"Error stopping local agent {agent_id} (PID: {pid_to_stop}): {e_kill}", exc_info=True)

            else: 
                logger.warning(f"Cannot actively stop agent {agent_id}: runtime '{runtime}', PID '{pid_to_stop}', container '{container_name}'. Marking as stopped if no PID/container.")
                if not pid_to_stop and not (runtime == "docker" and container_name):
                    stopped_successfully = True # No specific process to target, assume it's effectively stopped.
        
        except FileNotFoundError: 
            logger.error("'docker' command not found. Cannot stop Dockerized agent.", exc_info=True)
            await self._update_status_in_redis(status_key, {"status": "error_stop_failed", "error_detail": "Docker command not found."})
            return False
        except Exception as e: 
            logger.error(f"Unexpected error during stop_agent_process for {agent_id}: {e}", exc_info=True)
            await self._update_status_in_redis(status_key, {"status": "error_stop_failed", "error_detail": str(e)})
            return False

        # Final status update based on outcome
        if stopped_successfully:
            final_status_update = {"status": "stopped", "agent_id": agent_id}
            await self._update_status_in_redis(status_key, final_status_update)
            # Clean up dynamic fields after confirming stop
            await self._delete_fields_from_redis_status(status_key, ["pid", "container_name", "runtime", "last_active", "error_detail", "actual_container_id", "start_attempt_utc"])
            # Re-set status to ensure only "stopped" and "agent_id" (and last_updated_utc) remain.
            await self._update_status_in_redis(status_key, {"status": "stopped", "agent_id": agent_id}) # This ensures last_updated_utc is fresh
            logger.info(f"Agent {agent_id} marked as stopped in Redis.")
            return True
        else:
            logger.warning(f"Failed to stop agent {agent_id}. Current Redis status might be 'stopping' or original status if stop attempt failed early.")
            # Revert to a more accurate error status if still "stopping"
            current_redis_status_map = await self._get_status_from_redis(status_key)
            if current_redis_status_map.get("status") == "stopping":
                 await self._update_status_in_redis(status_key, {"status": "error_stop_failed", "error_detail": "Failed to confirm stop."})
            return False

    async def restart_agent_process(self, agent_id: str) -> bool:
        """
        Перезапускает процесс агента.

        Сначала принудительно останавливает существующий процесс агента (`stop_agent_process` с `force=True`).
        Если остановка не удалась, прерывает перезапуск.
        После успешной остановки и небольшой паузы (`settings.RESTART_DELAY_SECONDS`) запускает новый процесс агента (`start_agent_process`).
        Обновляет статус в Redis в соответствии с результатом каждой операции.

        Args:
            agent_id (str): Идентификатор агента для перезапуска.

        Returns:
            bool: True, если агент успешно перезапущен, иначе False.
        """
        logger.info(f"Restarting agent process for {agent_id}...")
        # Force stop, as it's a restart.
        stopped = await self.stop_agent_process(agent_id, force=True) 
        
        if not stopped:
            logger.error(f"Failed to stop agent {agent_id} during restart. Aborting restart.")
            # stop_agent_process should have set an appropriate error status.
            return False

        logger.info(f"Agent {agent_id} stopped (or stop attempted and assumed successful for restart). Pausing briefly...")
        await asyncio.sleep(settings.RESTART_DELAY_SECONDS) # Corrected: Use RESTART_DELAY_SECONDS

        logger.info(f"Proceeding to start agent {agent_id} after delay...")
        try:
            started = await self.start_agent_process(agent_id)
            if not started:
                logger.error(f"Restart failed: start_agent_process returned False for agent {agent_id}.")
                # start_agent_process should set its own error status (e.g. "error_start_failed")
                return False
            logger.info(f"Agent {agent_id} successfully started as part of restart sequence.")
            return True 
        except Exception as e:            
            logger.error(f"Restart for agent {agent_id} failed with an exception during start phase: {e}", exc_info=True)
            await self._update_status_in_redis(
                self.agent_status_key_template.format(agent_id), 
                {"status": "error_restart_failed", 
                 "error_detail": f"Exception during start phase of restart: {str(e)}"}
            )
            return False

    async def get_integration_status(self, agent_id: str, integration_type: IntegrationTypeStr) -> IntegrationStatusInfo:
        """
        Получает информацию о статусе для указанной интеграции агента.

        Извлекает данные из Redis. Если статус в Redis указывает на запущенный локальный процесс,
        проверяет его существование с помощью `os.kill(pid, 0)`.
        Если процесс не найден, обновляет статус в Redis на "error_process_lost".

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").

        Returns:
            IntegrationStatusInfo: Словарь с информацией о статусе интеграции, включая:
                `agent_id`, `integration_type`, `status`, `pid`, `last_active`, `runtime`,
                `container_name`, `error_detail`.
                Если статус не найден, возвращает статус "not_found".
        """
        integration_type_for_redis_key = integration_type.lower()

        status_key = self.integration_status_key_template.format(agent_id, integration_type_for_redis_key)
        status_data = await self._get_status_from_redis(status_key)

        if not status_data:
            return {
                "agent_id": agent_id, 
                "integration_type": integration_type,
                "status": "not_found", 
                "pid": None, 
                "last_active": None,
                "runtime": "local", 
                "container_name": None,
                "error_detail": None
            }

        current_status = status_data.get("status", "unknown")
        pid_val = status_data.get("pid")
        pid = int(pid_val) if pid_val and pid_val.isdigit() else None
        last_active_val = status_data.get("last_active")
        try:
            last_active = float(last_active_val) if last_active_val else None
        except (ValueError, TypeError):
            last_active = None
        
        runtime = status_data.get("runtime", "local") 
        container_name = status_data.get("container_name")

        # Validate process/container existence if status suggests it should be running
        if pid and current_status in ["running", "starting", "initializing"] and runtime == "local":
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                logger.warning(f"Integration {integration_type} for agent {agent_id} (local) status is '{current_status}' in Redis, but PID {pid} not found. Updating status.")
                current_status = "error_process_lost"
                await self._update_status_in_redis(status_key, {"status": current_status, "pid": "", "integration_type": integration_type})
                pid = None 
            except OSError as e:
                logger.error(f"Error checking PID {pid} for local integration {integration_type} of agent {agent_id}: {e}. Status remains '{current_status}'.")
        
        return {
            "agent_id": agent_id,
            "integration_type": integration_type,
            "status": current_status,
            "pid": pid,
            "last_active": last_active,
            "runtime": runtime,
            "container_name": container_name,
            "error_detail": status_data.get("error_detail")
        }

    async def start_integration_process(self, agent_id: str, integration_type: IntegrationTypeStr, integration_settings: Optional[Dict[str, Any]] = None) -> bool:
        """
        Запускает процесс интеграции для указанного агента.

        Проверяет, не запущен ли уже процесс или не находится ли он в процессе запуска.
        Определяет путь к скрипту интеграции на основе `integration_type`.
        Если скрипт не найден, устанавливает статус "error_script_not_found".
        Формирует команду для запуска скрипта интеграции Python (`python -m module_path ...`)
        с передачей `agent_id`, `integration_settings` (в JSON) и `redis_url`.
        Запускает процесс с помощью `ProcessLauncher` без захвата вывода.
        Обновляет статус в Redis ("starting", затем PID после успешного запуска, или "error_start_failed" при ошибке).

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").
            integration_settings (Optional[Dict[str, Any]]): Настройки для интеграции, передаваемые в процесс.
                                                              По умолчанию None.

        Returns:
            bool: True, если процесс интеграции успешно инициирован, иначе False.
        """
        integration_type_for_redis_key = integration_type.lower()

        status_key = self.integration_status_key_template.format(agent_id, integration_type_for_redis_key)
        
        try:
            current_integration_status_info = await self.get_integration_status(agent_id, integration_type)
            if current_integration_status_info["status"] == "running":
                logger.warning(f"Integration {integration_type} for agent {agent_id} already reported as running. Skipping start.")
                return True
            if current_integration_status_info["status"] == "starting":
                logger.warning(f"Integration {integration_type} for agent {agent_id} is already starting. Skipping duplicate start request.")
                return True
        except Exception as e:
            logger.error(f"Error checking integration status before start for {agent_id}/{integration_type}: {e}", exc_info=True)

        logger.info(f"Attempting to start integration process for {agent_id}/{integration_type}...")

        module_path = self.integration_module_paths.get(integration_type.upper())
        script_full_path = self.integration_script_full_paths.get(integration_type.upper())

        if not module_path or not script_full_path or not os.path.exists(script_full_path):
            logger.error(f"Integration script/module not found for type: {integration_type}. Path: {script_full_path}")
            await self._update_status_in_redis(status_key, {
                "status": "error_script_not_found", 
                "error_detail": f"Script not found for {integration_type}",
                "agent_id": agent_id,
                "integration_type": integration_type
            })
            return False

        initial_status_data: Dict[str, Any] = {
            "status": "starting",
            "agent_id": agent_id,
            "integration_type": integration_type,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time()),
            "runtime": "local"
        }
        
        try:
            cmd = [
                self.python_executable,
                "-m", module_path,
                "--agent-id", agent_id,
            ]
            if integration_settings:
                cmd.extend(["--integration-settings", json.dumps(integration_settings)])
            
            cmd.extend(["--redis-url", str(settings.REDIS_URL)])

            logger.info(f"Starting local integration {integration_type} for agent {agent_id}. Command: {' '.join(cmd)}")
            await self._update_status_in_redis(status_key, initial_status_data)

            process_obj, _, _ = await self.launcher.launch_process(
                command=cmd,
                process_id=f"local_integration_start_{agent_id}_{integration_type}",
                cwd=self.project_root,
                env_vars=self.process_env,
                capture_output=False
            )

            if process_obj and process_obj.pid is not None:
                logger.info(f"Local integration process {integration_type} for agent {agent_id} initiated start with PID {process_obj.pid}")
                await self._update_status_in_redis(status_key, {"pid": str(process_obj.pid), "integration_type": integration_type})
                return True
            else:
                err_msg = f"Failed to launch local integration process {integration_type} for agent {agent_id} (process_obj or pid is None)."
                logger.error(err_msg)
                await self._update_status_in_redis(status_key, {
                    "status": "error_start_failed", 
                    "error_detail": err_msg, 
                    "agent_id": agent_id, 
                    "integration_type": integration_type
                })
                return False
        
        except FileNotFoundError as fnf_e:
            logger.error(f"Failed to start integration {integration_type} for {agent_id} due to FileNotFoundError: {fnf_e}", exc_info=True)
            await self._update_status_in_redis(status_key, {
                "status": "error_script_not_found", 
                "error_detail": str(fnf_e), 
                "integration_type": integration_type
            })
            return False
        except Exception as e:
            logger.error(f"Failed to start integration {integration_type} for {agent_id}: {e}", exc_info=True)
            await self._update_status_in_redis(status_key, {
                "status": "error_start_failed", 
                "error_detail": str(e), 
                "integration_type": integration_type
            })
            return False

    async def stop_integration_process(self, agent_id: str, integration_type: IntegrationTypeStr, force: bool = False) -> bool:
        """
        Останавливает процесс интеграции для указанного агента.

        Получает текущий статус интеграции. Если уже остановлена, ничего не делает.
        Обновляет статус в Redis на "stopping".
        Для локальных процессов отправляет SIGTERM, затем SIGKILL (при `force=True`), если процесс не завершился.
        Остановка Docker-контейнеров для интеграций в настоящее время не реализована и вызовет ошибку, если `runtime`="docker" и есть `container_name`.
        После успешной остановки обновляет статус в Redis на "stopped" и удаляет динамические поля.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").
            force (bool): Если True, применяет принудительные методы остановки (SIGKILL для локальных).
                          По умолчанию False.

        Returns:
            bool: True, если процесс успешно остановлен (или уже был остановлен), иначе False.
        """
        integration_type_for_redis_key = integration_type.lower()

        status_key = self.integration_status_key_template.format(agent_id, integration_type_for_redis_key)
        self.logger.info(f"Attempting to stop integration {agent_id}/{integration_type}. Force: {force}")

        status_info = await self.get_integration_status(agent_id, integration_type)

        current_status = status_info.get("status", "unknown")
        pid_to_stop = status_info.get("pid")
        runtime = status_info.get("runtime", "local")
        container_name = status_info.get("container_name") 

        if current_status == "stopped" or current_status == "not_found":
            logger.info(f"Integration {integration_type} for agent {agent_id} is already stopped or not found.")
            redis_cli = await self.redis_client
            if await redis_cli.exists(status_key):
                 await self._update_status_in_redis(status_key, {
                     "status": "stopped", 
                     "agent_id": agent_id, 
                     "integration_type": integration_type
                 })
                 await self._delete_fields_from_redis_status(status_key, ["pid", "container_name", "runtime", "last_active", "error_detail", "start_attempt_utc"])
            return True

        await self._update_status_in_redis(status_key, {"status": "stopping", "integration_type": integration_type})
        stopped_successfully = False

        try:
            if runtime == "local" and pid_to_stop is not None:
                logger.info(f"Stopping local integration {integration_type} for agent {agent_id} (PID: {pid_to_stop}). Force: {force}")
                try:
                    os.kill(pid_to_stop, signal.SIGTERM) 
                    logger.info(f"SIGTERM sent to integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id}")
                    
                    wait_time = getattr(settings, 'PROCESS_GRACEFUL_SHUTDOWN_TIMEOUT', DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT)
                    for _ in range(int(wait_time / 0.1)): 
                        await asyncio.sleep(0.1)
                        try:
                            os.kill(pid_to_stop, 0)
                        except ProcessLookupError:
                            stopped_successfully = True 
                            logger.info(f"Integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id} terminated gracefully after SIGTERM.")
                            break 
                    else: 
                        logger.warning(f"Integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id} did not terminate after SIGTERM within {wait_time}s.")
                        if force:
                            logger.info(f"Forcing kill (SIGKILL) for integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id}.")
                            try:
                                os.kill(pid_to_stop, signal.SIGKILL)
                                await asyncio.sleep(0.1) 
                                try:
                                    os.kill(pid_to_stop, 0) 
                                    logger.warning(f"Integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id} still exists after SIGKILL attempt.")
                                except ProcessLookupError:
                                    logger.info(f"Integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id} confirmed terminated after SIGKILL.")
                                    stopped_successfully = True
                            except ProcessLookupError: 
                                logger.info(f"Integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id} was already gone before SIGKILL could be sent или confirmed.")
                                stopped_successfully = True
                            except Exception as e_sigkill:
                                logger.error(f"Error sending SIGKILL to integration {integration_type} (PID: {pid_to_stop}) for agent {agent_id}: {e_sigkill}", exc_info=True)
                
                except ProcessLookupError: 
                    logger.warning(f"Local integration process {integration_type} for agent {agent_id} (PID: {pid_to_stop}) not found (already stopped?).")
                    stopped_successfully = True 
                except Exception as e_kill: 
                    logger.error(f"Error stopping local integration {integration_type} for agent {agent_id} (PID: {pid_to_stop}): {e_kill}", exc_info=True)
            
            elif runtime == "docker": 
                 logger.warning(f"Docker runtime for integration {integration_type} (agent {agent_id}) stop is not currently implemented. Container: {container_name}")
                 if not container_name: 
                     logger.info(f"Integration {integration_type} (agent {agent_id}) is Docker runtime but has no container name, considering it stopped.")
                     stopped_successfully = True
                 else:
                     logger.error(f"Cannot stop Dockerized integration {integration_type} (agent {agent_id}), container {container_name}: not implemented.")

            else: 
                logger.warning(f"Cannot actively stop integration {integration_type} for agent {agent_id}: runtime '{runtime}', PID '{pid_to_stop}'. Marking as stopped if no PID/container.")
                if not pid_to_stop and not (runtime == "docker" and container_name):
                    stopped_successfully = True 
        
        except Exception as e: 
            logger.error(f"Unexpected error during stop_integration_process for {agent_id}/{integration_type}: {e}", exc_info=True)
            await self._update_status_in_redis(status_key, {
                "status": "error_stop_failed", 
                "error_detail": str(e), 
                "integration_type": integration_type
            })
            return False

        if stopped_successfully:
            final_status_update = {
                "status": "stopped", 
                "agent_id": agent_id, 
                "integration_type": integration_type
            }
            await self._update_status_in_redis(status_key, final_status_update)
            await self._delete_fields_from_redis_status(status_key, ["pid", "container_name", "runtime", "last_active", "error_detail", "start_attempt_utc"])
            await self._update_status_in_redis(status_key, {
                "status": "stopped", 
                "agent_id": agent_id, 
                "integration_type": integration_type
            }) 
            logger.info(f"Integration {integration_type} for agent {agent_id} marked as stopped in Redis.")
            return True
        else:
            logger.warning(f"Failed to stop integration {integration_type} for agent {agent_id}. Current Redis status might be 'stopping'.")
            current_redis_status_map = await self._get_status_from_redis(status_key)
            if current_redis_status_map.get("status") == "stopping": 
                 await self._update_status_in_redis(status_key, {
                     "status": "error_stop_failed", 
                     "error_detail": "Failed to confirm stop.", 
                     "integration_type": integration_type
                 })
            return False

    async def restart_integration_process(self, agent_id: str, integration_type: IntegrationTypeStr, integration_settings: Optional[Dict[str, Any]] = None) -> bool:
        logger.info(f"Restarting integration {integration_type} for agent {agent_id}...")
        
        stopped = await self.stop_integration_process(agent_id, integration_type, force=True) 
        
        if not stopped:
            logger.error(f"Failed to stop integration {integration_type} for agent {agent_id} during restart. Aborting restart.")
            return False

        delay_seconds = settings.RESTART_DELAY_SECONDS
        logger.info(f"Integration {integration_type} for agent {agent_id} stopped. Pausing for {delay_seconds}s...")
        await asyncio.sleep(delay_seconds) 

        logger.info(f"Proceeding to start integration {integration_type} for agent {agent_id} after delay...")
        try:
            started = await self.start_integration_process(agent_id, integration_type, integration_settings=integration_settings)
            if not started:
                logger.error(f"Restart failed: start_integration_process returned False for {agent_id}/{integration_type}.")
                return False
            logger.info(f"Integration {integration_type} for agent {agent_id} successfully started as part of restart sequence.")
            return True 
        except Exception as e: 
            logger.error(f"Restart for {agent_id}/{integration_type} failed with an exception during start phase: {e}", exc_info=True)
            
            integration_type_for_redis_key = integration_type.lower()
            status_key = self.integration_status_key_template.format(agent_id, integration_type_for_redis_key)
            await self._update_status_in_redis(
                status_key,
                {"status": "error_restart_failed", 
                 "error_detail": f"Exception during start phase of restart: {str(e)}",
                 "integration_type": integration_type
                 }
            )
            return False

    async def get_agent_status(self, agent_id: str) -> AgentStatusInfo:
        status_key = self.agent_status_key_template.format(agent_id)
        status_data = await self._get_status_from_redis(status_key)

        if not status_data:
            return {
                "agent_id": agent_id,
                "status": "not_found",
                "pid": None,
                "last_active": None,
                "runtime": "local", # Default assumption
                "container_name": None,
                "actual_container_id": None,
                "error_detail": None,
            }

        current_status = status_data.get("status", "unknown")
        pid_val = status_data.get("pid")
        pid = int(pid_val) if pid_val and pid_val.isdigit() else None
        
        last_active_val = status_data.get("last_active")
        try:
            last_active = float(last_active_val) if last_active_val else None
        except (ValueError, TypeError):
            last_active = None
        
        runtime = status_data.get("runtime", "local")
        container_name = status_data.get("container_name")
        actual_container_id = status_data.get("actual_container_id")

        # Validate process/container existence if status suggests it should be running
        active_statuses = ["running", "starting", "initializing", "running_pending_agent_confirm"]
        if current_status in active_statuses:
            if runtime == "local" and pid:
                try:
                    os.kill(pid, 0)  # Check if host process exists
                except ProcessLookupError:
                    logger.warning(f"Agent {agent_id} (local) status is '{current_status}' in Redis, but PID {pid} not found. Updating status.")
                    current_status = "error_process_lost"
                    await self._update_status_in_redis(status_key, {"status": current_status, "pid": ""})
                    pid = None
                except OSError as e:
                    logger.error(f"Error checking PID {pid} for local agent {agent_id}: {e}. Status remains '{current_status}'.")
            elif runtime == "docker" and container_name:
                try:
                    # Check if container is running
                    check_cmd = ["docker", "ps", "-q", "--filter", f"name=^{container_name}$"]
                    rc, stdout, stderr = await self.launcher.run_command_and_wait(check_cmd, f"docker_ps_check_{container_name}")
                    if rc == 0:
                        if not stdout.strip(): # Container not found in running list
                            logger.warning(f"Agent {agent_id} (Docker container {container_name}) status is '{current_status}' in Redis, but container not found running. Updating status.")
                            current_status = "error_container_lost" # Or "stopped"
                            await self._update_status_in_redis(status_key, {"status": current_status, "actual_container_id": ""})
                            actual_container_id = None
                        else: # Container is running, stdout is the ID
                            running_container_id = stdout.strip()
                            if actual_container_id != running_container_id:
                                logger.info(f"Agent {agent_id} (Docker container {container_name}) running with ID {running_container_id}. Updating actual_container_id.")
                                await self._update_status_in_redis(status_key, {"actual_container_id": running_container_id})
                                actual_container_id = running_container_id
                    else:
                        logger.error(f"Error checking Docker container status for {container_name}: RC:{rc}, stdout:'{stdout}', stderr:'{stderr}'. Status remains '{current_status}'.")
                except FileNotFoundError:
                    logger.error("'docker' command not found. Cannot verify Dockerized agent status.", exc_info=True)
                    current_status = "error_docker_unavailable" # Special status
                    await self._update_status_in_redis(status_key, {"status": current_status})

                except Exception as e:
                    logger.error(f"Unexpected error checking Docker container {container_name} for agent {agent_id}: {e}", exc_info=True)
        
        return {
            "agent_id": agent_id,
            "status": current_status,
            "pid": pid,
            "last_active": last_active,
            "runtime": runtime,
            "container_name": container_name,
            "actual_container_id": actual_container_id,
            "error_detail": status_data.get("error_detail"),
            "last_updated_utc": status_data.get("last_updated_utc"),
            "start_attempt_utc": status_data.get("start_attempt_utc"),
        }

    async def start_agent_process(self, agent_id: str, agent_settings: Optional[Dict[str, Any]] = None) -> bool:
        status_key = self.agent_status_key_template.format(agent_id)
        
        try:
            current_agent_status_info = await self.get_agent_status(agent_id)
            if current_agent_status_info["status"] in ["running", "running_pending_agent_confirm"]:
                logger.warning(f"Agent {agent_id} already reported as running or pending confirmation. Skipping start.")
                return True
            if current_agent_status_info["status"] == "starting":
                logger.warning(f"Agent {agent_id} is already starting. Skipping duplicate start request.")
                return True
        except Exception as e:
            logger.error(f"Error checking agent status before start for {agent_id}: {e}", exc_info=True)
            # Continue to attempt start, but log this issue.

        logger.info(f"Attempting to start agent process for {agent_id}...")

        initial_status_data: Dict[str, Any] = {
            "status": "starting",
            "agent_id": agent_id,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time()), # Mark activity at start attempt
            "pid": None,
            "container_name": None,
            "actual_container_id": None,
            "error_detail": None
        }

        try:
            if settings.RUN_AGENTS_WITH_DOCKER:
                initial_status_data["runtime"] = "docker"
                container_name = f"agent_runner_{agent_id}"
                initial_status_data["container_name"] = container_name
                
                env_vars = {
                    "AGENT_ID": agent_id,
                    "REDIS_URL": str(settings.REDIS_URL), # Ensure REDIS_URL is accessible from container
                    "MANAGER_HOST": settings.MANAGER_HOST, # Ensure MANAGER_HOST is accessible
                    "MANAGER_PORT": str(settings.MANAGER_PORT),
                    "PYTHONUNBUFFERED": "1", # Good practice
                    # Add other necessary env vars based on agent_runner needs
                }
                if agent_settings: # Pass agent_settings as JSON string in env var
                    env_vars["AGENT_SETTINGS_JSON"] = json.dumps(agent_settings)

                cmd = ["docker", "run", "-d", "--rm", "--name", container_name]
                for k, v_env in env_vars.items():
                    cmd.extend(["-e", f"{k}={v_env}"])
                
                # Network configuration might be needed here, e.g. --network host or user-defined network
                # This depends on how Redis and the manager API are exposed to Docker containers.
                # Example: if Redis is on host, use host.docker.internal on Docker Desktop, or host IP.
                # cmd.extend(["--network", "host"]) # If services are on localhost and need direct access

                cmd.append(settings.AGENT_DOCKER_IMAGE)
                # If agent_runner inside Docker needs specific command/args, append them here.
                # Otherwise, assume Docker image's ENTRYPOINT/CMD handles it using ENV VARS.

                logger.info(f"Starting Dockerized agent {agent_id}. Command: {' '.join(cmd)}")
                await self._update_status_in_redis(status_key, initial_status_data)

                rc, stdout, stderr = await self.launcher.run_command_and_wait(cmd, f"docker_run_{container_name}")

                if rc == 0 and stdout.strip():
                    actual_container_id = stdout.strip()
                    logger.info(f"Dockerized agent {agent_id} (container: {container_name}) initiated with ID: {actual_container_id}")
                    # Agent itself should update status to "running". Manager confirms launch.
                    await self._update_status_in_redis(status_key, {"status": "running_pending_agent_confirm", "actual_container_id": actual_container_id})
                    return True
                else:
                    err_msg = f"Failed to start Docker container {container_name} for agent {agent_id}. RC:{rc}, stdout:'{stdout}', stderr:'{stderr}'"
                    logger.error(err_msg)
                    await self._update_status_in_redis(status_key, {"status": "error_start_failed", "error_detail": err_msg, "runtime": "docker"})
                    return False
            else: # Local execution
                initial_status_data["runtime"] = "local"
                if not os.path.exists(self.agent_runner_script_full_path) and not self.agent_runner_module_path:
                     err_msg = f"Agent runner script/module not found. Path: {self.agent_runner_script_full_path}, Module: {self.agent_runner_module_path}"
                     logger.error(err_msg)
                     await self._update_status_in_redis(status_key, {"status": "error_script_not_found", "error_detail": err_msg, "runtime": "local"})
                     return False

                # Construct config_url
                # Assumes http for local development. Use https if appropriate for production.
                # Ensure API_V1_STR starts with a '/' if it's part of the path.
                api_v1_path = settings.API_V1_STR
                if not api_v1_path.startswith('/'):
                    api_v1_path = '/' + api_v1_path
                
                # Ensure no double slashes between host:port and api_v1_path
                base_url = f"http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}"
                config_url = f"{base_url}{api_v1_path}/agents/{agent_id}/config"

                cmd = [
                    self.python_executable,
                    "-m", self.agent_runner_module_path, # Use module path for robust execution
                    "--agent-id", agent_id,
                    # "--redis-url", str(settings.REDIS_URL),
                    # "--manager-host", settings.MANAGER_HOST, # Kept for other potential uses by agent
                    # "--manager-port", str(settings.MANAGER_PORT), # Kept for other potential uses by agent
                    "--config-url", config_url # Added config-url
                ]
                if agent_settings: # Pass as JSON string argument
                    cmd.extend(["--agent-settings-json", json.dumps(agent_settings)])

                logger.info(f"Starting local agent {agent_id}. Command: {' '.join(cmd)}")
                await self._update_status_in_redis(status_key, initial_status_data)

                process_obj, _, _ = await self.launcher.launch_process(
                    command=cmd,
                    process_id=f"local_agent_start_{agent_id}",
                    cwd=self.project_root, # Run from project root
                    env_vars=self.process_env, # Ensure PYTHONPATH is set
                    capture_output=False # Agent runs in background
                )

                if process_obj and process_obj.pid is not None:
                    logger.info(f"Local agent process {agent_id} initiated start with PID {process_obj.pid}")
                    # Agent itself should update status to "running". Manager confirms launch.
                    await self._update_status_in_redis(status_key, {"status": "running_pending_agent_confirm", "pid": str(process_obj.pid)})
                    return True
                else:
                    err_msg = f"Failed to launch local agent process {agent_id} (process_obj or pid is None)."
                    logger.error(err_msg)
                    await self._update_status_in_redis(status_key, {"status": "error_start_failed", "error_detail": err_msg, "runtime": "local"})
                    return False

        except FileNotFoundError as fnf_e: # E.g. python executable or docker not found
            logger.error(f"Failed to start agent {agent_id} due to FileNotFoundError: {fnf_e}", exc_info=True)
            err_detail = f"File not found: {str(fnf_e)}"
            current_runtime = initial_status_data.get("runtime", "unknown")
            await self._update_status_in_redis(status_key, {"status": "error_start_failed", "error_detail": err_detail, "runtime": current_runtime})
            return False
        except Exception as e:
            logger.error(f"Failed to start agent {agent_id}: {e}", exc_info=True)
            err_detail = str(e)
            current_runtime = initial_status_data.get("runtime", "unknown")
            await self._update_status_in_redis(status_key, {"status": "error_start_failed", "error_detail": err_detail, "runtime": current_runtime})
            return False

    async def _start_local_agent_process(self, agent_id: str, config_url: str, agent_settings: Dict[str, Any]) -> Optional[int]:
        """Helper to start a local agent process."""
        cmd = [
            self.python_executable,
            "-u", # Unbuffered stdout/stderr
            self.agent_runner_script_full_path,
            "--agent-id", agent_id,
            "--config-url", config_url,
            # Удален --redis-url так как runner_main.py его больше не использует напрямую
        ]
        # Дополнительные аргументы из agent_settings могут быть добавлены здесь, если runner_main.py их поддерживает
        # Например, если runner_main.py будет принимать --custom-arg value
        # if "custom_arg_value" in agent_settings:
        #     cmd.extend(["--custom-arg", str(agent_settings["custom_arg_value"])])

        pid = await self.launcher.launch_process(cmd, self.process_env, f"agent_{agent_id}")
        if pid:
            logger.info(f"Successfully started local agent process for {agent_id} with PID {pid}. Command: {' '.join(cmd)}")
        else:
            logger.error(f"Failed to start local agent process for {agent_id}. Command: {' '.join(cmd)}")
        return pid

# Example of how to use the ProcessManager (e.g., in an API router or a main service script)
async def example_usage():
    manager = ProcessManager()
    await manager.setup_manager()

    test_agent_id = "test_agent_001"
    
    # Start an agent (assuming RUN_AGENTS_WITH_DOCKER=False for local test)
    # You'd need to have the agent runner script and its dependencies set up.
    # print(f"Attempting to start agent: {test_agent_id}")
    # success_start = await manager.start_agent_process(test_agent_id)
    # print(f"Agent start success: {success_start}")
    # await asyncio.sleep(5) # Give it time to start and potentially update its own status
    
    # status = await manager.get_agent_status(test_agent_id)
    # print(f"Agent status: {status}")

    # print(f"Attempting to stop agent: {test_agent_id}")
    # success_stop = await manager.stop_agent_process(test_agent_id, force=True)
    # print(f"Agent stop success: {success_stop}")
    
    # status_after_stop = await manager.get_agent_status(test_agent_id)
    # print(f"Agent status after stop: {status_after_stop}")

    await manager.cleanup_manager()

if __name__ == "__main__":
    # This example requires a running Redis instance and appropriate settings.
    # Also, the agent runner scripts and integration scripts must exist at configured paths.
    
    # Configure logging for the example
    # logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # logging.getLogger("app.core.base.redis_manager").setLevel(logging.INFO) # Reduce verbosity of Redis manager if needed
    # logging.getLogger("app.core.base.process_launcher").setLevel(logging.INFO)
    
    # To run this example, ensure:
    # 1. Redis is running and REDIS_URL in settings is correct.
    # 2. AGENT_RUNNER_MODULE_PATH, AGENT_RUNNER_SCRIPT_FULL_PATH are correct.
    # 3. PYTHON_EXECUTABLE points to a valid Python interpreter.
    # 4. If testing Docker, Docker is running and AGENT_DOCKER_IMAGE is set.
    # 5. The agent script itself (e.g., runner_main.py) is runnable and handles its lifecycle.
    
    # asyncio.run(example_usage())
    pass
