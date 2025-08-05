"""
Менеджер жизненного цикла процессов.

Модуль содержит классы для управления жизненным циклом процессов:
запуск, остановка, перезапуск, принудительное завершение.
"""

import os
import signal
import asyncio
import logging
import json
from typing import Optional, Dict, Any, List, Tuple

from app.core.base.process_launcher import ProcessLauncher
from .base import ProcessManagerBase, ProcessInfo
from .config import ProcessConfiguration
from .exceptions import (
    ProcessStartupError, ProcessTerminationError
)


logger = logging.getLogger(__name__)


class ProcessLifecycleManager(ProcessManagerBase):
    """Менеджер для управления жизненным циклом процессов."""

    def __init__(self, config: ProcessConfiguration):
        super().__init__(config)
        self.launcher = ProcessLauncher()

    async def start_process(self, process_id: str, **kwargs) -> bool:
        """Базовая реализация - должна быть переопределена в наследниках."""
        raise NotImplementedError("Must be implemented by subclasses")

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Базовая реализация - должна быть переопределена в наследниках."""
        raise NotImplementedError("Must be implemented by subclasses")

    async def get_process_status(self, process_id: str) -> ProcessInfo:
        """Базовая реализация - должна быть переопределена в наследниках."""
        raise NotImplementedError("Must be implemented by subclasses")

    async def send_graceful_termination_signal(self, pid: int,
                                             timeout: float = None) -> bool:
        """Отправить сигнал SIGTERM для graceful завершения."""
        if timeout is None:
            timeout = self.config.defaults.GRACEFUL_SHUTDOWN_TIMEOUT

        try:
            logger.debug("Sending SIGTERM to PID %s", pid)
            os.kill(pid, signal.SIGTERM)

            # Ждем завершения процесса в течение timeout
            return await self.wait_for_process_termination(pid, timeout, "SIGTERM")

        except ProcessLookupError:
            logger.debug("Process %s already terminated", pid)
            return True
        except OSError as e:
            logger.error("Error sending SIGTERM to PID %s: %s", pid, e)
            return False

    async def wait_for_process_termination(self, pid: int, timeout: float,
                                         signal_name: str) -> bool:
        """Ожидать завершения процесса в течение timeout."""
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                os.kill(pid, 0)  # Проверить существование процесса
                await asyncio.sleep(0.5)  # Небольшая пауза перед следующей проверкой
            except ProcessLookupError:
                logger.debug("Process %s terminated after %s", pid, signal_name)
                return True
            except OSError:
                # Другие ошибки считаем как продолжающийся процесс
                await asyncio.sleep(0.5)

        logger.warning("Process %s did not terminate within %ss after %s",
                      pid, timeout, signal_name)
        return False

    async def force_kill_process_unified(self, pid: int, process_type: str,
                                       process_id: str) -> bool:
        """Унифицированное принудительное завершение процесса."""
        try:
            logger.warning("Force killing %s %s with PID %s",
                          process_type, process_id, pid)
            os.kill(pid, signal.SIGKILL)

            # Короткое ожидание для подтверждения завершения
            await asyncio.sleep(1.0)

            try:
                os.kill(pid, 0)
                logger.error("Process %s still exists after SIGKILL", pid)
                return False
            except ProcessLookupError:
                logger.info("Successfully force killed %s %s (PID %s)",
                           process_type, process_id, pid)
                return True

        except ProcessLookupError:
            logger.debug("Process %s already terminated", pid)
            return True
        except OSError as e:
            logger.error("Error force killing PID %s: %s", pid, e)
            return False

    async def build_process_command_unified(
            self, process_type: str, process_id: str, module_path: str,
            additional_settings: Optional[Dict[str, Any]] = None) -> List[str]:
        """Унифицированное построение команды для запуска процесса."""
        cmd = [
            self.config.python_executable,
            "-m", module_path,
        ]

        # Для интеграций всегда используем --agent-id
        if process_type.lower() == "integration":
            cmd.extend(["--agent-id", process_id])
            if additional_settings:
                cmd.extend(["--integration-settings", json.dumps(additional_settings)])
        else:  # для агентов
            cmd.extend([f"--{process_type.lower()}-id", process_id])
            if additional_settings:
                settings_key = f"--{process_type.lower()}-settings"
                cmd.extend([settings_key, json.dumps(additional_settings)])

        logger.debug("Built command for %s %s: %s",
                    process_type, process_id, ' '.join(cmd))
        return cmd

    async def launch_subprocess_unified(
            self, command: List[str], process_type: str,
            process_id: str) -> Tuple[bool, Optional[int]]:
        """Унифицированный запуск подпроцесса."""
        try:
            logger.debug("Launching %s %s. Command: %s",
                        process_type, process_id, ' '.join(command))

            process_obj, _, _ = await self.launcher.launch_process(
                command=command,
                process_id=f"{process_type}_start_{process_id}",
                cwd=self.config.project_root,
                env_vars=self.config.process_env,
                capture_output=False
            )

            if process_obj and process_obj.pid is not None:
                logger.info("Successfully launched %s %s with PID %s",
                           process_type, process_id, process_obj.pid)
                return True, process_obj.pid

            logger.error("Failed to launch %s %s: No process or PID",
                        process_type, process_id)
            return False, None

        except (OSError, ProcessStartupError) as e:
            logger.error("Exception launching %s %s: %s",
                        process_type, process_id, e, exc_info=True)
            return False, None

    async def validate_start_prerequisites_unified(
            self, process_type: str, process_id: str, script_path: str) -> bool:
        """Унифицированная валидация предварительных условий для запуска."""
        if not script_path or not os.path.exists(script_path):
            logger.error("%s script not found: %s", process_type, script_path)
            return False

        if not process_id or not isinstance(process_id, str):
            logger.error("Invalid %s ID: %s", process_type, process_id)
            return False

        return True

    async def stop_process_unified(
            self, pid: Optional[int], process_type: str, process_id: str,
            force: bool = False) -> bool:
        """Унифицированная остановка процесса."""
        if not pid:
            logger.warning("No PID found for %s %s", process_type, process_id)
            return True  # Считаем успешным, так как процесс не запущен

        try:
            if force:
                return await self.force_kill_process_unified(pid, process_type, process_id)

            # Сначала попробовать graceful shutdown
            if await self.send_graceful_termination_signal(pid):
                return True

            # Если graceful не сработал, принудительно завершить
            logger.warning("Graceful shutdown failed for %s %s, using force",
                          process_type, process_id)
            return await self.force_kill_process_unified(pid, process_type, process_id)

        except (OSError, ProcessTerminationError) as e:
            logger.error("Error stopping %s %s: %s",
                        process_type, process_id, e, exc_info=True)
            return False

    async def restart_process_unified(
            self, process_info: Dict[str, Any], stop_method, start_method,
            settings: Optional[Dict[str, Any]] = None) -> bool:
        """Унифицированный перезапуск процесса."""
        process_type = process_info['process_type']
        process_id = process_info['process_id']

        logger.info("Restarting %s %s", process_type, process_id)

        # Остановить процесс
        stop_success = await stop_method(process_id, force=True)
        if not stop_success:
            logger.error("Failed to stop %s %s for restart", process_type, process_id)
            return False

        # Небольшая пауза перед перезапуском
        await asyncio.sleep(2.0)

        # Запустить процесс
        if settings:
            start_success = await start_method(process_id, settings)
        else:
            start_success = await start_method(process_id)

        if start_success:
            logger.info("Successfully restarted %s %s", process_type, process_id)
        else:
            logger.error("Failed to start %s %s after stop for restart",
                        process_type, process_id)

        return start_success
