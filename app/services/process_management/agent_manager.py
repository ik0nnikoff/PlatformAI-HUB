"""
Менеджер процессов агентов.

Модуль содержит класс для управления процессами агентов:
запуск, остановка, перезапуск, получение статуса.
"""

import logging
from typing import Optional, Dict, Any

from .base import AgentStatusInfo, AgentInfo
from .status_manager import ProcessStatusManager
from .lifecycle_manager import ProcessLifecycleManager
from .config import ProcessConfiguration


logger = logging.getLogger(__name__)


class AgentProcessManager(ProcessStatusManager, ProcessLifecycleManager):
    """Менеджер для управления процессами агентов."""

    def __init__(self, config: ProcessConfiguration):
        ProcessStatusManager.__init__(self, config)
        ProcessLifecycleManager.__init__(self, config)

    async def start_process(self, process_id: str, **kwargs) -> bool:
        """Запустить процесс агента."""
        agent_settings = kwargs.get('agent_settings')
        return await self.start_agent_process(process_id, agent_settings)

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Остановить процесс агента."""
        return await self.stop_agent_process(process_id, force)

    async def get_process_status(self, process_id: str) -> AgentInfo:
        """Получить статус процесса агента."""
        return await self.get_agent_status_info(process_id)

    async def start_agent_process(self, agent_id: str,
                                agent_settings: Optional[Dict[str, Any]] = None) -> bool:
        """
        Запустить процесс агента.

        Args:
            agent_id: Идентификатор агента
            agent_settings: Дополнительные настройки агента

        Returns:
            bool: True если запуск успешен, False иначе
        """
        try:
            # Проверить текущий статус агента
            current_status = await self.get_agent_status_info(agent_id)
            if current_status.status == self.config.defaults.STATUS_RUNNING:
                logger.warning("Agent %s already reported as running. Skipping start.", agent_id)
                return True

            if current_status.status == self.config.defaults.STATUS_STARTING:
                logger.warning(
                    "Agent %s is already starting. Skipping duplicate start request.",
                    agent_id
                )
                return True

            logger.info("Launching agent runner for agent_id: %s", agent_id)

            # Валидация предварительных условий
            if not await self.validate_start_prerequisites_unified(
                "agent", agent_id,
                self.config.agent_runner_script_full_path
            ):
                return False

            # Инициализация статуса "starting"
            status_key = self.config.get_agent_status_key(agent_id)
            await self.initialize_starting_status_unified(
                status_key, "agent", agent_id
            )

            # Построение команды для запуска
            command = await self.build_process_command_unified(
                "agent", agent_id, self.config.agent_runner_module_path,
                agent_settings
            )

            # Запуск подпроцесса
            success, pid = await self.launch_subprocess_unified(
                command, "agent", agent_id
            )

            if success and pid:
                # Обновление статуса с PID
                update_data = {
                    "status": self.config.defaults.STATUS_RUNNING,
                    "pid": str(pid)
                }
                await self._update_status_in_redis(status_key, update_data)
                logger.info("Successfully started agent %s with PID %s", agent_id, pid)
                return True

            # Обновление статуса об ошибке
            error_data = {
                "status": self.config.defaults.STATUS_ERROR,
                "error_detail": "Failed to launch subprocess"
            }
            await self._update_status_in_redis(status_key, error_data)
            logger.error("Failed to start agent %s", agent_id)
            return False

        except (OSError, ValueError, RuntimeError) as e:
            logger.error("Exception starting agent %s: %s", agent_id, e, exc_info=True)

            # Обновление статуса об ошибке
            try:
                status_key = self.config.get_agent_status_key(agent_id)
                error_data = {
                    "status": self.config.defaults.STATUS_ERROR,
                    "error_detail": str(e)
                }
                await self._update_status_in_redis(status_key, error_data)
            except OSError as update_error:
                logger.error(
                    "Failed to update error status for agent %s: %s",
                    agent_id, update_error
                )

            return False

    async def stop_agent_process(self, agent_id: str, force: bool = False) -> bool:
        """
        Остановить процесс агента.

        Args:
            agent_id: Идентификатор агента
            force: Принудительная остановка через SIGKILL

        Returns:
            bool: True если остановка успешна, False иначе
        """
        try:
            logger.info("Attempting to stop agent process for %s (force=%s)", agent_id, force)

            # Получить текущий статус агента
            current_status = await self.get_agent_status_info(agent_id)

            if current_status.status in [
                self.config.defaults.STATUS_NOT_FOUND,
                self.config.defaults.STATUS_STOPPED
            ]:
                logger.info("Agent %s is already stopped or not found", agent_id)
                return True

            # Обновить статус на "stopping"
            status_key = self.config.get_agent_status_key(agent_id)
            stopping_data = {"status": self.config.defaults.STATUS_STOPPING}
            await self._update_status_in_redis(status_key, stopping_data)

            # Остановка процесса
            stop_success = await self.stop_process_unified(
                current_status.pid, "agent", agent_id, force
            )

            if stop_success:
                # Очистка статуса остановленного процесса
                await self.cleanup_stopped_process_status_unified(
                    status_key, "agent", agent_id
                )
                logger.info("Successfully stopped agent %s", agent_id)
                return True

            # Обновление статуса об ошибке
            error_data = {
                "status": self.config.defaults.STATUS_ERROR,
                "error_detail": "Failed to stop process"
            }
            await self._update_status_in_redis(status_key, error_data)
            logger.error("Failed to stop agent %s", agent_id)
            return False

        except (OSError, ValueError, RuntimeError) as e:
            logger.error(
                "Exception stopping agent %s: %s", agent_id, e, exc_info=True
            )
            return False

    async def restart_agent_process(self, agent_id: str,
                                   agent_settings: Optional[Dict[str, Any]] = None) -> bool:
        """
        Перезапустить процесс агента.

        Args:
            agent_id: Идентификатор агента
            agent_settings: Дополнительные настройки агента

        Returns:
            bool: True если перезапуск успешен, False иначе
        """
        return await self.restart_process_unified(
            {"process_type": "agent", "process_id": agent_id},
            self.stop_agent_process,
            lambda aid, settings=None: self.start_agent_process(aid, settings),
            agent_settings
        )

    async def get_agent_status(self, agent_id: str) -> AgentStatusInfo:
        """
        Получить статус агента (для обратной совместимости).

        Args:
            agent_id: Идентификатор агента

        Returns:
            Dict[str, Any]: Информация о статусе агента
        """
        agent_info = await self.get_agent_status_info(agent_id)

        return {
            "agent_id": agent_info.agent_id,
            "status": agent_info.status,
            "pid": agent_info.pid,
            "last_active": agent_info.last_active,
            "error_detail": agent_info.error_detail
        }

    async def validate_agent_settings(self, agent_settings: Optional[Dict[str, Any]]) -> bool:
        """
        Валидировать настройки агента.

        Args:
            agent_settings: Настройки агента для валидации

        Returns:
            bool: True если настройки валидны, False иначе
        """
        if agent_settings is None:
            return True  # None настройки допустимы

        if not isinstance(agent_settings, dict):
            logger.error("Agent settings must be a dict, got %s", type(agent_settings))
            return False

        # Дополнительные проверки могут быть добавлены здесь
        return True
