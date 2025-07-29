"""
Voice_v2 STT Data Structures Module.

Provides data classes and enums for STT operations.
Separated from base_stt.py for size compliance with Phase 1.2.1 requirements.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ...core.interfaces import ProviderType, AudioFormat


class STTQuality(Enum):
    """STT quality levels for provider selection."""
    LOW = "low"
    STANDARD = "standard"
    HIGH = "high"
    PREMIUM = "premium"


@dataclass
class STTRequest:
    """STT request data structure."""
    audio_file_path: Union[str, Path]
    language: str = "auto"
    quality: STTQuality = STTQuality.STANDARD
    max_duration: Optional[float] = None
    custom_settings: Optional[Dict[str, Any]] = None


@dataclass
class STTResult:
    """STT operation result with metadata."""
    text: str
    confidence: float
    language_detected: Optional[str] = None
    processing_time: Optional[float] = None
    word_count: Optional[int] = None
    provider_metadata: Optional[Dict[str, Any]] = None


@dataclass
class STTCapabilities:
    """Provider capabilities description."""
    provider_type: ProviderType
    supported_formats: List[AudioFormat]
    supported_languages: List[str]
    max_file_size_mb: float
    max_duration_seconds: float
    supports_quality_levels: List[STTQuality]
    supports_language_detection: bool = False
    supports_word_timestamps: bool = False
    supports_speaker_diarization: bool = False
