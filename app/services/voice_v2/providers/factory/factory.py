"""
Enhanced Voice Provider Factory - Core Implementation

This module contains the main EnhancedVoiceProviderFactory class that was
extracted from the monolithic enhanced_factory.py file for better modularity.
"""

import logging
import importlib
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, timedelta

from .types import ProviderCategory, ProviderType, ProviderStatus
from .models import ProviderInfo, ProviderHealthInfo
from .interfaces import IEnhancedProviderFactory
from ..stt.base_stt import BaseSTTProvider
from ..tts.base_tts import BaseTTSProvider
from ..enhanced_connection_manager import (
    EnhancedConnectionManager,
    IConnectionManager
)
from ...core.exceptions import (
    VoiceServiceError,
    ConfigurationError,
    ProviderNotFoundError,
    ProviderNotAvailableError
)

logger = logging.getLogger(__name__)


class EnhancedVoiceProviderFactory(IEnhancedProviderFactory):
    """
    Enhanced provider factory с comprehensive provider management.

    Phase 3.3.1 Implementation Features:
    - Universal provider factory для STT и TTS
    - Dynamic provider loading через module paths
    - Configuration-based initialization с comprehensive validation
    - Provider registry management с metadata
    - Health monitoring и provider status tracking
    - Performance optimization через instance caching
    - Circuit breaker patterns для provider failures
    """

    def __init__(self, connection_manager: Optional[IConnectionManager] = None):
        """Initialize enhanced factory с connection management"""
        self._providers_registry: Dict[str, ProviderInfo] = {}
        self._provider_instances: Dict[str, Union[BaseSTTProvider, BaseTTSProvider]] = {}
        self._connection_manager = connection_manager or EnhancedConnectionManager()
        self._health_check_interval = timedelta(minutes=5)
        self._last_health_check: Dict[str, datetime] = {}
        self._initialized = False

        # Default provider configurations
        self._default_providers = {
            "openai_stt": ProviderInfo(
                name="openai_stt",
                category=ProviderCategory.STT,
                provider_type=ProviderType.OPENAI,
                module_path="app.services.voice_v2.providers.stt.openai_stt",
                class_name="OpenAISTTProvider",
                description="OpenAI Whisper STT Provider",
                version="1.0.0",
                dependencies=["openai"],
                priority=10
            ),
            "google_stt": ProviderInfo(
                name="google_stt",
                category=ProviderCategory.STT,
                provider_type=ProviderType.GOOGLE,
                module_path="app.services.voice_v2.providers.stt.google_stt",
                class_name="GoogleSTTProvider",
                description="Google Cloud Speech-to-Text Provider",
                version="1.0.0",
                dependencies=["google-cloud-speech"],
                priority=20
            ),
            "yandex_stt": ProviderInfo(
                name="yandex_stt",
                category=ProviderCategory.STT,
                provider_type=ProviderType.YANDEX,
                module_path="app.services.voice_v2.providers.stt.yandex_stt",
                class_name="YandexSTTProvider",
                description="Yandex SpeechKit STT Provider",
                version="1.0.0",
                dependencies=["aiohttp"],
                priority=30
            ),
            "openai_tts": ProviderInfo(
                name="openai_tts",
                category=ProviderCategory.TTS,
                provider_type=ProviderType.OPENAI,
                module_path="app.services.voice_v2.providers.tts.openai_tts",
                class_name="OpenAITTSProvider",
                description="OpenAI TTS Provider",
                version="1.0.0",
                dependencies=["openai"],
                priority=10
            ),
            "google_tts": ProviderInfo(
                name="google_tts",
                category=ProviderCategory.TTS,
                provider_type=ProviderType.GOOGLE,
                module_path="app.services.voice_v2.providers.tts.google_tts",
                class_name="GoogleTTSProvider",
                description="Google Cloud Text-to-Speech Provider",
                version="1.0.0",
                dependencies=["google-cloud-texttospeech"],
                priority=20
            ),
            "yandex_tts": ProviderInfo(
                name="yandex_tts",
                category=ProviderCategory.TTS,
                provider_type=ProviderType.YANDEX,
                module_path="app.services.voice_v2.providers.tts.yandex_tts",
                class_name="YandexTTSProvider",
                description="Yandex SpeechKit TTS Provider",
                version="1.0.0",
                dependencies=["aiohttp"],
                priority=30
            )
        }

    async def initialize(self):
        """Initialize factory с default providers"""
        if self._initialized:
            return

        logger.info("Initializing EnhancedVoiceProviderFactory...")

        # Register default providers
        for provider_info in self._default_providers.values():
            self.register_provider(provider_info)

        # Initialize connection manager
        await self._connection_manager.initialize()

        self._initialized = True
        logger.info("EnhancedVoiceProviderFactory initialized successfully")

    async def create_provider(
        self,
        provider_name: str,
        config: Dict[str, Any]
    ) -> Union[BaseSTTProvider, BaseTTSProvider]:
        """Create provider instance с enhanced error handling"""
        if not self._initialized:
            await self.initialize()

        if provider_name not in self._providers_registry:
            raise ProviderNotFoundError(f"Provider '{provider_name}' not found in registry")

        provider_info = self._providers_registry[provider_name]

        # Check if provider is enabled and healthy
        if not provider_info.enabled:
            raise ProviderNotAvailableError(f"Provider '{provider_name}' is disabled")

        # Check cached instance first
        if provider_name in self._provider_instances:
            instance = self._provider_instances[provider_name]
            if await self._is_provider_healthy(instance):
                return instance
            else:
                # Remove unhealthy instance
                del self._provider_instances[provider_name]

        try:
            # Dynamic module loading
            module = importlib.import_module(provider_info.module_path)
            provider_class = getattr(module, provider_info.class_name)

            # Create instance with configuration
            provider_instance = provider_class(
                provider_name=provider_name,
                config=config,
                connection_manager=self._connection_manager
            )

            # Validate instance
            await self._validate_provider_instance(provider_instance, provider_info)

            # Cache healthy instance
            self._provider_instances[provider_name] = provider_instance

            # Update health info
            provider_info.health_info.status = ProviderStatus.ACTIVE
            provider_info.health_info.last_check = datetime.utcnow()
            provider_info.health_info.error_message = None

            logger.info(f"Successfully created provider '{provider_name}'")
            return provider_instance

        except Exception as e:
            # Update health info on error
            provider_info.health_info.status = ProviderStatus.ERROR
            provider_info.health_info.last_check = datetime.utcnow()
            provider_info.health_info.error_message = str(e)

            logger.error(f"Failed to create provider '{provider_name}': {e}")
            raise VoiceServiceError(f"Failed to create provider '{provider_name}': {e}")

    def register_provider(self, provider_info: ProviderInfo) -> None:
        """Register new provider in registry"""
        self._providers_registry[provider_info.name] = provider_info
        logger.info(f"Registered provider '{provider_info.name}' ({provider_info.category.value})")

    def get_available_providers(
        self,
        category: Optional[ProviderCategory] = None,
        enabled_only: bool = True
    ) -> List[ProviderInfo]:
        """Get list of available providers с filtering"""
        providers = list(self._providers_registry.values())

        if category:
            providers = [p for p in providers if p.category == category]

        if enabled_only:
            providers = [p for p in providers if p.enabled]

        # Sort by priority (lower number = higher priority)
        providers.sort(key=lambda p: p.priority)

        return providers

    def get_provider_info(self, provider_name: str) -> Optional[ProviderInfo]:
        """Get provider metadata"""
        return self._providers_registry.get(provider_name)

    async def health_check(self, provider_name: Optional[str] = None) -> Dict[str, ProviderHealthInfo]:
        """Perform health check on providers"""
        if provider_name:
            providers_to_check = [provider_name] if provider_name in self._providers_registry else []
        else:
            providers_to_check = list(self._providers_registry.keys())

        health_results = {}

        for name in providers_to_check:
            provider_info = self._providers_registry[name]

            # Check if health check is needed
            last_check = self._last_health_check.get(name)
            if last_check and datetime.utcnow() - last_check < self._health_check_interval:
                health_results[name] = provider_info.health_info
                continue

            try:
                # Get or create provider instance for health check
                if name in self._provider_instances:
                    instance = self._provider_instances[name]
                else:
                    # Skip health check if no instance and provider is disabled
                    if not provider_info.enabled:
                        provider_info.health_info.status = ProviderStatus.DISABLED
                        health_results[name] = provider_info.health_info
                        continue

                    # Create minimal instance for health check
                    try:
                        instance = await self.create_provider(name, {})
                    except Exception as e:
                        provider_info.health_info.status = ProviderStatus.ERROR
                        provider_info.health_info.error_message = str(e)
                        health_results[name] = provider_info.health_info
                        continue

                # Perform health check
                is_healthy = await self._is_provider_healthy(instance)

                if is_healthy:
                    provider_info.health_info.status = ProviderStatus.ACTIVE
                    provider_info.health_info.error_message = None
                else:
                    provider_info.health_info.status = ProviderStatus.ERROR
                    provider_info.health_info.error_message = "Health check failed"

            except Exception as e:
                provider_info.health_info.status = ProviderStatus.ERROR
                provider_info.health_info.error_message = str(e)
                logger.error(f"Health check failed for provider '{name}': {e}")

            provider_info.health_info.last_check = datetime.utcnow()
            self._last_health_check[name] = datetime.utcnow()
            health_results[name] = provider_info.health_info

        return health_results

    async def _validate_provider_instance(
        self,
        instance: Union[BaseSTTProvider, BaseTTSProvider],
        provider_info: ProviderInfo
    ) -> None:
        """Validate created provider instance"""
        # Check if instance matches expected category
        if provider_info.category == ProviderCategory.STT:
            if not isinstance(instance, BaseSTTProvider):
                raise ConfigurationError(
                    f"Provider '{provider_info.name}' expected to be STT provider"
                )
        elif provider_info.category == ProviderCategory.TTS:
            if not isinstance(instance, BaseTTSProvider):
                raise ConfigurationError(
                    f"Provider '{provider_info.name}' expected to be TTS provider"
                )

        # Basic validation - instance should have provider_name and config
        if not hasattr(instance, 'provider_name'):
            raise ConfigurationError(
                f"Provider '{provider_info.name}' missing required 'provider_name' attribute"
            )

        if not hasattr(instance, 'config'):
            raise ConfigurationError(
                f"Provider '{provider_info.name}' missing required 'config' attribute"
            )

    async def _is_provider_healthy(self, provider: Union[BaseSTTProvider, BaseTTSProvider]) -> bool:
        """Check if provider instance is healthy"""
        try:
            # Simplified health check - just verify provider is callable
            return hasattr(provider, 'provider_name') and provider.enabled
        except Exception as e:
            logger.debug(f"Provider health check failed: {e}")
            return False

    async def cleanup(self) -> None:
        """Cleanup factory resources"""
        logger.info("Cleaning up EnhancedVoiceProviderFactory...")

        # Cleanup provider instances
        for provider_name, instance in self._provider_instances.items():
            try:
                if hasattr(instance, 'cleanup'):
                    await instance.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up provider '{provider_name}': {e}")

        self._provider_instances.clear()

        # Cleanup connection manager
        if self._connection_manager:
            await self._connection_manager.cleanup()

        self._initialized = False
        logger.info("EnhancedVoiceProviderFactory cleanup completed")

    # Дополнительные методы для тестирования

    def _get_default_config_for_provider(self, provider_name: str) -> Dict[str, Any]:
        """Get default configuration for a provider"""
        if provider_name not in self._default_providers:
            return {}

        provider_info = self._default_providers[provider_name]

        # Default configs by provider type
        if provider_info.provider_type == ProviderType.OPENAI:
            if provider_info.category == ProviderCategory.STT:
                return {
                    "model": "whisper-1",
                    "timeout": 30.0,
                    "max_retries": 3,
                    "api_key": ""
                }
            else:  # TTS
                return {
                    "model": "tts-1",
                    "voice": "alloy",
                    "timeout": 30.0,
                    "api_key": ""
                }
        elif provider_info.provider_type == ProviderType.GOOGLE:
            if provider_info.category == ProviderCategory.STT:
                return {
                    "timeout": 30.0,
                    "max_retries": 3,
                    "language_code": "ru-RU",
                    "credentials_path": "",
                    "project_id": ""
                }
            else:  # TTS
                return {
                    "timeout": 30.0,
                    "language_code": "ru-RU",
                    "credentials_path": "",
                    "project_id": ""
                }
        elif provider_info.provider_type == ProviderType.YANDEX:
            if provider_info.category == ProviderCategory.STT:
                return {
                    "timeout": 30.0,
                    "max_retries": 3,
                    "language": "ru",
                    "api_key": "",
                    "folder_id": ""
                }
            else:  # TTS
                return {
                    "timeout": 30.0,
                    "language": "ru",
                    "api_key": "",
                    "folder_id": ""
                }

        return {}

    def get_connection_manager(self) -> IConnectionManager:
        """Get connection manager instance"""
        return self._connection_manager

    async def shutdown(self) -> None:
        """Shutdown factory and cleanup resources"""
        await self.cleanup()

    async def _create_provider_instance(
        self,
        provider_info: ProviderInfo,
        config: Dict[str, Any]
    ) -> Union[BaseSTTProvider, BaseTTSProvider]:
        """Create provider instance - для тестирования"""
        # Dynamic module loading
        module = importlib.import_module(provider_info.module_path)
        provider_class = getattr(module, provider_info.class_name)

        # Create instance with provider name and configuration
        provider_instance = provider_class(
            provider_name=provider_info.name,
            config=config,
            connection_manager=self._connection_manager
        )

        return provider_instance
