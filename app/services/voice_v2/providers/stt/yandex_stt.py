"""
Yandex SpeechKit STT Provider для Voice_v2 - Phase 3.1.4

SOLID Principles Implement        return STTCapabilities(
            provider_type=ProviderType.YANDEX,
            supported_formats=[
                AudioFormat.WAV, AudioFormat.MP3, AudioFormat.OGG,
                AudioFormat.FLAC, AudioFormat.OPUS, AudioFormat.M4A
            ],
            supported_languages=[
                "ru-RU", "en-US", "tr-TR", "uk-UA", "uz-UZ", "kk-KK"
            ],Single Responsibility: Только Yandex SpeechKit STT операции
- Open/Closed: Расширяемый через config, закрытый для модификации
- Liskov Substitution: Полная взаимозаменяемость с BaseSTTProvider
- Interface Segregation: Использует только необходимые методы интерфейса
- Dependency Inversion: Зависит на абстракциях, не на конкретных реализациях

Performance Target: ≤2.5s для 30-секундного аудио
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from io import BytesIO

from aiohttp import ClientTimeout, ClientSession, TCPConnector

from app.core.config import settings
from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.core.schemas import STTRequest
from app.services.voice_v2.providers.stt.models import STTResult, STTCapabilities, STTQuality
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.core.exceptions import (
    VoiceServiceError,
    ProviderNotAvailableError,
    AudioProcessingError,
    VoiceServiceTimeout
)
from app.services.voice_v2.utils.performance import PerformanceTimer
from app.services.voice_v2.providers.retry_mixin import provider_operation

logger = logging.getLogger(__name__)


class YandexSTTProvider(BaseSTTProvider):
    """
    High-performance Yandex SpeechKit STT Provider for voice_v2.

    Features:
    - API Key authentication (NOT IAM Token - per project requirements)
    - Connection pooling for performance optimization
    - OGG to WAV conversion for WhatsApp compatibility
    - Comprehensive error handling with proper fallback
    - Performance metrics collection
    - SOLID principles compliance (SRP, OCP, LSP, ISP, DIP)

    Performance targets:
    - Connection initialization: ≤100ms
    - STT processing: ≤2.5s for 30s audio
    - Error recovery: ≤50ms
    """

    # Yandex SpeechKit constants
    STT_API_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    MAX_FILE_SIZE_MB = 1.0  # Yandex limit for synchronous recognition
    DEFAULT_SAMPLE_RATE = 16000

    def __init__(self, provider_name: str, config: Dict[str, Any], priority: int = 3, enabled: bool = True, **kwargs):
        """Initialize Yandex STT provider with connection pooling."""
        super().__init__(provider_name, config, priority, enabled, **kwargs)

        # Yandex API credentials
        self.api_key = config.get("api_key") or (
            settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
        )
        self.folder_id = config.get("folder_id") or settings.YANDEX_FOLDER_ID

        # Connection configuration
        self.max_connections = config.get("max_connections", 10)
        self.connection_timeout = config.get("connection_timeout", 30.0)
        self.read_timeout = config.get("read_timeout", 60.0)

        # Session management
        self._session: Optional[ClientSession] = None
        self._connector: Optional[TCPConnector] = None

        # Performance tracking
        self._request_count = 0
        self._total_processing_time = 0.0

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields for Yandex provider."""
        return []  # API key and folder_id can come from settings

    async def get_capabilities(self) -> STTCapabilities:
        """Get Yandex STT provider capabilities."""
        return STTCapabilities(
            provider_type=ProviderType.YANDEX,
            supported_formats=[
                AudioFormat.WAV, AudioFormat.MP3, AudioFormat.OGG,
                AudioFormat.FLAC, AudioFormat.OPUS, AudioFormat.M4A
            ],
            supported_languages=[
                "ru-RU", "en-US", "tr-TR", "uk-UA", "uz-UZ", "kk-KK"
            ],
            max_file_size_mb=self.MAX_FILE_SIZE_MB,
            max_duration_seconds=30.0,  # Yandex sync recognition limit
            supports_quality_levels=[STTQuality.STANDARD, STTQuality.HIGH],
            supports_language_detection=False,
            supports_word_timestamps=False,
            supports_speaker_diarization=False
        )

    async def initialize(self) -> None:
        """Initialize Yandex STT provider with optimized connection pooling."""
        if self._initialized:
            return

        try:
            with PerformanceTimer("yandex_stt_init") as timer:
                # Validate credentials
                if not self.api_key:
                    raise ProviderNotAvailableError(
                        provider="Yandex STT",
                        reason="API key not configured (YANDEX_API_KEY)"
                    )

                if not self.folder_id:
                    raise ProviderNotAvailableError(
                        provider="Yandex STT",
                        reason="Folder ID not configured (YANDEX_FOLDER_ID)"
                    )

                # Create optimized connection pool
                self._connector = TCPConnector(
                    limit=self.max_connections,
                    limit_per_host=self.max_connections,
                    keepalive_timeout=30,
                    use_dns_cache=True,
                    ttl_dns_cache=300
                )

                # Create HTTP session with performance optimizations
                timeout = ClientTimeout(
                    total=self.connection_timeout + self.read_timeout,
                    connect=self.connection_timeout,
                    sock_read=self.read_timeout
                )

                self._session = ClientSession(
                    connector=self._connector,
                    timeout=timeout,
                    headers={"User-Agent": "PlatformAI-Voice-v2/1.0"}
                )

                # Perform health check
                await self.health_check()

            self._initialized = True
            logger.info(f"Yandex STT provider initialized in {timer.elapsed_ms:.1f}ms")

        except Exception as e:
            logger.error(f"Yandex STT initialization failed: {e}", exc_info=True)
            await self._cleanup_connections()

            if isinstance(e, (ProviderNotAvailableError, VoiceServiceError)):
                raise
            raise ProviderNotAvailableError(f"Yandex STT init error: {e}")

    async def cleanup(self) -> None:
        """Clean up Yandex STT provider resources."""
        await self._cleanup_connections()
        self._initialized = False
        logger.info("Yandex STT provider cleaned up")

    async def _cleanup_connections(self) -> None:
        """Internal method to clean up HTTP connections."""
        if self._session:
            await self._session.close()
            self._session = None

        if self._connector:
            await self._connector.close()
            self._connector = None

    async def health_check(self) -> bool:
        """Check Yandex SpeechKit API availability."""
        if not self._session:
            return False

        try:
            # Quick health check with minimal request
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
                "Content-Type": "application/octet-stream"
            }

            # Send minimal test request
            test_data = b'\x00' * 16  # Minimal audio data for test

            async with self._session.post(
                self.STT_API_URL,
                headers=headers,
                data=test_data,
                params={"lang": "ru-RU", "format": "lpcm", "sampleRateHertz": 16000}
            ) as response:
                # Accept any non-401 status (even errors are okay for health check)
                is_healthy = response.status != 401

                if not is_healthy:
                    logger.warning(f"Yandex STT health check failed: status {response.status}")

                return is_healthy

        except Exception as e:
            logger.warning(f"Yandex STT health check error: {e}")
            return False

    async def _validate_request(self, request: STTRequest) -> None:
        """Override request validation to handle language normalization."""
        from ...utils.validators import ConfigurationValidator

        # Validate audio data exists
        if not request.audio_data or len(request.audio_data) == 0:
            raise AudioProcessingError("Audio data is empty")

        # Validate audio data size (approximate check)
        if len(request.audio_data) > 25 * 1024 * 1024:  # 25MB limit
            raise AudioProcessingError("Audio data too large")

        if not ConfigurationValidator.validate_language_code(request.language):
            raise AudioProcessingError(f"Bad language: {request.language}")

        # Check capabilities with language normalization
        caps = await self.get_capabilities()
        
        # Use format from request or default to mp3
        if request.format:
            fmt = request.format.value
        else:
            fmt = "mp3"  # Default format

        # Convert AudioFormat enums to strings for comparison
        supported_formats = [af.value for af in caps.supported_formats]
        if fmt not in supported_formats:
            raise AudioProcessingError(f"Format {fmt} unsupported")

        # Quality check - use default STANDARD quality (no quality in new schema)
        # if request.quality not in caps.supports_quality_levels:
        #     raise AudioProcessingError(f"Quality {request.quality.value} unsupported")

        # Language validation with normalization for Yandex
        if request.language != "auto":
            normalized_lang = self._normalize_language(request.language)
            if normalized_lang not in caps.supported_languages:
                raise AudioProcessingError(f"Language {request.language} (normalized: {normalized_lang}) unsupported")

    @provider_operation("Yandex STT Transcription")
    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        """
        Core Yandex STT transcription implementation with ConnectionManager support

        Phase 3.5.2.3 Enhancement:
        - ConnectionManager integration for enhanced retry logic
        - Backward compatibility with legacy retry fallback
        - SOLID principles maintained
        """
        if not self._session:
            raise VoiceServiceError("Yandex STT not initialized")

        with PerformanceTimer("yandex_stt_transcribe") as timer:
            try:
                # Use audio_data directly from request
                audio_data = request.audio_data

                # Validate file size
                if len(audio_data) > self.MAX_FILE_SIZE_MB * 1024 * 1024:
                    raise AudioProcessingError(
                        f"File too large: {len(audio_data)} bytes (max: {self.MAX_FILE_SIZE_MB}MB)"
                    )

                # Process audio data for Yandex compatibility
                # Use format from request or default
                audio_format = request.format.value if request.format else "mp3"
                processed_audio, yandex_format = await self._process_audio_data(audio_data, audio_format)

                # Prepare API request
                headers = {
                    "Authorization": f"Api-Key {self.api_key}",
                    "x-folder-id": self.folder_id,
                    "Content-Type": "application/octet-stream"
                }

                params = {
                    "lang": self._normalize_language(request.language),
                    "format": yandex_format,
                    "sampleRateHertz": self.DEFAULT_SAMPLE_RATE
                }

                # Skip custom settings (not available in new schema)
                # if request.custom_settings:
                #     params.update(request.custom_settings)

                # Use ConnectionManager if available, fallback to legacy retry
                if self._has_connection_manager():
                    transcript, metadata = await self._perform_transcription(headers, params, processed_audio)
                else:
                    # Legacy fallback for backward compatibility
                    transcript, metadata = await self._transcribe_with_retry(headers, params, processed_audio)

                # Update performance metrics
                self._request_count += 1
                self._total_processing_time += timer.elapsed_seconds

                return STTResult(
                    text=transcript,
                    confidence=metadata.get("confidence", 1.0),
                    language_detected=params["lang"],
                    processing_time=timer.elapsed_seconds,
                    word_count=len(transcript.split()) if transcript else 0,
                    provider_metadata={
                        "yandex_format": yandex_format,
                        "original_format": audio_format,
                        "file_size_bytes": len(audio_data),
                        "response_metadata": metadata
                    }
                )

            except Exception as e:
                logger.error(f"Yandex STT transcription failed: {e}", exc_info=True)

                if isinstance(e, (VoiceServiceError, AudioProcessingError)):
                    raise

                raise VoiceServiceError(f"Yandex STT error: {e}") from e

    async def _perform_transcription(
        self,
        headers: Dict[str, str],
        params: Dict[str, Any],
        audio_data: bytes
    ) -> tuple[str, Dict[str, Any]]:
        """
        Enhanced transcription with ConnectionManager integration

        Phase 3.5.2.3: Uses centralized retry logic from ConnectionManager
        """
        return await self._execute_with_connection_manager(
            operation_name="yandex_stt_transcription",
            request_func=self._execute_yandex_transcription,
            headers=headers,
            params=params,
            audio_data=audio_data
        )

    async def _execute_yandex_transcription(
        self,
        session,  # ConnectionManager provides session
        headers: Dict[str, str],
        params: Dict[str, Any],
        audio_data: bytes,
        **kwargs  # Accept all additional ConnectionManager parameters
    ) -> tuple[str, Dict[str, Any]]:
        """
        Direct Yandex API call - used by ConnectionManager

        Single Responsibility: Only API communication
        """
        async with session.post(
            self.STT_API_URL,
            headers=headers,
            params=params,
            data=audio_data
        ) as response:

            if response.status == 200:
                result = await response.json()
                transcript = result.get("result", "")

                return transcript, {
                    "confidence": 1.0,  # Yandex doesn't always return confidence
                    "response_data": result
                }

            # Handle other errors
            error_text = await response.text()
            raise VoiceServiceError(
                f"Yandex API error {response.status}: {error_text}"
            )

    async def _load_audio_file(self, audio_path: Path) -> bytes:
        """Load audio file with error handling."""
        try:
            return audio_path.read_bytes()
        except Exception as e:
            raise AudioProcessingError(f"Failed to load audio file: {e}") from e

    async def _process_audio_data(self, audio_data: bytes, audio_format: str) -> tuple[bytes, str]:
        """
        Process audio data for Yandex compatibility.

        Handles format conversion for Yandex SpeechKit requirements.
        """
        file_extension = audio_format.lower()

        # Check if OGG file needs conversion (WhatsApp compatibility)
        if file_extension == 'ogg':
            return await self._convert_ogg_to_wav(audio_data, file_extension)
        
        # Convert MP3 to OPUS for better Yandex compatibility
        if file_extension == 'mp3':
            return await self._convert_mp3_to_opus(audio_data)
        
        # Check if FLAC needs conversion to WAV for Yandex
        if file_extension == 'flac':
            return await self._convert_to_wav(audio_data, file_extension)

        # Map file extension to Yandex format
        # Yandex SpeechKit API expects specific format strings
        format_mapping = {
            'wav': 'lpcm',
            'mp3': 'mp3',       # MP3 as native format
            'flac': 'lpcm',     # FLAC should be converted to LPCM
            'opus': 'oggopus',
            'ogg': 'oggopus'
        }

        yandex_format = format_mapping.get(file_extension, 'lpcm')
        return audio_data, yandex_format

    async def _convert_ogg_to_wav(self, audio_data: bytes, audio_format: str) -> tuple[bytes, str]:
        """Convert OGG to WAV for WhatsApp files using pydub."""
        try:
            from pydub import AudioSegment

            # Check audio data header
            if audio_data[:4] == b'RIFF' or audio_data[8:12] == b'WAVE':
                logger.debug("Audio data is already WAV format")
                return audio_data, 'lpcm'

            # Try loading as OGG first
            try:
                audio_segment = AudioSegment.from_ogg(BytesIO(audio_data))
            except Exception:
                # Try auto-detection
                try:
                    audio_segment = AudioSegment.from_file(BytesIO(audio_data))
                except Exception:
                    # Use original data as fallback
                    return audio_data, 'oggopus'

            if audio_segment:
                # Convert to WAV with Yandex-compatible parameters
                wav_buffer = BytesIO()
                audio_segment.export(
                    wav_buffer,
                    format="wav",
                    parameters=["-ar", str(self.DEFAULT_SAMPLE_RATE), "-ac", "1"]
                )

                converted_data = wav_buffer.getvalue()
                logger.info(f"Converted OGG to WAV: {len(audio_data)} -> {len(converted_data)} bytes")
                return converted_data, 'lpcm'

            # Fallback to original data
            return audio_data, 'oggopus'

        except Exception as e:
            logger.warning(f"Audio conversion failed: {e}")
            return audio_data, 'oggopus'

    async def _convert_mp3_to_opus(self, audio_data: bytes) -> tuple[bytes, str]:
        """Convert MP3 to OPUS for better Yandex SpeechKit compatibility."""
        try:
            from pydub import AudioSegment

            # Load MP3 audio data
            audio_segment = AudioSegment.from_mp3(BytesIO(audio_data))

            if audio_segment:
                # Convert to OPUS with Yandex-compatible parameters
                opus_buffer = BytesIO()
                audio_segment.export(
                    opus_buffer,
                    format="opus",
                    parameters=["-ar", str(self.DEFAULT_SAMPLE_RATE), "-ac", "1"]
                )

                converted_data = opus_buffer.getvalue()
                logger.info(f"Converted MP3 to OPUS: {len(audio_data)} -> {len(converted_data)} bytes")
                return converted_data, 'oggopus'

            # Fallback to original data
            return audio_data, 'mp3'

        except Exception as e:
            logger.warning(f"MP3 to OPUS conversion failed: {e}")
            return audio_data, 'mp3'

    async def _convert_to_wav(self, audio_data: bytes, audio_format: str) -> tuple[bytes, str]:
        """Convert MP3/FLAC to WAV for Yandex SpeechKit compatibility."""
        try:
            from pydub import AudioSegment

            # Load audio data from bytes
            audio_segment = AudioSegment.from_file(BytesIO(audio_data), format=audio_format)

            if audio_segment:
                # Convert to WAV with Yandex-compatible parameters
                wav_buffer = BytesIO()
                audio_segment.export(
                    wav_buffer,
                    format="wav",
                    parameters=["-ar", str(self.DEFAULT_SAMPLE_RATE), "-ac", "1"]
                )

                converted_data = wav_buffer.getvalue()
                logger.info(f"Converted {audio_format.upper()} to WAV: {len(audio_data)} -> {len(converted_data)} bytes")
                return converted_data, 'lpcm'

            # Fallback to original data
            return audio_data, 'lpcm'

        except Exception as e:
            logger.warning(f"Audio conversion from {audio_format} to WAV failed: {e}")
            return audio_data, 'lpcm'

    def _normalize_language(self, language: str) -> str:
        """Normalize language code for Yandex API."""
        if language == "auto":
            return "ru-RU"  # Default to Russian

        # Map common codes to Yandex format
        language_mapping = {
            "ru": "ru-RU",
            "en": "en-US",
            "tr": "tr-TR",
            "uk": "uk-UA",
            "uz": "uz-UZ",
            "kk": "kk-KK"
        }

        return language_mapping.get(language, language)

    async def _transcribe_with_retry(
        self,
        headers: Dict[str, str],
        params: Dict[str, Any],
        audio_data: bytes
    ) -> tuple[str, Dict[str, Any]]:
        """
        Legacy transcription with exponential backoff retry

        Phase 3.5.2.3: Preserved for backward compatibility
        Will be deprecated after ConnectionManager migration is complete
        """
        max_retries = 2

        for attempt in range(max_retries + 1):
            try:
                # Use direct API call method with self._session for legacy calls
                transcript, metadata = await self._execute_yandex_transcription(self._session, headers, params, audio_data)
                # Add attempt info to metadata for compatibility
                metadata["attempt"] = attempt + 1
                return transcript, metadata

            except VoiceServiceError as e:
                # Check for rate limit specifically
                if "429" in str(e) and attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait_time)
                    continue
                # Re-raise other VoiceServiceErrors
                if attempt == max_retries:
                    raise

            except asyncio.TimeoutError:
                if attempt < max_retries:
                    logger.warning(f"Request timeout, retrying (attempt {attempt + 1})")
                    continue
                raise VoiceServiceTimeout(
                    operation="Yandex STT request",
                    timeout_seconds=self.read_timeout,
                    provider="yandex"
                )

            except Exception as e:
                if attempt < max_retries and not isinstance(e, VoiceServiceError):
                    logger.warning(f"Request failed, retrying (attempt {attempt + 1}): {e}")
                    continue
                raise

        raise VoiceServiceError("All Yandex STT retry attempts failed")

    def get_status_info(self) -> Dict[str, Any]:
        """Get provider status information."""
        return {
            "provider_name": self.provider_name,
            "enabled": self.enabled,
            "initialized": self._initialized,
            "priority": self.priority,
            "request_count": self._request_count,
            "avg_processing_time": (
                self._total_processing_time / self._request_count
                if self._request_count > 0 else 0.0
            ),
            "capabilities": {
                "max_file_size_mb": self.MAX_FILE_SIZE_MB,
                "api_endpoint": self.STT_API_URL
            }
        }
