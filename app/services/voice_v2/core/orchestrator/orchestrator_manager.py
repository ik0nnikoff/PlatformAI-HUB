"""
Base Orchestrator Manager for Voice v2 Service
Main coordination logic for voice operations with performance optimization integration
"""

import logging
from typing import Dict, Optional, Any

from ..interfaces import (
    FullSTTProvider, FullTTSProvider, CacheInterface, FileManagerInterface,
    ProviderType
)
from ..schemas import STTRequest, TTSRequest, STTResponse, TTSResponse
from ..config import VoiceConfig, get_config
from ..exceptions import VoiceServiceError
from ...providers.enhanced_factory import EnhancedVoiceProviderFactory
from ..performance_manager import VoicePerformanceManager, create_performance_manager
from .types import IOrchestratorManager
from .provider_manager import VoiceProviderManager
from .stt_manager import VoiceSTTManager
from .tts_manager import VoiceTTSManager

logger = logging.getLogger(__name__)


class VoiceOrchestratorManager(IOrchestratorManager):
    """
    Main orchestrator manager coordinating voice operations

    Delegates responsibilities to specialized managers:
    - VoiceProviderManager: Provider access and circuit breaker
    - VoiceSTTManager: STT operations with fallback
    - VoiceTTSManager: TTS operations with fallback
    """

    def __init__(
        self,
        stt_providers: Optional[Dict[ProviderType, FullSTTProvider]] = None,
        tts_providers: Optional[Dict[ProviderType, FullTTSProvider]] = None,
        cache_manager: Optional[CacheInterface] = None,
        file_manager: Optional[FileManagerInterface] = None,
        config: Optional[VoiceConfig] = None,
        enhanced_factory: Optional[EnhancedVoiceProviderFactory] = None,
        performance_manager: Optional[VoicePerformanceManager] = None
    ):
        """Initialize orchestrator manager with optional performance optimization"""
        logger.debug("Initializing VoiceOrchestratorManager")

        # Core dependencies
        self._cache_manager = cache_manager
        self._file_manager = file_manager
        self._voice_config = config or get_config()
        self._initialized = False

        # Performance optimization system
        self._performance_manager = performance_manager

        # Initialize provider system (Enhanced Factory or Legacy)
        self._initialize_provider_system(stt_providers, tts_providers, enhanced_factory)

        # Initialize state tracking
        self._initialize_state_tracking()

        # Initialize specialized managers
        self._initialize_managers()

        # Initialize performance system if enabled
        self._initialize_performance_system()

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

    def _initialize_managers(self) -> None:
        """Initialize specialized managers"""
        # Create provider manager
        self._provider_manager = VoiceProviderManager(
            stt_providers=self._stt_providers,
            tts_providers=self._tts_providers,
            enhanced_factory=self._enhanced_factory,
            config=self._voice_config
        )

        # Create STT manager
        self._stt_manager = VoiceSTTManager(
            provider_manager=self._provider_manager,
            cache_manager=self._cache_manager,
            metrics_collector=None,  # Will be set during initialization
            connection_manager=None   # Will be set during initialization
        )

        # Create TTS manager
        self._tts_manager = VoiceTTSManager(
            provider_manager=self._provider_manager,
            cache_manager=self._cache_manager,
            metrics_collector=None,  # Will be set during initialization
            connection_manager=None   # Will be set during initialization
        )

    def _initialize_performance_system(self) -> None:
        """Initialize performance optimization system if enabled and not provided"""
        if self._performance_manager is None:
            self._performance_manager = create_performance_manager()
            if self._performance_manager:
                logger.info("Performance optimization system initialized")
            else:
                logger.debug("Performance optimization disabled via configuration")

    @classmethod
    async def create_with_enhanced_factory(
        cls,
        factory_config: Dict[str, Any],
        cache_manager: CacheInterface,
        file_manager: FileManagerInterface,
        voice_config: Optional[VoiceConfig] = None
    ) -> "VoiceOrchestratorManager":
        """
        Factory method для создания orchestrator с Enhanced Factory

        Args:
            factory_config: Configuration для Enhanced Factory
            cache_manager: Cache interface implementation
            file_manager: File storage interface implementation
            voice_config: Voice configuration

        Returns:
            Configured VoiceOrchestratorManager instance
        """
        logger.info("Creating orchestrator manager with Enhanced Factory pattern")

        # Create Enhanced Factory
        enhanced_factory = EnhancedVoiceProviderFactory()
        # Factory will be initialized when first provider is requested

        # Create orchestrator manager instance
        orchestrator = cls(
            cache_manager=cache_manager,
            file_manager=file_manager,
            config=voice_config,
            enhanced_factory=enhanced_factory
        )

        await orchestrator.initialize()
        return orchestrator

    async def initialize(self) -> None:
        """Initialize orchestrator manager and all sub-managers"""
        if self._initialized:
            logger.warning("VoiceOrchestratorManager already initialized")
            return

        logger.info("Initializing VoiceOrchestratorManager")

        try:
            # Initialize provider manager
            await self._provider_manager.initialize()

            # Initialize STT manager
            await self._stt_manager.initialize()

            # Initialize TTS manager
            await self._tts_manager.initialize()

            # Initialize performance system if available
            if self._performance_manager:
                await self._performance_manager.initialize()
                logger.info("Performance optimization system activated")

            self._initialized = True
            logger.info("VoiceOrchestratorManager initialized successfully")

        except Exception as e:
            logger.error("Failed to initialize VoiceOrchestratorManager: %s", e)
            await self.cleanup()
            raise VoiceServiceError(f"Orchestrator initialization failed: {e}")

    async def cleanup(self) -> None:
        """Cleanup orchestrator manager and all sub-managers"""
        logger.info("Cleaning up VoiceOrchestratorManager")

        try:
            # Cleanup performance system first
            if hasattr(self, '_performance_manager') and self._performance_manager:
                await self._performance_manager.cleanup()

            # Cleanup managers
            if hasattr(self, '_tts_manager'):
                await self._tts_manager.cleanup()

            if hasattr(self, '_stt_manager'):
                await self._stt_manager.cleanup()

            if hasattr(self, '_provider_manager'):
                await self._provider_manager.cleanup()

            self._initialized = False
            logger.info("VoiceOrchestratorManager cleanup completed")

        except Exception as e:
            logger.error("Error during VoiceOrchestratorManager cleanup: %s", e)

    async def transcribe_audio(self, request: STTRequest) -> STTResponse:
        """
        Transcribe audio using STT manager

        Args:
            request: STT request with audio file and parameters

        Returns:
            STTResponse with transcription result
        """
        if not self._initialized:
            raise VoiceServiceError("Orchestrator Manager not initialized")

        return await self._stt_manager.transcribe_audio(request)

    async def synthesize_speech(self, request: TTSRequest) -> TTSResponse:
        """
        Synthesize speech using TTS manager

        Args:
            request: TTS request with text and parameters

        Returns:
            TTSResponse with audio data and metadata
        """
        if not self._initialized:
            raise VoiceServiceError("Orchestrator Manager not initialized")

        return await self._tts_manager.synthesize_speech(request)

    async def get_health_status(self) -> dict:
        """Get comprehensive health status"""
        if not self._initialized:
            return {
                "status": "not_initialized",
                "component": "orchestrator_manager"
            }

        try:
            # Get health status from all managers
            provider_health = await self._provider_manager.get_health_status()
            stt_health = await self._stt_manager.get_health_status()
            tts_health = await self._tts_manager.get_health_status()

            # Get performance system health if available
            performance_health = None
            if self._performance_manager:
                performance_health = await self._performance_manager.get_health_status()

            managers_health = {
                "provider_manager": provider_health,
                "stt_manager": stt_health,
                "tts_manager": tts_health
            }

            if performance_health:
                managers_health["performance_manager"] = performance_health

            return {
                "status": "healthy",
                "initialized": self._initialized,
                "operation_count": self._operation_count,
                "average_processing_time": (
                    self._total_processing_time / self._operation_count
                    if self._operation_count > 0 else 0
                ),
                "performance_enabled": self._performance_manager is not None,
                "managers": managers_health,
                "component": "orchestrator_manager"
            }
        except Exception as e:
            logger.error("Error getting health status: %s", e)
            return {
                "status": "error",
                "error": str(e),
                "component": "orchestrator_manager"
            }

    def get_operation_statistics(self) -> dict:
        """Get operation statistics"""
        return {
            "total_operations": self._operation_count,
            "total_processing_time": self._total_processing_time,
            "average_processing_time": (
                self._total_processing_time / self._operation_count
                if self._operation_count > 0 else 0
            )
        }

    @property
    def performance_manager(self) -> Optional[VoicePerformanceManager]:
        """Get performance manager instance if available"""
        return self._performance_manager

    @property
    def is_performance_enabled(self) -> bool:
        """Check if performance optimization is enabled"""
        return self._performance_manager is not None

    async def get_performance_metrics(self) -> Optional[Dict[str, Any]]:
        """Get performance metrics if performance system is enabled"""
        if self._performance_manager:
            return await self._performance_manager.get_metrics()
        return None
