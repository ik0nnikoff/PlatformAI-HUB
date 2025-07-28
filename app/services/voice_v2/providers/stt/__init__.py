"""
Voice_v2 STT Providers Package.

Exports base STT provider and data models for voice_v2 STT functionality.
"""

from .base_stt import BaseSTTProvider
from .models import STTRequest, STTResult, STTCapabilities, STTQuality

__all__ = [
    "BaseSTTProvider",
    "STTRequest", 
    "STTResult",
    "STTCapabilities",
    "STTQuality"
]
