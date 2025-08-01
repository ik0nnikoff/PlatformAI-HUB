"""
Voice_v2 Core Module

Core components for voice_v2 system following SOLID principles.
Contains orchestrator, base classes, configuration, and schemas.
"""

from .exceptions import (
    VoiceServiceError,
    VoiceServiceTimeout,
    VoiceProviderError,
    VoiceConfigurationError,
    create_voice_error,
)
# Временно отключено до исправления импортов
# from .stt_factory import STTProviderFactory
# Coordinator removed as unused
# from .stt_coordinator import STTCoordinator

# Core exports (will be populated as files are created)
__all__ = [
    # Exceptions
    "VoiceServiceError",
    "VoiceServiceTimeout",
    "VoiceProviderError",
    "VoiceConfigurationError",
    "create_voice_error",
    # STT Components - временно отключено
    # "STTProviderFactory",
    # Coordinator removed as unused
    # "STTCoordinator",
]
