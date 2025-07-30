"""
🔶 DEPRECATED: app.services.voice package

⚠️ WARNING: This entire package contains legacy voice system components.

🎯 MIGRATION STATUS: All voice functionality has been migrated to voice_v2:
- Legacy voice decisions → LangGraph voice tools
- Legacy VoiceServiceOrchestrator → voice_v2.core.orchestrator.VoiceServiceOrchestrator
- Legacy providers → voice_v2.providers.*

📋 CONTEXT: Phase 4.4.4 - Legacy voice system cleanup

🚫 DO NOT USE FOR NEW FEATURES
✅ USE INSTEAD: app.services.voice_v2.*
"""

import warnings

# Issue deprecation warning when module is imported
warnings.warn(
    "app.services.voice package is deprecated. "
    "Use app.services.voice_v2 package instead.",
    DeprecationWarning,
    stacklevel=2
)

from .base import (
    VoiceServiceBase,
    STTServiceBase, 
    TTSServiceBase,
    VoiceServiceError,
    VoiceServiceTimeout,
    VoiceConfigMixin,
    AudioFileProcessor,
    RateLimiter
)
from .minio_manager import MinioFileManager
from .voice_orchestrator import VoiceServiceOrchestrator

__all__ = [
    "VoiceServiceBase",
    "STTServiceBase",
    "TTSServiceBase", 
    "VoiceServiceError",
    "VoiceServiceTimeout",
    "VoiceConfigMixin",
    "AudioFileProcessor",
    "RateLimiter",
    "MinioFileManager",
    "VoiceServiceOrchestrator"
]
