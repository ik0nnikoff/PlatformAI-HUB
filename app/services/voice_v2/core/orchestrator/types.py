"""
Orchestrator Types and Interfaces
Phase 3.5.3.2 - Модульное разделение orchestrator для улучшения поддерживаемости
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Any
from ..interfaces import FullSTTProvider, FullTTSProvider
from ..schemas import STTRequest, TTSRequest, STTResponse, TTSResponse, ProviderCapabilities


class IProviderManager(ABC):
    """Interface for provider management operations"""

    @abstractmethod
    async def get_stt_provider(self, provider_name: str) -> Optional[FullSTTProvider]:
        """Get STT provider by name"""
        raise NotImplementedError

    @abstractmethod
    async def get_tts_provider(self, provider_name: str) -> Optional[FullTTSProvider]:
        """Get TTS provider by name"""
        raise NotImplementedError

    @abstractmethod
    async def health_check_provider(
        self,
        provider_name: str,
        provider_type: str
    ) -> Dict[str, Any]:
        """Check provider health"""
        raise NotImplementedError


class ISTTManager(ABC):
    """Interface for STT operations"""

    @abstractmethod
    async def transcribe_audio(self, request: STTRequest) -> STTResponse:
        """Transcribe audio to text"""
        raise NotImplementedError


class ITTSManager(ABC):
    """Interface for TTS operations"""

    @abstractmethod
    async def synthesize_speech(self, request: TTSRequest) -> TTSResponse:
        """Synthesize text to speech"""
        raise NotImplementedError


class IOrchestratorManager(ABC):
    """Interface for orchestrator core operations"""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize orchestrator"""
        raise NotImplementedError

    @abstractmethod
    async def cleanup(self) -> None:
        """Cleanup orchestrator resources"""
        raise NotImplementedError

    @abstractmethod
    async def get_provider_capabilities(
        self,
        provider_name: str,
        provider_type: str
    ) -> ProviderCapabilities:
        """Get provider capabilities"""
        raise NotImplementedError
