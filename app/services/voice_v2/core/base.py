"""
Voice_v2 Base Classes and Abstractions

Following SOLID principles, especially Liskov Substitution Principle (LSP):
- All derived classes must be substitutable for their base classes
- Common interface contracts for all voice service components
- Shared functionality through mixins to avoid code duplication

Design patterns implemented:
- Abstract base classes for contracts
- Mixin classes for shared functionality  
- Type-safe interfaces with proper async patterns
- Performance-first design with connection pooling
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, AsyncGenerator
import time
import logging
from contextlib import asynccontextmanager

from .exceptions import VoiceServiceError


logger = logging.getLogger(__name__)


class VoiceServiceBase(ABC):
    """
    Abstract base class for all voice services
    
    LSP Compliance: All subclasses must implement the same interface
    Single Responsibility: Base contract for voice service lifecycle
    """
    
    def __init__(self, name: str, config: Dict[str, Any]):
        """
        Initialize voice service
        
        Args:
            name: Service name for logging and identification
            config: Service-specific configuration
        """
        super().__init__()  # Call mixins initialization
        self.name = name
        self.config = config
        self._initialized = False
        self._metrics: Dict[str, Any] = {}
        self._logger = logging.getLogger(f"voice_v2.{name}")
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize service resources (connections, auth, etc.)"""
        raise NotImplementedError
    
    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up service resources"""
        raise NotImplementedError
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if service is healthy and operational"""
        raise NotImplementedError
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get service performance metrics"""
        return self._metrics.copy()
    
    def is_initialized(self) -> bool:
        """Check if service is initialized"""
        return self._initialized
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.cleanup()


class STTServiceBase(VoiceServiceBase):
    """
    Abstract base class for Speech-to-Text providers
    
    LSP Compliance: All STT providers implement identical interface
    Interface Segregation: Only STT-specific methods
    """
    
    @abstractmethod
    async def transcribe_audio(
        self, 
        audio_file_path: str,
        language: str = "auto",
        **kwargs
    ) -> str:
        """
        Transcribe audio file to text
        
        Args:
            audio_file_path: Path to audio file
            language: Language code or "auto" for detection
            **kwargs: Provider-specific options
            
        Returns:
            Transcribed text
            
        Raises:
            VoiceServiceError: On transcription failure
        """
        raise NotImplementedError
    
    @abstractmethod
    async def get_supported_formats(self) -> List[str]:
        """Get list of supported audio formats"""
        raise NotImplementedError
    
    @abstractmethod
    async def get_supported_languages(self) -> List[str]:
        """Get list of supported language codes"""
        raise NotImplementedError


class TTSServiceBase(VoiceServiceBase):
    """
    Abstract base class for Text-to-Speech providers
    
    LSP Compliance: All TTS providers implement identical interface
    Interface Segregation: Only TTS-specific methods
    """
    
    @abstractmethod
    async def synthesize_speech(
        self,
        text: str,
        voice: Optional[str] = None,
        language: str = "auto",
        **kwargs
    ) -> str:
        """
        Synthesize speech from text
        
        Args:
            text: Text to synthesize
            voice: Voice identifier
            language: Language code
            **kwargs: Provider-specific options
            
        Returns:
            URL to generated audio file
            
        Raises:
            VoiceServiceError: On synthesis failure
        """
        raise NotImplementedError
    
    @abstractmethod
    async def get_available_voices(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Get available voices for language
        
        Args:
            language: Language code filter
            
        Returns:
            List of voice metadata dicts
        """
        raise NotImplementedError
    
    @abstractmethod
    async def get_supported_languages(self) -> List[str]:
        """Get list of supported language codes"""
        raise NotImplementedError


class VoiceConfigMixin:
    """
    Mixin class for configuration management
    
    Single Responsibility: Configuration handling only
    Reusable across different voice service types
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config_cache: Dict[str, Any] = {}
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value with caching
        
        Args:
            key: Configuration key (supports dot notation)
            default: Default value if key not found
            
        Returns:
            Configuration value
        """
        if key in self._config_cache:
            return self._config_cache[key]
        
        # Support dot notation (e.g., "provider.openai.api_key")
        keys = key.split('.')
        value = self.config
        
        try:
            for k in keys:
                value = value[k]
            self._config_cache[key] = value
            return value
        except (KeyError, TypeError):
            return default
    
    def validate_config(self, required_keys: List[str]) -> None:
        """
        Validate required configuration keys exist
        
        Args:
            required_keys: List of required configuration keys
            
        Raises:
            VoiceConfigurationError: If required keys are missing
        """
        from .exceptions import VoiceConfigurationError
        
        missing_keys = []
        for key in required_keys:
            if self.get_config_value(key) is None:
                missing_keys.append(key)
        
        if missing_keys:
            raise VoiceConfigurationError(
                config_field="required_keys",
                invalid_value=missing_keys,
                reason="Missing required configuration keys"
            )


class AudioFileProcessor:
    """
    Utility class for audio file processing
    
    Single Responsibility: Audio file operations only
    Performance optimized with async operations
    """
    
    def __init__(self, max_file_size_mb: int = 25):
        """
        Initialize audio processor
        
        Args:
            max_file_size_mb: Maximum allowed file size in MB
        """
        self.max_file_size_mb = max_file_size_mb
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self._logger = logging.getLogger("voice_v2.audio_processor")
    
    async def validate_audio_file(self, file_path: str) -> bool:
        """
        Validate audio file size and accessibility
        
        Args:
            file_path: Path to audio file
            
        Returns:
            True if file is valid
            
        Raises:
            VoiceServiceError: If file is invalid
        """
        try:
            import aiofiles.os
            
            # Check file exists and is readable
            if not await aiofiles.os.path.exists(file_path):
                raise VoiceServiceError(f"Audio file not found: {file_path}")
            
            # Check file size
            file_size = await aiofiles.os.path.getsize(file_path)
            if file_size > self.max_file_size_bytes:
                size_mb = file_size / (1024 * 1024)
                raise VoiceServiceError(
                    f"Audio file too large: {size_mb:.1f}MB > {self.max_file_size_mb}MB"
                )
            
            return True
            
        except Exception as e:
            if isinstance(e, VoiceServiceError):
                raise
            raise VoiceServiceError(f"Audio file validation failed: {str(e)}")
    
    async def get_audio_duration(self, file_path: str) -> float:
        """
        Get audio file duration in seconds
        
        Args:
            file_path: Path to audio file
            
        Returns:
            Duration in seconds
            
        Note: This is a placeholder - actual implementation would use
              audio processing library like librosa or pydub
        """
        # TODO: Implement with audio processing library
        return 0.0
    
    @asynccontextmanager
    async def temporary_file(self, suffix: str = ".wav") -> AsyncGenerator[str, None]:
        """
        Create temporary file for audio processing
        
        Args:
            suffix: File extension
            
        Yields:
            Path to temporary file
        """
        import tempfile
        import aiofiles.os
        
        temp_file = None
        try:
            # Create temporary file
            fd, temp_file = tempfile.mkstemp(suffix=suffix)
            import os
            os.close(fd)  # Close file descriptor, keep file path
            
            yield temp_file
            
        finally:
            # Cleanup temporary file
            if temp_file and await aiofiles.os.path.exists(temp_file):
                try:
                    await aiofiles.os.remove(temp_file)
                except Exception as e:
                    self._logger.warning(f"Failed to cleanup temp file {temp_file}: {e}")


class PerformanceMixin:
    """
    Mixin for performance tracking and optimization
    
    Single Responsibility: Performance monitoring only
    Tracks operation timing and success rates
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._operation_times: List[float] = []
        self._operation_count = 0
        self._error_count = 0
    
    @asynccontextmanager
    async def track_operation(self, operation_name: str):
        """
        Context manager for tracking operation performance
        
        Args:
            operation_name: Name of operation being tracked
            
        Yields:
            Context for the tracked operation
        """
        start_time = time.time()
        self._operation_count += 1
        
        try:
            yield
            
            # Record successful operation
            duration = time.time() - start_time
            self._operation_times.append(duration)
            
            # Keep only last 100 operations for memory efficiency
            if len(self._operation_times) > 100:
                self._operation_times = self._operation_times[-100:]
                
        except Exception as e:
            self._error_count += 1
            duration = time.time() - start_time
            self._logger.warning(
                f"Operation {operation_name} failed after {duration:.3f}s: {e}"
            )
            raise
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        if not self._operation_times:
            return {
                "operation_count": self._operation_count,
                "error_count": self._error_count,
                "error_rate": 0.0,
                "avg_duration": 0.0,
                "max_duration": 0.0,
                "min_duration": 0.0
            }
        
        return {
            "operation_count": self._operation_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(self._operation_count, 1),
            "avg_duration": sum(self._operation_times) / len(self._operation_times),
            "max_duration": max(self._operation_times),
            "min_duration": min(self._operation_times),
        }
