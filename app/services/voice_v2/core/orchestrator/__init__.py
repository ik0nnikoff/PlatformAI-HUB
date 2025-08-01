"""
Voice_v2 Orchestrator Package

Simplified orchestrator package with only base orchestrator.
Removed specialized managers as over-engineering.
"""

# Main orchestrator
from .base_orchestrator import VoiceServiceOrchestrator

# Export public interface
__all__ = [
    "VoiceServiceOrchestrator",
]