"""
Yandex SpeechKit STT Provider - Simplified Version

Упрощенная версия Yandex STT provider с модульной архитектурой.
Reduced from 614 to ~350 lines через extraction audio processing логики.

Features:
- Модульная архитектура с YandexAudioProcessor
- SOLID principles compliance
- Reduced cyclomatic complexity
- Performance optimization
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any

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
from .yandex_audio_processor import YandexAudioProcessor

logger = logging.getLogger(__name__)


class YandexSTTProvider(BaseSTTProvider):
    """
    Simplified Yandex SpeechKit STT Provider.

    Delegated audio processing to YandexAudioProcessor for modularity.
    Single Responsibility: Yandex API integration and coordination.
    """

    # Yandex SpeechKit constants
    STT_API_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    MAX_FILE_SIZE_MB = 1.0
    DEFAULT_SAMPLE_RATE = 16000

    def __init__(self, provider_name: str, config: Dict[str, Any], priority: int = 3, enabled: bool = True, **kwargs):
        """Initialize Yandex STT provider."""
        super().__init__(provider_name, config, priority, enabled, **kwargs)

        # API credentials
        self.api_key = config.get("api_key") or (
            settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
        )
        self.folder_id = config.get("folder_id") or settings.YANDEX_FOLDER_ID

        # Connection configuration
        self.max_connections = config.get("max_connections", 10)
        self.connection_timeout = config.get("connection_timeout", 30.0)
        self.read_timeout = config.get("read_timeout", 60.0)

        # Modular components
        self.audio_processor = YandexAudioProcessor()

        # Session management
        self._session: Optional[ClientSession] = None
        self._connector: Optional[TCPConnector] = None

        # Performance tracking
        self._request_count = 0
        self._total_processing_time = 0.0

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
            quality=STTQuality.HIGH,
            supports_streaming=False,
            supports_real_time=False,
            supports_speaker_diarization=False
        )

    async def initialize(self) -> None:
        """Initialize provider and connections."""
        if not self.api_key:
            raise ProviderNotAvailableError("Yandex API key not configured")

        if not self.folder_id:
            raise ProviderNotAvailableError("Yandex folder ID not configured")

        await self._setup_connection_pool()

        # Test connection
        if not await self.health_check():
            raise ProviderNotAvailableError("Yandex STT service not available")

        logger.info("Yandex STT provider '%s' initialized successfully", self.provider_name)

    async def _setup_connection_pool(self) -> None:
        """Setup HTTP connection pool."""
        self._connector = TCPConnector(
            limit=self.max_connections,
            limit_per_host=self.max_connections,
            ttl_dns_cache=300,
            use_dns_cache=True,
            enable_cleanup_closed=True
        )

        timeout = ClientTimeout(
            total=self.connection_timeout + self.read_timeout,
            connect=self.connection_timeout,
            sock_read=self.read_timeout
        )

        self._session = ClientSession(
            connector=self._connector,
            timeout=timeout,
            headers={'Authorization': f'Api-Key {self.api_key}'}
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        await self._cleanup_connections()

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
                    "folderId": self.folder_id,
                    "format": "wav",
                    "sampleRateHertz": self.DEFAULT_SAMPLE_RATE
                },
                timeout=ClientTimeout(total=10.0)
            ) as response:
                # Even if transcription fails, 200 or 400 means service is up
                return response.status in [200, 400]

        except Exception as e:
            logger.warning("Yandex STT health check failed: %s", e)
            return False

    async def _validate_request(self, request: STTRequest) -> None:
        """Validate STT request."""
        if not request.audio_data:
            raise AudioProcessingError("Empty audio data")

        if len(request.audio_data) > self.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise AudioProcessingError(f"Audio file too large: {len(request.audio_data)} bytes")

        if not self.audio_processor.validate_audio_format(request.audio_format):
            raise AudioProcessingError(f"Unsupported audio format: {request.audio_format}")

    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        """Main transcription implementation."""
        timer = PerformanceTimer()

        try:
            # Validate request
            await self._validate_request(request)

            # Process audio data
            processed_audio, final_format = await self.audio_processor.process_audio_data(
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
            processing_time = timer.get_elapsed_seconds()
            self._update_performance_stats(processing_time)

            return result

        except Exception as e:
            logger.error("Yandex STT transcription failed: %s", e)
            raise

    async def _transcribe_with_retry(self,
                                   audio_data: bytes,
                                   audio_format: str,
                                   language: str,
                                   enable_profanity_filter: bool = True,
                                   max_retries: int = 3) -> STTResult:
        """Perform transcription with retry logic."""
        normalized_language = self.audio_processor.normalize_language(language)

        for attempt in range(max_retries):
            try:
                if not self._session:
                    await self._setup_connection_pool()

                params = {
                    "folderId": self.folder_id,
                    "format": audio_format,
                    "sampleRateHertz": self.DEFAULT_SAMPLE_RATE,
                    "lang": normalized_language,
                    "profanityFilter": str(enable_profanity_filter).lower()
                }

                async with self._session.post(
                    self.STT_API_URL,
                    data=audio_data,
                    params=params
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return self._parse_transcription_result(result)
                    else:
                        error_text = await response.text()
                        if response.status == 429 and attempt < max_retries - 1:
                            # Rate limiting - wait and retry
                            await asyncio.sleep(2 ** attempt)
                            continue

                        raise VoiceServiceError(
                            f"Yandex STT API error {response.status}: {error_text}"
                        )

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise VoiceServiceTimeout("Yandex STT request timeout")

            except Exception:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise

        raise VoiceServiceError("All retry attempts failed")

    def _parse_transcription_result(self, result: Dict[str, Any]) -> STTResult:
        """Parse Yandex API response."""
        result_text = result.get('result', '').strip()

        return STTResult(
            text=result_text,
            confidence=0.95,  # Yandex doesn't provide confidence scores
            language_detected=None,  # Yandex doesn't return detected language
            processing_time_ms=0,  # Will be set by caller
            provider_specific_data={
                "yandex_result": result,
                "api_version": "v1"
            }
        )

    def _update_performance_stats(self, processing_time: float) -> None:
        """Update internal performance statistics."""
        self._request_count += 1
        self._total_processing_time += processing_time

    def get_status_info(self) -> Dict[str, Any]:
        """Get provider status information."""
        avg_processing_time = (
            self._total_processing_time / self._request_count
            if self._request_count > 0 else 0.0
        )

        return {
            "provider_name": self.provider_name,
            "provider_type": "yandex_stt",
            "status": "ready" if self._session else "not_initialized",
            "config": {
                "api_url": self.STT_API_URL,
                "max_file_size_mb": self.MAX_FILE_SIZE_MB,
                "max_connections": self.max_connections,
                "connection_timeout": self.connection_timeout,
                "read_timeout": self.read_timeout
            },
            "performance": {
                "requests_processed": self._request_count,
                "average_processing_time": round(avg_processing_time, 3),
                "total_processing_time": round(self._total_processing_time, 3)
            },
            "capabilities": {
                "supports_streaming": False,
                "supports_real_time": False,
                "max_file_size_mb": self.MAX_FILE_SIZE_MB,
                "supported_formats": ["wav", "opus", "ogg", "mp3"],
                "supported_languages": ["ru-RU", "en-US", "tr-TR", "uk-UA"]
            }
        }
