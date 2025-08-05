"""
Координатор процессов и основной фасад ProcessManager.

Модуль содержит координатор для управления жизненным циклом процессов
и основной класс ProcessManager, объединяющий все компоненты.
"""

import logging
from typing import Optional, Dict, Any

from app.core.base.redis_manager import RedisClientManager
from .base import AgentStatusInfo, IntegrationStatusInfo, IntegrationTypeStr
from .config import ProcessConfiguration
from .agent_manager import AgentProcessManager
from .integration_manager import IntegrationProcessManager


logger = logging.getLogger(__name__)


class ProcessLifecycleCoordinator:
    """Координатор для управления жизненным циклом процессов."""

    def __init__(self, agent_manager: AgentProcessManager,
                 integration_manager: IntegrationProcessManager):
        self.agent_manager = agent_manager
        self.integration_manager = integration_manager

    async def start_agent_with_integrations(self, agent_id: str,
                                          agent_settings: Optional[Dict[str, Any]] = None,
                                          integrations: Optional[list] = None) -> Dict[str, bool]:
        """
        Запустить агента и его интеграции в правильном порядке.

        Args:
            agent_id: Идентификатор агента
            agent_settings: Настройки агента
            integrations: Список интеграций для запуска

        Returns:
            Dict[str, bool]: Результаты запуска для агента и каждой интеграции
        """
        results = {}

        # Сначала запустить агента
        logger.info("Starting coordinated launch for agent %s", agent_id)
        agent_result = await self.agent_manager.start_agent_process(agent_id, agent_settings)
        results["agent"] = agent_result

        if not agent_result:
            logger.error("Failed to start agent %s, skipping integrations", agent_id)
            return results

        # Запустить интеграции
        if integrations:
            for integration_config in integrations:
                if not isinstance(integration_config, dict):
                    continue

                integration_type = integration_config.get("type")
                integration_settings = integration_config.get("settings")

                if not integration_type:
                    continue

                # Проверить, что интеграция включена
                if not integration_settings or not integration_settings.get("enabled", True):
                    logger.info(
                        "Integration %s for agent %s is disabled, skipping",
                        integration_type, agent_id
                    )
                    continue

                logger.info("Starting integration %s for agent %s", integration_type, agent_id)
                integration_result = await self.integration_manager.start_integration_process(
                    agent_id, integration_type, integration_settings
                )
                results[f"integration_{integration_type}"] = integration_result

        return results

    async def stop_agent_with_integrations(self, agent_id: str,
                                         integration_types: Optional[list] = None,
                                         force: bool = False) -> Dict[str, bool]:
        """
        Остановить интеграции и агента в правильном порядке.

        Args:
            agent_id: Идентификатор агента
            integration_types: Список типов интеграций для остановки
            force: Принудительная остановка

        Returns:
            Dict[str, bool]: Результаты остановки для каждой интеграции и агента
        """
        results = {}

        logger.info("Starting coordinated shutdown for agent %s", agent_id)

        # Сначала остановить интеграции
        if integration_types:
            for integration_type in integration_types:
                logger.info("Stopping integration %s for agent %s", integration_type, agent_id)
                integration_result = await self.integration_manager.stop_integration_process(
                    agent_id, integration_type, force
                )
                results[f"integration_{integration_type}"] = integration_result

        # Затем остановить агента
        agent_result = await self.agent_manager.stop_agent_process(agent_id, force)
        results["agent"] = agent_result

        return results


class ProcessManager(RedisClientManager):
    """
    Основной фасад для управления процессами агентов и интеграций.

    Этот класс объединяет все компоненты системы управления процессами
    и предоставляет единый интерфейс для внешнего использования.
    """

    def __init__(self):
        """Инициализировать ProcessManager с конфигурацией по умолчанию."""
        super().__init__()
        self.config = ProcessConfiguration()
        self.agent_manager = AgentProcessManager(self.config)
        self.integration_manager = IntegrationProcessManager(self.config)
        self.coordinator = ProcessLifecycleCoordinator(
            self.agent_manager, self.integration_manager
        )

    async def setup_manager(self):
        """Инициализировать соединения и настройки менеджера."""
        await self.agent_manager.setup()
        await self.integration_manager.setup()
        logger.info("ProcessManager setup completed")

    async def cleanup_manager(self):
        """Очистить ресурсы менеджера."""
        await self.agent_manager.cleanup()
        await self.integration_manager.cleanup()
        logger.info("ProcessManager cleanup completed")

    # Основные методы агентов
    async def start_agent_process(self, agent_id: str,
                                agent_settings: Optional[Dict[str, Any]] = None) -> bool:
        """Запустить процесс агента."""
        return await self.agent_manager.start_agent_process(agent_id, agent_settings)

    async def stop_agent_process(self, agent_id: str, force: bool = False) -> bool:
        """Остановить процесс агента."""
        return await self.agent_manager.stop_agent_process(agent_id, force)

    async def get_agent_status(self, agent_id: str) -> AgentStatusInfo:
        """Получить статус агента."""
        return await self.agent_manager.get_agent_status(agent_id)

    # Основные методы интеграций
    async def start_integration_process(
        self, agent_id: str, integration_type: IntegrationTypeStr,
        integration_settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Запустить процесс интеграции."""
        return await self.integration_manager.start_integration_process(
            agent_id, integration_type, integration_settings
        )

    async def stop_integration_process(self, agent_id: str, integration_type: IntegrationTypeStr,
                                      force: bool = False) -> bool:
        """Остановить процесс интеграции."""
        return await self.integration_manager.stop_integration_process(
            agent_id, integration_type, force
        )

    async def get_integration_status(self, agent_id: str,
                                   integration_type: IntegrationTypeStr) -> IntegrationStatusInfo:
        """Получить статус интеграции."""
        return await self.integration_manager.get_integration_status(agent_id, integration_type)

    # Координированные операции
    async def start_agent_with_integrations(self, agent_id: str,
                                          agent_settings: Optional[Dict[str, Any]] = None,
                                          integrations: Optional[list] = None) -> Dict[str, bool]:
        """Запустить агента с интеграциями в правильном порядке."""
        return await self.coordinator.start_agent_with_integrations(
            agent_id, agent_settings, integrations
        )

    async def stop_agent_with_integrations(self, agent_id: str,
                                         integration_types: Optional[list] = None,
                                         force: bool = False) -> Dict[str, bool]:
        """Остановить агента с интеграциями в правильном порядке."""
        return await self.coordinator.stop_agent_with_integrations(
            agent_id, integration_types, force
        )

    # Конфигурационные свойства
    @property
    def agent_status_key_template(self) -> str:
        """Шаблон ключа Redis для статуса агента."""
        return self.config.defaults.agent_status_key_template

    @property
    def integration_status_key_template(self) -> str:
        """Шаблон ключа Redis для статуса интеграции."""
        return self.config.defaults.integration_status_key_template

    @property
    def project_root(self) -> str:
        """Корневой путь проекта."""
        return self.config.project_root

    @property
    def python_executable(self) -> str:
        """Путь к исполняемому файлу Python."""
        return self.config.python_executable
