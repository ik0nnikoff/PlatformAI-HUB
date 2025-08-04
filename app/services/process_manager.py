"""
Модуль для управления жизненным циклом процессов агентов и интеграций.

Содержит класс ProcessManager, который отвечает за запуск, остановку, перезапуск
и мониторинг состояния локальных процессов агентов и процессов интеграций.
Использует Redis для хранения и обновления информации о состоянии процессов.
"""

import asyncio
import json
import logging
import os
import signal
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone
from pathlib import Path
from redis import exceptions as redis_exceptions  # Added import

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
    локальных процессов агентов и процессов интеграций.
    Использует Redis для хранения и обновления информации о состоянии процессов.
    Наследует от `RedisClientManager` для управления соединением с Redis.

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
        self.logger = logger  # Initialize instance logger
        self.launcher = ProcessLauncher()

        self.agent_status_key_template = "agent_status:{}"  # agent_id
        self.integration_status_key_template = (
            "integration_status:{}:{}"  # agent_id, integration_type_str
        )

        # Determine Project Root with pathlib and detailed logging
        try:
            # Assuming this file is in app/services/process_manager.py
            # Path(__file__) -> /path/to/app/services/process_manager.py
            # .resolve() -> resolves symlinks, makes absolute
            # .parent -> /path/to/app/services
            # .parent -> /path/to/app
            # .parent -> /path/to/ (project_root)
            resolved_file_path = Path(__file__).resolve()
            # self.logger.info(
            #     "ProcessManager.__init__: Path(__file__).resolve() = %s",
            #     resolved_file_path
            # )

            project_root_path = resolved_file_path.parent.parent.parent
            self.project_root = str(project_root_path)
            # Convert Path object to string for os.path.join later

            # self.logger.info(
            #     "ProcessManager.__init__: Calculated PROJECT_ROOT_PATH with pathlib = %s",
            #     self.project_root
            # )

            # Validate the calculated project root
            if not os.path.isdir(self.project_root) or not os.path.exists(
                os.path.join(self.project_root, "app")
            ):
                self.logger.error(
                    "Calculated project root with pathlib does not seem to be "
                    "a valid project directory: %s",
                    self.project_root,
                )
                # Fallback logic (consider removing or making more robust if pathlib fails)
                dev_path = "/Users/jb/Projects/PlatformAI/PlatformAI-HUB"
                # User's known correct path
                if os.path.isdir(dev_path) and os.path.exists(
                    os.path.join(dev_path, "app")
                ):
                    self.project_root = dev_path
                    self.logger.warning(
                        "Falling back to hardcoded development project_root: %s",
                        self.project_root,
                    )
                else:
                    # Fallback to os.getcwd() as a last resort if hardcoded path is also invalid
                    self.project_root = os.getcwd()
                    self.logger.error(
                        "CRITICAL: Project root detection failed with pathlib and fallback, "
                        "using CWD: %s. This will likely cause issues.",
                        self.project_root,
                    )
            # else:
            # self.logger.info(
            #     "PROJECT_ROOT_PATH %s (from pathlib) seems valid.", self.project_root
            # )

        except Exception as e:
            self.logger.error(
                "Error calculating PROJECT_ROOT_PATH with pathlib: %s",
                e,
                exc_info=True,
            )
            # Fallback to os.getcwd() if any exception occurs during path calculation
            self.project_root = os.getcwd()
            self.logger.warning(
                "Fell back to using current working directory as project_root due to exception: %s",
                self.project_root,
            )

        self.python_executable = settings.PYTHON_EXECUTABLE

        # Agent runner paths
        self.agent_runner_module_path = settings.AGENT_RUNNER_MODULE_PATH
        self.agent_runner_script_full_path = settings.AGENT_RUNNER_SCRIPT_FULL_PATH

        # Integration paths (using string keys for integration types)
        self.integration_module_paths: Dict[IntegrationTypeStr, str] = {
            "TELEGRAM": "app.integrations.telegram.telegram_bot_main",
            "WHATSAPP": "app.integrations.whatsapp.whatsapp_main",
            # Add other integration types here
        }
        self.integration_script_full_paths: Dict[IntegrationTypeStr, str] = {
            k: os.path.join(self.project_root, v.replace(".", os.sep) + ".py")
            for k, v in self.integration_module_paths.items()
        }

        # Ensure PYTHONPATH is correctly set for subprocesses
        self.process_env = os.environ.copy()
        self.process_env["PYTHONPATH"] = self.project_root + (
            os.pathsep + self.process_env.get("PYTHONPATH", "")
            if self.process_env.get("PYTHONPATH")
            else ""
        )
        self.process_env["PYTHONUNBUFFERED"] = "1"  # Common practice for scripts

    async def setup_manager(self):
        """
        Инициализирует соединение с Redis для ProcessManager.

        Вызывает `setup_redis_client` из родительского класса `RedisClientManager`,
        используя `settings.REDIS_URL`.
        Логирует успешную инициализацию.
        """
        await self.setup_redis_client(redis_url=str(settings.REDIS_URL))
        logger.debug("ProcessManager initialized with Redis connection to %s", settings.REDIS_URL)

    async def cleanup_manager(self):
        """
        Закрывает соединение с Redis, используемое ProcessManager.

        Вызывает `close_redis_resources` из родительского класса `RedisClientManager`.
        Логирует завершение очистки.
        """
        await self.close_redis_resources()
        logger.info("ProcessManager cleaned up Redis connection.")

    # --- Process Management Utilities (Phase 3: Деduplication) ---
    async def _check_process_exists(self, pid: int) -> bool:
        """
        Проверяет существование процесса по PID.

        Args:
            pid (int): PID процесса для проверки

        Returns:
            bool: True если процесс существует, False если нет
        """
        try:
            os.kill(pid, 0)  # Check if process exists
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return False

    async def _send_graceful_termination_signal(
        self,
        pid: int,
        process_type: str,
        process_id: str,
        timeout: float
    ) -> bool:
        """
        Отправляет SIGTERM процессу и ждет его завершения.

        Args:
            pid (int): PID процесса
            process_type (str): Тип процесса ("agent" или "integration")
            process_id (str): Идентификатор процесса для логирования
            timeout (float): Время ожидания завершения процесса

        Returns:
            bool: True если процесс завершился, False если нужно принудительное завершение
        """
        try:
            os.kill(pid, signal.SIGTERM)
            self.logger.info("SIGTERM sent to %s %s (PID: %s)", process_type, process_id, pid)

            for _ in range(int(timeout / 0.1)):
                await asyncio.sleep(0.1)
                if not await self._check_process_exists(pid):
                    self.logger.info(
                        "%s %s (PID: %s) terminated gracefully after SIGTERM.",
                        process_type.capitalize(),
                        process_id,
                        pid,
                    )
                    return True

            self.logger.warning(
                "%s %s (PID: %s) did not terminate after SIGTERM within %.1fs.",
                process_type.capitalize(),
                process_id,
                pid,
                timeout,
            )
            return False

        except ProcessLookupError:
            self.logger.warning(
                "%s process %s (PID: %s) not found (already stopped?).",
                process_type.capitalize(),
                process_id,
                pid,
            )
            return True
        except OSError as e:
            self.logger.error(
                "Error stopping %s %s (PID: %s): %s",
                process_type,
                process_id,
                pid,
                e,
                exc_info=True,
            )
            return False

    async def _force_kill_process_unified(
        self,
        pid: int,
        process_type: str,
        process_id: str
    ) -> bool:
        """
        Принудительно завершает процесс через SIGKILL.

        Args:
            pid (int): PID процесса
            process_type (str): Тип процесса ("agent" или "integration")
            process_id (str): Идентификатор процесса для логирования

        Returns:
            bool: True если процесс успешно завершен
        """
        try:
            self.logger.info(
                "Forcing kill (SIGKILL) for %s %s (PID: %s).", process_type, process_id, pid
            )
            os.kill(pid, signal.SIGKILL)
            await asyncio.sleep(0.1)  # Brief moment for SIGKILL to take effect

            if await self._check_process_exists(pid):
                self.logger.warning(
                    "%s %s (PID: %s) still exists after SIGKILL attempt.",
                    process_type.capitalize(),
                    process_id,
                    pid,
                )
                return False

            self.logger.info(
                "%s %s (PID: %s) confirmed terminated after SIGKILL.",
                process_type.capitalize(),
                process_id,
                pid,
            )
            return True

        except ProcessLookupError:
            self.logger.info(
                "%s %s (PID: %s) was already gone before SIGKILL could be sent.",
                process_type.capitalize(),
                process_id,
                pid,
            )
            return True
        except OSError as e:
            self.logger.error(
                "Error sending SIGKILL to %s %s (PID: %s): %s",
                process_type,
                process_id,
                pid,
                e,
                exc_info=True,
            )
            return False

    async def _validate_process_status_unified(
        self,
        pid: Optional[int],
        current_status: str,
        status_key: str,
        process_type: str,
        process_id: str,
    ) -> Tuple[str, Optional[int]]:
        """
        Унифицированная проверка существования процесса для агентов и интеграций.

        Args:
            pid: PID процесса (может быть None)
            current_status: Текущий статус из Redis
            status_key: Ключ статуса в Redis
            process_type: Тип процесса ("agent" или "integration")
            process_id: Идентификатор процесса

        Returns:
            Tuple[str, Optional[int]]: Валидированный статус и PID
        """
        if pid and current_status in ["running", "starting", "initializing"]:
            if await self._check_process_exists(pid):
                return current_status, pid

            # Process lost
            self.logger.warning(
                "%s %s status is '%s' in Redis, but PID %s not found. Updating status.",
                process_type.capitalize(),
                process_id,
                current_status,
                pid,
            )
            new_status = "error_process_lost"

            update_data = {"status": new_status, "pid": ""}
            if process_type == "integration":
                update_data["integration_type"] = (
                    process_id.split("_")[-1] if "_" in process_id else process_id
                )

            await self._update_status_in_redis(status_key, update_data)
            return new_status, None

        return current_status, pid

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
            logger.warning(
                "Attempted to update status for key %s with an empty mapping. Original dict: %s",
                key,
                status_dict,
            )
            return

        redis_cli = await self.redis_client
        await redis_cli.hset(key, mapping=mapping)  # type: ignore
        logger.debug("Updated status for key %s with mapping: %s", key, mapping)

    async def _get_status_from_redis(self, key: str) -> Dict[str, str]:
        """
        Извлекает статус (хеш) из Redis по ключу.

        Если клиент Redis недоступен или ключ не существует, возвращает пустой словарь.
        Декодирует ключи и значения из байтов в строки UTF-8.
        В случае ошибок Redis или декодирования логирует проблему и возвращает пустой словарь.

        Args:
            key (str): Ключ Redis для извлечения хеша.

        Returns:
            Dict[str, str]: Словарь со статусной информацией, где ключи и значения
                являются строками. Возвращает пустой словарь, если ключ не найден
                или произошла ошибка.
        """
        if not await self.is_redis_client_available():
            logger.warning("Redis client not available when trying to get status for key: %s", key)
            return {}
        try:
            # self.redis_client is from RedisClientManager, an instance of redis.asyncio.Redis
            # decode_responses=False was used during client creation in RedisClientManager,
            # so hgetall returns Dict[bytes, bytes].
            redis_cli = await self.redis_client  # Get the actual client instance
            raw_status_data = await redis_cli.hgetall(key)  # type: ignore

            if not raw_status_data:
                return {}

            # Decode keys and values from bytes to str
            decoded_status_data: Dict[str, str] = {}
            for k_bytes, v_bytes in raw_status_data.items():
                try:
                    k_str = k_bytes.decode("utf-8")
                    v_str = v_bytes.decode("utf-8")
                    decoded_status_data[k_str] = v_str
                except UnicodeDecodeError:
                    logger.warning(
                        "Could not decode UTF-8 for key or value in Redis hash for key %s. "
                        "Key bytes: %r. Value bytes: %r. Skipping problematic item.",
                        key,
                        k_bytes,
                        v_bytes,
                    )
            return decoded_status_data

        except redis_exceptions.RedisError as e:
            logger.error("RedisError getting status from Redis for key %s: %s", key, e)
            return {}
        except Exception as e:
            logger.error(
                "Unexpected error getting status from Redis for key %s: %s - %s",
                key,
                e.__class__.__name__,
                e,
            )
            return {}

    async def _delete_fields_from_redis_status(self, key: str, fields: List[str]):
        """
        Удаляет указанные поля из хеша статуса в Redis.

        Использует команду HDEL.

        Args:
            key (str): Ключ Redis, в котором находится хеш.
            fields (List[str]): Список имен полей для удаления.
        """
        if not fields:
            return
        redis_cli = await self.redis_client
        await redis_cli.hdel(key, *fields)
        logger.debug("Deleted fields %s from key %s", fields, key)

    async def _delete_status_key_from_redis(self, key: str):
        """
        Полностью удаляет ключ статуса из Redis.

        Использует команду DEL.

        Args:
            key (str): Ключ Redis для удаления.
        """
        redis_cli = await self.redis_client
        await redis_cli.delete(key)
        logger.debug("Deleted status key %s", key)

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
        logger.info("Completely deleted status for agent %s from Redis.", agent_id)

    async def delete_integration_status_completely(
        self, agent_id: str, integration_type: IntegrationTypeStr
    ):
        """
        Полностью удаляет всю информацию о статусе для указанной интеграции агента из Redis.

        Формирует ключ статуса интеграции и вызывает `_delete_status_key_from_redis`.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").
        """
        integration_type_for_redis_key = integration_type.lower()

        status_key = self.integration_status_key_template.format(
            agent_id, integration_type_for_redis_key
        )
        await self._delete_status_key_from_redis(status_key)
        logger.info(
            "Completely deleted status for integration %s of agent %s from Redis.",
            integration_type,
            agent_id,
        )

    async def _validate_stop_prerequisites(self, agent_id: str) -> Dict[str, Any]:
        """
        Проверяет предварительные условия для остановки процесса агента.

        Returns:
            Dict с полями: should_stop (bool), current_status (str), pid (int), status_key (str)
        """
        status_info = await self.get_agent_status(agent_id)
        current_status = status_info.get("status", "unknown")
        pid_to_stop = status_info.get("pid")
        status_key = self.agent_status_key_template.format(agent_id)

        should_stop = current_status not in ("stopped", "not_found")

        if not should_stop:
            logger.info("Agent %s is already stopped or not found.", agent_id)

        return {
            "should_stop": should_stop,
            "current_status": current_status,
            "pid": pid_to_stop,
            "status_key": status_key,
        }

    async def _send_graceful_stop_signal(self, pid: int, agent_id: str, timeout: float) -> bool:
        """
        Отправляет SIGTERM процессу и ждет его завершения.

        Returns:
            bool: True если процесс завершился, False если нужно принудительное завершение
        """
        return await self._send_graceful_termination_signal(pid, "agent", agent_id, timeout)

    async def _force_kill_process(self, pid: int, agent_id: str) -> bool:
        """
        Принудительно завершает процесс через SIGKILL.

        Returns:
            bool: True если процесс успешно завершен
        """
        return await self._force_kill_process_unified(pid, "agent", agent_id)

    async def _cleanup_stopped_agent_status(self, agent_id: str, status_key: str) -> None:
        """
        Очищает статус остановленного агента в Redis.
        """
        final_status_update = {"status": "stopped", "agent_id": agent_id}
        await self._update_status_in_redis(status_key, final_status_update)

        # Clean up dynamic fields after confirming stop
        await self._delete_fields_from_redis_status(
            status_key,
            ["pid", "last_active", "error_detail", "start_attempt_utc"],
        )

        # Re-set status to ensure only "stopped" and "agent_id" (and last_updated_utc) remain
        await self._update_status_in_redis(status_key, {"status": "stopped", "agent_id": agent_id})
        logger.info("Agent %s marked as stopped in Redis.", agent_id)

    async def _execute_agent_stop_procedure(
        self, agent_id: str, pid: int, status_key: str, force: bool
    ) -> bool:
        """
        Выполняет процедуру остановки агента с указанным PID.

        Returns:
            bool: True если процесс успешно остановлен
        """
        logger.info(
            "Stopping agent process %s (PID: %s). Force: %s",
            agent_id,
            pid,
            force,
        )

        # Try graceful stop first
        wait_time = getattr(
            settings,
            "PROCESS_GRACEFUL_SHUTDOWN_TIMEOUT",
            DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
        )
        stopped_gracefully = await self._send_graceful_stop_signal(pid, agent_id, wait_time)

        if not stopped_gracefully and force:
            # Force kill if graceful stop failed and force=True
            stopped_gracefully = await self._force_kill_process(pid, agent_id)

        if stopped_gracefully:
            await self._cleanup_stopped_agent_status(agent_id, status_key)
            return True

        # Handle failed stop
        current_redis_status_map = await self._get_status_from_redis(status_key)
        if current_redis_status_map.get("status") == "stopping":
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_stop_failed",
                    "error_detail": "Failed to confirm stop.",
                },
            )
        return False

    async def _handle_already_stopped_agent(self, agent_id: str, status_key: str) -> bool:
        """
        Обрабатывает случай, когда агент уже остановлен.

        Returns:
            bool: Always True (агент уже остановлен)
        """
        redis_cli = await self.redis_client
        if await redis_cli.exists(status_key):
            await self._update_status_in_redis(
                status_key, {"status": "stopped", "agent_id": agent_id}
            )
            await self._delete_fields_from_redis_status(
                status_key,
                ["pid", "last_active", "error_detail", "start_attempt_utc"],
            )
        return True

    # Integration Stop Helper Methods
    async def _validate_integration_stop_prerequisites(
        self, agent_id: str, integration_type: str, status_info: Dict[str, Any]
    ) -> Tuple[bool, Optional[int]]:
        """Проверяет предварительные условия для остановки интеграции."""
        current_status = status_info.get("status", "unknown")
        pid_to_stop = status_info.get("pid")

        if current_status in ["stopped", "not_found"]:
            self.logger.info(
                "Integration %s for agent %s is already stopped or not found.",
                integration_type,
                agent_id,
            )
            return True, None

        return False, pid_to_stop

    async def _send_graceful_stop_signal_integration(
        self, integration_type: str, agent_id: str, pid: int
    ) -> bool:
        """Отправляет graceful stop сигнал процессу интеграции."""
        process_id = f"{integration_type} for agent {agent_id}"
        wait_time = getattr(
            settings,
            "PROCESS_GRACEFUL_SHUTDOWN_TIMEOUT",
            DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
        )
        return await self._send_graceful_termination_signal(
            pid, "integration", process_id, wait_time
        )

    async def _force_kill_integration_process(
        self, integration_type: str, agent_id: str, pid: int
    ) -> bool:
        """Принудительно завершает процесс интеграции."""
        process_id = f"{integration_type} for agent {agent_id}"
        return await self._force_kill_process_unified(pid, "integration", process_id)

    async def _cleanup_stopped_integration_status(
        self, status_key: str, agent_id: str, integration_type: str
    ) -> None:
        """Очищает статус остановленной интеграции в Redis."""
        final_status_update = {
            "status": "stopped",
            "agent_id": agent_id,
            "integration_type": integration_type,
        }
        await self._update_status_in_redis(status_key, final_status_update)
        await self._delete_fields_from_redis_status(
            status_key,
            ["pid", "last_active", "error_detail", "start_attempt_utc"],
        )
        self.logger.info(
            "Integration %s for agent %s marked as stopped in Redis.", integration_type, agent_id
        )

    async def _execute_integration_stop_procedure(
        self,
        integration_type: str,
        agent_id: str,
        pid: Optional[int],
        force: bool,
    ) -> bool:
        """Выполняет процедуру остановки интеграции."""
        if pid is None:
            self.logger.warning(
                "Cannot actively stop integration %s for agent %s: PID is None. "
                "Marking as stopped.",
                integration_type,
                agent_id,
            )
            return True

        # Попытка graceful stop
        graceful_success = await self._send_graceful_stop_signal_integration(
            integration_type, agent_id, pid
        )
        if graceful_success:
            return True

        # Force kill если требуется
        if force:
            return await self._force_kill_integration_process(integration_type, agent_id, pid)

        return False

    async def _handle_already_stopped_integration(
        self, agent_id: str, integration_type: str, status_key: str
    ) -> bool:
        """Обрабатывает ситуацию, когда интеграция уже остановлена."""
        self.logger.info(
            "Integration %s for agent %s is already stopped or not found.",
            integration_type,
            agent_id,
        )
        redis_cli = await self.redis_client
        if await redis_cli.exists(status_key):
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "stopped",
                    "agent_id": agent_id,
                    "integration_type": integration_type,
                },
            )
            await self._delete_fields_from_redis_status(
                status_key,
                ["pid", "last_active", "error_detail", "start_attempt_utc"],
            )
        return True

    # Integration Start Helper Methods
    async def _validate_integration_start_prerequisites(
        self, agent_id: str, integration_type: str
    ) -> bool:
        """Проверяет предварительные условия для запуска интеграции."""
        try:
            current_integration_status_info = await self.get_integration_status(
                agent_id, integration_type
            )
            current_status = current_integration_status_info.get("status")

            if current_status == "running":
                self.logger.warning(
                    "Integration %s for agent %s already reported as running. Skipping start.",
                    integration_type,
                    agent_id,
                )
                return False
            if current_status == "starting":
                self.logger.warning(
                    "Integration %s for agent %s is already starting. "
                    "Skipping duplicate start request.",
                    integration_type,
                    agent_id,
                )
                return False

            return True
        except Exception as e:
            self.logger.error(
                "Error checking integration status before start for %s/%s: %s",
                agent_id,
                integration_type,
                e,
                exc_info=True,
            )
            return True  # Продолжаем попытку запуска даже при ошибке проверки статуса

    async def _validate_integration_script_paths(
        self, integration_type: str, status_key: str, agent_id: str
    ) -> bool:
        """Проверяет наличие скрипта интеграции."""
        module_path = self.integration_module_paths.get(integration_type.upper())
        script_full_path = self.integration_script_full_paths.get(integration_type.upper())

        if not module_path or not script_full_path or not os.path.exists(script_full_path):
            self.logger.error(
                "Integration script/module not found for type: %s. Path: %s",
                integration_type,
                script_full_path,
            )
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_script_not_found",
                    "error_detail": f"Script not found for {integration_type}",
                    "agent_id": agent_id,
                    "integration_type": integration_type,
                },
            )
            return False
        return True

    async def _build_integration_command(
        self,
        integration_type: str,
        agent_id: str,
        integration_settings: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Формирует команду для запуска интеграции."""
        module_path = self.integration_module_paths.get(integration_type.upper())

        cmd = [
            self.python_executable,
            "-m",
            module_path,
            "--agent-id",
            agent_id,
        ]

        if integration_settings:
            cmd.extend(["--integration-settings", json.dumps(integration_settings)])

        return cmd

    async def _launch_integration_process(
        self,
        cmd: List[str],
        integration_type: str,
        agent_id: str,
        status_key: str,
    ) -> bool:
        """Запускает процесс интеграции."""
        try:
            self.logger.debug(
                "Starting integration %s for agent %s. Command: %s",
                integration_type,
                agent_id,
                " ".join(cmd),
            )

            process_obj, _, _ = await self.launcher.launch_process(
                command=cmd,
                process_id=f"integration_start_{agent_id}_{integration_type}",
                cwd=self.project_root,
                env_vars=self.process_env,
                capture_output=False,
            )

            if process_obj and process_obj.pid is not None:
                self.logger.info(
                    "Integration process %s for agent %s initiated start with PID %s",
                    integration_type,
                    agent_id,
                    process_obj.pid,
                )
                await self._update_status_in_redis(
                    status_key,
                    {
                        "pid": str(process_obj.pid),
                        "integration_type": integration_type,
                    },
                )
                return True

            err_msg = (
                f"Failed to launch integration process {integration_type} "
                f"for agent {agent_id} (process_obj or pid is None)."
            )
            self.logger.error(err_msg)
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_start_failed",
                    "error_detail": err_msg,
                    "agent_id": agent_id,
                    "integration_type": integration_type,
                },
            )
            return False

        except FileNotFoundError as fnf_e:
            self.logger.error(
                "Failed to start integration %s for %s due to FileNotFoundError: %s",
                integration_type,
                agent_id,
                fnf_e,
                exc_info=True,
            )
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_script_not_found",
                    "error_detail": str(fnf_e),
                    "integration_type": integration_type,
                },
            )
            return False
        except Exception as e:
            self.logger.error(
                "Failed to start integration %s for %s: %s",
                integration_type,
                agent_id,
                e,
                exc_info=True,
            )
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_start_failed",
                    "error_detail": str(e),
                    "integration_type": integration_type,
                },
            )
            return False

    # Status Helper Methods
    async def _parse_pid_from_status(self, pid_val: Any) -> Optional[int]:
        """Парсит PID из данных статуса."""
        if pid_val and str(pid_val).isdigit():
            return int(pid_val)
        return None

    async def _parse_last_active_from_status(self, last_active_val: Any) -> Optional[float]:
        """Парсит last_active из данных статуса."""
        try:
            return float(last_active_val) if last_active_val else None
        except (ValueError, TypeError):
            return None

    async def _validate_process_existence(
        self,
        pid: int,
        agent_id: str,
        integration_type: str,
        current_status: str,
        status_key: str,
    ) -> Tuple[str, Optional[int]]:
        """Проверяет существование процесса для интеграции."""
        process_id = f"{agent_id}_{integration_type}"
        return await self._validate_process_status_unified(
            pid, current_status, status_key, "integration", process_id
        )

    async def _validate_agent_process_existence(
        self, pid: int, agent_id: str, current_status: str, status_key: str
    ) -> Tuple[str, Optional[int]]:
        """Проверяет существование процесса для агента."""
        return await self._validate_process_status_unified(
            pid, current_status, status_key, "agent", agent_id
        )

    # Agent Start Helper Methods
    async def _validate_agent_start_prerequisites(self, agent_id: str) -> bool:
        """Проверяет предварительные условия для запуска агента."""
        try:
            current_agent_status_info = await self.get_agent_status(agent_id)
            current_status = current_agent_status_info.get("status")

            if current_status == "running":
                self.logger.warning(
                    "Agent %s already reported as running. Skipping start.", agent_id
                )
                return False
            if current_status == "starting":
                self.logger.warning(
                    "Agent %s is already starting. Skipping duplicate start request.", agent_id
                )
                return False

            return True
        except Exception as e:
            self.logger.error(
                "Error checking agent status before start for %s: %s",
                agent_id,
                e,
                exc_info=True,
            )
            return True  # Продолжаем попытку запуска даже при ошибке проверки статуса

    async def _build_agent_command(
        self, agent_id: str, agent_settings: Optional[Dict[str, Any]]
    ) -> List[str]:
        """Формирует команду для запуска агента."""
        cmd = [
            self.python_executable,
            "-m",
            self.agent_runner_module_path,
            "--agent-id",
            agent_id,
        ]

        if agent_settings:
            cmd.extend(["--agent-settings", json.dumps(agent_settings)])

        return cmd

    async def _launch_agent_process(self, cmd: List[str], agent_id: str, status_key: str) -> bool:
        """Запускает процесс агента."""
        try:
            process_obj, _, _ = await self.launcher.launch_process(
                command=cmd,
                process_id=f"agent_start_{agent_id}",
                cwd=self.project_root,
                env_vars=self.process_env,
                capture_output=False,
            )

            if process_obj and process_obj.pid is not None:
                self.logger.info(
                    "Agent process %s initiated start with PID %s", agent_id, process_obj.pid
                )
                await self._update_status_in_redis(status_key, {"pid": str(process_obj.pid)})
                return True

            err_msg = f"Failed to launch agent process {agent_id} (process_obj or pid is None)."
            self.logger.error(err_msg)
            await self._update_status_in_redis(
                status_key,
                {"status": "error_start_failed", "error_detail": err_msg},
            )
            return False

        except FileNotFoundError as fnf_e:
            self.logger.error(
                "Failed to start agent %s due to FileNotFoundError: %s",
                agent_id,
                fnf_e,
                exc_info=True,
            )
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_script_not_found",
                    "error_detail": str(fnf_e),
                },
            )
            return False
        except Exception as e:
            self.logger.error("Failed to start agent {agent_id}: {e}", exc_info=True)
            await self._update_status_in_redis(
                status_key,
                {"status": "error_start_failed", "error_detail": str(e)},
            )
            return False

    async def stop_agent_process(self, agent_id: str, force: bool = False) -> bool:
        """
        Останавливает локальный процесс агента.

        Args:
            agent_id (str): Идентификатор агента.
            force (bool): Если True, применяет принудительные методы остановки
                (SIGKILL для локальных).

        Returns:
            bool: True, если процесс успешно остановлен (или уже был остановлен), иначе False.
        """
        # Validate prerequisites
        prerequisites = await self._validate_stop_prerequisites(agent_id)
        if not prerequisites["should_stop"]:
            return await self._handle_already_stopped_agent(agent_id, prerequisites["status_key"])

        status_key = prerequisites["status_key"]
        pid_to_stop = prerequisites["pid"]

        # Mark as stopping
        await self._update_status_in_redis(status_key, {"status": "stopping"})

        try:
            if not pid_to_stop:
                logger.warning(
                    "Cannot actively stop agent %s: no PID available. Marking as stopped.",
                    agent_id,
                )
                await self._cleanup_stopped_agent_status(agent_id, status_key)
                return True

            # Execute stop procedure
            return await self._execute_agent_stop_procedure(
                agent_id, pid_to_stop, status_key, force
            )

        except Exception as e:
            logger.error(
                "Unexpected error during stop_agent_process for %s: %s",
                agent_id,
                e,
                exc_info=True,
            )
            await self._update_status_in_redis(
                status_key,
                {"status": "error_stop_failed", "error_detail": str(e)},
            )
            return False

    async def restart_agent_process(self, agent_id: str) -> bool:
        """
        Перезапускает процесс агента.

        Сначала принудительно останавливает существующий процесс агента
        (`stop_agent_process` с `force=True`). Если остановка не удалась, прерывает перезапуск.
        После успешной остановки и небольшой паузы (`settings.RESTART_DELAY_SECONDS`)
        запускает новый процесс агента (`start_agent_process`).
        Обновляет статус в Redis в соответствии с результатом каждой операции.

        Args:
            agent_id (str): Идентификатор агента для перезапуска.

        Returns:
            bool: True, если агент успешно перезапущен, иначе False.
        """
        logger.info("Restarting agent process for %s...", agent_id)
        # Force stop, as it's a restart.
        stopped = await self.stop_agent_process(agent_id, force=True)

        if not stopped:
            logger.error("Failed to stop agent %s during restart. Aborting restart.", agent_id)
            # stop_agent_process should have set an appropriate error status.
            return False

        logger.info(
            "Agent %s stopped (or stop attempted and assumed successful for restart). "
            "Pausing briefly...",
            agent_id,
        )
        await asyncio.sleep(settings.RESTART_DELAY_SECONDS)  # Corrected: Use RESTART_DELAY_SECONDS

        logger.info("Proceeding to start agent %s after delay...", agent_id)
        try:
            started = await self.start_agent_process(agent_id)
            if not started:
                logger.error(
                    "Restart failed: start_agent_process returned False for agent %s.", agent_id
                )
                # start_agent_process should set its own error status (e.g. "error_start_failed")
                return False
            logger.info("Agent %s successfully started as part of restart sequence.", agent_id)
            return True
        except Exception as e:
            logger.error(
                "Restart for agent %s failed with an exception during start phase: %s",
                agent_id,
                e,
                exc_info=True,
            )
            await self._update_status_in_redis(
                self.agent_status_key_template.format(agent_id),
                {
                    "status": "error_restart_failed",
                    "error_detail": f"Exception during start phase of restart: {str(e)}",
                },
            )
            return False

    async def get_integration_status(
        self, agent_id: str, integration_type: IntegrationTypeStr
    ) -> IntegrationStatusInfo:
        """
        Получает информацию о статусе для указанной интеграции агента.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").

        Returns:
            IntegrationStatusInfo: Словарь с информацией о статусе интеграции.
        """
        integration_type_for_redis_key = integration_type.lower()
        status_key = self.integration_status_key_template.format(
            agent_id, integration_type_for_redis_key
        )

        # Получаем данные из Redis
        status_data = await self._get_status_from_redis(status_key)
        if not status_data:
            return {
                "agent_id": agent_id,
                "integration_type": integration_type,
                "status": "not_found",
                "pid": None,
                "last_active": None,
                "error_detail": None,
            }

        # Парсим данные статуса
        current_status = status_data.get("status", "unknown")
        pid = await self._parse_pid_from_status(status_data.get("pid"))
        last_active = await self._parse_last_active_from_status(status_data.get("last_active"))

        # Валидируем существование процесса
        validated_status, validated_pid = await self._validate_process_existence(
            pid, agent_id, integration_type, current_status, status_key
        )

        return {
            "agent_id": agent_id,
            "integration_type": integration_type,
            "status": validated_status,
            "pid": validated_pid,
            "last_active": last_active,
            "error_detail": status_data.get("error_detail"),
        }

    async def start_integration_process(
        self,
        agent_id: str,
        integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Запускает процесс интеграции для указанного агента.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции (например, "TELEGRAM").
            integration_settings (Optional[Dict[str, Any]]): Настройки для интеграции.

        Returns:
            bool: True, если процесс интеграции успешно инициирован, иначе False.
        """
        integration_type_for_redis_key = integration_type.lower()
        status_key = self.integration_status_key_template.format(
            agent_id, integration_type_for_redis_key
        )

        self.logger.info(
            "Attempting to start integration process for %s/%s...", agent_id, integration_type
        )

        # Проверяем предварительные условия
        if not await self._validate_integration_start_prerequisites(agent_id, integration_type):
            return True  # Уже запущен или в процессе запуска

        # Проверяем наличие скриптов
        if not await self._validate_integration_script_paths(
            integration_type, status_key, agent_id
        ):
            return False

        # Устанавливаем начальный статус
        initial_status_data = {
            "status": "starting",
            "agent_id": agent_id,
            "integration_type": integration_type,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time()),
        }
        await self._update_status_in_redis(status_key, initial_status_data)

        # Формируем команду запуска
        cmd = await self._build_integration_command(
            integration_type, agent_id, integration_settings
        )

        # Запускаем процесс
        return await self._launch_integration_process(cmd, integration_type, agent_id, status_key)

    async def stop_integration_process(
        self,
        agent_id: str,
        integration_type: IntegrationTypeStr,
        force: bool = False,
    ) -> bool:
        """
        Останавливает локальный процесс интеграции для указанного агента.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции
                (например, "TELEGRAM").
            force (bool): Если True, применяет принудительные методы остановки
                (SIGКILL для локальных).

        Returns:
            bool: True, если процесс успешно остановлен (или уже был остановлен), иначе False.
        """
        integration_type_for_redis_key = integration_type.lower()
        status_key = self.integration_status_key_template.format(
            agent_id, integration_type_for_redis_key
        )
        self.logger.info(
            "Attempting to stop integration %s/%s. Force: %s", agent_id, integration_type, force
        )

        try:
            # Получаем статус интеграции
            status_info = await self.get_integration_status(agent_id, integration_type)

            # Проверяем предварительные условия
            already_stopped, pid_to_stop = await self._validate_integration_stop_prerequisites(
                agent_id, integration_type, status_info
            )

            if already_stopped:
                return await self._handle_already_stopped_integration(
                    agent_id, integration_type, status_key
                )

            # Обновляем статус на "stopping"
            await self._update_status_in_redis(
                status_key,
                {"status": "stopping", "integration_type": integration_type},
            )

            # Выполняем процедуру остановки
            stopped_successfully = await self._execute_integration_stop_procedure(
                integration_type, agent_id, pid_to_stop, force
            )

            # Обрабатываем результат
            if stopped_successfully:
                await self._cleanup_stopped_integration_status(
                    status_key, agent_id, integration_type
                )
                return True

            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_stop_failed",
                    "error_detail": "Failed to confirm stop.",
                    "integration_type": integration_type,
                },
            )
            return False

        except Exception as e:
            self.logger.error(
                "Unexpected error during stop_integration_process for %s/%s: %s",
                agent_id,
                integration_type,
                e,
                exc_info=True,
            )
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_stop_failed",
                    "error_detail": str(e),
                    "integration_type": integration_type,
                },
            )
            return False

    async def restart_integration_process(
        self,
        agent_id: str,
        integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Перезапускает процесс интеграции.

        Сначала принудительно останавливает существующий процесс интеграции,
        затем запускает новый процесс с теми же настройками.

        Args:
            agent_id (str): Идентификатор агента.
            integration_type (IntegrationTypeStr): Тип интеграции.
            integration_settings (Optional[Dict[str, Any]]): Настройки интеграции.

        Returns:
            bool: True если интеграция успешно перезапущена, иначе False.
        """
        logger.info("Restarting integration %s for agent %s...", integration_type, agent_id)

        stopped = await self.stop_integration_process(agent_id, integration_type, force=True)

        if not stopped:
            logger.error(
                "Failed to stop integration %s for agent %s during restart. Aborting restart.",
                integration_type,
                agent_id,
            )
            return False

        delay_seconds = settings.RESTART_DELAY_SECONDS
        logger.info(
            "Integration %s for agent %s stopped. Pausing for %ss...",
            integration_type,
            agent_id,
            delay_seconds,
        )
        await asyncio.sleep(delay_seconds)

        logger.info(
            "Proceeding to start integration %s for agent %s after delay...",
            integration_type,
            agent_id,
        )
        try:
            started = await self.start_integration_process(
                agent_id,
                integration_type,
                integration_settings=integration_settings,
            )
            if not started:
                logger.error(
                    "Restart failed: start_integration_process returned False for %s/%s.",
                    agent_id,
                    integration_type,
                )
                return False
            logger.info(
                "Integration %s for agent %s successfully started as part of restart sequence.",
                integration_type,
                agent_id,
            )
            return True
        except Exception as e:
            logger.error(
                "Restart for %s/%s failed with an exception during start phase: %s",
                agent_id,
                integration_type,
                e,
                exc_info=True,
            )

            integration_type_for_redis_key = integration_type.lower()
            status_key = self.integration_status_key_template.format(
                agent_id, integration_type_for_redis_key
            )
            await self._update_status_in_redis(
                status_key,
                {
                    "status": "error_restart_failed",
                    "error_detail": f"Exception during start phase of restart: {str(e)}",
                    "integration_type": integration_type,
                },
            )
            return False

    async def get_agent_status(self, agent_id: str) -> AgentStatusInfo:
        """
        Получает информацию о статусе для указанного агента.

        Args:
            agent_id (str): Идентификатор агента.

        Returns:
            AgentStatusInfo: Словарь с информацией о статусе агента.
        """
        status_key = self.agent_status_key_template.format(agent_id)

        # Получаем данные из Redis
        status_data = await self._get_status_from_redis(status_key)
        if not status_data:
            return {
                "agent_id": agent_id,
                "status": "not_found",
                "pid": None,
                "last_active": None,
                "error_detail": None,
            }

        # Парсим данные статуса
        current_status = status_data.get("status", "unknown")
        pid = await self._parse_pid_from_status(status_data.get("pid"))
        last_active = await self._parse_last_active_from_status(status_data.get("last_active"))

        # Валидируем существование процесса
        validated_status, validated_pid = await self._validate_agent_process_existence(
            pid, agent_id, current_status, status_key
        )

        return {
            "agent_id": agent_id,
            "status": validated_status,
            "pid": validated_pid,
            "last_active": last_active,
            "error_detail": status_data.get("error_detail"),
        }

    async def start_agent_process(
        self, agent_id: str, agent_settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Запускает локальный процесс агента.

        Args:
            agent_id (str): Идентификатор агента.
            agent_settings (Optional[Dict[str, Any]]): Настройки для агента.

        Returns:
            bool: True, если процесс агента успешно инициирован, иначе False.
        """
        status_key = self.agent_status_key_template.format(agent_id)
        self.logger.info("Attempting to start agent process for %s...", agent_id)

        # Проверяем предварительные условия
        if not await self._validate_agent_start_prerequisites(agent_id):
            return True  # Уже запущен или в процессе запуска

        # Устанавливаем начальный статус
        initial_status_data = {
            "status": "starting",
            "agent_id": agent_id,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time()),
        }
        await self._update_status_in_redis(status_key, initial_status_data)

        # Формируем команду запуска
        cmd = await self._build_agent_command(agent_id, agent_settings)

        # Запускаем процесс
        return await self._launch_agent_process(cmd, agent_id, status_key)

    async def _start_agent_process(
        self, agent_id: str, config_url: str, _agent_settings: Dict[str, Any]
    ) -> Optional[int]:
        """Helper to start a agent process."""
        cmd = [
            self.python_executable,
            "-u",  # Unbuffered stdout/stderr
            self.agent_runner_script_full_path,
            "--agent-id",
            agent_id,
            "--config-url",
            config_url,
        ]

        process_obj, _, _ = await self.launcher.launch_process(
            command=cmd,
            process_id=f"agent_{agent_id}",
            cwd=self.project_root,
            env_vars=self.process_env,
            capture_output=False,
        )

        if process_obj and process_obj.pid:
            logger.info(
                "Successfully started agent process for %s with PID %s. Command: %s",
                agent_id,
                process_obj.pid,
                " ".join(cmd),
            )
            return process_obj.pid

        logger.error("Failed to start agent process for %s. Command: %s", agent_id, " ".join(cmd))
        return None


if __name__ == "__main__":
    # This example requires a running Redis instance and appropriate settings.
    # Also, the agent runner scripts and integration scripts must exist at configured paths.

    # Configure logging for the example
    # logging.basicConfig(
    #     level=logging.DEBUG,
    #     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    # )
    # logging.getLogger("app.core.base.redis_manager").setLevel(logging.INFO)
    # Reduce verbosity of Redis manager if needed
    # logging.getLogger("app.core.base.process_launcher").setLevel(logging.INFO)

    # To run this example, ensure:
    # 1. Redis is running and REDIS_URL in settings is correct.
    # 2. AGENT_RUNNER_MODULE_PATH, AGENT_RUNNER_SCRIPT_FULL_PATH are correct.
    # 3. PYTHON_EXECUTABLE points to a valid Python interpreter.
    # 4. The agent script itself (e.g., runner_main.py) is runnable and handles its lifecycle.

    # asyncio.run(example_usage())
    pass
