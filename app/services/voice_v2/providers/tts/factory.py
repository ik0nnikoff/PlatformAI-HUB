"""
TTS Provider Factory

Centralized factory for creating TTS provider instances.
Supports dynamic provider loading and configuration-based initialization.

Phase 3.5.2.4: Created to fix missing TTS factory imports in tests
"""

from typing import Dict, Any, List
import logging

from app.services.voice_v2.core.exceptions import (
    VoiceConfigurationError,
    ProviderNotFoundError
)
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.tts.openai_tts import OpenAITTSProvider
from app.services.voice_v2.providers.tts.google_tts import GoogleTTSProvider
from app.services.voice_v2.providers.tts.yandex_tts import YandexTTSProvider

logger = logging.getLogger(__name__)


class TTSProviderFactory:
    """
    Factory class for creating TTS provider instances

    Supports:
    - Dynamic provider loading
    - Configuration validation
    - Provider registry management
    """

    _PROVIDER_REGISTRY = {
        "openai": OpenAITTSProvider,
        "google": GoogleTTSProvider,
        "yandex": YandexTTSProvider,
    }

    @classmethod
    def create_provider(
        cls,
        provider_name: str,
        config: Dict[str, Any]
    ) -> BaseTTSProvider:
        """
        Create TTS provider instance

        Args:
            provider_name: Name of the provider (openai, google, yandex)
            config: Provider configuration

        Returns:
            Configured TTS provider instance

        Raises:
            ProviderNotFoundError: If provider not registered
            VoiceConfigurationError: If configuration invalid
        """
        if provider_name not in cls._PROVIDER_REGISTRY:
            available = ", ".join(cls._PROVIDER_REGISTRY.keys())
            raise ProviderNotFoundError(
                f"TTS provider '{provider_name}' not found. "
                f"Available providers: {available}"
            )

        try:
            provider_class = cls._PROVIDER_REGISTRY[provider_name]
            return provider_class(config)

        except Exception as e:
            raise VoiceConfigurationError(
                f"Failed to create TTS provider '{provider_name}': {e}"
            )

    @classmethod
    def get_available_providers(cls) -> List[str]:
        """Get list of available TTS provider names"""
        return list(cls._PROVIDER_REGISTRY.keys())

    @classmethod
    def register_provider(
        cls,
        name: str,
        provider_class: type
    ) -> None:
        """
        Register new TTS provider

        Args:
            name: Provider name
            provider_class: Provider class (must inherit from BaseTTSProvider)
        """
        if not issubclass(provider_class, BaseTTSProvider):
            raise VoiceConfigurationError(
                f"Provider class must inherit from BaseTTSProvider"
            )

        cls._PROVIDER_REGISTRY[name] = provider_class
        logger.info("Registered TTS provider: %s", name)


# Global factory instance for backward compatibility
tts_factory = TTSProviderFactory()
