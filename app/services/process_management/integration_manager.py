"""
Менеджер процессов интеграций.

Модуль содержит класс для управления процессами интеграций:
запуск, остановка, перезапуск, получение статуса.
"""

import logging
from typing import Optional, Dict, Any

from .base import IntegrationStatusInfo, IntegrationInfo, IntegrationTypeStr
from .status_manager import ProcessStatusManager
from .lifecycle_manager import ProcessLifecycleManager
from .config import ProcessConfiguration


logger = logging.getLogger(__name__)


class IntegrationProcessManager(ProcessStatusManager, ProcessLifecycleManager):
    """Менеджер для управления процессами интеграций."""

    def __init__(self, config: ProcessConfiguration):
        super().__init__(config)
        self._supported_integration_types = {"TELEGRAM", "WHATSAPP"}

    async def start_process(self, process_id: str, **kwargs) -> bool:
        """Запустить процесс интеграции."""
        agent_id, integration_type = process_id.split("_", 1)
        integration_settings = kwargs.get('integration_settings')
        return await self.start_integration_process(
            agent_id, integration_type, integration_settings
        )

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Остановить процесс интеграции."""
        agent_id, integration_type = process_id.split("_", 1)
        return await self.stop_integration_process(agent_id, integration_type, force)

    async def get_process_status(self, process_id: str) -> IntegrationInfo:
        """Получить статус процесса интеграции."""
        agent_id, integration_type = process_id.split("_", 1)
        return await self.get_integration_status_info(agent_id, integration_type)

    async def start_integration_process(
        self, agent_id: str, integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Запустить процесс интеграции.

        Args:
            agent_id: Идентификатор агента
            integration_type: Тип интеграции (TELEGRAM, WHATSAPP)
            integration_settings: Настройки интеграции

        Returns:
            bool: True если запуск успешен, False иначе
        """
        try:
            # Валидация и проверка состояния
            validation_result = await self._validate_integration_start_conditions(
                agent_id, integration_type
            )
            if validation_result is not None:
                return validation_result

            return await self._prepare_and_launch_integration(
                agent_id, integration_type, integration_settings
            )

        except (ValueError, KeyError) as e:
            logger.error("Validation error for integration type %s: %s", integration_type, e)
            return False

    async def _validate_integration_start_conditions(
        self, agent_id: str, integration_type: IntegrationTypeStr
    ) -> Optional[bool]:
        """Валидация условий для запуска интеграции. None = продолжить, bool = вернуть результат."""
        # Валидация типа интеграции
        if not self._validate_integration_type(integration_type):
            return False

        # Проверить текущий статус интеграции
        current_status = await self.get_integration_status_info(agent_id, integration_type)

        if current_status.status == self.config.defaults.STATUS_RUNNING:
            logger.warning(
                "Integration %s for agent %s already running. Skipping start.",
                integration_type, agent_id
            )
            return True

        if current_status.status == self.config.defaults.STATUS_STARTING:
            logger.warning(
                "Integration %s for agent %s is already starting. "
                "Skipping duplicate start request.",
                integration_type, agent_id
            )
            return True

        return None  # Continue with start process

    async def _prepare_and_launch_integration(
        self, agent_id: str, integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]]
    ) -> bool:
        """Подготовка и запуск интеграции."""
        logger.info(
            "Attempting to start integration process for %s/%s...",
            agent_id, integration_type
        )

        # Получить пути для интеграции
        module_path = self.config.integration_module_paths.get(integration_type.upper())
        script_path = self.config.integration_script_full_paths.get(integration_type.upper())

        # Валидация предварительных условий
        if not await self.validate_start_prerequisites_unified(
            "integration", f"{agent_id}_{integration_type}", script_path
        ):
            return False

        # Инициализация статуса "starting"
        status_key = self.config.get_integration_status_key(agent_id, integration_type)
        initial_status_data = await self.initialize_starting_status_unified(
            status_key, "integration", agent_id
        )
        initial_status_data["integration_type"] = integration_type
        await self._update_status_in_redis(status_key, initial_status_data)

        # Построение команды для запуска
        command = await self.build_process_command_unified(
            "integration", agent_id, module_path, integration_settings
        )

        # Запуск подпроцесса
        success, pid = await self.launch_subprocess_unified(
            command, "integration", f"{agent_id}_{integration_type}"
        )

        if success and pid:
            # Обновление статуса с PID
            update_data = {
                "status": self.config.defaults.STATUS_RUNNING,
                "pid": str(pid),
                "integration_type": integration_type
            }
            await self._update_status_in_redis(status_key, update_data)
            logger.info(
                "Successfully started integration %s for agent %s with PID %s",
                integration_type, agent_id, pid
            )
            return True

        # Обновление статуса об ошибке
        error_data = {
            "status": self.config.defaults.STATUS_ERROR,
            "error_detail": "Failed to launch subprocess",
            "agent_id": agent_id,
            "integration_type": integration_type
        }
        await self._update_status_in_redis(status_key, error_data)
        logger.error(
            "Failed to start integration %s for agent %s",
            integration_type, agent_id
        )
        return False

    async def stop_integration_process(self, agent_id: str, integration_type: IntegrationTypeStr,
                                      force: bool = False) -> bool:
        """
        Остановить процесс интеграции.

        Args:
            agent_id: Идентификатор агента
            integration_type: Тип интеграции
            force: Принудительная остановка через SIGKILL

        Returns:
            bool: True если остановка успешна, False иначе
        """
        try:
            logger.info(
                "Attempting to stop integration process for %s/%s (force=%s)",
                agent_id, integration_type, force
            )

            # Валидация типа интеграции
            if not self._validate_integration_type(integration_type):
                return False

            # Получить текущий статус интеграции
            current_status = await self.get_integration_status_info(agent_id, integration_type)

            if current_status.status in [
                self.config.defaults.STATUS_NOT_FOUND,
                self.config.defaults.STATUS_STOPPED
            ]:
                logger.info(
                    "Integration %s for agent %s is already stopped or not found",
                    integration_type, agent_id
                )
                return True

            # Обновить статус на "stopping"
            status_key = self.config.get_integration_status_key(agent_id, integration_type)
            stopping_data = {
                "status": self.config.defaults.STATUS_STOPPING,
                "integration_type": integration_type
            }
            await self._update_status_in_redis(status_key, stopping_data)

            # Остановка процесса
            stop_success = await self.stop_process_unified(
                current_status.pid, "integration", f"{agent_id}_{integration_type}", force
            )

            if stop_success:
                # Очистка статуса остановленного процесса
                await self.cleanup_stopped_process_status_unified(
                    status_key, "integration", f"{agent_id}_{integration_type}"
                )
                # Добавить integration_type в финальный статус
                final_data = {"integration_type": integration_type}
                await self._update_status_in_redis(status_key, final_data)
                logger.info(
                    "Successfully stopped integration %s for agent %s",
                    integration_type, agent_id
                )
                return True

            # Обновление статуса об ошибке
            error_data = {
                "status": self.config.defaults.STATUS_ERROR,
                "error_detail": "Failed to stop process",
                "integration_type": integration_type
            }
            await self._update_status_in_redis(status_key, error_data)
            logger.error(
                "Failed to stop integration %s for agent %s",
                integration_type, agent_id
            )
            return False

        except (OSError, ValueError, RuntimeError) as e:
            logger.error(
                "Exception stopping integration %s for agent %s: %s",
                integration_type, agent_id, e, exc_info=True
            )
            return False

    async def restart_integration_process(
        self, agent_id: str, integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Перезапустить процесс интеграции.

        Args:
            agent_id: Идентификатор агента
            integration_type: Тип интеграции
            integration_settings: Настройки интеграции

        Returns:
            bool: True если перезапуск успешен, False иначе
        """
        return await self.restart_process_unified(
            {"process_type": "integration", "process_id": f"{agent_id}_{integration_type}"},
            lambda aid_itype, force=False: self.stop_integration_process(
                agent_id, integration_type, force
            ),
            lambda aid_itype, settings=None: self.start_integration_process(
                agent_id, integration_type, settings
            ),
            integration_settings
        )

    async def get_integration_status(
        self, agent_id: str, integration_type: IntegrationTypeStr
    ) -> IntegrationStatusInfo:
        """
        Получить статус интеграции (для обратной совместимости).

        Args:
            agent_id: Идентификатор агента
            integration_type: Тип интеграции

        Returns:
            Dict[str, Any]: Информация о статусе интеграции
        """
        integration_info = await self.get_integration_status_info(agent_id, integration_type)

        return {
            "agent_id": integration_info.agent_id,
            "integration_type": integration_info.integration_type,
            "status": integration_info.status,
            "pid": integration_info.pid,
            "last_active": integration_info.last_active,
            "error_detail": integration_info.error_detail
        }

    def _validate_integration_type(self, integration_type: IntegrationTypeStr) -> bool:
        """
        Валидировать тип интеграции.

        Args:
            integration_type: Тип интеграции для валидации

        Returns:
            bool: True если тип поддерживается, False иначе
        """
        if integration_type.upper() not in self._supported_integration_types:
            logger.error("Unsupported integration type: %s", integration_type)
            return False

        # Проверить наличие соответствующих путей
        if integration_type.upper() not in self.config.integration_module_paths:
            logger.error("Module path not configured for integration type: %s", integration_type)
            return False

        if integration_type.upper() not in self.config.integration_script_full_paths:
            logger.error("Script path not configured for integration type: %s", integration_type)
            return False

        return True

    async def validate_integration_settings(
        self, integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]]
    ) -> bool:
        """
        Валидировать настройки интеграции.

        Args:
            integration_type: Тип интеграции
            integration_settings: Настройки интеграции для валидации

        Returns:
            bool: True если настройки валидны, False иначе
        """
        if integration_settings is None:
            return True  # None настройки допустимы

        if not isinstance(integration_settings, dict):
            logger.error("Integration settings must be a dict, got %s", type(integration_settings))
            return False

        # Специфичные валидации по типам интеграций
        if integration_type.upper() == "TELEGRAM":
            return self._validate_telegram_settings(integration_settings)
        if integration_type.upper() == "WHATSAPP":
            return self._validate_whatsapp_settings(integration_settings)

        return True

    def _validate_telegram_settings(self, settings: Dict[str, Any]) -> bool:
        """Валидировать настройки Telegram интеграции."""
        # Проверка наличия обязательных полей для Telegram
        if "botToken" in settings and not settings["botToken"]:
            logger.error("Telegram botToken is required but empty")
            return False

        return True

    def _validate_whatsapp_settings(self, _settings: Dict[str, Any]) -> bool:
        """Валидировать настройки WhatsApp интеграции."""
        # Дополнительные проверки для WhatsApp могут быть добавлены здесь
        return True
