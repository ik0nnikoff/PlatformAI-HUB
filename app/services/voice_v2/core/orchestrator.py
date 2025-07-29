"""
Voice_v2 Orchestrator - Modular Voice Service Orchestrator

Implements modular orchestrator following SOLID principles.
Delegates responsibilities to specialized managers in orchestrator/ module.

This file now serves as the main entry point, importing modular components.
"""

# Import modular orchestrator components
from .orchestrator.orchestrator_manager import (
    VoiceOrchestratorManager,
    IOrchestratorManager
)
from .orchestrator.provider_manager import (
    VoiceProviderManager,
    IProviderManager
)
from .orchestrator.stt_manager import (
    VoiceSTTManager,
    ISTTManager
)
from .orchestrator.tts_manager import (
    VoiceTTSManager,
    ITTSManager
)
from .orchestrator_new import VoiceServiceOrchestrator

# Re-export for backward compatibility
__all__ = [
    # Main orchestrator classes
    "VoiceServiceOrchestrator",         # Main legacy-compatible orchestrator
    "VoiceOrchestratorManager",         # New modular orchestrator manager

    # Specialized managers
    "VoiceProviderManager",             # Provider management
    "VoiceSTTManager",                  # STT operations
    "VoiceTTSManager",                  # TTS operations

    # Interfaces
    "IProviderManager",
    "ISTTManager",
    "ITTSManager",
    "IOrchestratorManager"
]
