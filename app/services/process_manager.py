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
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from redis import exceptions as redis_exceptions  # Added import

from app.core.base.process_launcher import ProcessLauncher
from app.core.base.redis_manager import RedisClientManager
from app.core.config import settings

# Placeholder for actual Pydantic schemas if needed later. For now, using Dicts.
AgentStatusInfo = Dict[str, Any]
IntegrationStatusInfo = Dict[str, Any]

# Fallback for IntegrationType if the enum is not directly used/imported
# This allows using string keys like "TELEGRAM"
IntegrationTypeStr = str

logger = logging.getLogger(__name__)

# Define a default for graceful shutdown if not in settings
DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT = 10.0


# Phase 4: SOLID Principles - ProcessConfiguration Class (SRP)
@dataclass
class ProcessConfiguration:
    """
    Конфигурация для процессов (Single Responsibility Principle).
    Управляет всеми путями, исполняемыми файлами и настройками окружения.
    """

    project_root: str
    python_executable: str
    agent_runner_module_path: str
    agent_runner_script_full_path: str
    integration_module_paths: Dict[IntegrationTypeStr, str]
    integration_script_full_paths: Dict[IntegrationTypeStr, str]
    process_env: Dict[str, str]
    agent_status_key_template: str = "agent_status:{}"
    integration_status_key_template: str = "integration_status:{}:{}"

    @classmethod
    def create_default(cls) -> "ProcessConfiguration":
        """Создает конфигурацию с настройками по умолчанию."""
        # Определение корневого каталога проекта
        project_root = cls._detect_project_root()

        # Настройка путей интеграций
        integration_module_paths = {
            "TELEGRAM": "app.integrations.telegram.telegram_bot_main",
            "WHATSAPP": "app.integrations.whatsapp.whatsapp_main",
        }

        integration_script_full_paths = {
            k: os.path.join(project_root, v.replace(".", os.sep) + ".py")
            for k, v in integration_module_paths.items()
        }

        # Настройка переменных окружения
        process_env = os.environ.copy()
        process_env["PYTHONPATH"] = project_root + (
            os.pathsep + process_env.get("PYTHONPATH", "")
            if process_env.get("PYTHONPATH")
            else ""
        )

        return cls(
            project_root=project_root,
            python_executable=settings.PYTHON_EXECUTABLE,
            agent_runner_module_path=settings.AGENT_RUNNER_MODULE_PATH,
            agent_runner_script_full_path=settings.AGENT_RUNNER_SCRIPT_FULL_PATH,
            integration_module_paths=integration_module_paths,
            integration_script_full_paths=integration_script_full_paths,
            process_env=process_env,
        )

    @staticmethod
    def _detect_project_root() -> str:
        """Определяет корневой каталог проекта с fallback логикой."""
        try:
            resolved_file_path = Path(__file__).resolve()
            project_root_path = resolved_file_path.parent.parent.parent
            detected_root = str(project_root_path)

            logger.debug("Project root detected: %s", detected_root)
            return detected_root
        except Exception as e:
            logger.warning("Failed to detect project root via __file__: %s", e)
            return ProcessConfiguration._apply_fallback_detection()

    @staticmethod
    def _apply_fallback_detection() -> str:
        """Применяет fallback логику для определения project root."""
        fallback_options = [
            os.getcwd(),
            "/Users/jb/Projects/PlatformAI/PlatformAI-HUB",  # Development fallback
            ".",
        ]

        for option in fallback_options:
            if os.path.exists(option) and os.path.isdir(option):
                logger.info("Using fallback project root: %s", option)
                return option

        logger.error("All fallback options failed, using current directory")
        return os.getcwd()

    def validate_paths(self) -> bool:
        """Валидирует все пути в конфигурации."""
        if not os.path.exists(self.project_root):
            logger.error("Project root does not exist: %s", self.project_root)
            return False

        if not os.path.exists(self.agent_runner_script_full_path):
            logger.error(
                "Agent runner script not found: %s", self.agent_runner_script_full_path
            )
            return False

        for integration_type, script_path in self.integration_script_full_paths.items():
            if not os.path.exists(script_path):
                logger.warning(
                    "Integration script not found for %s: %s",
                    integration_type,
                    script_path,
                )

        return True


@dataclass
class AgentStartupRequest:
    """
    Параметры запуска агента с интеграциями для предотвращения R0913.
    """

    agent_id: str
    integration_types: List[str]
    agent_settings: Optional[Dict[str, Any]] = None
    integration_settings: Optional[Dict[str, Dict[str, Any]]] = None
    force_stop: bool = False  # Для restart операций


# Phase 4: SOLID Principles - Configuration Classes for reducing arguments
@dataclass
class ProcessValidationConfig:
    """Конфигурация для валидации процессов (следуя принципу SRP)."""

    pid: Optional[int]
    current_status: str
    status_key: str
    process_type: str
    process_id: str


@dataclass
class ProcessTerminationConfig:
    """Конфигурация для завершения процессов (следуя принципу SRP)."""

    pid: int
    process_type: str
    process_id: str
    timeout: float = DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT


class ProcessStatusManager:
    """Управляет статусами процессов в Redis (Single Responsibility)."""

    def __init__(self, redis_manager: "RedisClientManager"):
        self.redis_manager = redis_manager
        self.logger = logger.getChild("StatusManager")

    async def update_process_status(self, key: str, status_dict: Dict[str, Any]):
        """Обновляет статус процесса в Redis."""
        status_dict["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
        if "last_active" not in status_dict:
            status_dict["last_active"] = str(time.time())

        mapping = {k: str(v) for k, v in status_dict.items() if v is not None}
        if not mapping:
            self.logger.warning("Пустой mapping для ключа %s", key)
            return

        redis_cli = await self.redis_manager.redis_client
        if redis_cli is not None:
            await redis_cli.hset(key, mapping=mapping)
            self.logger.debug("Updated status for key %s", key)
        else:
            self.logger.error("Redis client недоступен для обновления статуса %s", key)

    async def get_process_status(self, key: str) -> Dict[str, str]:
        """Получает статус процесса из Redis."""
        if not await self.redis_manager.is_redis_client_available():
            self.logger.error("Redis client недоступен для получения статуса %s", key)
            return {}

        try:
            redis_cli = await self.redis_manager.redis_client
            if redis_cli is not None:
                result = await redis_cli.hgetall(key)
                return {k.decode("utf-8"): v.decode("utf-8") for k, v in result.items()}
            
            self.logger.error("Redis client недоступен для получения статуса %s", key)
            return {}
        except redis_exceptions.RedisError as e:
            self.logger.error("Redis error getting status %s: %s", key, e)
            return {}
        except Exception as e:
            self.logger.error(
                "Unexpected error getting status %s: %s", key, e, exc_info=True
            )
            return {}

    async def delete_process_status(self, key: str):
        """Удаляет статус процесса из Redis."""
        redis_cli = await self.redis_manager.redis_client
        await redis_cli.delete(key)
        self.logger.debug("Deleted status key %s", key)


class ProcessLifecycleManager:
    """Управляет жизненным циклом процессов (Single Responsibility)."""

    def __init__(self):
        self.logger = logger.getChild("LifecycleManager")

    async def check_process_exists(self, pid: int) -> bool:
        """Проверяет существование процесса."""
        if not pid or pid <= 0:
            return False

        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
        except Exception as e:
            self.logger.error("Unexpected error checking PID %s: %s", pid, e)
            return False

    async def terminate_process_gracefully(
        self, process_config: Dict[str, Any]
    ) -> bool:
        """Graceful завершение процесса (уменьшено количество аргументов)."""
        pid = process_config["pid"]
        timeout = process_config["timeout"]
        process_type = process_config.get("process_type", "unknown")
        process_id = process_config.get("process_id", "unknown")

        if not await self.check_process_exists(pid):
            self.logger.debug("Process %s (PID: %s) не существует", process_id, pid)
            return True

        try:
            self.logger.debug("Отправка SIGTERM процессу %s (PID: %s)", process_id, pid)
            os.kill(pid, signal.SIGTERM)

            start_time = time.time()
            while time.time() - start_time < timeout:
                if not await self.check_process_exists(pid):
                    self.logger.info(
                        "Process %s %s (PID: %s) gracefully terminated",
                        process_type,
                        process_id,
                        pid,
                    )
                    return True
                await asyncio.sleep(0.1)

            self.logger.warning(
                "Process %s %s (PID: %s) не завершился за %ss",
                process_type,
                process_id,
                pid,
                timeout,
            )
            return False

        except (OSError, ProcessLookupError):
            self.logger.debug("Process %s (PID: %s) уже завершен", process_id, pid)
            return True
        except Exception as e:
            self.logger.error(
                "Ошибка при graceful termination %s (PID: %s): %s",
                process_id,
                pid,
                e,
                exc_info=True,
            )
            return False

    async def force_kill_process(self, process_config: Dict[str, Any]) -> bool:
        """Принудительное завершение процесса."""
        pid = process_config["pid"]
        process_type = process_config.get("process_type", "unknown")
        process_id = process_config.get("process_id", "unknown")

        if not await self.check_process_exists(pid):
            self.logger.debug("Process %s (PID: %s) не существует", process_id, pid)
            return True

        try:
            self.logger.warning(
                "Force killing %s %s (PID: %s)", process_type, process_id, pid
            )
            os.kill(pid, signal.SIGKILL)
            await asyncio.sleep(0.5)

            if not await self.check_process_exists(pid):
                self.logger.info(
                    "Process %s %s (PID: %s) force killed",
                    process_type,
                    process_id,
                    pid,
                )
                return True

            self.logger.error("Failed to force kill %s (PID: %s)", process_id, pid)
            return False

        except (OSError, ProcessLookupError):
            self.logger.debug("Process %s (PID: %s) уже завершен", process_id, pid)
            return True
        except Exception as e:
            self.logger.error(
                "Ошибка при force kill %s (PID: %s): %s",
                process_id,
                pid,
                e,
                exc_info=True,
            )
            return False


# Phase 4: SOLID Principles - ProcessManagerBase Abstract Class
class ProcessManagerBase(RedisClientManager, ABC):
    """
    Абстрактный базовый класс для всех менеджеров процессов (Open/Closed Principle).
    Содержит общую функциональность и определяет интерфейс для наследников.
    """

    def __init__(self, config: ProcessConfiguration):
        """Инициализирует базовый менеджер с конфигурацией."""
        super().__init__()
        self.config = config
        self.logger = logger.getChild(self.__class__.__name__)
        self.status_manager = ProcessStatusManager(self)
        self.lifecycle_manager = ProcessLifecycleManager()
        self.launcher = ProcessLauncher()

        # Валидация конфигурации
        if not self.config.validate_paths():
            self.logger.warning(
                "Configuration validation failed, some features may not work"
            )

    @abstractmethod
    async def start_process(
        self, process_id: str, settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Абстрактный метод для запуска процесса."""
        ...

    @abstractmethod
    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Абстрактный метод для остановки процесса."""
        ...

    @abstractmethod
    async def get_process_status(self, process_id: str) -> Dict[str, Any]:
        """Абстрактный метод для получения статуса процесса."""
        ...

    @abstractmethod
    def _get_status_key(self, process_id: str) -> str:
        """Абстрактный метод для получения ключа статуса в Redis."""
        ...

    # Общие методы для всех менеджеров процессов
    async def _validate_process_status_unified(
        self, config: ProcessValidationConfig
    ) -> Tuple[str, int, str]:
        """Унифицированная валидация статуса процесса."""
        try:
            status_data = await self.status_manager.get_process_status(
                config.status_key
            )
            if not status_data:
                return "not_found", 0, "Нет данных в Redis"

            # Парсинг PID из статуса
            try:
                pid = int(status_data.get("pid", 0))
            except (ValueError, TypeError):
                return "invalid", 0, "Неверный формат PID в Redis"

            if pid <= 0:
                return "stopped", 0, "Процесс остановлен (PID=0)"

            # Проверка существования процесса
            if not await self.lifecycle_manager.check_process_exists(pid):
                return "stopped", pid, "Процесс не найден в системе"

            current_status = status_data.get("status", "unknown")
            return current_status, pid, "Процесс активен"

        except Exception as e:
            self.logger.error("Error validating process status: %s", e, exc_info=True)
            return "error", 0, f"Ошибка валидации: {e}"

    async def _cleanup_stopped_process_status_unified(
        self, status_key: str, process_type: str, process_id: str
    ):
        """Унифицированная очистка статуса остановленного процесса."""
        try:
            await self.status_manager.update_process_status(
                status_key,
                {
                    "status": "stopped",
                    "pid": 0,
                    "stopped_at": datetime.now(timezone.utc).isoformat(),
                    "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
            self.logger.debug("Статус %s %s очищен", process_type, process_id)
        except Exception as e:
            self.logger.error(
                "Error cleaning up %s %s status: %s", process_type, process_id, e
            )

    async def _force_kill_process_unified(
        self, pid: int, process_type: str, process_id: str
    ) -> bool:
        """Унифицированное принудительное завершение процесса."""
        process_config = {
            "pid": pid,
            "process_type": process_type,
            "process_id": process_id,
        }
        return await self.lifecycle_manager.force_kill_process(process_config)

    async def _send_sigterm_signal(
        self, pid: int, process_type: str, process_id: str
    ) -> bool:
        """Отправляет сигнал SIGTERM процессу."""
        try:
            os.kill(pid, signal.SIGTERM)
            self.logger.info(
                "SIGTERM signal sent to %s process %s (PID: %s)",
                process_type,
                process_id,
                pid,
            )
            return True
        except ProcessLookupError:
            self.logger.warning(
                "%s process %s (PID: %s) not found (already stopped?)",
                process_type.capitalize(),
                process_id,
                pid,
            )
            return False
        except PermissionError:
            self.logger.error(
                "Permission denied when sending SIGTERM to %s process %s (PID: %s)",
                process_type,
                process_id,
                pid,
            )
            return False
        except Exception as e:
            self.logger.error(
                "Error sending SIGTERM to %s process %s (PID: %s): %s",
                process_type,
                process_id,
                pid,
                e,
                exc_info=True,
            )
            return False


class ProcessManager(ProcessManagerBase):
    """
    Управляет жизненным циклом дочерних процессов для агентов и интеграций.

    Отвечает за запуск, остановку, перезапуск и мониторинг состояния
    локальных процессов агентов и процессов интеграций.
    Использует Redis для хранения и обновления информации о состоянии процессов.
    Наследует от `RedisClientManager` для управления соединением с Redis.

    """

    def __init__(self, config: Optional[ProcessConfiguration] = None):
        """
        Инициализирует ProcessManager с конфигурацией.

        Args:
            config: Конфигурация процессов. Если не указана, создается по умолчанию.
        """
        if config is None:
            config = ProcessConfiguration.create_default()

        super().__init__(config)  # Initialize ProcessManagerBase

        # Сохраняем обратную совместимость - выставляем атрибуты как раньше
        self._set_compatibility_attributes()

    def _set_compatibility_attributes(self):
        """Устанавливает атрибуты для обратной совместимости через properties."""
        # Все атрибуты доступны через config и не дублируются как instance attributes
        self.logger.debug("Using property-based compatibility attributes")

    # Compatibility properties для обратной совместимости (не создают дополнительные атрибуты)
    @property
    def agent_status_key_template(self):
        """Возвращает шаблон ключа статуса агента."""
        return self.config.agent_status_key_template

    @property
    def integration_status_key_template(self):
        """Возвращает шаблон ключа статуса интеграции."""
        return self.config.integration_status_key_template

    @property
    def project_root(self):
        """Возвращает корневую директорию проекта."""
        return self.config.project_root

    @property
    def python_executable(self):
        """Возвращает путь к Python исполняемому файлу."""
        return self.config.python_executable

    @property
    def agent_runner_module_path(self):
        """Возвращает путь к модулю запуска агентов."""
        return self.config.agent_runner_module_path

    @property
    def agent_runner_script_full_path(self):
        """Возвращает полный путь к скрипту запуска агентов."""
        return self.config.agent_runner_script_full_path

    @property
    def integration_module_paths(self):
        """Возвращает пути к модулям интеграций."""
        return self.config.integration_module_paths

    @property
    def integration_script_full_paths(self):
        """Возвращает полные пути к скриптам интеграций."""
        return self.config.integration_script_full_paths

    @property
    def process_env(self):
        """Возвращает переменные окружения для процессов."""
        return self.config.process_env

    # Implementation of abstract methods from ProcessManagerBase
    async def start_process(
        self, process_id: str, settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Unified start process method - delegates to specific implementations."""
        # This is a general interface - actual implementations are in specific methods
        self.logger.warning(
            "Using generic start_process interface - use start_agent_process or "
            "start_integration_process"
        )
        return False

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Unified stop process method - delegates to specific implementations."""
        # Try to determine if it's an agent or integration and delegate
        agent_status_key = self.agent_status_key_template.format(process_id)
        agent_status = await self.status_manager.get_process_status(agent_status_key)

        if agent_status:
            return await self.stop_agent_process(process_id, force)

        # Could be integration - but we need integration type
        self.logger.warning(
            "Cannot determine process type for %s - use specific stop methods",
            process_id,
        )
        return False

    async def get_process_status(self, process_id: str) -> Dict[str, Any]:
        """Unified get status method - tries agent first, then integrations."""
        # Try as agent first
        try:
            return await self.get_agent_status(process_id)
        except Exception:
            # Could be integration, but we need more context
            self.logger.warning(
                "Cannot determine process type for status of %s", process_id
            )
            return {"status": "unknown", "error": "Cannot determine process type"}

    def _get_status_key(self, process_id: str) -> str:
        """Returns agent status key by default."""
        return self.agent_status_key_template.format(process_id)

    def _init_project_root(self):
        """Определяет корневой каталог проекта."""
        try:
            resolved_file_path = Path(__file__).resolve()
            project_root_path = resolved_file_path.parent.parent.parent
            self.project_root = str(project_root_path)

            if not self._validate_project_root():
                self._apply_project_root_fallback()

        except Exception as e:
            self.logger.error(
                "Error calculating PROJECT_ROOT_PATH with pathlib: %s",
                e,
                exc_info=True,
            )
            self.project_root = os.getcwd()
            self.logger.warning(
                "Fell back to using current working directory as project_root due to exception: %s",
                self.project_root,
            )

    def _validate_project_root(self) -> bool:
        """Проверяет валидность корневого каталога проекта."""
        return os.path.isdir(self.project_root) and os.path.exists(
            os.path.join(self.project_root, "app")
        )

    def _apply_project_root_fallback(self):
        """Применяет fallback логику для определения project_root."""
        self.logger.error(
            "Calculated project root with pathlib does not seem to be "
            "a valid project directory: %s",
            self.project_root,
        )

        dev_path = "/Users/jb/Projects/PlatformAI/PlatformAI-HUB"
        if os.path.isdir(dev_path) and os.path.exists(os.path.join(dev_path, "app")):
            self.project_root = dev_path
            self.logger.warning(
                "Falling back to hardcoded development project_root: %s",
                self.project_root,
            )
        else:
            self.project_root = os.getcwd()
            self.logger.error(
                "CRITICAL: Project root detection failed with pathlib and fallback, "
                "using CWD: %s. This will likely cause issues.",
                self.project_root,
            )

    def _init_executable_paths(self):
        """Инициализирует пути к исполняемым файлам."""
        self.python_executable = settings.PYTHON_EXECUTABLE
        self.agent_runner_module_path = settings.AGENT_RUNNER_MODULE_PATH
        self.agent_runner_script_full_path = settings.AGENT_RUNNER_SCRIPT_FULL_PATH

    def _init_integration_paths(self):
        """Инициализирует пути к интеграциям."""
        self.integration_module_paths: Dict[IntegrationTypeStr, str] = {
            "TELEGRAM": "app.integrations.telegram.telegram_bot_main",
            "WHATSAPP": "app.integrations.whatsapp.whatsapp_main",
            # Add other integration types here
        }
        self.integration_script_full_paths: Dict[IntegrationTypeStr, str] = {
            k: os.path.join(self.project_root, v.replace(".", os.sep) + ".py")
            for k, v in self.integration_module_paths.items()
        }

    def _init_process_environment(self):
        """Настраивает переменные окружения для дочерних процессов."""
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
        logger.debug(
            "ProcessManager initialized with Redis connection to %s", settings.REDIS_URL
        )

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
        self, pid: int, process_type: str, process_id: str, timeout: float
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
            if not await self._send_sigterm_signal(pid, process_type, process_id):
                return True  # Process already stopped

            return await self._wait_for_process_termination(
                pid, process_type, process_id, timeout
            )

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

    async def _wait_for_process_termination(
        self, pid: int, process_type: str, process_id: str, timeout: float
    ) -> bool:
        """Ожидает завершения процесса в течение timeout."""
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

    async def _force_kill_process_unified(
        self, pid: int, process_type: str, process_id: str
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
                "Forcing kill (SIGKILL) for %s %s (PID: %s).",
                process_type,
                process_id,
                pid,
            )

            # Execute SIGKILL
            try:
                os.kill(pid, signal.SIGKILL)
                await asyncio.sleep(0.1)  # Brief moment for SIGKILL to take effect
            except ProcessLookupError:
                self.logger.debug(
                    "%s %s (PID: %s) already gone during SIGKILL.",
                    process_type.capitalize(),
                    process_id,
                    pid,
                )
                return True
            except OSError as e:
                self.logger.error(
                    "OSError during SIGKILL of %s %s (PID: %s): %s",
                    process_type,
                    process_id,
                    pid,
                    e,
                )
                return False

            return await self._verify_process_killed(pid, process_type, process_id)

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

    async def _verify_process_killed(
        self, pid: int, process_type: str, process_id: str
    ) -> bool:
        """Проверяет, что процесс действительно завершен после SIGKILL."""
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

    async def _validate_process_status_unified(
        self, config: ProcessValidationConfig
    ) -> Tuple[str, int, str]:
        """
        Унифицированная проверка существования процесса для агентов и интеграций.

        Args:
            config (ProcessValidationConfig): Конфигурация для валидации процесса.

        Returns:
            Tuple[str, int, str]: Валидированный статус, PID и сообщение
        """
        if config.pid and config.current_status in [
            "running",
            "starting",
            "initializing",
        ]:
            if await self._check_process_exists(config.pid):
                return config.current_status, config.pid, "Process running"

            # Process lost
            self.logger.warning(
                "%s %s status is '%s' in Redis, but PID %s not found. Updating status.",
                config.process_type.capitalize(),
                config.process_id,
                config.current_status,
                config.pid,
            )
            new_status = "error_process_lost"

            update_data = {"status": new_status, "pid": ""}
            if config.process_type == "integration":
                update_data["integration_type"] = (
                    config.process_id.split("_")[-1]
                    if "_" in config.process_id
                    else config.process_id
                )

            await self._update_status_in_redis(config.status_key, update_data)
            return new_status, 0, "Process lost"

        return config.current_status, config.pid or 0, "Status validated"

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
            logger.warning(
                "Redis client not available when trying to get status for key: %s", key
            )
            return {}

        try:
            redis_cli = await self.redis_client
            raw_status_data = await redis_cli.hgetall(key)  # type: ignore

            if not raw_status_data:
                return {}

            return self._decode_redis_hash(raw_status_data, key)

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

    def _decode_redis_hash(
        self, raw_data: Dict[bytes, bytes], key: str
    ) -> Dict[str, str]:
        """Декодирует Redis hash из bytes в строки."""
        decoded_status_data: Dict[str, str] = {}

        for k_bytes, v_bytes in raw_data.items():
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
        if redis_cli is not None:
            await redis_cli.hdel(key, *fields)
            logger.debug("Deleted fields %s from key %s", fields, key)
        else:
            logger.error("Redis client недоступен для удаления полей из ключа %s", key)

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

    async def _cleanup_stopped_agent_status(
        self, agent_id: str, status_key: str
    ) -> None:
        """Очищает статус остановленного агента в Redis."""
        await self._cleanup_stopped_process_status_unified(
            status_key, "agent", agent_id
        )

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
        stopped_gracefully = await self._send_graceful_termination_signal(
            pid, "agent", agent_id, wait_time
        )

        if not stopped_gracefully and force:
            # Force kill if graceful stop failed and force=True
            stopped_gracefully = await self._force_kill_process_unified(
                pid, "agent", agent_id
            )

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

    async def _handle_already_stopped_agent(
        self, agent_id: str, status_key: str
    ) -> bool:
        """Обрабатывает случай, когда агент уже остановлен."""
        redis_cli = await self.redis_client
        if await redis_cli.exists(status_key):
            await self._cleanup_stopped_process_status_unified(
                status_key, "agent", agent_id
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

    async def _cleanup_stopped_integration_status(
        self, status_key: str, agent_id: str, integration_type: str
    ) -> None:
        """Очищает статус остановленной интеграции в Redis."""
        await self._cleanup_stopped_process_status_unified(
            status_key, "integration", agent_id, integration_type
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
        process_id = f"{integration_type} for agent {agent_id}"
        wait_time = getattr(
            settings,
            "PROCESS_GRACEFUL_SHUTDOWN_TIMEOUT",
            DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
        )
        graceful_success = await self._send_graceful_termination_signal(
            pid, "integration", process_id, wait_time
        )
        if graceful_success:
            return True

        # Force kill если требуется
        if force:
            process_id = f"{integration_type} for agent {agent_id}"
            return await self._force_kill_process_unified(
                pid, "integration", process_id
            )

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
            await self._cleanup_stopped_process_status_unified(
                status_key, "integration", agent_id, integration_type
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

            return await self._validate_start_prerequisites_unified(
                "integration", f"{agent_id}:{integration_type}", current_status
            )
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
        if not integration_type:
            self.logger.error("Integration type is None for agent %s", agent_id)
            return False
            
        module_path = self.integration_module_paths.get(integration_type.upper())
        script_full_path = self.integration_script_full_paths.get(
            integration_type.upper()
        )

        if (
            not module_path
            or not script_full_path
            or not os.path.exists(script_full_path)
        ):
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
        return await self._build_process_command_unified(
            "integration", agent_id, integration_settings, integration_type
        )

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

            process_obj = await self._execute_integration_launch(
                cmd, integration_type, agent_id
            )

            if process_obj and process_obj.pid is not None:
                return await self._handle_successful_integration_launch(
                    process_obj, integration_type, agent_id, status_key
                )

            return await self._handle_failed_integration_launch(
                integration_type, agent_id, status_key, "process_obj or pid is None"
            )

        except FileNotFoundError as fnf_e:
            return await self._handle_process_launch_file_error(
                "integration",
                f"{integration_type} for agent {agent_id}",
                status_key,
                fnf_e,
            )
        except Exception as e:
            return await self._handle_process_launch_general_error(
                "integration", f"{integration_type} for agent {agent_id}", status_key, e
            )

    async def _execute_integration_launch(
        self, cmd: List[str], integration_type: str, agent_id: str
    ):
        """Выполняет запуск процесса интеграции."""
        process_obj, _, _ = await self.launcher.launch_process(
            command=cmd,
            process_id=f"integration_start_{agent_id}_{integration_type}",
            cwd=self.project_root,
            env_vars=self.process_env,
            capture_output=False,
        )
        return process_obj

    async def _handle_successful_integration_launch(
        self, process_obj, integration_type: str, agent_id: str, status_key: str
    ) -> bool:
        """Обрабатывает успешный запуск интеграции."""
        return await self._handle_process_launch_success(
            process_obj,
            "integration",
            f"{integration_type} for agent {agent_id}",
            status_key,
            integration_type=integration_type,
        )

    async def _handle_failed_integration_launch(
        self, integration_type: str, agent_id: str, status_key: str, reason: str
    ) -> bool:
        """Обрабатывает неудачный запуск интеграции."""
        return await self._handle_process_launch_failure(
            "integration",
            f"{integration_type} for agent {agent_id}",
            status_key,
            reason,
            agent_id=agent_id,
            integration_type=integration_type,
        )

    # Unified Error Handling Methods (Anti-duplication)
    async def _handle_process_launch_file_error(
        self,
        process_type: str,
        process_id: str,
        status_key: str,
        error: FileNotFoundError,
    ) -> bool:
        """Унифицированная обработка FileNotFoundError при запуске процесса."""
        self.logger.error(
            "Failed to start %s %s due to FileNotFoundError: %s",
            process_type,
            process_id,
            error,
            exc_info=True,
        )
        await self._update_status_in_redis(
            status_key,
            {
                "status": "error_script_not_found",
                "error_detail": str(error),
            },
        )
        return False

    async def _handle_process_launch_general_error(
        self, process_type: str, process_id: str, status_key: str, error: Exception
    ) -> bool:
        """Унифицированная обработка общих ошибок при запуске процесса."""
        self.logger.error(
            "Failed to start %s %s: %s",
            process_type,
            process_id,
            error,
            exc_info=True,
        )
        await self._update_status_in_redis(
            status_key,
            {
                "status": "error_start_failed",
                "error_detail": str(error),
            },
        )
        return False

    async def _handle_process_launch_success(
        self,
        process_obj,
        process_type: str,
        process_id: str,
        status_key: str,
        **extra_fields,
    ) -> bool:
        """Унифицированная обработка успешного запуска процесса."""
        self.logger.info(
            "%s process %s initiated start with PID %s",
            process_type.capitalize(),
            process_id,
            process_obj.pid,
        )

        status_update = {"pid": str(process_obj.pid)}
        status_update.update(extra_fields)

        await self._update_status_in_redis(status_key, status_update)
        return True

    async def _handle_process_launch_failure(
        self,
        process_type: str,
        process_id: str,
        status_key: str,
        reason: str,
        **extra_fields,
    ) -> bool:
        """Унифицированная обработка неудачного запуска процесса."""
        err_msg = f"Failed to launch {process_type} process {process_id} ({reason})."
        self.logger.error(err_msg)

        status_update = {
            "status": "error_start_failed",
            "error_detail": err_msg,
        }
        status_update.update(extra_fields)

        await self._update_status_in_redis(status_key, status_update)
        return False

    # Status Helper Methods
    async def _parse_pid_from_status(self, pid_val: Any) -> Optional[int]:
        """Парсит PID из данных статуса."""
        if pid_val and str(pid_val).isdigit():
            return int(pid_val)
        return None

    async def _parse_last_active_from_status(
        self, last_active_val: Any
    ) -> Optional[float]:
        """Парсит last_active из данных статуса."""
        try:
            return float(last_active_val) if last_active_val else None
        except (ValueError, TypeError):
            return None

    async def _validate_process_existence(
        self, config: ProcessValidationConfig
    ) -> Tuple[str, Optional[int]]:
        """Проверяет существование процесса для интеграции."""
        # Config уже содержит все необходимые данные
        status, pid, _ = await self._validate_process_status_unified(config)
        return status, pid if pid != 0 else None

    async def _validate_agent_process_existence(
        self, pid: int, agent_id: str, current_status: str, status_key: str
    ) -> Tuple[str, Optional[int]]:
        """Проверяет существование процесса для агента."""
        config = ProcessValidationConfig(
            pid=pid,
            current_status=current_status,
            status_key=status_key,
            process_type="agent",
            process_id=agent_id,
        )
        status, validated_pid, _ = await self._validate_process_status_unified(config)
        return status, validated_pid if validated_pid != 0 else None

    # Unified Status Initialization Methods (Anti-duplication)
    async def _initialize_starting_status_unified(
        self,
        status_key: str,
        process_type: str,
        agent_id: str,
        integration_type: Optional[str] = None,
    ) -> None:
        """Унифицированная инициализация статуса 'starting' для процесса."""
        initial_status_data = {
            "status": "starting",
            "agent_id": agent_id,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time()),
        }

        if process_type == "integration" and integration_type:
            initial_status_data["integration_type"] = integration_type

        await self._update_status_in_redis(status_key, initial_status_data)

    # Unified Command Building Methods (Anti-duplication)
    async def _build_process_command_unified(
        self,
        process_type: str,
        process_id: str,
        settings: Optional[Dict[str, Any]] = None,
        integration_type: Optional[str] = None,
    ) -> List[str]:
        """Унифицированная формировка команды для запуска процесса."""
        if process_type == "agent":
            cmd = [
                self.python_executable,
                "-m",
                self.agent_runner_module_path,
                "--agent-id",
                process_id,
            ]
            if settings:
                cmd.extend(["--agent-settings", json.dumps(settings)])
        else:  # integration
            if not integration_type:
                raise ValueError(f"Integration type is required for integration process {process_id}")
            module_path = self.integration_module_paths.get(integration_type.upper())
            cmd = [
                self.python_executable,
                "-m",
                module_path,
                "--agent-id",
                process_id,
            ]
            if settings:
                cmd.extend(["--integration-settings", json.dumps(settings)])

        return cmd

    # Unified Status Cleanup Methods (Anti-duplication)
    async def _cleanup_stopped_process_status_unified(
        self,
        status_key: str,
        process_type: str,
        process_id: str,
        integration_type: Optional[str] = None,
    ) -> None:
        """Унифицированная очистка статуса остановленного процесса в Redis."""
        if process_type == "agent":
            final_status_update = {"status": "stopped", "agent_id": process_id}
            log_message = f"Agent {process_id} marked as stopped in Redis."
        else:  # integration
            final_status_update = {
                "status": "stopped",
                "agent_id": process_id,
                "integration_type": integration_type or "",
            }
            log_message = f"Integration {integration_type} for agent {process_id} marked as stopped in Redis."

        await self._update_status_in_redis(status_key, final_status_update)
        await self._delete_fields_from_redis_status(
            status_key,
            ["pid", "last_active", "error_detail", "start_attempt_utc"],
        )

        # Re-set status to ensure only required fields remain
        await self._update_status_in_redis(status_key, final_status_update)
        self.logger.info(log_message)

    # Unified Prerequisites Validation (Anti-duplication)
    async def _validate_start_prerequisites_unified(
        self, process_type: str, process_id: str, current_status: Optional[str]
    ) -> bool:
        """Унифицированная проверка предварительных условий для запуска процесса."""
        if current_status == "running":
            self.logger.warning(
                "%s %s already reported as running. Skipping start.",
                process_type.capitalize(),
                process_id,
            )
            return False
        if current_status == "starting":
            self.logger.warning(
                "%s %s is already starting. Skipping duplicate start request.",
                process_type.capitalize(),
                process_id,
            )
            return False
        return True

    # Agent Start Helper Methods
    async def _validate_agent_start_prerequisites(self, agent_id: str) -> bool:
        """Проверяет предварительные условия для запуска агента."""
        try:
            current_agent_status_info = await self.get_agent_status(agent_id)
            current_status = current_agent_status_info.get("status")

            return await self._validate_start_prerequisites_unified(
                "agent", agent_id, current_status
            )

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
        return await self._build_process_command_unified(
            "agent", agent_id, agent_settings
        )

    async def _launch_agent_process(
        self, cmd: List[str], agent_id: str, status_key: str
    ) -> bool:
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
                return await self._handle_process_launch_success(
                    process_obj, "agent", agent_id, status_key
                )

            return await self._handle_process_launch_failure(
                "agent", agent_id, status_key, "process_obj or pid is None"
            )

        except FileNotFoundError as fnf_e:
            return await self._handle_process_launch_file_error(
                "agent", agent_id, status_key, fnf_e
            )
        except Exception as e:
            return await self._handle_process_launch_general_error(
                "agent", agent_id, status_key, e
            )

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
            return await self._handle_already_stopped_agent(
                agent_id, prerequisites["status_key"]
            )

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
            logger.error(
                "Failed to stop agent %s during restart. Aborting restart.", agent_id
            )
            # stop_agent_process should have set an appropriate error status.
            return False

        logger.info(
            "Agent %s stopped (or stop attempted and assumed successful for restart). "
            "Pausing briefly...",
            agent_id,
        )
        await asyncio.sleep(
            settings.RESTART_DELAY_SECONDS
        )  # Corrected: Use RESTART_DELAY_SECONDS

        logger.info("Proceeding to start agent %s after delay...", agent_id)
        try:
            started = await self.start_agent_process(agent_id)
            if not started:
                logger.error(
                    "Restart failed: start_agent_process returned False for agent %s.",
                    agent_id,
                )
                # start_agent_process should set its own error status (e.g. "error_start_failed")
                return False
            logger.info(
                "Agent %s successfully started as part of restart sequence.", agent_id
            )
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
        last_active = await self._parse_last_active_from_status(
            status_data.get("last_active")
        )

        # Валидируем существование процесса
        process_id = f"{agent_id}_{integration_type}"
        config = ProcessValidationConfig(
            pid=pid,
            current_status=current_status,
            status_key=status_key,
            process_type="integration",
            process_id=process_id,
        )
        validated_status, validated_pid = await self._validate_process_existence(config)

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
            "Attempting to start integration process for %s/%s...",
            agent_id,
            integration_type,
        )

        # Проверяем предварительные условия
        if not await self._validate_integration_start_prerequisites(
            agent_id, integration_type
        ):
            return True  # Уже запущен или в процессе запуска

        # Проверяем наличие скриптов
        if not await self._validate_integration_script_paths(
            integration_type, status_key, agent_id
        ):
            return False

        # Устанавливаем начальный статус
        await self._initialize_starting_status_unified(
            status_key, "integration", agent_id, integration_type
        )

        # Формируем команду запуска
        cmd = await self._build_integration_command(
            integration_type, agent_id, integration_settings
        )

        # Запускаем процесс
        return await self._launch_integration_process(
            cmd, integration_type, agent_id, status_key
        )

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
            "Attempting to stop integration %s/%s. Force: %s",
            agent_id,
            integration_type,
            force,
        )

        try:
            # Получаем статус интеграции
            status_info = await self.get_integration_status(agent_id, integration_type)

            # Проверяем предварительные условия
            already_stopped, pid_to_stop = (
                await self._validate_integration_stop_prerequisites(
                    agent_id, integration_type, status_info
                )
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
        logger.info(
            "Restarting integration %s for agent %s...", integration_type, agent_id
        )

        stopped = await self.stop_integration_process(
            agent_id, integration_type, force=True
        )

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
        last_active = await self._parse_last_active_from_status(
            status_data.get("last_active")
        )

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
        await self._initialize_starting_status_unified(status_key, "agent", agent_id)

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

        logger.error(
            "Failed to start agent process for %s. Command: %s", agent_id, " ".join(cmd)
        )
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


# Phase 4: SOLID Principles - Specialized Process Managers
class AgentProcessManager(ProcessManagerBase):
    """
    Специализированный менеджер для процессов агентов (Single Responsibility Principle).
    Содержит только логику, специфичную для агентов.
    """

    def __init__(self, config: Optional[ProcessConfiguration] = None):
        """Инициализирует менеджер агентов."""
        if config is None:
            config = ProcessConfiguration.create_default()
        super().__init__(config)

    async def start_process(
        self, process_id: str, settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Запускает процесс агента."""
        return await self._start_agent_process_impl(process_id, settings)

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Останавливает процесс агента."""
        return await self._stop_agent_process_impl(process_id, force)

    async def get_process_status(self, process_id: str) -> Dict[str, Any]:
        """Получает статус процесса агента."""
        return await self._get_agent_status_impl(process_id)

    def _get_status_key(self, process_id: str) -> str:
        """Возвращает ключ статуса агента в Redis."""
        return self.config.agent_status_key_template.format(process_id)

    async def _start_agent_process_impl(
        self, agent_id: str, agent_settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Реализация запуска процесса агента."""
        self.logger.info("Starting agent process: %s", agent_id)

        # Валидация предварительных условий
        status_key = self._get_status_key(agent_id)
        validation_result = await self._validate_start_prerequisites_unified(
            "agent", agent_id
        )
        if not validation_result:
            return False

        try:
            # Построение команды
            command = await self._build_agent_command(agent_id, agent_settings)
            if not command:
                self.logger.error("Failed to build agent command for %s", agent_id)
                return False

            # Инициализация статуса
            await self._initialize_starting_status_unified(
                status_key, "agent", agent_id
            )

            # Запуск процесса
            process = await self.launcher.launch_process(
                command=command,
                cwd=self.config.project_root,
                env=self.config.process_env,
            )

            if not process:
                self.logger.error("Failed to launch agent process %s", agent_id)
                return False

            # Обновление статуса с PID
            await self.status_manager.update_process_status(
                status_key,
                {
                    "status": "running",
                    "pid": process.pid,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "command": " ".join(command),
                },
            )

            self.logger.info(
                "Agent %s started successfully with PID %s", agent_id, process.pid
            )
            return True

        except Exception as e:
            self.logger.error("Error starting agent %s: %s", agent_id, e, exc_info=True)
            await self._cleanup_stopped_process_status_unified(
                status_key, "agent", agent_id
            )
            return False

    async def _stop_agent_process_impl(
        self, agent_id: str, force: bool = False
    ) -> bool:
        """Реализация остановки процесса агента."""
        self.logger.info("Stopping agent process: %s (force=%s)", agent_id, force)

        status_key = self._get_status_key(agent_id)

        # Валидация статуса
        config = ProcessValidationConfig(
            pid=None,
            current_status="unknown",
            status_key=status_key,
            process_type="agent",
            process_id=agent_id,
        )

        validated_status, pid, message = await self._validate_process_status_unified(
            config
        )

        if validated_status in ("not_found", "stopped"):
            self.logger.info("Agent %s already stopped or not found", agent_id)
            await self._cleanup_stopped_process_status_unified(
                status_key, "agent", agent_id
            )
            return True

        if validated_status == "error" or pid <= 0:
            self.logger.error("Cannot stop agent %s: %s", agent_id, message)
            return False

        try:
            # Попытка корректной остановки
            if not force:
                graceful_success = (
                    await self.lifecycle_manager.terminate_process_gracefully(
                        {
                            "pid": pid,
                            "timeout": DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
                            "process_type": "agent",
                            "process_id": agent_id,
                        }
                    )
                )

                if graceful_success:
                    await self._cleanup_stopped_process_status_unified(
                        status_key, "agent", agent_id
                    )
                    self.logger.info("Agent %s stopped gracefully", agent_id)
                    return True

            # Принудительная остановка
            force_success = await self._force_kill_process_unified(
                pid, "agent", agent_id
            )
            if force_success:
                await self._cleanup_stopped_process_status_unified(
                    status_key, "agent", agent_id
                )
                self.logger.info("Agent %s force stopped", agent_id)
                return True
            else:
                self.logger.error("Failed to force stop agent %s", agent_id)
                return False

        except Exception as e:
            self.logger.error("Error stopping agent %s: %s", agent_id, e, exc_info=True)
            return False

    async def _get_agent_status_impl(self, agent_id: str) -> Dict[str, Any]:
        """Реализация получения статуса агента."""
        status_key = self._get_status_key(agent_id)

        config = ProcessValidationConfig(
            pid=None,
            current_status="unknown",
            status_key=status_key,
            process_type="agent",
            process_id=agent_id,
        )

        try:
            validated_status, validated_pid, message = (
                await self._validate_process_status_unified(config)
            )

            status_data = await self.status_manager.get_process_status(status_key)
            last_active = (
                status_data.get("last_active", "unknown") if status_data else "unknown"
            )

            return {
                "agent_id": agent_id,
                "status": validated_status,
                "pid": validated_pid,
                "last_active": last_active,
                "message": message,
                "error_detail": (
                    status_data.get("error_detail") if status_data else None
                ),
            }
        except Exception as e:
            self.logger.error(
                "Error getting agent status %s: %s", agent_id, e, exc_info=True
            )
            return {
                "agent_id": agent_id,
                "status": "error",
                "pid": 0,
                "last_active": "unknown",
                "message": f"Error: {e}",
                "error_detail": str(e),
            }

    async def _build_agent_command(
        self, agent_id: str, agent_settings: Optional[Dict[str, Any]]
    ) -> Optional[List[str]]:
        """Строит команду для запуска агента."""
        try:
            command = [
                self.config.python_executable,
                "-m",
                self.config.agent_runner_module_path,
                agent_id,
            ]

            if agent_settings:
                command.extend(["--settings", json.dumps(agent_settings)])

            self.logger.debug(
                "Built agent command for %s: %s", agent_id, " ".join(command)
            )
            return command
        except Exception as e:
            self.logger.error("Error building agent command for %s: %s", agent_id, e)
            return None


class IntegrationProcessManager(ProcessManagerBase):
    """
    Специализированный менеджер для процессов интеграций (Single Responsibility Principle).
    Содержит только логику, специфичную для интеграций.
    """

    def __init__(self, config: Optional[ProcessConfiguration] = None):
        """Инициализирует менеджер интеграций."""
        if config is None:
            config = ProcessConfiguration.create_default()
        super().__init__(config)

    async def start_process(
        self, process_id: str, settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Запускает процесс интеграции."""
        # process_id для интеграций должен быть в формате "agent_id:integration_type"
        parts = process_id.split(":", 1)
        if len(parts) != 2:
            self.logger.error("Invalid integration process_id format: %s", process_id)
            return False

        agent_id, integration_type = parts
        return await self._start_integration_process_impl(
            agent_id, integration_type, settings
        )

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Останавливает процесс интеграции."""
        parts = process_id.split(":", 1)
        if len(parts) != 2:
            self.logger.error("Invalid integration process_id format: %s", process_id)
            return False

        agent_id, integration_type = parts
        return await self._stop_integration_process_impl(
            agent_id, integration_type, force
        )

    async def get_process_status(self, process_id: str) -> Dict[str, Any]:
        """Получает статус процесса интеграции."""
        parts = process_id.split(":", 1)
        if len(parts) != 2:
            return {"status": "error", "message": "Invalid process_id format"}

        agent_id, integration_type = parts
        return await self._get_integration_status_impl(agent_id, integration_type)

    def _get_status_key(self, process_id: str) -> str:
        """Возвращает ключ статуса интеграции в Redis."""
        parts = process_id.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid integration process_id format: {process_id}")

        agent_id, integration_type = parts
        return self.config.integration_status_key_template.format(
            agent_id, integration_type
        )

    async def _start_integration_process_impl(
        self,
        agent_id: str,
        integration_type: str,
        settings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Реализация запуска процесса интеграции."""
        self.logger.info(
            "Starting integration process: %s:%s", agent_id, integration_type
        )

        # Валидация предварительных условий
        status_key = self.config.integration_status_key_template.format(
            agent_id, integration_type
        )
        validation_result = await self._validate_start_prerequisites_unified(
            "integration", f"{agent_id}:{integration_type}"
        )
        if not validation_result:
            return False

        try:
            # Построение команды
            command = await self._build_integration_command(
                agent_id, integration_type, settings
            )
            if not command:
                self.logger.error(
                    "Failed to build integration command for %s:%s",
                    agent_id,
                    integration_type,
                )
                return False

            # Инициализация статуса
            await self._initialize_starting_status_unified(
                status_key, "integration", f"{agent_id}:{integration_type}"
            )

            # Запуск процесса
            process = await self.launcher.launch_process(
                command=command,
                cwd=self.config.project_root,
                env=self.config.process_env,
            )

            if not process:
                self.logger.error(
                    "Failed to launch integration process %s:%s",
                    agent_id,
                    integration_type,
                )
                return False

            # Обновление статуса с PID
            await self.status_manager.update_process_status(
                status_key,
                {
                    "status": "running",
                    "pid": process.pid,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "command": " ".join(command),
                    "integration_type": integration_type,
                },
            )

            self.logger.info(
                "Integration %s:%s started successfully with PID %s",
                agent_id,
                integration_type,
                process.pid,
            )
            return True

        except Exception as e:
            self.logger.error(
                "Error starting integration %s:%s: %s",
                agent_id,
                integration_type,
                e,
                exc_info=True,
            )
            await self._cleanup_stopped_process_status_unified(
                status_key, "integration", f"{agent_id}:{integration_type}"
            )
            return False

    async def _stop_integration_process_impl(
        self, agent_id: str, integration_type: str, force: bool = False
    ) -> bool:
        """Реализация остановки процесса интеграции."""
        self.logger.info(
            "Stopping integration process: %s:%s (force=%s)",
            agent_id,
            integration_type,
            force,
        )

        status_key = self.config.integration_status_key_template.format(
            agent_id, integration_type
        )

        # Валидация статуса
        config = ProcessValidationConfig(
            pid=None,
            current_status="unknown",
            status_key=status_key,
            process_type="integration",
            process_id=f"{agent_id}_{integration_type}",
        )

        validated_status, pid, message = await self._validate_process_status_unified(
            config
        )

        if validated_status in ("not_found", "stopped"):
            self.logger.info(
                "Integration %s:%s already stopped or not found",
                agent_id,
                integration_type,
            )
            await self._cleanup_stopped_process_status_unified(
                status_key, "integration", f"{agent_id}:{integration_type}"
            )
            return True

        if validated_status == "error" or pid <= 0:
            self.logger.error(
                "Cannot stop integration %s:%s: %s", agent_id, integration_type, message
            )
            return False

        try:
            # Попытка корректной остановки
            if not force:
                graceful_success = (
                    await self.lifecycle_manager.terminate_process_gracefully(
                        {
                            "pid": pid,
                            "timeout": DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
                            "process_type": "integration",
                            "process_id": f"{agent_id}:{integration_type}",
                        }
                    )
                )

                if graceful_success:
                    await self._cleanup_stopped_process_status_unified(
                        status_key, "integration", f"{agent_id}:{integration_type}"
                    )
                    self.logger.info(
                        "Integration %s:%s stopped gracefully",
                        agent_id,
                        integration_type,
                    )
                    return True

            # Принудительная остановка
            force_success = await self._force_kill_process_unified(
                pid, "integration", f"{agent_id}:{integration_type}"
            )
            if force_success:
                await self._cleanup_stopped_process_status_unified(
                    status_key, "integration", f"{agent_id}:{integration_type}"
                )
                self.logger.info(
                    "Integration %s:%s force stopped", agent_id, integration_type
                )
                return True
            else:
                self.logger.error(
                    "Failed to force stop integration %s:%s", agent_id, integration_type
                )
                return False

        except Exception as e:
            self.logger.error(
                "Error stopping integration %s:%s: %s",
                agent_id,
                integration_type,
                e,
                exc_info=True,
            )
            return False

    async def _get_integration_status_impl(
        self, agent_id: str, integration_type: str
    ) -> Dict[str, Any]:
        """Реализация получения статуса интеграции."""
        status_key = self.config.integration_status_key_template.format(
            agent_id, integration_type
        )

        config = ProcessValidationConfig(
            pid=None,
            current_status="unknown",
            status_key=status_key,
            process_type="integration",
            process_id=f"{agent_id}_{integration_type}",
        )

        try:
            validated_status, validated_pid, message = (
                await self._validate_process_status_unified(config)
            )

            status_data = await self.status_manager.get_process_status(status_key)
            last_active = (
                status_data.get("last_active", "unknown") if status_data else "unknown"
            )

            return {
                "agent_id": agent_id,
                "integration_type": integration_type,
                "status": validated_status,
                "pid": validated_pid,
                "last_active": last_active,
                "message": message,
                "error_detail": (
                    status_data.get("error_detail") if status_data else None
                ),
            }
        except Exception as e:
            self.logger.error(
                "Error getting integration status %s:%s: %s",
                agent_id,
                integration_type,
                e,
                exc_info=True,
            )
            return {
                "agent_id": agent_id,
                "integration_type": integration_type,
                "status": "error",
                "pid": 0,
                "last_active": "unknown",
                "message": f"Error: {e}",
                "error_detail": str(e),
            }

    async def _build_integration_command(
        self, agent_id: str, integration_type: str, settings: Optional[Dict[str, Any]]
    ) -> Optional[List[str]]:
        """Строит команду для запуска интеграции."""
        try:
            module_path = self.config.integration_module_paths.get(integration_type)
            if not module_path:
                self.logger.error("Unknown integration type: %s", integration_type)
                return None

            command = [
                self.config.python_executable,
                "-m",
                module_path,
                agent_id,
            ]

            if settings:
                command.extend(["--settings", json.dumps(settings)])

            self.logger.debug(
                "Built integration command for %s:%s: %s",
                agent_id,
                integration_type,
                " ".join(command),
            )
            return command
        except Exception as e:
            self.logger.error(
                "Error building integration command for %s:%s: %s",
                agent_id,
                integration_type,
                e,
            )
            return None


# Phase 4: SOLID Principles - Process Coordinator for managing relationships
class ProcessLifecycleCoordinator:
    """
    Координатор жизненного цикла процессов (Interface Segregation & Dependency Inversion).
    Управляет взаимодействием между AgentProcessManager и IntegrationProcessManager.
    """

    def __init__(self, config: Optional[ProcessConfiguration] = None):
        """Инициализирует координатор."""
        if config is None:
            config = ProcessConfiguration.create_default()

        self.config = config
        self.logger = logger.getChild("ProcessCoordinator")
        self.agent_manager = AgentProcessManager(config)
        self.integration_manager = IntegrationProcessManager(config)

    async def start_agent_with_integrations(self, request: AgentStartupRequest) -> bool:
        """
        Запускает агента и связанные с ним интеграции в правильном порядке.

        Args:
            request: Параметры запуска агента и интеграций

        Returns:
            bool: True если все процессы запущены успешно
        """
        self.logger.info(
            "Starting agent %s with integrations: %s",
            request.agent_id,
            request.integration_types,
        )

        # 1. Сначала запускаем агента
        agent_success = await self.agent_manager.start_process(
            request.agent_id, request.agent_settings
        )
        if not agent_success:
            self.logger.error("Failed to start agent %s", request.agent_id)
            return False

        # 2. Ждем немного, чтобы агент инициализировался
        await asyncio.sleep(2)

        # 3. Запускаем интеграции
        successful_integrations = []
        for integration_type in request.integration_types:
            process_id = f"{request.agent_id}:{integration_type}"
            settings = (
                request.integration_settings.get(integration_type)
                if request.integration_settings
                else None
            )

            success = await self.integration_manager.start_process(process_id, settings)
            if success:
                successful_integrations.append(integration_type)
                self.logger.info(
                    "Started integration %s for agent %s",
                    integration_type,
                    request.agent_id,
                )
            else:
                self.logger.error(
                    "Failed to start integration %s for agent %s",
                    integration_type,
                    request.agent_id,
                )

        if len(successful_integrations) == len(request.integration_types):
            self.logger.info(
                "All integrations started successfully for agent %s", request.agent_id
            )
            return True
        else:
            self.logger.warning(
                "Only %d/%d integrations started for agent %s",
                len(successful_integrations),
                len(request.integration_types),
                request.agent_id,
            )
            return False

    async def stop_agent_with_integrations(
        self, agent_id: str, integration_types: List[str], force: bool = False
    ) -> bool:
        """
        Останавливает агента и связанные интеграции в правильном порядке.

        Args:
            agent_id: ID агента
            integration_types: Список типов интеграций для остановки
            force: Принудительная остановка

        Returns:
            bool: True если все процессы остановлены успешно
        """
        self.logger.info(
            "Stopping agent %s with integrations: %s (force=%s)",
            agent_id,
            integration_types,
            force,
        )

        success_count = 0
        total_count = len(integration_types) + 1  # +1 для агента

        # 1. Сначала останавливаем интеграции
        for integration_type in integration_types:
            process_id = f"{agent_id}:{integration_type}"
            success = await self.integration_manager.stop_process(process_id, force)
            if success:
                success_count += 1
                self.logger.info(
                    "Stopped integration %s for agent %s", integration_type, agent_id
                )
            else:
                self.logger.error(
                    "Failed to stop integration %s for agent %s",
                    integration_type,
                    agent_id,
                )

        # 2. Ждем немного
        await asyncio.sleep(1)

        # 3. Останавливаем агента
        agent_success = await self.agent_manager.stop_process(agent_id, force)
        if agent_success:
            success_count += 1
            self.logger.info("Stopped agent %s", agent_id)
        else:
            self.logger.error("Failed to stop agent %s", agent_id)

        if success_count == total_count:
            self.logger.info(
                "All processes stopped successfully for agent %s", agent_id
            )
            return True
        else:
            self.logger.warning(
                "Only %d/%d processes stopped for agent %s",
                success_count,
                total_count,
                agent_id,
            )
            return False

    async def restart_agent_with_integrations(
        self, request: AgentStartupRequest
    ) -> bool:
        """
        Перезапускает агента и интеграции.

        Args:
            request: Параметры запуска агента и интеграций (включая force_stop)

        Returns:
            bool: True если перезапуск прошел успешно
        """
        self.logger.info(
            "Restarting agent %s with integrations: %s",
            request.agent_id,
            request.integration_types,
        )

        # 1. Останавливаем
        stop_success = await self.stop_agent_with_integrations(
            request.agent_id, request.integration_types, request.force_stop
        )
        if not stop_success:
            self.logger.warning(
                "Stop phase had issues during restart of agent %s", request.agent_id
            )

        # 2. Ждем между остановкой и запуском
        await asyncio.sleep(3)

        # 3. Запускаем
        start_success = await self.start_agent_with_integrations(request)

        if start_success:
            self.logger.info("Agent %s restarted successfully", request.agent_id)
            return True
        else:
            self.logger.error("Failed to restart agent %s", request.agent_id)
            return False

    async def get_full_status(
        self, agent_id: str, integration_types: List[str]
    ) -> Dict[str, Any]:
        """
        Получает полный статус агента и всех его интеграций.

        Args:
            agent_id: ID агента
            integration_types: Список типов интеграций

        Returns:
            Dict: Полная информация о статусе
        """
        result = {
            "agent_id": agent_id,
            "agent_status": {},
            "integration_statuses": {},
            "overall_status": "unknown",
        }

        try:
            # Статус агента
            agent_status = await self.agent_manager.get_process_status(agent_id)
            result["agent_status"] = agent_status

            # Статусы интеграций
            for integration_type in integration_types:
                process_id = f"{agent_id}:{integration_type}"
                integration_status = await self.integration_manager.get_process_status(
                    process_id
                )
                result["integration_statuses"][integration_type] = integration_status

            # Определяем общий статус
            agent_running = agent_status.get("status") == "running"
            running_integrations = sum(
                1
                for status in result["integration_statuses"].values()
                if status.get("status") == "running"
            )
            total_integrations = len(integration_types)

            if agent_running and running_integrations == total_integrations:
                result["overall_status"] = "fully_running"
            elif agent_running and running_integrations > 0:
                result["overall_status"] = "partially_running"
            elif agent_running:
                result["overall_status"] = "agent_only"
            elif running_integrations > 0:
                result["overall_status"] = "integrations_only"
            else:
                result["overall_status"] = "stopped"

            result["summary"] = {
                "agent_running": agent_running,
                "running_integrations": running_integrations,
                "total_integrations": total_integrations,
            }

        except Exception as e:
            self.logger.error(
                "Error getting full status for agent %s: %s", agent_id, e, exc_info=True
            )
            result["overall_status"] = "error"
            result["error"] = str(e)

        return result
