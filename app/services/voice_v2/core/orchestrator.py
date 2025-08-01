"""
Voice_v2 Orchestrator - Simplified Entry Point

Main entry point for voice_v2 orchestrator.
Removed specialized managers as unused over-engineering.
"""

# Import main orchestrator
from .orchestrator.base_orchestrator import VoiceServiceOrchestrator

# Export for public API
__all__ = [
    "VoiceServiceOrchestrator",
]
