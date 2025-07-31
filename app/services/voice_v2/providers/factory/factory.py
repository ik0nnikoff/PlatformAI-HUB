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

        # Check if provider has required API keys (skip if not available)
        provider_type = provider_info.provider_type.value.lower()
        if not self._has_required_api_keys(provider_type):
            logger.info(f"Skipping provider '{provider_name}' - missing required API keys")
            raise ProviderNotAvailableError(f"Provider '{provider_name}' missing required API keys")

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

            # Create instance with configuration and provider info
            provider_instance = provider_class(
                provider_name=provider_name,
                config=config,
                priority=provider_info.priority,
                enabled=provider_info.enabled,
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

    async def create_stt_provider(self, provider_type: str) -> Optional[BaseSTTProvider]:
        """Create STT provider instance by type"""
        if not self._initialized:
            await self.initialize()

        # Check if API keys are available for provider
        if not self._has_required_api_keys(provider_type):
            logger.info(f"Skipping {provider_type} STT provider - missing API keys")
            return None

        # Map provider type to provider name
        provider_name_map = {
            "openai": "openai_stt",
            "google": "google_stt",
            "yandex": "yandex_stt"
        }

        provider_name = provider_name_map.get(provider_type.lower())
        if not provider_name:
            logger.warning(f"Unknown STT provider type: {provider_type}")
            return None

        try:
            # Get default config for provider
            config = self._get_default_config_for_provider(provider_name)

            # Create provider using generic create_provider method
            provider = await self.create_provider(provider_name, config)

            # Validate it's actually an STT provider
            if not isinstance(provider, BaseSTTProvider):
                logger.error(f"Provider {provider_name} is not an STT provider")
                return None

            return provider

        except Exception as e:
            logger.error(f"Failed to create STT provider {provider_type}: {e}")
            return None

    async def create_tts_provider(self, provider_type: str) -> Optional[BaseTTSProvider]:
        """Create TTS provider instance by type"""
        if not self._initialized:
            await self.initialize()

        # Check if API keys are available for provider
        if not self._has_required_api_keys(provider_type):
            logger.info(f"Skipping {provider_type} TTS provider - missing API keys")
            return None

        # Map provider type to provider name
        provider_name_map = {
            "openai": "openai_tts",
            "google": "google_tts",
            "yandex": "yandex_tts"
        }

        provider_name = provider_name_map.get(provider_type.lower())
        if not provider_name:
            logger.warning(f"Unknown TTS provider type: {provider_type}")
            return None

        try:
            # Get default config for provider
            config = self._get_default_config_for_provider(provider_name)

            # Create provider using generic create_provider method
            provider = await self.create_provider(provider_name, config)

            # Validate it's actually a TTS provider
            if not isinstance(provider, BaseTTSProvider):
                logger.error(f"Provider {provider_name} is not a TTS provider")
                return None

            return provider

        except Exception as e:
            logger.error(f"Failed to create TTS provider {provider_type}: {e}")
            return None

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
        """Get default configuration for a provider with real API keys"""
        if provider_name not in self._default_providers:
            return {}

        provider_info = self._default_providers[provider_name]

        # Map provider type to config generator
        if provider_info.provider_type == ProviderType.OPENAI:
            return self._get_openai_config(provider_info)
        elif provider_info.provider_type == ProviderType.GOOGLE:
            return self._get_google_config(provider_info)
        elif provider_info.provider_type == ProviderType.YANDEX:
            return self._get_yandex_config(provider_info)

        return {}

    def _get_openai_config(self, provider_info) -> Dict[str, Any]:
        """Get OpenAI provider configuration"""
        from app.core.config import settings
        openai_key = settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else ""

        if provider_info.category == ProviderCategory.STT:
            return {
                "model": "whisper-1",
                "timeout": 30.0,
                "max_retries": 3,
                "api_key": openai_key
            }
        else:  # TTS
            return {
                "model": "tts-1",
                "voice": "alloy",
                "timeout": 30.0,
                "api_key": openai_key
            }

    def _get_google_config(self, provider_info) -> Dict[str, Any]:
        """Get Google provider configuration"""
        from app.core.config import settings

        base_config = {
            "timeout": 30.0,
            "language_code": "ru-RU",
            "credentials_path": settings.GOOGLE_APPLICATION_CREDENTIALS or "",
            "project_id": settings.GOOGLE_CLOUD_PROJECT_ID or ""
        }

        if provider_info.category == ProviderCategory.STT:
            base_config["max_retries"] = 3

        return base_config

    def _get_yandex_config(self, provider_info) -> Dict[str, Any]:
        """Get Yandex provider configuration"""
        from app.core.config import settings
        yandex_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else ""

        base_config = {
            "timeout": 30.0,
            "language": "ru",
            "api_key": yandex_key,
            "folder_id": settings.YANDEX_FOLDER_ID or ""
        }

        if provider_info.category == ProviderCategory.STT:
            base_config["max_retries"] = 3

        return base_config

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

    def _has_required_api_keys(self, provider_type: str) -> bool:
        """Check if provider has required API keys configured"""
        from app.core.config import settings

        provider_type = provider_type.lower()

        if provider_type == "openai":
            return settings.OPENAI_API_KEY is not None
        elif provider_type == "google":
            return (settings.GOOGLE_APPLICATION_CREDENTIALS is not None and
                   settings.GOOGLE_CLOUD_PROJECT_ID is not None)
        elif provider_type == "yandex":
            return settings.YANDEX_API_KEY is not None
        else:
            logger.warning(f"Unknown provider type for API key check: {provider_type}")
            return False
