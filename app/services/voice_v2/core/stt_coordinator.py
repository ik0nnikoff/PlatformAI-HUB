"""
STT Coordinator - Phase 3.1.5
Централизованное управление STT провайдерами с fallback логикой
"""

from typing import List, Dict, Any, Optional
import logging
import asyncio

from ..providers.stt.base_stt import BaseSTTProvider
from .stt_factory import STTProviderFactory
from .exceptions import (
    ProviderNotAvailableError,
    TranscriptionError,
    ConfigurationError
)

logger = logging.getLogger(__name__)


class STTCoordinator:
    """Координатор STT провайдеров с fallback логикой"""

    def __init__(self):
        self.providers: List[BaseSTTProvider] = []
        self.is_initialized = False
        self.config: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()

    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Инициализирует координатор с провайдерами

        Args:
            config: Конфигурация с списком провайдеров
        """
        async with self._lock:
            if self.is_initialized:
                logger.warning("STT Coordinator already initialized")
                return

            self.config = config
            providers_config = config.get('providers', [])

            if not providers_config:
                raise ConfigurationError("No STT providers configured")

            # Создаем провайдеры по приоритету
            created_providers = []
            for provider_config in sorted(providers_config, key=lambda x: x.get('priority', 999)):
                if not provider_config.get('enabled', True):
                    continue

                provider_name = provider_config.get('provider')
                if not provider_name:
                    logger.warning("Provider config missing 'provider' field")
                    continue

                try:
                    provider = await STTProviderFactory.create_provider(
                        provider_name, provider_config
                    )
                    created_providers.append(provider)
                    logger.info(f"Initialized STT provider: {provider_name}")

                except Exception as e:
                    logger.error(f"Failed to initialize provider {provider_name}: {e}")
                    continue

            if not created_providers:
                raise ProviderNotAvailableError("No STT providers could be initialized")

            self.providers = created_providers
            self.is_initialized = True

            logger.info(f"STT Coordinator initialized with {len(self.providers)} providers")

    async def transcribe(self, audio_path: str, language: str = None) -> str:
        """
        Выполняет транскрипцию с fallback между провайдерами

        Args:
            audio_path: Путь к аудио файлу
            language: Язык для распознавания

        Returns:
            Текст транскрипции

        Raises:
            ProviderNotAvailableError: Если нет доступных провайдеров
            TranscriptionError: Если все провайдеры вернули ошибку
        """
        if not self.is_initialized:
            raise ConfigurationError("STT Coordinator not initialized")

        # Используем язык из конфигурации по умолчанию
        if language is None:
            language = self.config.get('default_language', 'ru-RU')

        available_providers = [p for p in self.providers if p.is_available]

        if not available_providers:
            raise ProviderNotAvailableError("No available STT providers")

        last_error = None

        # Пробуем провайдеры по порядку приоритета
        for provider in available_providers:
            try:
                logger.debug(f"Attempting transcription with {provider.provider_name}")
                result = await self._transcribe_with_provider(provider, audio_path, language)

                if result:
                    logger.info(f"Successful transcription with {provider.provider_name}")
                    return result

            except Exception as e:
                logger.warning(f"Provider {provider.provider_name} failed: {e}")
                last_error = e
                continue

        # Если все провайдеры упали
        error_msg = f"All STT providers failed. Last error: {last_error}"
        logger.error(error_msg)
        raise TranscriptionError(error_msg)

    async def _transcribe_with_provider(
        self,
        provider: BaseSTTProvider,
        audio_path: str,
        language: str
    ) -> str:
        """Выполняет транскрипцию с конкретным провайдером"""
        return await provider.transcribe(audio_path, language=language)

    async def cleanup(self) -> None:
        """Освобождает ресурсы всех провайдеров"""
        async with self._lock:
            if not self.is_initialized:
                return

            cleanup_tasks = []
            for provider in self.providers:
                if hasattr(provider, 'cleanup'):
                    cleanup_tasks.append(provider.cleanup())

            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)

            self.providers.clear()
            self.is_initialized = False

            logger.info("STT Coordinator cleaned up")

    def get_status(self) -> Dict[str, Any]:
        """Возвращает статус координатора и провайдеров"""
        return {
            'initialized': self.is_initialized,
            'total_providers': len(self.providers),
            'available_providers': len([p for p in self.providers if p.is_available]),
            'providers': [
                {
                    'name': p.provider_name,
                    'available': p.is_available,
                    'capabilities': p.get_capabilities()
                }
                for p in self.providers
            ]
        }

    async def health_check(self) -> Dict[str, Any]:
        """Проверяет состояние всех провайдеров"""
        if not self.is_initialized:
            return {'status': 'not_initialized'}

        provider_health = {}
        for provider in self.providers:
            try:
                health = await provider.health_check()
                provider_health[provider.provider_name] = {
                    'status': 'healthy' if health else 'unhealthy',
                    'available': provider.is_available
                }
            except Exception as e:
                provider_health[provider.provider_name] = {
                    'status': 'error',
                    'error': str(e),
                    'available': False
                }

        return {
            'status': 'initialized',
            'providers': provider_health
        }
