"""
Voice_v2 Orchestrator - Modular Voice Service Orchestrator

Implements modular orchestrator following SOLID principles.
Delegates responsibilities to specialized managers in orchestrator/ module.

This file now serves as the main entry point, importing modular components.
"""

# Import modular orchestrator components
from .orchestrator import (
    VoiceServiceOrchestrator,
    VoiceOrchestratorManager,
    VoiceProviderManager,
    VoiceSTTManager,
    VoiceTTSManager,
    IProviderManager,
    ISTTManager,
    ITTSManager,
    IOrchestratorManager
)

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
