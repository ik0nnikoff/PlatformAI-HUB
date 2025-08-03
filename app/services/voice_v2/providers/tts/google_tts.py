"""
Google Cloud Text-to-Speech Provider for Voice_v2 Architecture

Phase 3.2.3 implementation following Phase 1.3 architectural requirements:
- LSP compliance with BaseTTSProvider interface
- SOLID principles implementation
- Performance optimization patterns
- Advanced voice configuration support

Architecture References:
- Phase_1_3_1_architecture_review.md: LSP compliance patterns
- Phase_1_1_4_architecture_patterns.md: Successful patterns from app/services/voice
- Phase_1_2_3_performance_optimization.md: Async patterns and connection pooling
- Phase_1_2_2_solid_principles.md: Interface Segregation in provider design
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from google.cloud import texttospeech_v1
from google.cloud.texttospeech_v1 import types
from google.api_core import exceptions as google_exceptions

from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSQuality, TTSCapabilities
from app.services.voice_v2.core.exceptions import (
    AudioProcessingError,
    VoiceConfigurationError
)
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.providers.retry_mixin import provider_operation
from ...infrastructure.minio_manager import MinioFileManager
from ...core.config import get_config
logger = logging.getLogger(__name__)


class GoogleTTSProvider(BaseTTSProvider):
    """
    Google Cloud Text-to-Speech Provider

    Architecture Compliance:
    - LSP: Full substitutability with BaseTTSProvider
    - SRP: Only Google Cloud TTS operations, delegated infrastructure concerns
    - OCP: Extensible via config, closed for modification
    - ISP: Uses minimal interface from BaseTTSProvider
    - DIP: Depends on abstractions (BaseTTSProvider, interfaces)

    Performance Features from Phase 1.2.3:
    - Connection pooling for Google Cloud client
    - Async patterns throughout
    - Advanced voice configuration
    - Circuit breaker for failed connections
    - Natural language detection
    """

    def __init__(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int = 2,
        enabled: bool = True,
        **kwargs
    ):
        """Initialize Google TTS provider."""
        super().__init__(provider_name, config, priority, enabled, **kwargs)

        # Google Cloud TTS Configuration
        self._google_config = {
            "max_retries": config.get("max_retries", 3),
            "timeout": config.get("timeout", 30.0),
            "max_text_length": config.get("max_text_length", 5000),
            "voice_name": config.get("voice_name", "en-US-Wavenet-D"),
            "language_code": config.get("language_code", "en-US"),
            "speaking_rate": config.get("speaking_rate", 1.0),
            "pitch": config.get("pitch", 0.0),
            "credentials_path": config.get("credentials_path"),
            "project_id": config.get("project_id"),
            "effects_profile_id": config.get("effects_profile_id"),
            "volume_gain_db": config.get("volume_gain_db", 0.0),
            "sample_rate_hertz": config.get("sample_rate_hertz", 24000)
        }

        # Internal state - lazy initialization pattern
        self._client: Optional[texttospeech_v1.TextToSpeechAsyncClient] = None

        logger.debug(
            "GoogleTTSProvider initialized: voice=%s, language=%s",
            self._google_config["voice_name"],
            self._google_config["language_code"])

    async def get_capabilities(self) -> TTSCapabilities:
        """
        Get Google Cloud TTS provider capabilities.

        Returns:
            TTSCapabilities with Google Cloud specific features
        """
        try:
            # Get available voices if client is initialized
            available_voices = []
            if self._client:
                voices_data = await self.get_available_voices()
                available_voices = [
                    {"name": voice["name"], "language": voice["language_codes"][0]}
                    for voice in voices_data[:10]  # Limit for performance
                ]

            return TTSCapabilities(
                provider_type=ProviderType.GOOGLE,
                supported_formats=[AudioFormat.MP3, AudioFormat.WAV, AudioFormat.OGG],
                supported_languages=[
                    "en-US", "en-GB", "es-US", "fr-FR", "de-DE", "it-IT",
                    "ja-JP", "ko-KR", "zh-CN", "ru-RU", "pt-BR", "nl-NL"
                ],
                available_voices=available_voices,
                max_text_length=self._google_config["max_text_length"],
                supports_ssml=True,
                supports_speed_control=True,
                supports_pitch_control=True,
                supports_custom_voices=False,
                quality_levels=[TTSQuality.STANDARD, TTSQuality.HIGH, TTSQuality.PREMIUM]
            )
        except Exception as e:
            logger.warning("Failed to get Google Cloud TTS capabilities: %s", e)
            # Return basic capabilities on error
            return TTSCapabilities(
                provider_type=ProviderType.GOOGLE,
                supported_formats=[AudioFormat.MP3],
                supported_languages=["en-US"],
                available_voices=[],
                max_text_length=self._google_config["max_text_length"],
                quality_levels=[TTSQuality.STANDARD]
            )

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields for Google Cloud TTS."""
        return ["credentials_path", "project_id"]

    async def initialize(self) -> None:
        """
        Initialize Google Cloud TTS client with performance optimization.

        Implements lazy initialization and connection pooling patterns
        from Phase 1.2.3 performance optimization.
        """
        if not self._google_config["credentials_path"]:
            raise VoiceConfigurationError(
                config_field="credentials_path",
                reason="Google Cloud credentials path not configured"
            )

        if not self._google_config["project_id"]:
            raise VoiceConfigurationError(
                config_field="project_id",
                reason="Google Cloud project ID not configured"
            )

        try:
            # Initialize Google Cloud TTS client
            self._client = texttospeech_v1.TextToSpeechAsyncClient()

            # Test connectivity with a simple list voices call
            await self._test_connectivity()

            logger.debug("Google Cloud TTS client initialized: project=%s",
                        self._google_config["project_id"])

        except Exception as e:
            logger.error("Failed to initialize Google Cloud TTS client: %s", e)
            raise AudioProcessingError(f"Google Cloud TTS initialization failed: {e}") from e

    async def cleanup(self) -> None:
        """Clean up Google Cloud TTS client resources."""
        if self._client:
            # Google Cloud clients auto-manage their connections
            # Just reset the reference
            self._client = None
        logger.debug("Google Cloud TTS client cleaned up")

    async def _test_connectivity(self) -> None:
        """Test Google Cloud TTS API connectivity."""
        if not self._client:
            raise AudioProcessingError("Google Cloud TTS client not initialized")

        try:
            # Simple API call to verify connectivity
            await self._client.list_voices()
            logger.debug("Google Cloud TTS connectivity test successful")
        except Exception as e:
            logger.error("Google Cloud TTS connectivity test failed: %s", e)
            raise AudioProcessingError(f"Google Cloud TTS connectivity failed: {e}") from e

    @provider_operation("Google TTS Synthesis")
    async def _synthesize_implementation(self, request: TTSRequest) -> str:
        """
        Core synthesis implementation for Google Cloud TTS with ConnectionManager support.

        Phase 3.5.2.3 Enhancement:
        - ConnectionManager integration for enhanced retry logic
        - Backward compatibility with legacy retry fallback
        - SOLID principles maintained
        """
        if not self._client:
            await self.initialize()

        # Prepare synthesis parameters
        synthesis_params = self._prepare_synthesis_params(request)

        # Use ConnectionManager if available, fallback to legacy retry
        if self._has_connection_manager():
            audio_data = await self._perform_synthesis(synthesis_params)
        else:
            # Legacy fallback for backward compatibility
            audio_data = await self._synthesize_with_retry(synthesis_params)

        # Upload to storage and return URL
        audio_url = await self._upload_audio_to_storage(
            audio_data,
            f"google_tts_{int(time.time() * 1000)}.mp3",
            request
        )

        return audio_url

    async def _perform_synthesis(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Enhanced synthesis with direct Google TTS client

        Phase 3.5.2.3: Uses direct client for simplicity
        """
        if not self._client:
            raise AudioProcessingError("Google Cloud TTS client not initialized")
        return await self._execute_google_synthesis(synthesis_params)

    async def _execute_google_synthesis(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Direct Google API call - used by ConnectionManager

        Single Responsibility: Only API communication
        """
        response = await self._client.synthesize_speech(**synthesis_params)
        return response.audio_content

    def _prepare_synthesis_params(self, request: TTSRequest) -> Dict[str, Any]:
        """
        Prepare Google Cloud TTS synthesis parameters.

        Implements advanced voice configuration based on quality settings.
        """
        # Configure voice and language
        voice_name, language_code = self._configure_voice_settings(request)

        # Build synthesis components
        synthesis_input = types.SynthesisInput(text=request.text)
        voice = self._create_voice_selection_params(language_code, voice_name)
        audio_config = self._create_audio_config(request)

        return {
            "input": synthesis_input,
            "voice": voice,
            "audio_config": audio_config
        }

    def _configure_voice_settings(self, request: TTSRequest) -> tuple[str, str]:
        """Configure voice name and language code based on request quality."""
        voice_name = request.voice or self._google_config["voice_name"]
        language_code = request.language or self._google_config["language_code"]

        # Advanced voice configuration for premium quality
        if request.quality in [TTSQuality.HIGH, TTSQuality.PREMIUM]:
            voice_name = self._upgrade_to_premium_voice(voice_name, language_code)

        return voice_name, language_code

    def _upgrade_to_premium_voice(self, voice_name: str, language_code: str) -> str:
        """Upgrade to high-quality voice if available."""
        if "Journey" not in voice_name and "WaveNet" not in voice_name:
            return self._get_premium_voice_name(language_code)
        return voice_name

    def _create_voice_selection_params(self, language_code: str, voice_name: str) -> types.VoiceSelectionParams:
        """Create voice selection parameters."""
        return types.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name
        )

    def _create_audio_config(self, request: TTSRequest) -> types.AudioConfig:
        """Create audio configuration with quality optimization."""
        audio_config = types.AudioConfig(
            audio_encoding=types.AudioEncoding.MP3,
            speaking_rate=request.speed or self._google_config["speaking_rate"],
            pitch=self._google_config["pitch"],
            volume_gain_db=self._google_config["volume_gain_db"],
            sample_rate_hertz=self._google_config["sample_rate_hertz"]
        )

        # Add effects profile for premium quality
        if (request.quality == TTSQuality.PREMIUM and
            self._google_config["effects_profile_id"]):
            audio_config.effects_profile_id = [self._google_config["effects_profile_id"]]

        return audio_config

    def _get_premium_voice_name(self, language_code: str) -> str:
        """Get premium voice name for the specified language."""
        # Mapping of language codes to premium voices
        premium_voices = {
            "en-US": "en-US-Journey-D",
            "en-GB": "en-GB-Journey-D",
            "es-US": "es-US-Journey-D",
            "fr-FR": "fr-FR-Journey-D",
            "de-DE": "de-DE-Journey-D",
            "it-IT": "it-IT-Journey-D",
            "ja-JP": "ja-JP-Wavenet-A",
            "ko-KR": "ko-KR-Wavenet-A",
            "zh-CN": "zh-CN-Wavenet-A",
            "ru-RU": "ru-RU-Wavenet-A"
        }

        return premium_voices.get(language_code, self._google_config["voice_name"])

    async def _synthesize_with_retry(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Legacy synthesis with retry logic for Google Cloud API.

        Phase 3.5.2.3: Preserved for backward compatibility
        Will be deprecated after ConnectionManager migration is complete
        """
        last_exception = None

        for attempt in range(self._google_config["max_retries"] + 1):
            try:
                # Use direct API call method
                return await self._execute_google_synthesis(synthesis_params)

            except google_exceptions.Unauthenticated as e:
                # Don't retry authentication errors
                raise AudioProcessingError(f"Google Cloud authentication failed: {e}") from e

            except (google_exceptions.RetryError, google_exceptions.GoogleAPICallError, Exception) as e:
                last_exception = e
                if not await self._handle_google_retryable_error(e, attempt):
                    break
                continue

        # Should not reach here, but in case all retries failed
        raise AudioProcessingError(
            f"Google Cloud TTS failed after {self._google_config['max_retries']} retries: {last_exception}"
        )

    async def _handle_google_retryable_error(self, error: Exception, attempt: int) -> bool:
        """
        Handle retryable Google Cloud errors with exponential backoff.

        Returns:
            bool: True if should retry, False if max retries exceeded
        """
        if attempt >= self._google_config["max_retries"]:
            self._raise_google_retry_exceeded_error(error)
            return False

        if not self._should_retry_google_error(error):
            self._raise_google_non_retryable_error(error)
            return False

        delay = 2 ** attempt  # Exponential backoff
        error_type = self._get_google_error_type(error)

        logger.warning("Google Cloud %s, retrying in %ss (attempt %s)", error_type, delay, attempt + 1)
        await asyncio.sleep(delay)
        return True

    def _should_retry_google_error(self, error: Exception) -> bool:
        """Check if Google Cloud error should be retried."""
        if isinstance(error, google_exceptions.GoogleAPICallError):
            return error.code in [429, 503]  # Rate limit or service unavailable
        return isinstance(error, (google_exceptions.RetryError, Exception))

    def _get_google_error_type(self, error: Exception) -> str:
        """Get human-readable error type for Google Cloud errors."""
        if isinstance(error, google_exceptions.RetryError):
            return "retry error"
        if isinstance(error, google_exceptions.GoogleAPICallError):
            return "API error"
        return "unexpected error"

    def _raise_google_non_retryable_error(self, error: Exception) -> None:
        """Raise appropriate error for non-retryable Google Cloud exceptions."""
        if isinstance(error, google_exceptions.GoogleAPICallError):
            raise AudioProcessingError(f"Google Cloud API error: {error}")

    def _raise_google_retry_exceeded_error(self, error: Exception) -> None:
        """Raise appropriate error when max retries exceeded for Google Cloud."""
        if isinstance(error, google_exceptions.RetryError):
            raise AudioProcessingError(f"Google Cloud retry limit exceeded: {error}")
        if isinstance(error, google_exceptions.GoogleAPICallError):
            raise AudioProcessingError(f"Google Cloud API error: {error}")
        raise AudioProcessingError(f"Google Cloud TTS synthesis failed: {error}")

    async def get_available_voices(self) -> List[Dict[str, Any]]:
        """
        Get list of available voices from Google Cloud TTS.

        Returns:
            List of voice configurations with metadata
        """
        if not self._client:
            await self.initialize()

        try:
            response = await self._client.list_voices()

            voices = []
            for voice in response.voices:
                voice_info = {
                    "name": voice.name,
                    "language_codes": list(voice.language_codes),
                    "ssml_gender": voice.ssml_gender.name,
                    "natural_sample_rate_hertz": voice.natural_sample_rate_hertz
                }
                voices.append(voice_info)

            return voices

        except Exception as e:
            logger.error("Failed to get Google Cloud voices: %s", e)
            raise AudioProcessingError(f"Failed to retrieve Google Cloud voices: {e}") from e

    async def _upload_audio_to_storage(
        self,
        audio_data: bytes,
        filename: str,
        request: TTSRequest
    ) -> str:
        """
        Upload generated audio to MinIO storage and return presigned URL.

        Args:
            audio_data: Synthesized audio bytes
            filename: Generated filename for the audio file
            request: Original TTS request

        Returns:
            Presigned URL for audio file access

        Raises:
            AudioProcessingError: If upload fails
        """
        try:
            # Get voice configuration
            voice_config = get_config()
            storage_config = voice_config.file_storage
            
            # Initialize MinIO manager
            minio_manager = MinioFileManager(
                endpoint=storage_config.minio_endpoint or "127.0.0.1:9000",
                access_key=storage_config.minio_access_key or "minioadmin", 
                secret_key=storage_config.minio_secret_key or "minioadmin",
                bucket_name=storage_config.minio_bucket or "voice-files",
                secure=storage_config.minio_secure or False
            )
            
            # Ensure MinIO manager is initialized
            await minio_manager.initialize()

            # Generate object key from filename  
            agent_id = getattr(request, 'agent_id', 'default')
            user_id = getattr(request, 'user_id', 'default')
            object_key = f"tts/google/{agent_id}/{user_id}/{filename}"

            # Determine content type (Google TTS typically outputs MP3)
            content_type = "audio/mpeg"  # Google TTS default format

            # Upload file to MinIO
            await minio_manager.upload_file(
                file_data=audio_data,
                object_key=object_key,
                content_type=content_type,
                metadata={
                    "provider": "google",
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "voice": getattr(request, 'voice', 'default'),
                    "language": getattr(request, 'language', 'en-US'),
                    "format": "mp3",
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
            )

            # Generate presigned URL (expires in 24 hours)
            presigned_url = await minio_manager.generate_presigned_url(
                object_key=object_key,
                expires_hours=24,
                method="GET"
            )

            # Cleanup MinIO manager
            await minio_manager.cleanup()

            logger.debug("Audio uploaded to MinIO: %s (size: %d bytes)", 
                        object_key, len(audio_data))

            return presigned_url

        except Exception as e:
            logger.error("Failed to upload audio to MinIO storage: %s", e, exc_info=True)
            raise AudioProcessingError(f"Audio storage upload failed: {e}") from e

    def _estimate_audio_duration(self, text: str, speaking_rate: float = 1.0) -> float:
        """
        Estimate audio duration for Google Cloud TTS.

        Based on average speaking rate of ~150 words per minute.
        """
        words = len(text.split())
        base_duration = (words / 150) * 60  # Base duration in seconds
        return base_duration / speaking_rate

    def _get_provider_metadata(self, request: TTSRequest) -> Dict[str, Any]:
        """Get provider-specific metadata for the synthesis result."""
        return {
            "voice_name": request.voice or self._google_config["voice_name"],
            "language_code": request.language or self._google_config["language_code"],
            "speaking_rate": request.speed or self._google_config["speaking_rate"],
            "pitch": self._google_config["pitch"],
            "audio_encoding": "MP3",
            "sample_rate_hertz": self._google_config["sample_rate_hertz"],
            "effects_profile": self._google_config["effects_profile_id"],
            "provider_version": "texttospeech_v1"
        }

    async def health_check(self) -> bool:
        """
        Perform health check for Google Cloud TTS service.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            if not self._client:
                await self.initialize()

            # Test with list voices call
            await self._client.list_voices()
            return True

        except Exception as e:
            logger.warning("Google Cloud TTS health check failed: %s", e)
            return False

    @property
    def provider_type(self) -> ProviderType:
        """Get the provider type."""
        return ProviderType.GOOGLE

    @property
    def supported_formats(self) -> List[AudioFormat]:
        """Get supported audio formats for Google Cloud TTS."""
        return [
            AudioFormat.MP3,
            AudioFormat.WAV,
            AudioFormat.OGG
        ]

    @property
    def max_text_length(self) -> int:
        """Maximum text length supported by Google Cloud TTS."""
        return self._google_config["max_text_length"]

    def __str__(self) -> str:
        """String representation of the provider."""
        return (f"GoogleTTSProvider(voice={self._google_config['voice_name']}, "
               f"language={self._google_config['language_code']}, enabled={self.enabled})")

    def __repr__(self) -> str:
        """Detailed string representation of the provider."""
        return (
            f"GoogleTTSProvider("
            f"voice={self._google_config['voice_name']}, "
            f"language={self._google_config['language_code']}, "
            f"priority={self.priority}, "
            f"enabled={self.enabled}"
            f")"
        )
