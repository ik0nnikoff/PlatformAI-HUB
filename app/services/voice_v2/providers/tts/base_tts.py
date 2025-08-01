"""
TTS Base Provider для Voice_v2 - Phase 3.5.2.2 Enhanced with RetryMixin

Применяет архитектурные принципы и добавляет RetryMixin integration:
- LSP compliance (Phase_1_3_1_architecture_review.md)
- SOLID principles (Phase_1_2_2_solid_principles.md)
- Performance patterns (Phase_1_2_3_performance_optimization.md)
- RetryMixin для centralized retry configuration
- ConnectionManager integration support

SOLID Principles Implementation:
- Single Responsibility: Только TTS base functionality
- Open/Closed: Extensible via inheritance, closed for modification
- Liskov Substitution: All providers fully interchangeable
- Interface Segregation: Minimal interface, focused on TTS
- Dependency Inversion: Depends on abstractions, not concretions
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from app.services.voice_v2.core.schemas import TTSRequest
from .models import TTSResult, TTSCapabilities
from ...core.exceptions import VoiceServiceError, ProviderNotAvailableError, AudioProcessingError
from ...utils.validators import ConfigurationValidator
from ..retry_mixin import RetryMixin

logger = logging.getLogger(__name__)


class BaseTTSProvider(ABC, RetryMixin):
    """
    Minimal abstract base for TTS providers with RetryMixin integration.

    Phase 3.5.2.2 Enhancement:
    - RetryMixin integration for centralized retry configuration
    - ConnectionManager support standardization
    - LSP compliance maintained
    - SOLID principles preserved
    """

    def __init__(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int = 1,
        enabled: bool = True
    ):
        self.provider_name = provider_name
        self.config = config
        self.priority = priority
        self.enabled = enabled
        self._initialized = False

        # Initialize retry configuration через RetryMixin
        self._retry_config = self._get_retry_config(config)
        logger.debug(f"{provider_name} TTS provider with retry config: {self._retry_config.max_attempts} attempts")

        # Quick config validation - SRP principle
        missing = [f for f in self.get_required_config_fields() if f not in config]
        if missing:
            raise VoiceServiceError(f"Missing config fields: {missing}")

    def get_required_config_fields(self) -> List[str]:
        """
        Default implementation - providers can override for specific requirements.

        Returns:
            List of required configuration field names
        """
        return []  # Base implementation requires no fields

    @abstractmethod
    async def get_capabilities(self) -> TTSCapabilities:
        """Get provider capabilities and supported features."""
        raise NotImplementedError

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize provider resources (connections, clients, etc.)."""
        raise NotImplementedError

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up provider resources."""
        raise NotImplementedError

    @abstractmethod
    async def _synthesize_implementation(self, request: TTSRequest) -> TTSResult:
        """Core synthesis implementation - provider specific."""
        raise NotImplementedError

    async def synthesize_speech(self, request: TTSRequest) -> TTSResult:
        """
        Main synthesis method with validation and error handling.

        Follows Phase 1.1.4 patterns from reference system:
        - Provider availability check
        - Lazy initialization
        - Request validation
        - Performance tracking
        - Enriched results
        """
        if not self.enabled:
            raise ProviderNotAvailableError(f"Provider {self.provider_name} disabled")

        if not self._initialized:
            await self.initialize()
            self._initialized = True

        await self._validate_request(request)

        try:
            start_time = asyncio.get_event_loop().time()
            result = await self._synthesize_implementation(request)

            # Enrich result with metadata (Phase 1.2.3 performance patterns)
            if result.processing_time is None:
                result.processing_time = asyncio.get_event_loop().time() - start_time
            if result.text_length is None:
                result.text_length = len(request.text)

            logger.debug("TTS synthesis completed: %s chars, %.2fs",
                         result.text_length, result.processing_time)
            return result

        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
            if isinstance(e, VoiceServiceError):
                raise
            raise VoiceServiceError(f"TTS synthesis error: {e}") from e

    async def _validate_request(self, request: TTSRequest) -> None:
        """
        Request validation following Phase 1.3 patterns.

        Single Responsibility: Only request validation logic
        """
        if not request.text or not request.text.strip():
            raise AudioProcessingError("Empty text for synthesis")

        # Check capabilities first for language validation
        caps = await self.get_capabilities()

        if request.language != "auto" and request.language not in caps.supported_languages:
            raise AudioProcessingError(f"Language {request.language} not supported")

        if not ConfigurationValidator.validate_language_code(request.language):
            raise AudioProcessingError(f"Invalid language: {request.language}")

        if len(request.text) > caps.max_text_length:
            raise AudioProcessingError(
                f"Text too long: {len(request.text)} chars (max: {caps.max_text_length})"
            )

        # Skip format validation (no output_format in new schema)
        # if request.output_format not in caps.supported_formats:
        #     raise AudioProcessingError(f"Format {request.output_format.value} not supported")

        # Skip quality validation (no quality in new schema)
        # if request.quality not in caps.quality_levels:
        #     raise AudioProcessingError(f"Quality {request.quality.value} not supported")

    async def health_check(self) -> bool:
        """
        Provider health check.

        Basic implementation - providers can override for specific checks.
        """
        try:
            if not self._initialized:
                await self.initialize()
                self._initialized = True
            return True
        except Exception as e:
            logger.warning("Health check failed for %s: %s", self.provider_name, e)
            return False

    async def estimate_audio_duration(self, text: str) -> float:
        """
        Estimate audio duration from text.

        Uses Phase 1.1.2 reference system calculation:
        ~150 words per minute, average word length 5 characters
        """
        if not text:
            return 0.0

        words = len(text) / 5  # Average word length
        return (words / 150) * 60  # 150 WPM to seconds

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.provider_name}, enabled={self.enabled})"
