"""
STT Manager for Voice v2 Service
Handles Speech-to-Text operations with provider fallback logic
"""

import asyncio
import logging
from typing import Optional, List

from ..interfaces import FullSTTProvider, ProviderType
from ..schemas import STTRequest, STTResponse, VoiceOperation
from ..exceptions import VoiceServiceError
from .types import ISTTManager

logger = logging.getLogger(__name__)


class VoiceSTTManager(ISTTManager):
    """Manages STT operations with fallback logic"""

    def __init__(self, provider_manager, cache_manager, metrics_collector, connection_manager):
        """
        Initialize STT manager

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
        """Initialize STT manager"""
        logger.debug("Initializing VoiceSTTManager")
        self._initialized = True
        logger.info("VoiceSTTManager initialized successfully")

    async def cleanup(self) -> None:
        """Cleanup STT manager resources"""
        logger.debug("Cleaning up VoiceSTTManager")
        self._initialized = False
        logger.info("VoiceSTTManager cleanup completed")

    async def transcribe_audio(self, request: STTRequest) -> STTResponse:
        """
        Transcribe audio using STT providers with fallback

        Args:
            request: STT request with audio file and parameters

        Returns:
            STTResponse with transcription result

        Raises:
            VoiceServiceError: If manager not initialized or all providers fail
        """
        if not self._initialized:
            raise VoiceServiceError("STT Manager not initialized")

        last_error = None

        # Check cache first
        cached_result = await self._get_cached_stt_result(request)
        if cached_result:
            # If cached_result is already an STTResponse, return it
            if isinstance(cached_result, STTResponse):
                return cached_result
            # If it's a string, wrap it in STTResponse
            return STTResponse(
                text=cached_result,
                provider="cache",
                processing_time=0.1
            )

        # Get provider chain for fallback
        provider_chain = self._get_stt_provider_chain(None)

        # Try each provider in order
        for provider_type in provider_chain:
            if not self._is_provider_available(provider_type):
                continue

            # Get provider
            provider = await self._get_stt_provider(provider_type)
            if not provider:
                logger.warning("STT provider %s not available", provider_type)
                continue

            try:
                logger.info("Attempting STT with provider %s", provider_type)

                # Execute with performance tracking
                response = await self._execute_with_performance_tracking(
                    provider.transcribe_audio,
                    VoiceOperation.STT,
                    provider_type,
                    request
                )

                # Record success and cache result
                self._record_provider_success(provider_type)
                await self._cache_stt_result(request, response.text)

                logger.info("STT successful with provider %s", provider_type)
                return response

            except Exception as e:
                last_error = e
                logger.warning("STT provider %s failed: %s", provider_type, e)
                await self._handle_provider_error(provider_type)
                continue

        # All providers failed
        error_msg = f"All STT providers failed. Last error: {last_error}"
        logger.error(error_msg)
        raise VoiceServiceError(error_msg)

    async def _get_cached_stt_result(self, request: STTRequest) -> Optional[str]:
        """Get cached STT result"""
        if not self._cache_manager:
            return None

        try:
            # Create cache key from request
            cache_key = f"stt:{request.audio_file}:{request.language or 'auto'}"
            return await self._cache_manager.get_cached_result(cache_key)
        except Exception as e:
            logger.warning("Failed to get cached STT result: %s", e)
            return None

    async def _cache_stt_result(self, request: STTRequest, result: str) -> None:
        """Cache STT result"""
        if not self._cache_manager:
            return

        try:
            cache_key = f"stt:{request.audio_file}:{request.language or 'auto'}"
            await self._cache_manager.cache_result(cache_key, result, ttl=86400)  # 24 hours
        except Exception as e:
            logger.warning("Failed to cache STT result: %s", e)

    def _get_stt_provider_chain(self, preferred_provider: Optional[str]) -> List[ProviderType]:
        """Get STT provider chain with fallback order"""
        return self._provider_manager.get_stt_provider_chain(preferred_provider)

    async def _get_stt_provider(self, provider_type: ProviderType) -> Optional[FullSTTProvider]:
        """Get STT provider instance"""
        provider_name = self._provider_type_to_name(provider_type)
        return await self._provider_manager.get_stt_provider(provider_name)

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
        """Get STT manager health status"""
        return {
            "initialized": self._initialized,
            "available_providers": [
                provider_type.value
                for provider_type in ProviderType
                if self._is_provider_available(provider_type)
            ],
            "component": "stt_manager"
        }
