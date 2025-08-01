"""
Voice_v2 Core Module - Phase 4.1.1 Consolidated

Core components for voice_v2 system following SOLID principles.
Contains orchestrator, configuration, schemas, and interfaces.
"""

# Core orchestrator - main entry point
from .orchestrator import VoiceServiceOrchestrator

# Core exceptions
from .exceptions import (
    VoiceServiceError,
    VoiceServiceTimeout,
    VoiceProviderError,
    VoiceConfigurationError,
    create_voice_error,
)

# Core configuration
from .config import VoiceConfig, get_config

# Core interfaces and schemas
from .interfaces import (
    ProviderType, AudioFormat, VoiceLanguage,
    FullSTTProvider, FullTTSProvider,
    CacheInterface, FileManagerInterface
)
from .schemas import (
    STTRequest, STTResponse, TTSRequest, TTSResponse,
    VoiceOperation, OperationStatus
)

# Core exports
__all__ = [
    # Main orchestrator
    "VoiceServiceOrchestrator",

    # Exceptions
    "VoiceServiceError",
    "VoiceServiceTimeout",
    "VoiceProviderError",
    "VoiceConfigurationError",
    "create_voice_error",

    # Configuration
    "VoiceConfig",
    "get_config",

    # Core types and enums
    "ProviderType",
    "AudioFormat",
    "VoiceLanguage",
    "VoiceOperation",
    "OperationStatus",

    # Interfaces
    "FullSTTProvider",
    "FullTTSProvider",
    "CacheInterface",
    "FileManagerInterface",

    # Schemas
    "STTRequest",
    "STTResponse",
    "TTSRequest",
    "TTSResponse",
]
