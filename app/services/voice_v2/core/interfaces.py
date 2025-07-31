"""
Voice_v2 Type Definitions and Protocol Interfaces

Following Interface Segregation Principle (ISP):
- Small, focused interfaces for specific responsibilities
- Clients depend only on methods they actually use
- Clear separation of concerns between different capabilities

Design philosophy:
- Protocol-based typing for duck typing support
- Strict type safety with generics
- Async-first design patterns
- Performance-oriented interfaces
"""

from abc import ABC, abstractmethod
from enum import Enum
import datetime
from typing import Protocol, Dict, Any, List, Optional, TypeVar
from typing_extensions import TypedDict


# Type variables for generic interfaces
T = TypeVar('T')
ConfigT = TypeVar('ConfigT', bound='BaseConfig')


class AudioFormat(Enum):
    """Supported audio formats"""
    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"
    M4A = "m4a"
    WEBM = "webm"
    OPUS = "opus"


class VoiceLanguage(Enum):
    """Supported voice languages"""
    AUTO = "auto"
    RUSSIAN = "ru-RU"
    ENGLISH = "en-US"
    CHINESE = "zh-CN"
    SPANISH = "es-ES"
    FRENCH = "fr-FR"
    GERMAN = "de-DE"


class ProviderType(Enum):
    """Voice service provider types"""
    OPENAI = "openai"
    GOOGLE = "google"
    YANDEX = "yandex"


class PerformanceTier(Enum):
    """Performance tier classification for optimization"""
    CRITICAL = "critical"    # Highest priority, requires fastest response
    HIGH = "high"           # High priority, balanced performance  
    STANDARD = "standard"   # Standard priority, default optimization


class CacheBackend(Enum):
    """Cache backend types"""
    REDIS = "redis"
    MEMORY = "memory"
    DISK = "disk"


class FileStorageBackend(Enum):
    """File storage backend types"""
    MINIO = "minio"
    AWS_S3 = "aws_s3"
    LOCAL = "local"


class PerformanceLevel(Enum):
    """Performance optimization levels"""
    LOW = "low"
    BALANCED = "balanced"
    HIGH = "high"
    MAXIMUM = "maximum"


# Configuration TypedDicts for type safety
class BaseConfig(TypedDict):
    """Base configuration structure"""
    enabled: bool
    timeout_seconds: int
    max_retries: int


class STTProviderConfig(BaseConfig):
    """STT provider configuration"""
    provider_type: ProviderType
    api_key: str
    supported_formats: List[AudioFormat]
    supported_languages: List[VoiceLanguage]
    max_file_size_mb: int


class TTSProviderConfig(BaseConfig):
    """TTS provider configuration"""
    provider_type: ProviderType
    api_key: str
    default_voice: str
    supported_languages: List[VoiceLanguage]
    output_format: AudioFormat


class VoiceMetric(TypedDict):
    """Voice operation metrics"""
    operation_type: str
    provider: ProviderType
    duration_ms: float
    success: bool
    timestamp: datetime.datetime
    file_size_bytes: Optional[int]
    language: Optional[VoiceLanguage]


# Core Protocol Interfaces (ISP Compliance)

class HealthCheckable(Protocol):
    """Interface for health checking capability"""
    async def health_check(self) -> bool:
        """Check if service is healthy and operational"""
        raise NotImplementedError


class Configurable(Protocol[ConfigT]):
    """Interface for configuration management"""
    def get_config(self) -> ConfigT:
        """Get current configuration"""
        raise NotImplementedError

    def update_config(self, config: ConfigT) -> None:
        """Update configuration"""
        raise NotImplementedError


class MetricsCollector(Protocol):
    """Interface for metrics collection"""
    async def record_metric(self, metric: VoiceMetric) -> None:
        """Record a performance metric"""
        raise NotImplementedError

    async def get_metrics(self, operation_type: Optional[str] = None) -> List[VoiceMetric]:
        """Retrieve collected metrics"""
        raise NotImplementedError


class FileValidator(Protocol):
    """Interface for file validation capability"""
    async def validate_file(self, file_path: str) -> bool:
        """Validate file accessibility and format"""
        raise NotImplementedError

    def get_supported_formats(self) -> List[AudioFormat]:
        """Get list of supported file formats"""
        raise NotImplementedError


class LanguageDetector(Protocol):
    """Interface for language detection capability"""
    async def detect_language(self, audio_file_path: str) -> VoiceLanguage:
        """Detect language from audio file"""
        raise NotImplementedError

    def get_supported_languages(self) -> List[VoiceLanguage]:
        """Get list of supported languages"""
        raise NotImplementedError


# STT-specific interfaces

class STTProvider(Protocol):
    """
    Speech-to-Text provider interface

    ISP Compliance: Only STT-specific methods
    Clients can depend only on transcription capability
    """
    async def transcribe_audio(
        self,
        audio_file_path: str,
        language: VoiceLanguage = VoiceLanguage.AUTO,
        **kwargs
    ) -> str:
        """
        Transcribe audio file to text

        Args:
            audio_file_path: Path to audio file
            language: Target language for transcription
            **kwargs: Provider-specific options

        Returns:
            Transcribed text

        Raises:
            VoiceServiceError: On transcription failure
        """
        raise NotImplementedError


class BatchSTTProvider(Protocol):
    """Interface for batch STT processing"""
    async def transcribe_batch(
        self,
        audio_files: List[str],
        language: VoiceLanguage = VoiceLanguage.AUTO
    ) -> List[str]:
        """Transcribe multiple audio files in batch"""
        raise NotImplementedError


class StreamingSTTProvider(Protocol):
    """Interface for streaming STT processing"""
    async def transcribe_stream(
        self,
        audio_stream,
        language: VoiceLanguage = VoiceLanguage.AUTO
    ) -> str:
        """Transcribe audio from stream"""
        raise NotImplementedError


# TTS-specific interfaces

class TTSProvider(Protocol):
    """
    Text-to-Speech provider interface

    ISP Compliance: Only TTS-specific methods
    Clients can depend only on synthesis capability
    """
    async def synthesize_speech(
        self,
        text: str,
        voice: Optional[str] = None,
        language: VoiceLanguage = VoiceLanguage.AUTO,
        **kwargs
    ) -> str:
        """
        Synthesize speech from text

        Args:
            text: Text to synthesize
            voice: Voice identifier
            language: Target language
            **kwargs: Provider-specific options

        Returns:
            URL to generated audio file

        Raises:
            VoiceServiceError: On synthesis failure
        """
        raise NotImplementedError


class VoiceListProvider(Protocol):
    """Interface for voice listing capability"""
    async def get_available_voices(
        self,
        language: Optional[VoiceLanguage] = None
    ) -> List[Dict[str, str]]:
        """Get available voices for language"""
        raise NotImplementedError


class CustomVoiceProvider(Protocol):
    """Interface for custom voice creation"""
    async def create_custom_voice(
        self,
        voice_name: str,
        training_data: List[str]
    ) -> str:
        """Create custom voice from training data"""
        raise NotImplementedError


# Cache interfaces

class CacheInterface(Protocol):
    """
    Cache interface for storing/retrieving results

    ISP Compliance: Only caching methods
    Supports both STT and TTS result caching
    """
    async def get(self, key: str) -> Optional[str]:
        """Retrieve cached value"""
        raise NotImplementedError

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store value in cache with TTL"""
        raise NotImplementedError

    async def delete(self, key: str) -> bool:
        """Delete cached value"""
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        raise NotImplementedError


class RateLimiterInterface(Protocol):
    """
    Rate limiter interface for request throttling

    ISP Compliance: Only rate limiting methods
    Supports distributed rate limiting with Redis
    """
    async def is_allowed(self, user_id: str, operation: str = "default") -> bool:
        """Check if request is allowed for user"""
        raise NotImplementedError

    async def get_remaining_requests(self, user_id: str, operation: str = "default") -> int:
        """Get remaining requests for user"""
        raise NotImplementedError

    async def get_reset_time(self, user_id: str, operation: str = "default") -> float:
        """Get time until rate limit reset"""
        raise NotImplementedError

    async def clear_user_limit(self, user_id: str, operation: str = "default") -> bool:
        """Clear rate limit for user"""
        raise NotImplementedError


class STTCacheInterface(Protocol):
    """STT-specific cache interface"""
    async def get_stt_result(
        self,
        audio_file_hash: str,
        provider: ProviderType,
        language: VoiceLanguage
    ) -> Optional[str]:
        """Get cached STT result"""
        raise NotImplementedError

    async def cache_stt_result(
        self,
        audio_file_hash: str,
        provider: ProviderType,
        language: VoiceLanguage,
        result: str,
        ttl_seconds: int = 3600
    ) -> None:
        """Cache STT result"""
        raise NotImplementedError


class TTSCacheInterface(Protocol):
    """TTS-specific cache interface"""
    async def get_tts_result(
        self,
        text_hash: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage
    ) -> Optional[str]:
        """Get cached TTS result URL"""
        raise NotImplementedError

    async def cache_tts_result(
        self,
        text_hash: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage,
        audio_url: str,
        ttl_seconds: int = 3600
    ) -> None:
        """Cache TTS result URL"""
        raise NotImplementedError


# File management interfaces

class FileManagerInterface(Protocol):
    """
    File storage and management interface

    ISP Compliance: Only file operations
    Supports both local and cloud storage
    """
    async def upload_file(self, local_path: str, remote_path: str) -> str:
        """Upload file and return URL"""
        raise NotImplementedError

    async def download_file(self, remote_url: str, local_path: str) -> str:
        """Download file and return local path"""
        raise NotImplementedError

    async def delete_file(self, remote_path: str) -> bool:
        """Delete file from storage"""
        raise NotImplementedError

    async def file_exists(self, remote_path: str) -> bool:
        """Check if file exists"""
        raise NotImplementedError


class TemporaryFileManager(Protocol):
    """Interface for temporary file management"""
    async def create_temp_file(self, suffix: str = ".wav") -> str:
        """Create temporary file and return path"""
        raise NotImplementedError

    async def cleanup_temp_file(self, file_path: str) -> None:
        """Clean up temporary file"""
        raise NotImplementedError


# Combined interfaces for full-featured providers

class FullSTTProvider(
    STTProvider,
    HealthCheckable,
    FileValidator,
    LanguageDetector,
    Configurable[STTProviderConfig],
    Protocol
):
    """Full-featured STT provider with all capabilities"""


class FullTTSProvider(
    TTSProvider,
    VoiceListProvider,
    HealthCheckable,
    Configurable[TTSProviderConfig],
    Protocol
):
    """Full-featured TTS provider with all capabilities"""


# Abstract base classes for concrete implementations

class BaseVoiceService(ABC):
    """Abstract base for voice services with common functionality"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._metrics: List[VoiceMetric] = []

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize service resources"""
        raise NotImplementedError

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up service resources"""
        raise NotImplementedError

    async def record_metric(self, metric: VoiceMetric) -> None:
        """Record performance metric"""
        self._metrics.append(metric)

        # Keep only last 1000 metrics for memory efficiency
        if len(self._metrics) > 1000:
            self._metrics = self._metrics[-1000:]
