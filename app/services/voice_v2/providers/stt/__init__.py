"""
Voice_v2 STT Providers Package.

Exports base STT provider, concrete providers, and data models for voice_v2 STT functionality.
"""

from .base_stt import BaseSTTProvider
from .models import STTRequest, STTResult, STTCapabilities, STTQuality
from .openai_stt import OpenAISTTProvider
from .google_stt import GoogleSTTProvider
from .yandex_stt import YandexSTTProvider

__all__ = [
    "BaseSTTProvider",
    "STTRequest", 
    "STTResult",
    "STTCapabilities",
    "STTQuality",
    "OpenAISTTProvider",
    "GoogleSTTProvider", 
    "YandexSTTProvider"
]
