"""
Base Orchestrator Implementation
Main VoiceServiceOrchestrator class
"""

import logging
from typing import Dict, Optional, Any

from ..interfaces import (
    FullSTTProvider, FullTTSProvider, CacheInterface, FileManagerInterface,
    ProviderType
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
        logger.info(f"Orchestrator initialized with {mode} mode")

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
            logger.error(f"Failed to initialize orchestrator: {e}", exc_info=True)
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
                logger.info(f"Validated {stt_count} STT and {tts_count} TTS providers")

    async def _perform_health_checks(self) -> None:
        """Perform health checks on all configured providers"""
        # Check STT providers
        for provider_type, provider in self._stt_providers.items():
            if hasattr(provider, 'health_check'):
                try:
                    is_healthy = await provider.health_check()
                    if not is_healthy:
                        logger.warning(f"STT provider {provider_type} failed health check")
                except Exception as e:
                    logger.warning(f"STT provider {provider_type} health check error: {e}")

        # Check TTS providers
        for provider_type, provider in self._tts_providers.items():
            if hasattr(provider, 'health_check'):
                try:
                    is_healthy = await provider.health_check()
                    if not is_healthy:
                        logger.warning(f"TTS provider {provider_type} failed health check")
                except Exception as e:
                    logger.warning(f"TTS provider {provider_type} health check error: {e}")

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
        """Delegate to modular STT manager when available"""
        # This is a compatibility layer - for now use legacy implementation
        if not self._initialized:
            raise VoiceServiceError("Orchestrator not initialized")

        # TODO: Integrate with modular VoiceSTTManager
        # For now, provide basic implementation
        raise NotImplementedError("STT integration with modular managers pending")

    async def synthesize_speech(self, request: TTSRequest) -> TTSResponse:
        """Delegate to modular TTS manager when available"""
        # This is a compatibility layer - for now use legacy implementation
        if not self._initialized:
            raise VoiceServiceError("Orchestrator not initialized")

        # TODO: Integrate with modular VoiceTTSManager
        # For now, provide basic implementation
        raise NotImplementedError("TTS integration with modular managers pending")
