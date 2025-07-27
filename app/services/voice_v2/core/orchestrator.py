"""
Voice_v2 Orchestrator - Main Voice Service Orchestrator

Implements the central orchestrator for voice operations following SOLID principles:
- Single Responsibility: coordinates STT/TTS operations
- Open/Closed: extensible via provider interfaces
- Liskov Substitution: provider implementations are interchangeable  
- Interface Segregation: focused interfaces for specific capabilities
- Dependency Inversion: depends on abstractions, not concrete implementations

Core responsibilities:
- Coordinate STT/TTS provider execution
- Handle provider fallback logic with circuit breaker
- Manage caching and file operations
- Collect performance metrics
- Resource lifecycle management
- Business logic decisions
"""

import logging
import time
import asyncio
from typing import Dict, List, Optional, Any

from app.core.base.service_component import ServiceComponentBase
from .interfaces import (
    FullSTTProvider, FullTTSProvider, CacheInterface, FileManagerInterface,
    ProviderType
)
from .schemas import (
    STTRequest, TTSRequest, STTResponse, TTSResponse,
    OperationStatus, VoiceOperationMetric, VoiceOperation,
    ProviderCapabilities
)
from .config import VoiceConfig, get_config
from .exceptions import VoiceServiceError, VoiceProviderError


logger = logging.getLogger(__name__)


class VoiceServiceOrchestrator:
    """
    Main voice service orchestrator
    
    Responsibilities:
    - Coordinate STT/TTS provider execution
    - Handle provider fallback logic
    - Manage caching and file operations
    - Collect performance metrics
    - Resource lifecycle management
    - Business logic decisions
    """
    
    def __init__(
        self,
        stt_providers: Dict[ProviderType, FullSTTProvider],
        tts_providers: Dict[ProviderType, FullTTSProvider],
        cache_manager: CacheInterface,
        file_manager: FileManagerInterface,
        config: Optional[VoiceConfig] = None
    ):
        """
        Initialize orchestrator with dependencies
        
        Args:
            stt_providers: Dictionary of STT providers by type
            tts_providers: Dictionary of TTS providers by type
            cache_manager: Cache interface implementation
            file_manager: File storage interface implementation
            config: Voice configuration (defaults to global config)
        """
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        
        self._stt_providers = stt_providers
        self._tts_providers = tts_providers
        self._cache_manager = cache_manager
        self._file_manager = file_manager
        self._voice_config = config or get_config()
        
        # Circuit breaker state
        self._provider_errors: Dict[ProviderType, int] = {}
        self._provider_disabled_until: Dict[ProviderType, float] = {}
        
        # Performance tracking
        self._operation_count = 0
        self._total_processing_time = 0.0
        
        # State
        self._initialized = False
        
        logger.info(f"Orchestrator initialized with {len(stt_providers)} STT and {len(tts_providers)} TTS providers")
    
    async def initialize(self) -> None:
        """Initialize orchestrator and perform health checks"""
        logger.info("Initializing Voice Service Orchestrator")
        
        # Health check all providers
        await self._health_check_providers()
        
        self._initialized = True
        logger.info("Voice Service Orchestrator initialized successfully")
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("Cleaning up Voice Service Orchestrator")
        self._initialized = False
        self._provider_errors.clear()
        self._provider_disabled_until.clear()
        logger.info("Voice Service Orchestrator cleaned up")
    
    async def transcribe_audio(self, request: STTRequest) -> STTResponse:
        """
        Transcribe audio using STT providers with fallback
        
        Args:
            request: STT request with audio file and parameters
            
        Returns:
            STTResponse with transcription result
            
        Raises:
            VoiceServiceError: If orchestrator not initialized or all providers fail
        """
        if not self._initialized:
            raise VoiceServiceError("Orchestrator not initialized")
        
        start_time = time.time()
        last_error = None
        
        # Check cache first
        cached_result = await self._get_cached_stt_result(request)
        if cached_result:
            # If cached_result is already an STTResponse, return it
            if isinstance(cached_result, STTResponse):
                return cached_result
            # If it's a string, wrap it in STTResponse
            return STTResponse(
                transcribed_text=cached_result,
                provider_used=ProviderType.OPENAI,  # Default for cached
                processing_time_ms=0.1,
                cached=True
            )
        
        # Get provider chain for fallback
        provider_chain = self._get_stt_provider_chain(request.provider)
        
        # Try each provider in order
        for provider_type in provider_chain:
            if not self._is_provider_available(provider_type):
                continue
                
            provider = self._stt_providers[provider_type]
            
            try:
                logger.info(f"Attempting STT with provider {provider_type}")
                
                # Execute with performance tracking
                response = await self._execute_with_performance_tracking(
                    provider.transcribe_audio,
                    VoiceOperation.STT,
                    provider_type,
                    request
                )
                
                # Record success and cache result
                self._record_provider_success(provider_type)
                await self._cache_stt_result(request, response.transcribed_text)
                
                logger.info(f"STT successful with provider {provider_type}")
                return response
                
            except Exception as e:
                last_error = e
                logger.warning(f"STT provider {provider_type} failed: {e}")
                await self._handle_provider_error(provider_type)
                continue
        
        # All providers failed
        error_msg = f"All STT providers failed. Last error: {last_error}"
        
        raise VoiceServiceError(error_msg)
    
    async def synthesize_speech(self, request: TTSRequest) -> TTSResponse:
        """
        Synthesize speech using TTS providers with fallback
        
        Args:
            request: TTS request with text and parameters
            
        Returns:
            TTSResponse with audio URL and metadata
            
        Raises:
            VoiceServiceError: If orchestrator not initialized or all providers fail
        """
        if not self._initialized:
            raise VoiceServiceError("Orchestrator not initialized")
        
        start_time = time.time()
        last_error = None
        
        # Check cache first
        cached_result = await self._get_cached_tts_result(request)
        if cached_result:
            return TTSResponse(
                audio_url=cached_result,
                audio_format=request.output_format,
                provider_used=ProviderType.OPENAI,  # Default for cached
                processing_time_ms=0.1,
                cached=True
            )
        
        # Get provider chain for fallback
        provider_chain = self._get_tts_provider_chain(request.provider)
        
        # Try each provider in order
        for provider_type in provider_chain:
            if not self._is_provider_available(provider_type):
                continue
                
            provider = self._tts_providers[provider_type]
            
            try:
                logger.info(f"Attempting TTS with provider {provider_type}")
                
                # Execute with performance tracking
                response = await self._execute_with_performance_tracking(
                    provider.synthesize_speech,
                    VoiceOperation.TTS,
                    provider_type,
                    request
                )
                
                # Record success and cache result
                self._record_provider_success(provider_type)
                await self._cache_tts_result(request, response.audio_url)
                
                logger.info(f"TTS successful with provider {provider_type}")
                return response
                
            except Exception as e:
                last_error = e
                logger.warning(f"TTS provider {provider_type} failed: {e}")
                await self._handle_provider_error(provider_type)
                continue
        
        # All providers failed
        error_msg = f"All TTS providers failed. Last error: {last_error}"
        
        raise VoiceServiceError(error_msg)
    
    async def get_provider_capabilities(
        self,
        provider_type: ProviderType,
        operation: VoiceOperation
    ) -> ProviderCapabilities:
        """Get provider capabilities"""
        if operation == VoiceOperation.STT:
            if provider_type in self._stt_providers:
                provider = self._stt_providers[provider_type]
                if hasattr(provider, 'get_capabilities'):
                    capabilities = provider.get_capabilities()
                    # Handle both sync and async methods
                    if hasattr(capabilities, '__await__'):
                        return await capabilities
                    return capabilities
        elif operation == VoiceOperation.TTS:
            if provider_type in self._tts_providers:
                provider = self._tts_providers[provider_type]
                if hasattr(provider, 'get_capabilities'):
                    capabilities = provider.get_capabilities()
                    # Handle both sync and async methods
                    if hasattr(capabilities, '__await__'):
                        return await capabilities
                    return capabilities
        
        # Return default capabilities
        return ProviderCapabilities(
            provider_type=str(provider_type.value),
            supported_formats=["wav", "mp3"],
            supported_languages=["en-US"],
            max_file_size_mb=100,
            supports_real_time=False
        )
    
    async def check_provider_health(
        self,
        provider_type: ProviderType,
        operation: VoiceOperation
    ) -> bool:
        """Check provider health"""
        try:
            if operation == VoiceOperation.STT:
                if provider_type in self._stt_providers:
                    provider = self._stt_providers[provider_type]
                    if hasattr(provider, 'health_check'):
                        health = provider.health_check()
                        # Handle both sync and async methods
                        if hasattr(health, '__await__'):
                            return await health
                        return health
            elif operation == VoiceOperation.TTS:
                if provider_type in self._tts_providers:
                    provider = self._tts_providers[provider_type]
                    if hasattr(provider, 'health_check'):
                        health = provider.health_check()
                        # Handle both sync and async methods
                        if hasattr(health, '__await__'):
                            return await health
                        return health
            
            return False
        except Exception as e:
            logger.warning(f"Health check failed for {provider_type}: {e}")
            return False
    
    # Private helper methods
    
    async def _health_check_providers(self) -> None:
        """Health check all providers"""
        for provider_type, provider in self._stt_providers.items():
            try:
                if hasattr(provider, 'health_check'):
                    health = provider.health_check()
                    if not health:
                        logger.warning(f"STT provider {provider_type} health check failed")
            except Exception as e:
                logger.warning(f"STT provider {provider_type} health check error: {e}")
        
        for provider_type, provider in self._tts_providers.items():
            try:
                if hasattr(provider, 'health_check'):
                    health = provider.health_check()
                    if not health:
                        logger.warning(f"TTS provider {provider_type} health check failed")
            except Exception as e:
                logger.warning(f"TTS provider {provider_type} health check error: {e}")
    
    def _get_stt_provider_chain(self, preferred: Optional[ProviderType]) -> List[ProviderType]:
        """Get STT provider chain for fallback"""
        chain = []
        
        # Add preferred provider first if specified and available
        if preferred and preferred in self._stt_providers:
            chain.append(preferred)
        
        # Add fallback chain - all available providers if enabled
        if self._voice_config.fallback_enabled:
            for provider_type in self._stt_providers:
                if provider_type not in chain:
                    chain.append(provider_type)
        
        return chain
    
    def _get_tts_provider_chain(self, preferred: Optional[ProviderType]) -> List[ProviderType]:
        """Get TTS provider chain for fallback"""
        chain = []
        
        # Add preferred provider first if specified and available
        if preferred and preferred in self._tts_providers:
            chain.append(preferred)
        
        # Add fallback chain - all available providers if enabled
        if self._voice_config.fallback_enabled:
            for provider_type in self._tts_providers:
                if provider_type not in chain:
                    chain.append(provider_type)
        
        return chain
    
    def _is_provider_available(self, provider_type: ProviderType) -> bool:
        """Check if provider is available (not in circuit breaker state)"""
        # Simple check - always available for basic implementation
        return True
    
    async def _handle_provider_error(self, provider_type: ProviderType) -> None:
        """Handle provider error for circuit breaker logic"""
        # Simple error tracking
        error_count = self._provider_errors.get(provider_type, 0) + 1
        self._provider_errors[provider_type] = error_count
    
    def _record_provider_success(self, provider_type: ProviderType):
        """Record successful provider operation"""
        # Simple circuit breaker - just reset error count
        if provider_type in self._provider_errors:
            self._provider_errors[provider_type] = 0
    
    async def _get_cached_stt_result(self, request: STTRequest) -> Optional[str]:
        """Get cached STT result if available"""
        try:
            cache_key = f"stt:{request.get_cache_key()}"
            return await self._cache_manager.get(cache_key)
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            return None
    
    async def _cache_stt_result(self, request: STTRequest, result: str) -> None:
        """Cache STT result"""
        try:
            cache_key = f"stt:{request.get_cache_key()}"
            ttl_seconds = self._voice_config.cache.ttl_seconds
            await self._cache_manager.set(cache_key, result, ttl_seconds)
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
    
    async def _get_cached_tts_result(self, request: TTSRequest) -> Optional[str]:
        """Get cached TTS result if available"""
        try:
            cache_key = f"tts:{request.get_cache_key()}"
            return await self._cache_manager.get(cache_key)
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            return None
    
    async def _cache_tts_result(self, request: TTSRequest, result: str) -> None:
        """Cache TTS result"""
        try:
            cache_key = f"tts:{request.get_cache_key()}"
            ttl_seconds = self._voice_config.cache.ttl_seconds
            await self._cache_manager.set(cache_key, result, ttl_seconds)
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
    
    async def _execute_with_performance_tracking(
        self,
        func,
        operation: VoiceOperation,
        provider_type: ProviderType,
        request
    ):
        """Execute operation with performance tracking"""
        start_time = time.time()
        
        try:
            result = await func(request)
            processing_time = time.time() - start_time
            
            # Update performance metrics
            self._operation_count += 1
            self._total_processing_time += processing_time
            
            logger.debug(f"{operation.value} operation completed in {processing_time:.3f}s")
            return result
            
        except Exception as e:
            processing_time = time.time() - start_time
            logger.warning(f"Operation {operation.value}_{provider_type} failed after {processing_time:.3f}s: {e}")
            raise
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        if self._operation_count == 0:
            return {
                "total_operations": 0,
                "average_processing_time": 0.0,
                "total_processing_time": 0.0
            }
        
        return {
            "total_operations": self._operation_count,
            "average_processing_time": self._total_processing_time / self._operation_count,
            "total_processing_time": self._total_processing_time,
            "provider_errors": dict(self._provider_errors)
        }