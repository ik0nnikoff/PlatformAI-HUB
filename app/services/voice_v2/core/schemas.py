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
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
import hashlib
from pydantic import BaseModel, Field, field_validator
from enum import Enum

from .interfaces import ProviderType, AudioFormat


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


class VoiceOperationMetric(BaseModel):
    """Voice operation metrics schema"""
    
    operation: VoiceOperation = Field(..., description="Operation type")
    status: OperationStatus = Field(..., description="Operation status")
    provider: ProviderType = Field(..., description="Provider used")
    duration_ms: float = Field(..., ge=0, description="Operation duration")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Metric timestamp")
    
    class Config:
        extra = "forbid"


class STTRequest(BaseModel):
    """Speech-to-Text request schema"""
    
    audio_file_path: str = Field(..., description="Path to audio file for transcription")
    language: Optional[str] = Field(default=None, description="Target language (auto-detect if None)")
    provider: Optional[ProviderType] = Field(default=None, description="Preferred provider")
    options: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific options")
    
    @field_validator('audio_file_path')
    @classmethod
    def validate_audio_file_path(cls, v):
        """Validate audio file path exists and is readable"""
        if not v:
            raise ValueError("Audio file path cannot be empty")
        
        file_path = Path(v)
        if not file_path.exists():
            raise ValueError(f"Audio file not found: {v}")
        
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {v}")
            
        return str(file_path.resolve())
    
    def generate_cache_key(self) -> str:
        """Generate cache key for this request"""
        # Use file hash + options for cache key
        file_path = Path(self.audio_file_path)
        content = f"{file_path.stat().st_size}_{file_path.stat().st_mtime}_{self.language}_{self.options}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get_cache_key(self) -> str:
        """Alias for generate_cache_key for compatibility"""
        return self.generate_cache_key()
    
    class Config:
        extra = "forbid"


class TTSRequest(BaseModel):
    """Text-to-Speech request schema"""
    
    text: str = Field(..., description="Text to synthesize", min_length=1, max_length=5000)
    language: Optional[str] = Field(default=None, description="Target language")
    voice: Optional[str] = Field(default=None, description="Voice to use")
    provider: Optional[ProviderType] = Field(default=None, description="Preferred provider")
    output_format: AudioFormat = Field(default=AudioFormat.WAV, description="Output audio format")
    options: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific options")
    
    @field_validator('text')
    @classmethod
    def validate_text(cls, v):
        """Validate text content"""
        if not v or not v.strip():
            raise ValueError("Text cannot be empty")
        return v.strip()
    
    def generate_cache_key(self) -> str:
        """Generate cache key for this request"""
        content = f"{self.text}_{self.language}_{self.voice}_{self.output_format}_{self.options}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get_cache_key(self) -> str:
        """Alias for generate_cache_key for compatibility"""
        return self.generate_cache_key()
    
    class Config:
        extra = "forbid"


class STTResponse(BaseModel):
    """Speech-to-Text response schema"""
    
    transcribed_text: str = Field(..., description="Transcribed text result")
    language: Optional[str] = Field(default=None, description="Detected/used language")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Transcription confidence")
    provider_used: ProviderType = Field(..., description="Provider that processed the request")
    processing_time_ms: float = Field(..., ge=0, description="Processing time in milliseconds")
    cached: bool = Field(default=False, description="Whether result was cached")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provider metadata")
    
    @field_validator('transcribed_text')
    @classmethod
    def validate_transcribed_text(cls, v):
        """Validate transcribed text is not empty"""
        if not v or not v.strip():
            raise ValueError("Transcribed text cannot be empty")
        return v.strip()
    
    class Config:
        extra = "forbid"


class TTSResponse(BaseModel):
    """Text-to-Speech response schema"""
    
    audio_url: str = Field(..., description="URL to generated audio file")
    audio_format: AudioFormat = Field(..., description="Audio format of generated file")
    duration_seconds: Optional[float] = Field(default=None, ge=0, description="Audio duration")
    provider_used: ProviderType = Field(..., description="Provider that processed the request")
    processing_time_ms: float = Field(..., ge=0, description="Processing time in milliseconds")
    cached: bool = Field(default=False, description="Whether result was cached")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provider metadata")
    
    @field_validator('audio_url')
    @classmethod
    def validate_audio_url(cls, v):
        """Validate audio URL is not empty"""
        if not v or not v.strip():
            raise ValueError("Audio URL cannot be empty")
        return v.strip()
    
    class Config:
        extra = "forbid"


class PerformanceMetrics(BaseModel):
    """Performance metrics tracking schema"""
    
    operation: str = Field(..., description="Operation type (stt/tts)")
    provider: ProviderType = Field(..., description="Provider used")
    success: bool = Field(..., description="Operation success status")
    duration_ms: float = Field(..., ge=0, description="Operation duration in milliseconds")
    cached: bool = Field(default=False, description="Whether result was cached")
    error_type: Optional[str] = Field(default=None, description="Error type if failed")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Metrics timestamp")
    
    class Config:
        extra = "forbid"


class ProviderStatus(BaseModel):
    """Provider status tracking schema"""
    
    provider: ProviderType = Field(..., description="Provider identifier")
    available: bool = Field(..., description="Provider availability status")
    last_success: Optional[datetime] = Field(default=None, description="Last successful operation")
    last_failure: Optional[datetime] = Field(default=None, description="Last failed operation")
    failure_count: int = Field(default=0, ge=0, description="Recent failure count")
    circuit_breaker_open: bool = Field(default=False, description="Circuit breaker status")
    average_response_time_ms: Optional[float] = Field(default=None, ge=0, description="Average response time")
    
    class Config:
        extra = "forbid"


class VoiceServiceHealth(BaseModel):
    """Overall voice service health schema"""
    
    status: str = Field(..., description="Overall service status")
    providers: List[ProviderStatus] = Field(..., description="Provider status list")
    cache_status: str = Field(..., description="Cache service status")
    file_storage_status: str = Field(..., description="File storage status")
    total_requests: int = Field(default=0, ge=0, description="Total requests processed")
    successful_requests: int = Field(default=0, ge=0, description="Successful requests")
    failed_requests: int = Field(default=0, ge=0, description="Failed requests")
    average_response_time_ms: float = Field(default=0, ge=0, description="Average response time")
    uptime_seconds: float = Field(default=0, ge=0, description="Service uptime")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Health check timestamp")
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100
    
    class Config:
        extra = "forbid"


class ErrorResponse(BaseModel):
    """Error response schema"""
    
    error_code: str = Field(..., description="Error code identifier")
    error_message: str = Field(..., description="Human-readable error message")
    error_type: str = Field(..., description="Error type classification")
    provider: Optional[ProviderType] = Field(default=None, description="Provider that caused error")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Error timestamp")
    request_id: Optional[str] = Field(default=None, description="Request identifier for tracking")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional error details")
    
    class Config:
        extra = "forbid"


class CacheStats(BaseModel):
    """Cache statistics schema"""
    
    hits: int = Field(default=0, ge=0, description="Cache hits count")
    misses: int = Field(default=0, ge=0, description="Cache misses count")
    size: int = Field(default=0, ge=0, description="Current cache size")
    max_size: int = Field(default=1000, ge=0, description="Maximum cache size")
    hit_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Cache hit rate")
    evictions: int = Field(default=0, ge=0, description="Cache evictions count")
    
    @property
    def total_requests(self) -> int:
        """Total cache requests"""
        return self.hits + self.misses
    
    def calculate_hit_rate(self) -> float:
        """Calculate current hit rate"""
        total = self.total_requests
        if total == 0:
            return 0.0
        return self.hits / total
    
    class Config:
        extra = "forbid"


class ProviderCapabilities(BaseModel):
    """Provider capabilities schema"""
    
    provider_type: str = Field(..., description="Provider type")
    supported_formats: List[str] = Field(..., description="Supported audio formats")
    supported_languages: List[str] = Field(..., description="Supported languages")
    max_file_size_mb: Optional[int] = Field(default=None, description="Maximum file size in MB")
    max_text_length: Optional[int] = Field(default=None, description="Maximum text length for TTS")
    supports_streaming: bool = Field(default=False, description="Streaming support")
    supports_real_time: bool = Field(default=False, description="Real-time processing support")
    
    class Config:
        extra = "forbid"


class VoiceFileInfo(BaseModel):
    """Voice file information schema for MinIO storage"""
    
    object_key: str = Field(..., description="MinIO object key")
    bucket_name: str = Field(..., description="MinIO bucket name")
    file_size: int = Field(..., description="File size in bytes")
    content_type: str = Field(..., description="MIME content type")
    upload_time: datetime = Field(..., description="Upload timestamp")
    metadata: Dict[str, str] = Field(default_factory=dict, description="Additional metadata")
    
    class Config:
        extra = "forbid"
