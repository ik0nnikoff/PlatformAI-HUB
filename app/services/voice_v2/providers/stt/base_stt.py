"""Minimal STT Base Provider - Phase 3.5.2.2 Enhanced with RetryMixin."""

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .models import STTRequest, STTResult, STTCapabilities
from ...core.exceptions import VoiceServiceError, ProviderNotAvailableError, AudioProcessingError
from ...utils.validators import AudioValidator, ConfigurationValidator
from ..retry_mixin import RetryMixin

if TYPE_CHECKING:
    from ..enhanced_connection_manager import IConnectionManager

logger = logging.getLogger(__name__)


class BaseSTTProvider(ABC, RetryMixin):
    """
    Minimal abstract base for STT providers with RetryMixin integration.

    Phase 3.5.2.2 Enhancement:
    - RetryMixin integration for centralized retry configuration
    - ConnectionManager support standardization
    - SOLID principles compliance
    """

    def __init__(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int = 1,
        enabled: bool = True,
        connection_manager: Optional['IConnectionManager'] = None
    ):
        self.provider_name = provider_name
        self.config = config
        self.priority = priority
        self.enabled = enabled
        self._initialized = False

        # Enhanced Connection Manager Integration (Phase 3.4.2.2)
        self._connection_manager = connection_manager

        # Initialize retry configuration через RetryMixin
        if self._has_connection_manager():
            # Get retry config for validation only
            self._get_retry_config(config)
            logger.debug(f"{provider_name} STT provider using ConnectionManager with retry config")

        # Quick config validation
        missing = [f for f in self.get_required_config_fields() if f not in config]
        if missing:
            raise VoiceServiceError(f"Missing config: {missing}")

    def get_required_config_fields(self) -> List[str]:
        """
        Default implementation - providers can override for specific requirements.

        Returns:
            List of required configuration field names
        """
        return []  # Base implementation requires no fields

    @abstractmethod
    async def get_capabilities(self) -> STTCapabilities:
        pass

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        pass

    @abstractmethod
    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        pass

    async def transcribe_audio(self, request: STTRequest) -> STTResult:
        """Main transcription with validation."""
        if not self.enabled:
            raise ProviderNotAvailableError(f"Provider {self.provider_name} disabled")

        if not self._initialized:
            await self.initialize()
            self._initialized = True

        await self._validate_request(request)

        try:
            start_time = asyncio.get_event_loop().time()
            result = await self._transcribe_implementation(request)

            # Enrich result
            if result.processing_time is None:
                result.processing_time = asyncio.get_event_loop().time() - start_time
            if result.word_count is None and result.text:
                result.word_count = len(result.text.split())

            return result

        except Exception as e:
            logger.error(f"STT failed: {e}")
            if isinstance(e, VoiceServiceError):
                raise
            raise VoiceServiceError(f"STT error: {e}") from e

    async def _validate_request(self, request: STTRequest) -> None:
        """Quick request validation."""
        audio_path = Path(request.audio_file_path)

        if not audio_path.exists():
            raise AudioProcessingError(f"File not found: {audio_path}")

        if not AudioValidator.validate_audio_format(audio_path):
            raise AudioProcessingError(f"Bad format: {audio_path.suffix}")

        if not AudioValidator.validate_audio_size(audio_path.stat().st_size):
            raise AudioProcessingError("File too large")

        if not ConfigurationValidator.validate_language_code(request.language):
            raise AudioProcessingError(f"Bad language: {request.language}")

        # Check capabilities
        caps = await self.get_capabilities()
        fmt = audio_path.suffix.lower().lstrip('.')

        # Convert AudioFormat enums to strings for comparison
        supported_formats = [af.value for af in caps.supported_formats]
        if fmt not in supported_formats:
            raise AudioProcessingError(f"Format {fmt} unsupported")

        if request.quality not in caps.supports_quality_levels:
            raise AudioProcessingError(f"Quality {request.quality.value} unsupported")

        if request.language != "auto" and request.language not in caps.supported_languages:
            raise AudioProcessingError(f"Language {request.language} unsupported")

    async def health_check(self) -> bool:
        """Health check."""
        try:
            if not self.enabled:
                return False

            if not self._initialized:
                await self.initialize()
                self._initialized = True

            caps = await self.get_capabilities()
            return len(caps.supported_formats) > 0

        except Exception:
            return False

    def get_status_info(self) -> Dict[str, Any]:
        """Status info."""
        return {
            "provider_name": self.provider_name,
            "enabled": self.enabled,
            "initialized": self._initialized,
            "priority": self.priority
        }

    def __str__(self) -> str:
        """String representation for logging."""
        return f"STTProvider({self.provider_name}, enabled={self.enabled}, priority={self.priority})"
