"""
Base Orchestrator Implementation
Main VoiceServiceOrchestrator class
"""

import logging
from typing import Dict, Optional, Any

from ..interfaces import (
    FullSTTProvider, FullTTSProvider, CacheInterface, FileManagerInterface,
    ProviderType, AudioFormat
)
from ..schemas import (
    STTRequest, TTSRequest, STTResponse, TTSResponse
)
from ..config import VoiceConfig, get_config
from ..exceptions import VoiceServiceError

from ...providers.enhanced_factory import EnhancedVoiceProviderFactory


logger = logging.getLogger(__name__)


class VoiceServiceOrchestrator:
    """
    Main voice service orchestrator

    Coordinates voice operations using specialized managers:
    - VoiceProviderManager: Provider access and circuit breaker
    - VoiceOperationManager: Performance tracking
    - VoiceFileManager: File operations and storage
    """

    def __init__(
        self,
        stt_providers: Optional[Dict[ProviderType, FullSTTProvider]] = None,
        tts_providers: Optional[Dict[ProviderType, FullTTSProvider]] = None,
        cache_manager: Optional[CacheInterface] = None,
        file_manager: Optional[FileManagerInterface] = None,
        config: Optional[VoiceConfig] = None,
        enhanced_factory: Optional[EnhancedVoiceProviderFactory] = None
    ):
        """
        Initialize orchestrator with dependencies

        Args:
            stt_providers: Dictionary of STT providers by type (legacy mode)
            tts_providers: Dictionary of TTS providers by type (legacy mode)
            cache_manager: Cache interface implementation
            file_manager: File storage interface implementation
            config: Voice configuration (defaults to global config)
            enhanced_factory: Enhanced factory for dynamic provider creation (recommended)
        """
        self.logger = logging.getLogger(f"{self.__class__.__name__}")

        # Initialize core components
        self._initialize_core_dependencies(cache_manager, file_manager, config)
        self._initialize_provider_system(stt_providers, tts_providers, enhanced_factory)
        self._initialize_state_tracking()

        # Log initialization mode
        mode = "Enhanced Factory" if enhanced_factory else "Legacy"
        logger.info("Orchestrator initialized with %s mode", mode)

    def _initialize_core_dependencies(
        self,
        cache_manager: Optional[CacheInterface],
        file_manager: Optional[FileManagerInterface],
        config: Optional[VoiceConfig]
    ) -> None:
        """Initialize core dependencies"""
        self._cache_manager = cache_manager
        self._file_manager = file_manager
        self._voice_config = config or get_config()
        self._initialized = False

    def _initialize_provider_system(
        self,
        stt_providers: Optional[Dict[ProviderType, FullSTTProvider]],
        tts_providers: Optional[Dict[ProviderType, FullTTSProvider]],
        enhanced_factory: Optional[EnhancedVoiceProviderFactory]
    ) -> None:
        """Initialize provider system (Enhanced Factory or Legacy)"""
        # Enhanced Factory Mode (recommended)
        self._enhanced_factory = enhanced_factory

        # Legacy mode support (backward compatibility)
        self._stt_providers = stt_providers or {}
        self._tts_providers = tts_providers or {}

        # Enhanced Factory provider cache
        self._factory_stt_cache: Dict[str, FullSTTProvider] = {}
        self._factory_tts_cache: Dict[str, FullTTSProvider] = {}

    def _initialize_state_tracking(self) -> None:
        """Initialize state and performance tracking"""
        # Circuit breaker state
        self._provider_errors: Dict[ProviderType, int] = {}
        self._provider_disabled_until: Dict[ProviderType, float] = {}

        # Performance tracking
        self._operation_count = 0
        self._total_processing_time = 0.0

    async def initialize(self) -> None:
        """
        Initialize the orchestrator

        Sets up providers and ensures all dependencies are ready.
        This should be called before using any voice operations.
        """
        if self._initialized:
            return

        try:
            # Initialize cache if provided
            if self._cache_manager:
                await self._cache_manager.initialize()
                logger.info("Cache manager initialized")

            # Validate providers
            self._validate_providers()

            # Perform health checks on providers
            await self._perform_health_checks()

            self._initialized = True
            logger.info("Voice service orchestrator initialized successfully")

        except Exception as e:
            logger.error("Failed to initialize orchestrator: %s", e, exc_info=True)
            raise VoiceServiceError(f"Orchestrator initialization failed: {e}") from e

    async def cleanup(self) -> None:
        """Cleanup resources"""
        logger.info("Cleaning up Voice Service Orchestrator")
        self._initialized = False
        self._provider_errors.clear()
        self._provider_disabled_until.clear()
        logger.info("Voice Service Orchestrator cleaned up")

    def _validate_providers(self) -> None:
        """Validate provider configuration"""
        if self._enhanced_factory:
            logger.info("Using Enhanced Factory - providers will be created on demand")
        else:
            stt_count = len(self._stt_providers)
            tts_count = len(self._tts_providers)
            if stt_count == 0 and tts_count == 0:
                logger.warning("No providers configured - operations may fail")
            else:
                logger.info("Validated %s STT and %s TTS providers", stt_count, tts_count)

    async def _perform_health_checks(self) -> None:
        """Perform health checks on all configured providers"""
        # Check STT providers
        for provider_type, provider in self._stt_providers.items():
            if hasattr(provider, 'health_check'):
                try:
                    is_healthy = await provider.health_check()
                    if not is_healthy:
                        logger.warning("STT provider %s failed health check", provider_type)
                except Exception as e:
                    logger.warning("STT provider %s health check error: %s", provider_type, e)

        # Check TTS providers
        for provider_type, provider in self._tts_providers.items():
            if hasattr(provider, 'health_check'):
                try:
                    is_healthy = await provider.health_check()
                    if not is_healthy:
                        logger.warning("TTS provider %s failed health check", provider_type)
                except Exception as e:
                    logger.warning("TTS provider %s health check error: %s", provider_type, e)

    @classmethod
    async def create_with_enhanced_factory(
        cls,
        factory_config: Dict[str, Any],
        cache_manager: CacheInterface,
        file_manager: FileManagerInterface,
        voice_config: Optional[VoiceConfig] = None
    ) -> "VoiceServiceOrchestrator":
        """
        Factory method для создания orchestrator с Enhanced Factory

        Args:
            factory_config: Configuration для Enhanced Factory
            cache_manager: Cache interface implementation
            file_manager: File storage interface implementation
            voice_config: Voice configuration

        Returns:
            Configured VoiceServiceOrchestrator instance
        """
        logger.info("Creating orchestrator with Enhanced Factory pattern")

        # Create Enhanced Factory
        enhanced_factory = EnhancedVoiceProviderFactory()
        # Factory will be initialized when first provider is requested

        # Create orchestrator instance
        orchestrator = cls(
            cache_manager=cache_manager,
            file_manager=file_manager,
            config=voice_config,
            enhanced_factory=enhanced_factory
        )

        await orchestrator.initialize()
        return orchestrator

    async def transcribe_audio(self, request: STTRequest) -> STTResponse:
        """
        Core STT transcription method - Phase 4.6.1 Implementation

        Converts audio to text using available STT providers with fallback chain.
        This is the main entry point for all STT operations in voice_v2.

        Args:
            request: STT request with audio data and settings

        Returns:
            STTResponse with transcribed text and metadata

        Raises:
            VoiceServiceError: If orchestrator not initialized or all providers fail
        """
        if not self._initialized:
            raise VoiceServiceError("Orchestrator not initialized")

        # Use modular managers if available, otherwise fallback to legacy
        if hasattr(self, '_stt_manager') and self._stt_manager:
            return await self._stt_manager.transcribe_audio(request)

        # Fallback to Enhanced Factory approach
        return await self._transcribe_with_enhanced_factory(request)

    async def synthesize_speech(self, request: TTSRequest) -> TTSResponse:
        """
        Core TTS synthesis method - Phase 4.6.1 Implementation

        Converts text to speech using available TTS providers with fallback chain.
        This is the main entry point for all TTS operations in voice_v2.

        Args:
            request: TTS request with text and voice settings

        Returns:
            TTSResponse with audio data and metadata

        Raises:
            VoiceServiceError: If orchestrator not initialized or all providers fail
        """
        if not self._initialized:
            raise VoiceServiceError("Orchestrator not initialized")

        # Use modular managers if available, otherwise fallback to legacy
        if hasattr(self, '_tts_manager') and self._tts_manager:
            return await self._tts_manager.synthesize_speech(request)

        # Fallback to Enhanced Factory approach
        return await self._synthesize_with_enhanced_factory(request)

    async def _transcribe_with_enhanced_factory(self, request: STTRequest) -> STTResponse:
        """Fallback STT implementation using Enhanced Factory"""
        if not self._enhanced_factory:
            raise VoiceServiceError("No STT implementation available (Enhanced Factory required)")

        # Get preferred provider chain (priority order: OpenAI, Google, Yandex)
        provider_types = ["openai", "google", "yandex"]
        last_error = None

        for provider_type in provider_types:
            try:
                # Get or create provider instance
                provider = await self._get_or_create_stt_provider(provider_type)
                if not provider:
                    continue

                # Attempt transcription
                result = await provider.transcribe_audio(request)
                logger.info("STT successful with provider %s", provider_type)
                return STTResponse(
                    text=result.text,
                    provider=provider_type,
                    processing_time=result.processing_time or 0.0,
                    language=result.language_detected,
                    confidence=result.confidence
                )

            except Exception as e:
                last_error = e
                logger.warning("STT provider %s failed: %s", provider_type, e)
                continue

        # All providers failed
        error_msg = f"All STT providers failed. Last error: {last_error}"
        logger.error(error_msg)
        raise VoiceServiceError(error_msg)

    async def _synthesize_with_enhanced_factory(self, request: TTSRequest) -> TTSResponse:
        """Fallback TTS implementation using Enhanced Factory"""
        if not self._enhanced_factory:
            raise VoiceServiceError("No TTS implementation available (Enhanced Factory required)")

        # Get preferred provider chain (priority order: OpenAI, Google, Yandex)
        provider_types = ["openai", "google", "yandex"]
        last_error = None

        for provider_type in provider_types:
            try:
                # Get or create provider instance
                provider = await self._get_or_create_tts_provider(provider_type)
                if not provider:
                    continue

                # Attempt synthesis
                result = await provider.synthesize_speech(request)
                logger.info("TTS successful with provider %s", provider_type)

                # Convert TTSResult to TTSResponse
                return await self._convert_tts_result_to_response(result, provider_type)

            except Exception as e:
                last_error = e
                logger.warning("TTS provider %s failed: %s", provider_type, e)
                continue

        # All providers failed
        error_msg = f"All TTS providers failed. Last error: {last_error}"
        logger.error(error_msg)
        raise VoiceServiceError(error_msg)

    async def _convert_tts_result_to_response(self, result, provider_type: str) -> TTSResponse:
        """Convert TTSResult to TTSResponse with audio data handling"""
        audio_data = await self._extract_audio_data(result)

        return TTSResponse(
            audio_data=audio_data,
            format=AudioFormat.MP3,  # Default format
            provider=provider_type,
            processing_time=result.processing_time or 0.0,
            duration=result.audio_duration,
            sample_rate=22050  # Default sample rate
        )

    async def _extract_audio_data(self, result) -> bytes:
        """Extract audio data from TTS result"""
        # Direct audio data available
        if hasattr(result, 'audio_data') and result.audio_data:
            return result.audio_data

        # Download audio from URL
        if hasattr(result, 'audio_url') and result.audio_url:
            return await self._download_audio_from_url(result.audio_url)

        return b""

    async def _download_audio_from_url(self, audio_url: str) -> bytes:
        """Download audio data from URL"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.debug("Downloaded %d bytes from %s", len(audio_data), audio_url)
                        return audio_data
                    else:
                        logger.warning("Failed to download audio: HTTP %d", response.status)
        except Exception as download_error:
            logger.warning("Failed to download audio from %s: %s", audio_url, download_error)

        return b""

    async def _get_or_create_stt_provider(self, provider_type: str):
        """Get or create STT provider using Enhanced Factory"""
        return await self._get_or_create_provider(
            provider_type,
            self._factory_stt_cache,
            self._enhanced_factory.create_stt_provider
        )

    async def _get_or_create_tts_provider(self, provider_type: str):
        """Get or create TTS provider using Enhanced Factory"""
        return await self._get_or_create_provider(
            provider_type,
            self._factory_tts_cache,
            self._enhanced_factory.create_tts_provider
        )

    async def _get_or_create_provider(self, provider_type: str, cache: dict, factory_method):
        """Generic method to get or create provider with caching"""
        if provider_type in cache:
            return cache[provider_type]

        return await self._create_and_cache_provider(provider_type, cache, factory_method)

    async def _create_and_cache_provider(self, provider_type: str, cache: dict, factory_method):
        """Create, initialize, and cache a provider"""
        try:
            provider = await factory_method(provider_type)
            if provider:
                await self._initialize_provider_if_needed(provider)
                cache[provider_type] = provider
                logger.debug("Created provider %s", provider_type)
            return provider
        except Exception as e:
            logger.error("Failed to create provider %s: %s", provider_type, e)
            return None

    async def _initialize_provider_if_needed(self, provider):
        """Initialize provider if it has an initialize method"""
        if hasattr(provider, 'initialize'):
            await provider.initialize()
