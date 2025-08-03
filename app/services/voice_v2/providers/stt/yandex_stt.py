"""
Yandex SpeechKit STT Provider - Consolidated Version

Интегрированная версия Yandex STT provider с встроенной audio processing логикой.
Removed YandexAudioProcessor extraction for consolidation and simplification.

Features:
- Consolidated audio processing
- SOLID principles compliance
- Performance optimization
- Reduced file dependencies
"""

import asyncio
import logging
import time
import io
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import ClientSession, TCPConnector, ClientTimeout

from app.services.voice_v2.core.exceptions import (
    AudioProcessingError,
    VoiceServiceError,
    VoiceServiceTimeout
)
from app.services.voice_v2.core.schemas import STTRequest
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.stt.models import STTResult, STTCapabilities, STTQuality
from app.core.config import settings
from .initialization_mixin import STTInitializationMixin
from .retry_mixin import STTRetryMixin

logger = logging.getLogger(__name__)


class YandexSTTProvider(BaseSTTProvider, STTInitializationMixin, STTRetryMixin):
    """
    Consolidated Yandex SpeechKit STT Provider с deduplication mixins.

    Phase 1.3 Deduplication:
    - STTInitializationMixin: стандартизация init/cleanup (~60 строк экономии)
    - STTRetryMixin: унификация retry логики (~40 строк экономии)
    - SOLID: Single responsibility, DRY principle compliance

    Integrated audio processing for simplified architecture.
    Single Responsibility: Complete Yandex STT integration.
    """

    # Yandex SpeechKit constants
    STT_API_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    MAX_FILE_SIZE_MB = 1.0
    DEFAULT_SAMPLE_RATE = 16000
    TIMEOUT_SECONDS = 30

    def __init__(self,
                 provider_name: str,
                 config: Dict[str, Any],
                 priority: int = 3,
                 enabled: bool = True,
                 **kwargs):
        """Initialize Yandex STT provider."""
        super().__init__(provider_name, config, priority, enabled, **kwargs)

        # Consolidate configuration - use fewer instance variables
        self._config = {
            'api_key': config.get("api_key") or (
                settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
            ),
            'folder_id': config.get("folder_id") or settings.YANDEX_FOLDER_ID,
            'max_connections': config.get("max_connections", 10),
            'connection_timeout': config.get("connection_timeout", 30.0),
            'read_timeout': config.get("read_timeout", 60.0),
            'supported_formats': ["wav", "ogg", "opus", "mp3"]
        }

        # Session management
        self._session: Optional[ClientSession] = None
        self._connector: Optional[TCPConnector] = None

        # Performance tracking
        self._stats = {'request_count': 0, 'total_processing_time': 0.0}

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields."""
        return []

    async def get_capabilities(self) -> STTCapabilities:
        """Get provider capabilities."""
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
            max_duration_seconds=300.0,
            supports_quality_levels=[STTQuality.HIGH],
            supports_speaker_diarization=False
        )

    def get_supported_formats(self) -> List[str]:
        """Get supported audio formats."""
        return [
            "wav", "opus", "mp3", "flac", "m4a", "ogg"
        ]

    def get_supported_languages(self) -> List[str]:
        """Get supported languages."""
        return [
            "ru-RU", "en-US", "tr-TR", "de-DE", "es-ES", "fr-FR", 
            "it-IT", "pl-PL", "kk-KZ", "uz-UZ", "he-IL", "ar-AE"
        ]

    async def initialize(self) -> None:
        """
        Инициализация через STTInitializationMixin - устранение дублирования.

        Phase 1.3 Deduplication: Использует стандартную логику инициализации.
        """
        # Создаем валидации через mixin
        validations = [
            self._create_api_key_validation(
                'api_key',
                "Yandex API key not configured"
            ),
            self._create_api_key_validation(
                'folder_id',
                "Yandex folder ID not configured"
            )
        ]

        # Создаем фабрику клиента
        async def create_yandex_client():
            await self._setup_connection_pool()

        # Используем стандартную инициализацию
        await self._standard_initialize(
            validation_checks=validations,
            client_factory=create_yandex_client,
            health_check=self.health_check,
            provider_name=self.provider_name
        )

    async def _setup_connection_pool(self) -> None:
        """Setup HTTP connection pool."""
        self._connector = TCPConnector(
            limit=self._config['max_connections'],
            limit_per_host=self._config['max_connections'],
            ttl_dns_cache=300,
            use_dns_cache=True,
            enable_cleanup_closed=True
        )

        timeout = ClientTimeout(
            total=self._config['connection_timeout'] + self._config['read_timeout'],
            connect=self._config['connection_timeout'],
            sock_read=self._config['read_timeout']
        )

        self._session = ClientSession(
            connector=self._connector,
            timeout=timeout,
            headers={'Authorization': f'Api-Key {self._config["api_key"]}'}
        )

    async def cleanup(self) -> None:
        """
        Очистка ресурсов через STTInitializationMixin - устранение дублирования.

        Phase 1.3 Deduplication: Использует стандартную логику cleanup.
        """
        # Создаем задачи очистки через mixin
        cleanup_tasks = [
            self._cleanup_connections
        ]

        # Используем стандартную очистку
        await self._standard_cleanup(
            cleanup_tasks=cleanup_tasks,
            provider_name=self.provider_name
        )

    async def _cleanup_connections(self) -> None:
        """Cleanup HTTP connections."""
        if self._session:
            await self._session.close()
            self._session = None

        if self._connector:
            await self._connector.close()
            self._connector = None

    async def health_check(self) -> bool:
        """Check provider health."""
        try:
            if not self._session:
                await self._setup_connection_pool()

            # Simple health check with minimal audio data
            test_audio = b"dummy_audio_data_for_health_check"

            async with self._session.post(
                self.STT_API_URL,
                data=test_audio,
                params={
                    "folderId": self._config['folder_id'],
                    "format": "wav",
                    "sampleRateHertz": self.DEFAULT_SAMPLE_RATE
                },
                timeout=ClientTimeout(total=10.0)
            ) as response:
                # Even if transcription fails, 200 or 400 means service is up
                return response.status in [200, 400]

        except (VoiceServiceError, AudioProcessingError):
            return False
        except (OSError, ConnectionError) as e:
            logger.warning("Yandex STT health check failed: %s", e)
            return False

    def _validate_request(
        self,
        audio_data: bytes,
        audio_format: str,
        language: str
    ) -> Dict[str, Any]:
        """Validate STT request."""
        if not audio_data:
            raise AudioProcessingError("Empty audio data")

        if len(audio_data) > self.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise AudioProcessingError(f"Audio file too large: {len(audio_data)} bytes")

        if not self.validate_audio_format(audio_format):
            raise AudioProcessingError(f"Unsupported audio format: {audio_format}")

        return {
            "audio_data_size": len(audio_data),
            "audio_format": audio_format,
            "language": language
        }

    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        """Main transcription implementation."""
        start_time = time.perf_counter()

        try:
            # Validate request
            self._validate_request(
                request.audio_data,
                request.audio_format,
                request.language or "ru-RU"
            )

            # Process audio data
            processed_audio, final_format = await self.process_audio_data(
                request.audio_data,
                request.audio_format
            )

            # Perform transcription with retry
            result = await self._transcribe_with_retry(
                audio_data=processed_audio,
                audio_format=final_format,
                language=request.language or "ru-RU",
                enable_profanity_filter=request.enable_profanity_filter
            )

            # Update performance stats
            processing_time = time.perf_counter() - start_time
            logger.debug("Yandex STT transcription completed in %.3fs", processing_time)
            self._update_performance_stats(processing_time)

            return result

        except Exception as e:
            logger.error("Yandex STT transcription failed: %s", e)
            raise

    async def _transcribe_with_retry(self,
                                     audio_data: bytes,
                                     audio_format: str,
                                     language: str,
                                     enable_profanity_filter: bool = True) -> STTResult:
        """
        Transcription replaced with STTRetryMixin - устранение дублирования.

        Phase 1.3 Deduplication: Использует стандартную retry логику через mixin.
        """
        # Подготавливаем параметры
        normalized_language = self.normalize_language(language)
        params = self._prepare_transcription_params(
            normalized_language, audio_format, enable_profanity_filter
        )

        # Создаем операцию транскрипции через mixin
        async def transcription_operation():
            if not self._session:
                await self._setup_connection_pool()
            return await self._execute_transcription_request(audio_data, params)

        # Используем стандартную retry логику
        return await self._standard_transcribe_with_retry(
            transcription_func=transcription_operation,
            error_handlers=self._get_yandex_error_handlers(),
            provider_name=self.provider_name
        )

    def _prepare_transcription_params(self, language: str, audio_format: str,
                                    enable_profanity_filter: bool) -> Dict[str, str]:
        """Prepare parameters for Yandex STT API request."""
        return {
            "folderId": self._config['folder_id'],
            "format": audio_format,
            "sampleRateHertz": self.DEFAULT_SAMPLE_RATE,
            "lang": language,
            "profanityFilter": str(enable_profanity_filter).lower()
        }

    async def _execute_transcription_request(self, audio_data: bytes,
                                           params: Dict[str, str]) -> STTResult:
        """Execute transcription request to Yandex API."""
        async with self._session.post(
            self.STT_API_URL,
            data=audio_data,
            params=params
        ) as response:
            if response.status == 200:
                result = await response.json()
                return self._parse_transcription_result(result)

            await self._handle_api_error_response(response)

    async def _handle_api_error_response(self, response) -> None:
        """Handle API error responses from Yandex."""
        error_text = await response.text()
        raise VoiceServiceError(
            f"Yandex STT API error {response.status}: {error_text}"
        )

    async def _handle_yandex_timeout_error(self, attempt: int, max_retries: int) -> bool:
        """Handle timeout errors with retry logic."""
        if attempt < max_retries - 1:
            await asyncio.sleep(1)
            return True
        raise VoiceServiceTimeout(
            "Yandex STT request timeout",
            timeout_seconds=self.TIMEOUT_SECONDS
        )

    async def _handle_yandex_general_error(self, error: Exception,
                                         attempt: int, max_retries: int) -> bool:
        """Handle general errors with retry logic."""
        if attempt < max_retries - 1:
            await asyncio.sleep(1)
            return True
        raise error

    def _parse_transcription_result(self, result: Dict[str, Any]) -> STTResult:
        """Parse Yandex API response."""
        result_text = result.get('result', '').strip()

        return STTResult(
            text=result_text,
            confidence=0.95,  # Yandex doesn't provide confidence scores
            language_detected=None,  # Yandex doesn't return detected language
            processing_time=0.0,  # Will be set by caller
            provider_metadata={
                "yandex_result": result,
                "api_version": "v1"
            }
        )

    def _update_performance_stats(self, processing_time: float) -> None:
        """Update internal performance statistics."""
        self._stats['request_count'] += 1
        self._stats['total_processing_time'] += processing_time

    def get_status_info(self) -> Dict[str, Any]:
        """Get provider status information."""
        avg_time = (
            self._stats['total_processing_time'] / self._stats['request_count']
            if self._stats['request_count'] > 0 else 0.0
        )

        return {
            "provider_name": self.provider_name,
            "provider_type": "yandex_stt",
            "status": "ready" if self._session else "not_initialized",
            "config": {
                "api_url": self.STT_API_URL,
                "max_file_size_mb": self.MAX_FILE_SIZE_MB,
                "max_connections": self._config['max_connections'],
                "connection_timeout": self._config['connection_timeout'],
                "read_timeout": self._config['read_timeout']
            },
            "performance": {
                "requests_processed": self._stats['request_count'],
                "average_processing_time": round(avg_time, 3),
                "total_processing_time": round(self._stats['total_processing_time'], 3)
            },
            "capabilities": {
                "supports_streaming": False,
                "supports_real_time": False,
                "max_file_size_mb": self.MAX_FILE_SIZE_MB,
                "supported_formats": ["wav", "opus", "ogg", "mp3"],
                "supported_languages": ["ru-RU", "en-US", "tr-TR", "uk-UA"]
            }
        }

    # Audio processing methods (integrated from YandexAudioProcessor)

    def validate_audio_format(self, audio_format: str) -> bool:
        """Validate if audio format is supported"""
        # Исправлено: обрабатываем enum AudioFormat
        audio_format_str = audio_format
        if hasattr(audio_format, 'value'):  # Это enum
            audio_format_str = audio_format.value
        return audio_format_str.lower() in self._config['supported_formats']

    def normalize_language(self, language: str) -> str:
        """Normalize language code for Yandex API"""
        language_map = {
            "ru": "ru-RU", "en": "en-US", "tr": "tr-TR", "uk": "uk-UA",
            "uz": "uz-UZ", "kk": "kk-KK", "de": "de-DE", "fr": "fr-FR",
            "es": "es-ES", "it": "it-IT", "he": "he-IL", "ar": "ar-AE"
        }

        # Handle full locale codes
        if "-" in language:
            return language

        return language_map.get(language.lower(), "ru-RU")

    async def process_audio_data(self, audio_data: bytes, audio_format: str) -> Tuple[bytes, str]:
        """Process audio data for Yandex STT API"""
        format_lower = audio_format.lower()

        # Direct passthrough for supported formats
        if format_lower in ["wav", "opus"]:
            return audio_data, format_lower

        # Convert unsupported formats to WAV
        if format_lower in ["ogg", "mp3"]:
            return await self._convert_to_wav(audio_data, format_lower)

        # Default fallback
        return audio_data, "wav"

    async def _convert_to_wav(self, audio_data: bytes, audio_format: str) -> Tuple[bytes, str]:
        """Convert audio to WAV format"""
        try:
            # Import at function level for better error handling
            from pydub import AudioSegment  # pylint: disable=import-outside-toplevel

            # Determine input format
            if audio_format == "mp3":
                audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            elif audio_format == "ogg":
                audio_segment = AudioSegment.from_ogg(io.BytesIO(audio_data))
            else:
                audio_segment = AudioSegment.from_file(io.BytesIO(audio_data))

            # Convert to WAV with Yandex-optimal parameters
            wav_io = io.BytesIO()
            audio_segment.export(
                wav_io,
                format="wav",
                parameters=["-ar", "16000", "-ac", "1", "-sample_fmt", "s16"]
            )

            converted_data = wav_io.getvalue()
            logger.debug(
                "Converted %s to WAV: %d -> %d bytes",
                audio_format, len(audio_data), len(converted_data)
            )

            return converted_data, "wav"

        except ImportError as exc:
            logger.error("pydub not available for audio conversion")
            raise RuntimeError("Audio conversion not available - pydub required") from exc
        except Exception as e:
            logger.error("Audio conversion to WAV failed: %s", e)
            raise

    def _get_yandex_error_handlers(self) -> dict:
        """Создает error handlers для Yandex STT операций."""
        def handle_timeout_error(error, attempt: int) -> bool:
            logger.warning("Yandex STT timeout (attempt %s): %s", attempt + 1, error)
            return True  # Retry timeouts

        def handle_service_error(error, attempt: int) -> bool:
            logger.warning("Yandex STT service error (attempt %s): %s", attempt + 1, error)
            return True  # Retry service errors

        def handle_audio_error(error, _attempt: int) -> bool:
            logger.error("Yandex STT audio error: %s", error)
            return False  # Don't retry audio processing errors

        return {
            asyncio.TimeoutError: handle_timeout_error,
            VoiceServiceError: handle_service_error,
            AudioProcessingError: handle_audio_error,
        }
