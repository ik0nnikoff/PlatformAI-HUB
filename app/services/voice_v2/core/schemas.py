"""
Voice_v2 Data Schemas and Models

SOLID Principles Implementation:
- Single Responsibility: Each schema handles one data structure type
- Open/Closed: Extensible for new fields without breaking existing code
- Liskov Substitution: All schemas follow same validation patterns
- Interface Segregation: Focused schemas for specific use cases
- Dependency Inversion: Depends on Pydantic abstractions

Features:
- Type-safe data models with Pydantic v2
- Comprehensive validation and serialization
- Request/response schemas for all operations
- Metrics and performance tracking models
- Consistent error handling schemas
- Compatibility with AgentRunner integration
"""

import base64
import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .interfaces import AudioFormat, ProviderType


class OperationStatus(Enum):
    """Operation status enumeration"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


class VoiceOperation(Enum):
    """Voice operation type enumeration"""

    STT = "stt"
    TTS = "tts"


# AgentRunner Compatibility Schemas
class VoiceFileInfo(BaseModel):
    """
    Voice file information schema (AgentRunner compatibility)

    Compatible with app/api/schemas/voice_schemas.py:VoiceFileInfo
    """

    file_id: str = Field(..., description="Unique file identifier")
    original_filename: str = Field(..., description="Original filename")
    mime_type: str = Field(..., description="File MIME type")
    size_bytes: int = Field(..., ge=0, description="File size in bytes")
    format: str = Field(..., description="Audio format (wav, mp3, ogg, etc.)")
    duration_seconds: Optional[float] = Field(default=None, ge=0, description="Audio duration")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")
    minio_bucket: str = Field(..., description="MinIO bucket name")
    minio_key: str = Field(..., description="MinIO object key")

    model_config = ConfigDict(extra="allow")  # Allow extra fields for compatibility


class VoiceProcessingResult(BaseModel):
    """
    Voice processing result schema (AgentRunner compatibility)

    Compatible with app/api/schemas/voice_schemas.py:VoiceProcessingResult
    """

    success: bool = Field(..., description="Whether processing was successful")
    text: Optional[str] = Field(default=None,
                                description="Transcribed text (STT) or input text (TTS)")
    audio_data: Optional[bytes] = Field(default=None, description="Audio data for TTS results")
    file_info: Optional[VoiceFileInfo] = Field(default=None, description="File information")
    error_message: Optional[str] = Field(default=None, description="Error message if failed")
    processing_time: float = Field(default=0.0, ge=0, description="Processing time in seconds")
    provider_used: Optional[str] = Field(default=None,
                                         description="Provider that processed the request")
    cached: bool = Field(default=False, description="Whether result came from cache")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    model_config = ConfigDict(extra="allow")  # Allow extra fields for compatibility


class VoiceSettings(BaseModel):
    """
    Voice settings schema for agent configuration

    Compatible with app/api/schemas/voice_schemas.py:VoiceSettings
    """

    enabled: bool = Field(default=False, description="Whether voice features are enabled")
    auto_stt: bool = Field(default=True, description="Automatic STT processing")
    auto_tts_on_keywords: bool = Field(default=False, description="Auto TTS based on keywords")
    intent_keywords: List[str] = Field(default_factory=list, description="Voice intent keywords")
    providers: List[Dict[str, Any]] = Field(
        default_factory=list, description="Provider configurations")
    max_file_size_mb: int = Field(default=25, ge=1, le=100, description="Max file size in MB")
    rate_limit_per_minute: int = Field(
        default=30, ge=1, le=100, description="Rate limit per minute")
    cache_enabled: bool = Field(default=True, description="Whether caching is enabled")
    cache_ttl_hours: int = Field(default=24, ge=1, le=168, description="Cache TTL in hours")

    model_config = ConfigDict(extra="allow")


# Core Voice_v2 Schemas
class VoiceOperationMetric(BaseModel):
    """Voice operation metrics schema"""

    operation: VoiceOperation = Field(..., description="Operation type")
    status: OperationStatus = Field(..., description="Operation status")
    provider: ProviderType = Field(..., description="Provider used")
    duration_ms: float = Field(..., ge=0, description="Operation duration")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Metric timestamp")

    model_config = ConfigDict(extra="forbid")


class STTRequest(BaseModel):
    """Speech-to-Text request schema"""

    audio_data: bytes = Field(..., description="Audio file data")
    language: Optional[str] = Field(default="auto", description="Audio language code")
    audio_format: Optional[AudioFormat] = Field(default=None, description="Audio format")

    @field_validator('audio_data')
    @classmethod
    def validate_audio_data(cls, v):
        """Validate that audio data is not empty"""
        if len(v) == 0:
            raise ValueError('Audio data cannot be empty')
        return v

    def get_cache_key(self) -> str:
        """Generate cache key for STT request"""
        content = base64.b64encode(self.audio_data).decode()[
            :100]  # First 100 chars of encoded data
        key_data = f"{content}_{self.audio_format}_{self.language}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    model_config = ConfigDict(extra="forbid")


class STTResponse(BaseModel):
    """Speech-to-Text response schema"""

    text: str = Field(..., description="Transcribed text")
    language: Optional[str] = Field(default=None, description="Detected language")
    confidence: Optional[float] = Field(default=None, ge=0, le=1, description="Confidence score")
    processing_time: float = Field(..., ge=0, description="Processing time in seconds")
    provider: str = Field(..., description="Provider used")

    model_config = ConfigDict(extra="forbid")


class TTSRequest(BaseModel):
    """Text-to-Speech request schema"""

    text: str = Field(..., min_length=1, max_length=4000, description="Text to synthesize")
    language: Optional[str] = Field(default="ru", description="Target language")
    voice: Optional[str] = Field(default=None, description="Voice ID or name")
    speed: Optional[float] = Field(default=1.0, ge=0.25, le=4.0, description="Speech speed")
    format: AudioFormat = Field(default=AudioFormat.OGG, description="Output audio format")

    @field_validator('text')
    @classmethod
    def validate_text(cls, v):
        """Validate that text is not empty or whitespace only"""
        if not v.strip():
            raise ValueError('Text cannot be empty or whitespace only')
        return v

    def cache_key(self) -> str:
        """Generate cache key for this TTS request."""
        key_data = f"{self.text}:{self.voice}:{self.format}:{self.speed}:{self.language}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    model_config = ConfigDict(extra="forbid")


class TTSResponse(BaseModel):
    """Text-to-Speech response schema"""

    audio_data: bytes = Field(..., description="Generated audio data")
    format: AudioFormat = Field(..., description="Audio format")
    sample_rate: Optional[int] = Field(default=None, description="Audio sample rate")
    duration: Optional[float] = Field(default=None, ge=0, description="Audio duration in seconds")
    processing_time: float = Field(..., ge=0, description="Processing time in seconds")
    provider: str = Field(..., description="Provider used")

    @field_validator('audio_data')
    @classmethod
    def validate_audio_data(cls, v):
        """Validate that audio data is not empty"""
        if len(v) == 0:
            raise ValueError('Audio data cannot be empty')
        return v

    model_config = ConfigDict(extra="forbid")


class CacheEntry(BaseModel):
    """Cache entry schema"""

    key: str = Field(..., description="Cache key")
    data: bytes = Field(..., description="Cached data")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Cache metadata")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    expires_at: Optional[datetime] = Field(default=None, description="Expiration timestamp")

    @staticmethod
    def generate_key(content: str) -> str:
        """Generate cache key from content"""
        return hashlib.sha256(content.encode()).hexdigest()

    def is_expired(self) -> bool:
        """Check if cache entry is expired"""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    model_config = ConfigDict(extra="forbid")


class ProviderCapabilities(BaseModel):
    """Provider capabilities schema"""

    provider_type: str = Field(..., description="Provider type")
    supported_formats: List[str] = Field(..., description="Supported audio formats")
    supported_languages: List[str] = Field(..., description="Supported languages")
    max_file_size_mb: Optional[int] = Field(default=None, description="Maximum file size in MB")
    max_text_length: Optional[int] = Field(default=None, description="Maximum text length for TTS")
    supports_streaming: bool = Field(default=False, description="Streaming support")
    supports_real_time: bool = Field(default=False, description="Real-time processing support")

    model_config = ConfigDict(extra="forbid")
