"""
Voice_v2 TTS Data Structures Module.

Provides data classes and enums for TTS operations.
Mirrors STT models for consistency and Phase 1.3 LSP compliance.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ...core.interfaces import ProviderType, AudioFormat


class TTSQuality(Enum):
    """TTS quality levels for provider selection."""
    LOW = "low"
    STANDARD = "standard"
    HIGH = "high"
    PREMIUM = "premium"


class VoiceGender(Enum):
    """Voice gender options."""
    MALE = "male"
    FEMALE = "female"
    NEUTRAL = "neutral"


@dataclass
class TTSRequest:
    """TTS request data structure with LSP compliance."""
    text: str
    voice: Optional[str] = None
    language: str = "auto"
    quality: TTSQuality = TTSQuality.STANDARD
    speed: float = 1.0
    pitch: float = 0.0
    volume: float = 1.0
    output_format: AudioFormat = AudioFormat.MP3
    custom_settings: Optional[Dict[str, Any]] = None


@dataclass
class TTSResult:
    """TTS operation result with metadata."""
    audio_url: str
    text_length: int
    audio_duration: Optional[float] = None
    processing_time: Optional[float] = None
    voice_used: Optional[str] = None
    language_used: Optional[str] = None
    provider_metadata: Optional[Dict[str, Any]] = None


@dataclass
class TTSCapabilities:
    """TTS Provider capabilities description."""
    provider_type: ProviderType
    supported_formats: List[AudioFormat]
    supported_languages: List[str]
    available_voices: List[Dict[str, str]]
    max_text_length: int
    supports_ssml: bool = False
    supports_speed_control: bool = False
    supports_pitch_control: bool = False
    supports_custom_voices: bool = False
    quality_levels: List[TTSQuality] = None

    def __post_init__(self):
        if self.quality_levels is None:
            self.quality_levels = [TTSQuality.STANDARD]


@dataclass
class VoiceInfo:
    """Voice information for TTS providers."""
    name: str
    language: str
    gender: VoiceGender
    description: Optional[str] = None
    sample_rate: int = 22050
    is_neural: bool = False
    is_premium: bool = False
