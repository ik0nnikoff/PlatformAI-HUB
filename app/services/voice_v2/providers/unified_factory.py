"""
Simplified Voice Provider Factory - Phase 3.1.1 Implementation

Unified factory for STT and TTS provider creation.
Replaces complex enterprise factory patterns with streamlined approach.
"""

import logging
from typing import Dict, Any, Optional

from ..providers.stt.yandex_stt import YandexSTTProvider
from ..providers.stt.google_stt import GoogleSTTProvider
from ..providers.stt.openai_stt import OpenAISTTProvider
from ..providers.tts.yandex_tts import YandexTTSProvider
from ..providers.tts.google_tts import GoogleTTSProvider
from ..providers.tts.openai_tts import OpenAITTSProvider
from ..core.interfaces import ProviderType
from .stt.base_stt import BaseSTTProvider
from .tts.base_tts import BaseTTSProvider

logger = logging.getLogger(__name__)


class VoiceProviderFactory:
    """
    Simplified factory for creating voice providers.

    Consolidates 5 factory files (737 lines) into single factory (~150 lines).
    Preserves essential functionality while removing enterprise over-engineering.
    """

    # Provider mappings
    STT_PROVIDERS = {
        ProviderType.YANDEX: YandexSTTProvider,
        ProviderType.GOOGLE: GoogleSTTProvider,
        ProviderType.OPENAI: OpenAISTTProvider,
    }

    TTS_PROVIDERS = {
        ProviderType.YANDEX: YandexTTSProvider,
        ProviderType.GOOGLE: GoogleTTSProvider,
        ProviderType.OPENAI: OpenAITTSProvider,
    }

    def __init__(self):
        """Initialize simplified factory."""
        self._stt_cache: Dict[ProviderType, BaseSTTProvider] = {}
        self._tts_cache: Dict[ProviderType, BaseTTSProvider] = {}
        logger.info("VoiceProviderFactory initialized")

    async def create_stt_provider(
        self,
        provider_type: ProviderType,
        config: Optional[Dict[str, Any]] = None
    ) -> BaseSTTProvider:
        """
        Create STT provider with caching.

        Args:
            provider_type: Type of STT provider to create
            config: Optional provider configuration

        Returns:
            Configured STT provider instance
        """
        # Return cached provider if available
        if provider_type in self._stt_cache:
            return self._stt_cache[provider_type]

        # Get provider class
        provider_class = self.STT_PROVIDERS.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown STT provider type: {provider_type}")

        try:
            # Create provider instance
            provider = provider_class()

            # Initialize with config if provided
            if config and hasattr(provider, 'configure'):
                await provider.configure(config)

            # Cache and return
            self._stt_cache[provider_type] = provider
            logger.info(f"Created STT provider: {provider_type}")
            return provider

        except Exception as e:
            logger.error(f"Failed to create STT provider {provider_type}: {e}")
            raise

    async def create_tts_provider(
        self,
        provider_type: ProviderType,
        config: Optional[Dict[str, Any]] = None
    ) -> BaseTTSProvider:
        """
        Create TTS provider with caching.

        Args:
            provider_type: Type of TTS provider to create
            config: Optional provider configuration

        Returns:
            Configured TTS provider instance
        """
        # Return cached provider if available
        if provider_type in self._tts_cache:
            return self._tts_cache[provider_type]

        # Get provider class
        provider_class = self.TTS_PROVIDERS.get(provider_type)
        if not provider_class:
            raise ValueError(f"Unknown TTS provider type: {provider_type}")

        try:
            # Create provider instance
            provider = provider_class()

            # Initialize with config if provided
            if config and hasattr(provider, 'configure'):
                await provider.configure(config)

            # Cache and return
            self._tts_cache[provider_type] = provider
            logger.info(f"Created TTS provider: {provider_type}")
            return provider

        except Exception as e:
            logger.error(f"Failed to create TTS provider {provider_type}: {e}")
            raise

    def get_available_stt_providers(self) -> list[ProviderType]:
        """Get list of available STT provider types."""
        return list(self.STT_PROVIDERS.keys())

    def get_available_tts_providers(self) -> list[ProviderType]:
        """Get list of available TTS provider types."""
        return list(self.TTS_PROVIDERS.keys())

    async def cleanup(self) -> None:
        """Cleanup cached providers."""
        logger.info("Cleaning up VoiceProviderFactory")

        # Cleanup STT providers
        for provider in self._stt_cache.values():
            if hasattr(provider, 'cleanup'):
                try:
                    await provider.cleanup()
                except Exception as e:
                    logger.warning(f"Error cleaning up STT provider: {e}")

        # Cleanup TTS providers
        for provider in self._tts_cache.values():
            if hasattr(provider, 'cleanup'):
                try:
                    await provider.cleanup()
                except Exception as e:
                    logger.warning(f"Error cleaning up TTS provider: {e}")

        self._stt_cache.clear()
        self._tts_cache.clear()
        logger.info("VoiceProviderFactory cleanup completed")


# Backward compatibility alias
EnhancedVoiceProviderFactory = VoiceProviderFactory


__all__ = ['VoiceProviderFactory', 'EnhancedVoiceProviderFactory']
