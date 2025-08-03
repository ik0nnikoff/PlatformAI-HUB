"""
STT Initialization Mixin - Phase 1.1 Deduplication

Mixin для стандартизации инициализации и очистки STT провайдеров.
Устраняет дублирование ~60 строк кода на провайдер.

Architecture Compliance:
- DRY Principle: Единая точка инициализации для всех провайдеров
- SOLID: Interface Segregation - специализированный mixin
- Maintainability: Централизованная логика изменений
"""

import logging
from typing import Any, Awaitable, Callable, List, Optional

from ...core.exceptions import ProviderNotAvailableError, VoiceConfigurationError

logger = logging.getLogger(__name__)


class STTInitializationMixin:
    """
    Mixin для стандартизации инициализации STT провайдеров.

    Устраняет дублирование:
    - Try-catch блоков инициализации
    - Логирования успеха/ошибок
    - Обработки исключений
    - Cleanup логики
    """

    async def _standard_initialize(
        self,
        validation_checks: List[Callable[[], None]],
        client_factory: Callable[[], Awaitable[Any]],
        health_check: Optional[Callable[[], Awaitable[bool]]] = None,
        provider_name: str = None
    ) -> None:
        """
        Стандартная логика инициализации STT провайдера.

        Args:
            validation_checks: Список функций валидации конфигурации
            client_factory: Функция создания клиента
            health_check: Опциональная проверка здоровья сервиса
            provider_name: Имя провайдера для логирования
        """
        provider_name = provider_name or getattr(self, 'provider_name', 'Unknown STT Provider')

        try:
            await self._execute_initialization_steps(
                validation_checks, client_factory, health_check, provider_name
            )
        except (ProviderNotAvailableError, VoiceConfigurationError):
            raise  # Пробрасываем без обработки
        except (OSError, ConnectionError, TimeoutError) as e:
            self._handle_initialization_error(provider_name, e)

    async def _execute_initialization_steps(
        self,
        validation_checks: List[Callable[[], None]],
        client_factory: Callable[[], Awaitable[Any]],
        health_check: Optional[Callable[[], Awaitable[bool]]],
        provider_name: str
    ) -> None:
        """Выполняет основные шаги инициализации."""
        logger.debug("Initializing %s...", provider_name)

        # Выполнение всех проверок конфигурации
        for validation_check in validation_checks:
            validation_check()

        # Создание клиента
        await client_factory()

        # Опциональная проверка здоровья
        await self._perform_health_check(health_check, provider_name)

        # Установка флага инициализации
        self._set_initialization_flag()

        logger.info("%s initialized successfully", provider_name)

    async def _perform_health_check(
        self,
        health_check: Optional[Callable[[], Awaitable[bool]]],
        provider_name: str
    ) -> None:
        """Выполняет проверку здоровья сервиса если она задана."""
        if health_check:
            health_result = await health_check()
            if not health_result:
                logger.warning("%s health check failed during initialization", provider_name)

    def _set_initialization_flag(self) -> None:
        """Устанавливает флаг успешной инициализации."""
        if hasattr(self, '_initialized'):
            self._initialized = True

    def _handle_initialization_error(self, provider_name: str, error: Exception) -> None:
        """Обрабатывает ошибки инициализации."""
        logger.error("Failed to initialize %s: %s", provider_name, error, exc_info=True)
        raise ProviderNotAvailableError(
            provider_name,
            f"Ошибка инициализации: {str(error)}"
        ) from error

    async def _standard_cleanup(
        self,
        cleanup_tasks: List[Callable[[], Awaitable[None]]],
        provider_name: str = None
    ) -> None:
        """
        Стандартная логика очистки ресурсов STT провайдера.

        Args:
            cleanup_tasks: Список асинхронных задач очистки
            provider_name: Имя провайдера для логирования
        """
        provider_name = provider_name or getattr(
            self, 'provider_name', 'Unknown STT Provider'
        )

        cleanup_errors = []

        try:
            # Выполнение всех задач очистки
            for cleanup_task in cleanup_tasks:
                try:
                    await cleanup_task()
                except (OSError, ConnectionError, TimeoutError) as e:
                    cleanup_errors.append(str(e))
                    logger.warning("Cleanup task failed for %s: %s", provider_name, e)

            # Сброс флага инициализации
            if hasattr(self, '_initialized'):
                self._initialized = False

            if cleanup_errors:
                logger.warning(
                    "%s cleanup completed with %d warnings",
                    provider_name, len(cleanup_errors)
                )
            else:
                logger.debug("%s cleaned up successfully", provider_name)

        except (OSError, ConnectionError) as e:
            logger.error("%s cleanup error: %s", provider_name, e, exc_info=True)

    def _create_config_validation(
        self,
        required_fields: List[str],
        config_source: Any = None
    ) -> Callable[[], None]:
        """
        Создает функцию валидации конфигурации.

        Args:
            required_fields: Список обязательных полей
            config_source: Источник конфигурации (self.config, self._google_config, etc.)

        Returns:
            Функция валидации конфигурации
        """
        def validate_config():
            config = config_source or getattr(self, 'config', {})
            missing_fields = []

            for field in required_fields:
                if isinstance(config, dict):
                    if not config.get(field):
                        missing_fields.append(field)
                else:
                    if not getattr(config, field, None):
                        missing_fields.append(field)

            if missing_fields:
                raise VoiceConfigurationError(
                    f"Missing required configuration fields: {', '.join(missing_fields)}"
                )

        return validate_config

    def _create_api_key_validation(
        self,
        api_key_attr: str = 'api_key',
        error_message: str = None
    ) -> Callable[[], None]:
        """
        Создает функцию валидации API ключа.

        Args:
            api_key_attr: Имя атрибута с API ключом
            error_message: Пользовательское сообщение об ошибке

        Returns:
            Функция валидации API ключа
        """
        def validate_api_key():
            api_key = getattr(self, api_key_attr, None)
            if not api_key:
                provider_name = getattr(self, 'provider_name', 'STT Provider')
                message = error_message or f"{provider_name} API key не настроен"
                raise ProviderNotAvailableError(provider_name, message)

        return validate_api_key

    def _create_client_cleanup(
        self,
        client_attrs: List[str]
    ) -> Callable[[], Awaitable[None]]:
        """
        Создает функцию очистки клиентов.

        Args:
            client_attrs: Список имен атрибутов клиентов для очистки

        Returns:
            Функция очистки клиентов
        """
        async def cleanup_clients():
            for attr_name in client_attrs:
                client = getattr(self, attr_name, None)
                if client:
                    try:
                        # Попытка корректного закрытия
                        if hasattr(client, 'close'):
                            await client.close()
                        elif hasattr(client, 'aclose'):
                            await client.aclose()
                    except (OSError, ConnectionError, TimeoutError) as e:
                        logger.debug("Client cleanup warning for %s: %s", attr_name, e)
                    finally:
                        setattr(self, attr_name, None)

        return cleanup_clients


# Экспорт для использования в провайдерах
__all__ = ['STTInitializationMixin']
