"""
Provider Manager - Управление провайдерами
Phase 3.5.3.2 - Модульное разделение orchestrator
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from .types import IProviderManager
from ..interfaces import FullSTTProvider, FullTTSProvider, ProviderType
from ..config import VoiceConfig
from ...providers.enhanced_factory import EnhancedVoiceProviderFactory


logger = logging.getLogger(__name__)


class VoiceProviderManager(IProviderManager):
    """
    Manages voice provider access and health checks

    Responsibilities:
    - Provider instance creation and caching
    - Enhanced Factory integration
    - Legacy provider fallback
    - Provider health monitoring
    """

    def __init__(
        self,
        stt_providers: Optional[Dict[ProviderType, FullSTTProvider]] = None,
        tts_providers: Optional[Dict[ProviderType, FullTTSProvider]] = None,
        enhanced_factory: Optional[EnhancedVoiceProviderFactory] = None,
        config: Optional[VoiceConfig] = None
    ):
        self._stt_providers = stt_providers or {}
        self._tts_providers = tts_providers or {}
        self._enhanced_factory = enhanced_factory
        self._config = config

        # Enhanced Factory caches
        self._factory_stt_cache: Dict[str, FullSTTProvider] = {}
        self._factory_tts_cache: Dict[str, FullTTSProvider] = {}

    async def get_stt_provider(self, provider_name: str) -> Optional[FullSTTProvider]:
        """
        Get STT provider using Enhanced Factory или legacy providers

        Args:
            provider_name: Provider name (openai, google, yandex)

        Returns:
            STT provider instance or None
        """
        # Enhanced Factory mode
        if self._enhanced_factory:
            if provider_name not in self._factory_stt_cache:
                try:
                    # Use unified create_provider method with STT suffix if needed
                    stt_provider_name = provider_name if provider_name.endswith('_stt') else f"{provider_name}_stt"
                    provider = await self._enhanced_factory.create_provider(stt_provider_name, {})
                    if provider:
                        self._factory_stt_cache[provider_name] = provider
                        logger.debug("Created STT provider %s via Enhanced Factory", provider_name)
                    return provider
                except Exception as e:
                    logger.error("Failed to create STT provider %s: %s", provider_name, e)
                    return None
            else:
                return self._factory_stt_cache[provider_name]

        # Legacy mode
        provider_type = self._name_to_provider_type(provider_name)
        return self._stt_providers.get(provider_type) if provider_type else None

    async def get_tts_provider(self, provider_name: str) -> Optional[FullTTSProvider]:
        """
        Get TTS provider using Enhanced Factory или legacy providers

        Args:
            provider_name: Provider name (openai, google, yandex)

        Returns:
            TTS provider instance or None
        """
        # Enhanced Factory mode
        if self._enhanced_factory:
            if provider_name not in self._factory_tts_cache:
                try:
                    # Use unified create_provider method with TTS suffix if needed
                    tts_provider_name = provider_name if provider_name.endswith('_tts') else f"{provider_name}_tts"
                    provider = await self._enhanced_factory.create_provider(tts_provider_name, {})
                    if provider:
                        self._factory_tts_cache[provider_name] = provider
                        logger.debug("Created TTS provider %s via Enhanced Factory", provider_name)
                    return provider
                except Exception as e:
                    logger.error("Failed to create TTS provider %s: %s", provider_name, e)
                    return None
            else:
                return self._factory_tts_cache[provider_name]

        # Legacy mode
        provider_type = self._name_to_provider_type(provider_name)
        return self._tts_providers.get(provider_type) if provider_type else None

    async def health_check_provider(
        self,
        provider_name: str,
        provider_category: str
    ) -> Dict[str, Any]:
        """
        Health check provider using Enhanced Factory

        Args:
            provider_name: Provider name
            provider_category: 'stt' or 'tts'

        Returns:
            Health check results
        """
        if self._enhanced_factory:
            return await self._enhanced_factory.health_check_provider(provider_name)
        else:
            # Legacy health check
            if provider_category == 'stt':
                provider = await self.get_stt_provider(provider_name)
            else:
                provider = await self.get_tts_provider(provider_name)

            if provider:
                # Basic health check - provider exists
                return {
                    "status": "healthy",
                    "provider": provider_name,
                    "mode": "legacy",
                    "last_check": datetime.utcnow().isoformat()
                }
            else:
                return {
                    "status": "unhealthy",
                    "provider": provider_name,
                    "mode": "legacy",
                    "error": "Provider not found"
                }

    def _name_to_provider_type(self, provider_name: str) -> Optional[ProviderType]:
        """Convert provider name to ProviderType enum"""
        name_mapping = {
            "openai": ProviderType.OPENAI,
            "google": ProviderType.GOOGLE,
            "yandex": ProviderType.YANDEX
        }
        return name_mapping.get(provider_name.lower())

    def _provider_type_to_name(self, provider_type: ProviderType) -> str:
        """Convert ProviderType enum to string name"""
        type_mapping = {
            ProviderType.OPENAI: "openai",
            ProviderType.GOOGLE: "google",
            ProviderType.YANDEX: "yandex"
        }
        return type_mapping.get(provider_type, "unknown")

    @property
    def stt_providers(self) -> Dict[str, FullSTTProvider]:
        """Get available STT providers (compatibility property)"""
        if self._enhanced_factory:
            return self._factory_stt_cache
        else:
            return {self._provider_type_to_name(k): v for k, v in self._stt_providers.items()}

    @property
    def tts_providers(self) -> Dict[str, FullTTSProvider]:
        """Get available TTS providers (compatibility property)"""
        if self._enhanced_factory:
            return self._factory_tts_cache
        else:
            return {self._provider_type_to_name(k): v for k, v in self._tts_providers.items()}

    async def get_available_providers(self, provider_type: str) -> List[str]:
        """Get list of available provider names"""
        if provider_type.lower() == 'stt':
            return list(self.stt_providers.keys())
        elif provider_type.lower() == 'tts':
            return list(self.tts_providers.keys())
        else:
            return []
