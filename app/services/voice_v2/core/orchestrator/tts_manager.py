"""
TTS Manager for Voice v2 Service
Handles Text-to-Speech operations with provider fallback logic
"""

import asyncio
import logging
from typing import Optional, List

from ..interfaces import FullTTSProvider, ProviderType
from ..schemas import TTSRequest, TTSResponse, VoiceOperation, AudioFormat
from ..exceptions import VoiceServiceError
from .types import ITTSManager

logger = logging.getLogger(__name__)


class VoiceTTSManager(ITTSManager):
    """Manages TTS operations with fallback logic"""

    def __init__(self, provider_manager, cache_manager, metrics_collector, connection_manager):
        """
        Initialize TTS manager

        Args:
            provider_manager: Provider management instance
            cache_manager: Cache management instance
            metrics_collector: Metrics collection instance
            connection_manager: Connection management instance
        """
        self._provider_manager = provider_manager
        self._cache_manager = cache_manager
        self._metrics_collector = metrics_collector
        self._connection_manager = connection_manager
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize TTS manager"""
        logger.debug("Initializing VoiceTTSManager")
        self._initialized = True
        logger.info("VoiceTTSManager initialized successfully")

    async def cleanup(self) -> None:
        """Cleanup TTS manager resources"""
        logger.debug("Cleaning up VoiceTTSManager")
        self._initialized = False
        logger.info("VoiceTTSManager cleanup completed")

    async def synthesize_speech(self, request: TTSRequest) -> TTSResponse:
        """
        Synthesize speech using TTS providers with fallback

        Args:
            request: TTS request with text and parameters

        Returns:
            TTSResponse with audio URL and metadata

        Raises:
            VoiceServiceError: If manager not initialized or all providers fail
        """
        if not self._initialized:
            raise VoiceServiceError("TTS Manager not initialized")

        last_error = None

        # Check cache first
        cached_result = await self._get_cached_tts_result(request)
        if cached_result:
            # Return cached result as TTSResponse
            return TTSResponse(
                audio_data=cached_result,
                format=AudioFormat.WAV,  # Default format
                provider="cached",
                processing_time=0.1
            )

        # Get provider chain for fallback (no preferred provider specified)
        provider_chain = self._get_tts_provider_chain(None)

        # Try each provider in order
        for provider_type in provider_chain:
            if not self._is_provider_available(provider_type):
                continue

            # Get provider
            provider = await self._get_tts_provider(provider_type)
            if not provider:
                logger.warning("TTS provider %s not available", provider_type)
                continue

            try:
                logger.info("Attempting TTS with provider %s", provider_type)

                # Execute with performance tracking
                response = await self._execute_with_performance_tracking(
                    provider.synthesize_speech,
                    VoiceOperation.TTS,
                    provider_type,
                    request
                )

                # Record success and cache result
                self._record_provider_success(provider_type)
                await self._cache_tts_result(request, response.audio_data)

                logger.info("TTS successful with provider %s", provider_type)
                return response

            except Exception as e:
                last_error = e
                logger.warning("TTS provider %s failed: %s", provider_type, e)
                await self._handle_provider_error(provider_type)
                continue

        # All providers failed
        error_msg = f"All TTS providers failed. Last error: {last_error}"
        logger.error(error_msg)
        raise VoiceServiceError(error_msg)

    async def _get_cached_tts_result(self, request: TTSRequest) -> Optional[bytes]:
        """Get cached TTS result"""
        if not self._cache_manager:
            return None

        try:
            # Create cache key from request
            cache_key = f"tts:{request.text}:{request.voice}:{request.format}"
            return await self._cache_manager.get_cached_result(cache_key)
        except Exception as e:
            logger.warning("Failed to get cached TTS result: %s", e)
            return None

    async def _cache_tts_result(self, request: TTSRequest, result: bytes) -> None:
        """Cache TTS result"""
        if not self._cache_manager:
            return

        try:
            cache_key = f"tts:{request.text}:{request.voice}:{request.format}"
            await self._cache_manager.cache_result(cache_key, result, ttl=86400)  # 24 hours
        except Exception as e:
            logger.warning("Failed to cache TTS result: %s", e)

    def _get_tts_provider_chain(self, preferred_provider: Optional[str]) -> List[ProviderType]:
        """Get TTS provider chain with fallback order"""
        return self._provider_manager.get_tts_provider_chain(preferred_provider)

    async def _get_tts_provider(self, provider_type: ProviderType) -> Optional[FullTTSProvider]:
        """Get TTS provider instance"""
        provider_name = self._provider_type_to_name(provider_type)
        return await self._provider_manager.get_tts_provider(provider_name)

    def _provider_type_to_name(self, provider_type: ProviderType) -> str:
        """Convert provider type to name"""
        return self._provider_manager.provider_type_to_name(provider_type)

    def _is_provider_available(self, provider_type: ProviderType) -> bool:
        """Check if provider is available"""
        return self._provider_manager.is_provider_available(provider_type)

    def _record_provider_success(self, provider_type: ProviderType) -> None:
        """Record provider success for metrics"""
        if self._metrics_collector:
            self._metrics_collector.record_provider_success(provider_type)

    async def _handle_provider_error(self, provider_type: ProviderType) -> None:
        """Handle provider error"""
        if self._metrics_collector:
            self._metrics_collector.record_provider_error(provider_type)

        # Update connection manager about the error
        if self._connection_manager:
            await self._connection_manager.handle_provider_error(provider_type)

    async def _execute_with_performance_tracking(
        self,
        operation_func,
        operation_type: VoiceOperation,
        provider_type: ProviderType,
        *args,
        **kwargs
    ):
        """Execute operation with performance tracking"""
        if not self._metrics_collector:
            return await operation_func(*args, **kwargs)

        start_time = asyncio.get_event_loop().time()
        try:
            result = await operation_func(*args, **kwargs)
            processing_time = asyncio.get_event_loop().time() - start_time

            # Record metrics
            self._metrics_collector.record_operation_metrics(
                operation_type,
                provider_type,
                processing_time,
                success=True
            )

            return result
        except Exception as e:
            processing_time = asyncio.get_event_loop().time() - start_time

            # Record failed metrics
            self._metrics_collector.record_operation_metrics(
                operation_type,
                provider_type,
                processing_time,
                success=False
            )

            raise e

    async def get_health_status(self) -> dict:
        """Get TTS manager health status"""
        return {
            "initialized": self._initialized,
            "available_providers": [
                provider_type.value
                for provider_type in ProviderType
                if self._is_provider_available(provider_type)
            ],
            "component": "tts_manager"
        }
