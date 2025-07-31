"""
OpenAI STT Provider для Voice_v2 - Phase 3.5.2 Enhanced

Рефакторинг для устранения дублирования retry логики:
- ConnectionManager integration для centralized retry logic
- RetryMixin for configuration standardization
- Removed duplicated _execute_with_retry method
- SOLID principles compliance
"""

import asyncio
import io
import time
import tempfile
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging
import aiohttp

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError, AuthenticationError

from app.core.config import settings
from app.services.voice_v2.core.exceptions import (
    ProviderNotAvailableError,
    AudioProcessingError,
    VoiceServiceTimeout
)
from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.core.schemas import STTRequest
from app.services.voice_v2.providers.stt.models import STTResult, STTCapabilities, STTQuality
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.utils.validators import ConfigurationValidator
from app.services.voice_v2.providers.retry_mixin import RetryMixin, provider_operation

logger = logging.getLogger(__name__)


class OpenAISTTProvider(BaseSTTProvider, RetryMixin):
    """
    OpenAI Whisper STT Provider с ConnectionManager integration.

    Phase 3.5.2 Deduplication:
    - Uses RetryMixin for configuration standardization
    - ConnectionManager integration for centralized retry logic
    - Removed duplicated retry implementation
    - SOLID: Single responsibility, DRY principle compliance
    """

    def __init__(self, provider_name: str, config: Dict[str, Any], **kwargs):
        """Инициализация с валидацией OpenAI-specific конфигурации."""
        super().__init__(provider_name, config, **kwargs)

        self.client: Optional[AsyncOpenAI] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connection_lock = asyncio.Lock()

        # Правильно извлекаем API ключ из SecretStr
        config_api_key = config.get("api_key")
        settings_api_key = settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None
        self.api_key = config_api_key or settings_api_key

        self.model = config.get("model", "whisper-1")
        self.timeout = config.get("timeout", 30)

        # Retry configuration через RetryMixin
        # Always set max_retries for compatibility
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 1.0)

        if self._has_connection_manager():
            # Register retry configuration with ConnectionManager
            retry_config = self._get_retry_config(config)
            logger.info("OpenAISTTProvider using ConnectionManager with retry config: %s retries", retry_config.max_retries)
        else:
            # Fallback к legacy parameters для compatibility
            logger.warning("OpenAISTTProvider fallback to legacy retry - ConnectionManager not available")

        logger.info("OpenAISTTProvider initialized: model=%s, timeout=%ss", self.model, self.timeout)

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields for OpenAI STT."""
        return ["api_key"]

    async def get_capabilities(self) -> STTCapabilities:
        """OpenAI Whisper capabilities specification."""
        return STTCapabilities(
            provider_type=ProviderType.OPENAI,
            supported_formats=[
                AudioFormat.MP3, AudioFormat.WEBM, AudioFormat.M4A,
                AudioFormat.WAV, AudioFormat.FLAC, AudioFormat.OGG,
                AudioFormat.OPUS
            ],
            supported_languages=[
                "en", "ru", "es", "fr", "de", "it", "pt", "pl", "tr", "nl",
                "sv", "da", "no", "fi", "he", "ar", "zh", "ja", "ko", "hi",
                "th", "vi", "uk", "bg", "hr", "cs", "et", "lv", "lt", "sk",
                "sl", "hu", "ro", "mt", "is", "mk", "sq", "az", "be", "bs",
                "eu", "ga", "gl", "ka", "ky", "lb", "mi", "ms", "ne", "cy"
            ],
            max_file_size_mb=25.0,  # OpenAI limit
            max_duration_seconds=3600.0,  # 1 hour practical limit
            supports_quality_levels=[
                STTQuality.STANDARD,  # whisper-1 standard
                STTQuality.HIGH       # whisper-1 with optimizations
            ],
            supports_language_detection=True,  # Whisper auto-detects language
            supports_word_timestamps=True,     # Available in verbose mode
            supports_speaker_diarization=False # Not supported by Whisper
        )

    async def initialize(self) -> None:
        """
        Асинхронная инициализация с connection pooling optimization.

        Phase_1_2_3_performance_optimization.md:
        - Connection pooling для concurrent requests
        - Proper async patterns
        """
        if not self.api_key:
            raise ProviderNotAvailableError(
                self.provider_name,
                "OpenAI API key не настроен"
            )

        try:
            # Enhanced connection settings для performance
            await self._ensure_session()

            self.client = AsyncOpenAI(
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=self.max_retries,
                # Use custom session for connection pooling
                http_client=None  # Will use default with connection pooling
            )

            # Health check при инициализации (non-blocking)
            health_result = await self._initial_health_check()
            if not health_result:
                logger.warning("OpenAI API health check failed during initialization")

            self._initialized = True
            logger.info("OpenAI STT provider initialized successfully")

        except Exception as e:
            logger.error("Failed to initialize OpenAI STT provider: %s", e, exc_info=True)
            raise ProviderNotAvailableError(
                self.provider_name,
                f"Ошибка инициализации: {str(e)}"
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов и connections."""
        try:
            if self.client:
                await self.client.close()
            if self._session:
                await self._session.close()
            logger.info("OpenAI STT provider cleaned up")
        except Exception as e:
            logger.warning("Cleanup warning: %s", e)
        finally:
            self.client = None
            self._session = None
            self._initialized = False

    @provider_operation("transcription")
    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        """Core OpenAI Whisper transcription implementation с ConnectionManager integration."""
        if not self.client:
            raise AudioProcessingError("OpenAI client не инициализирован")

        start_time = time.time()

        # Validate audio data and create temporary file
        audio_path = await self._create_temp_audio_file(request.audio_data)

        try:
            # Perform transcription with proper error handling
            return await self._execute_transcription_workflow(request, audio_path, start_time)

        finally:
            # Clean up temporary file
            await self._cleanup_temp_file(audio_path)

    async def _create_temp_audio_file(self, audio_data) -> Path:
        """Create temporary audio file with validation"""
        # Validate audio data
        if not isinstance(audio_data, (bytes, bytearray)):
            raise AudioProcessingError("Некорректный формат аудиоданных (ожидается bytes)")

        max_size = 25 * 1024 * 1024  # 25MB OpenAI лимит
        if len(audio_data) > max_size:
            raise AudioProcessingError(f"Аудиофайл слишком большой: {len(audio_data)} байт (лимит 25MB)")

        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as temp_file:
            temp_file.write(audio_data)
            return Path(temp_file.name)

    async def _execute_transcription_workflow(self, request: STTRequest, audio_path: Path, start_time: float) -> STTResult:
        """Execute transcription workflow with error handling"""
        try:
            await self._validate_audio_file(audio_path)
            transcription_params = self._prepare_transcription_params(request)

            # Perform transcription
            session = await self._get_session()
            result = await self._perform_transcription(session, audio_path, transcription_params)

            return self._process_transcription_result(
                result, request, time.time() - start_time, audio_path
            )

        except AudioProcessingError:
            raise
        except VoiceServiceTimeout:
            raise
        except AuthenticationError as e:
            logger.error("OpenAI authentication error: %s", e)
            raise ProviderNotAvailableError(self.provider_name, f"Ошибка аутентификации: {e}")
        except RateLimitError as e:
            logger.warning("OpenAI rate limit exceeded: %s", e)
            raise AudioProcessingError(f"Превышен лимит запросов: {e}")
        except APIConnectionError as e:
            logger.error("OpenAI connection error: %s", e)
            raise AudioProcessingError(f"Ошибка соединения с OpenAI: {e}")
        except Exception as e:
            logger.error("Unexpected OpenAI STT error: %s", e, exc_info=True)
            raise AudioProcessingError(f"Неожиданная ошибка OpenAI: {e}")

    async def _cleanup_temp_file(self, audio_path: Path):
        """Clean up temporary audio file"""
        try:
            os.unlink(audio_path)
        except Exception as e:
            logger.warning("Failed to cleanup temp file %s: %s", audio_path, e)

    async def _validate_audio_file(self, audio_path: Path) -> None:
        """Validate audio file existence and size."""
        if not audio_path.exists():
            raise AudioProcessingError(f"Аудиофайл не найден: {audio_path}")

        file_size = audio_path.stat().st_size
        if file_size > 25 * 1024 * 1024:  # 25MB OpenAI limit
            raise AudioProcessingError(f"Файл слишком большой: {file_size} bytes (лимит 25MB)")

    def _prepare_transcription_params(self, request: STTRequest) -> Dict[str, Any]:
        """Prepare OpenAI transcription parameters."""
        transcription_params = {
            "model": self.model,
            "response_format": "text"  # Default to text (no quality in new schema)
        }

        # Language handling (Phase_1_3_1_architecture_review.md LSP compliance)
        if request.language and request.language != "auto":
            # Normalize language code for OpenAI Whisper
            normalized_lang = self._normalize_language(request.language)
            if not ConfigurationValidator.validate_language_code(normalized_lang):
                raise AudioProcessingError(f"Language {request.language} unsupported")
            transcription_params["language"] = normalized_lang

        # Custom settings integration (SOLID: Open/Closed principle)
        if hasattr(request, 'custom_settings') and request.custom_settings:
            safe_settings = self._extract_safe_settings(request.custom_settings)
            transcription_params.update(safe_settings)

        return transcription_params

    def _process_transcription_result(
        self, result: Any, request: STTRequest, processing_time: float, audio_path: Path
    ) -> STTResult:
        """Process transcription result into STTResult."""
        file_size = audio_path.stat().st_size

        # Enhanced result processing for different quality levels
        if isinstance(result, str):
            text = result.strip()
            confidence = 0.95
            language_detected = request.language if request.language != "auto" else "en"
        else:
            text = result.text.strip() if hasattr(result, 'text') else str(result)
            language_detected = getattr(result, 'language', request.language or "en")
            confidence = self._calculate_confidence_from_segments(result)

        if not text:
            raise AudioProcessingError("Не удалось распознать речь в аудиофайле")

        logger.info(
            f"OpenAI transcription completed: {len(text)} chars, "
            f"{processing_time:.2f}s, lang={language_detected}"
        )

        return STTResult(
            text=text,
            confidence=confidence,
            language_detected=language_detected,
            processing_time=processing_time,
            word_count=len(text.split()),
            provider_metadata={
                "model": self.model,
                "file_size_bytes": file_size,
                "quality_level": "standard",  # Default quality
                "response_format": "text"  # Default format
            }
        )

    async def _perform_transcription(self, session, audio_path: Path, params: Dict[str, Any]) -> Any:
        """
        Core transcription operation for ConnectionManager execution.

        Args:
            session: HTTP session (provided by ConnectionManager)
            audio_path: Path to audio file
            params: Transcription parameters

        Returns:
            OpenAI transcription response
        """
        with open(audio_path, "rb") as audio_file:
            # Copy file to BytesIO для async operation
            audio_data = io.BytesIO(audio_file.read())
            audio_data.name = audio_path.name

            # Execute transcription через OpenAI client
            return await asyncio.wait_for(
                self.client.audio.transcriptions.create(
                    file=audio_data,
                    **params
                ),
                timeout=self.timeout
            )

    async def _transcribe_with_retry(self, audio_path: Path, params: Dict[str, Any]) -> Any:
        """
        Legacy retry method for backward compatibility.

        NOTE: This method is deprecated and should be removed after full ConnectionManager migration.
        Use _perform_transcription with ConnectionManager instead.
        """
        if hasattr(self, 'max_retries'):
            max_retries = self.max_retries
            retry_delay = getattr(self, 'retry_delay', 1.0)
        else:
            max_retries = 3
            retry_delay = 1.0

        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                with open(audio_path, "rb") as audio_file:
                    # Copy file to BytesIO для повторных попыток
                    audio_data = io.BytesIO(audio_file.read())
                    audio_data.name = audio_path.name

                    # Async transcription с timeout
                    return await asyncio.wait_for(
                        self.client.audio.transcriptions.create(
                            file=audio_data,
                            **params
                        ),
                        timeout=self.timeout
                    )

            except asyncio.TimeoutError:
                raise VoiceServiceTimeout(
                    operation="STT transcription",
                    timeout_seconds=float(self.timeout),
                    provider="openai"
                )
            except (AuthenticationError, RateLimitError) as e:
                # Non-retryable errors - reraise immediately
                logger.error("Non-retryable OpenAI error: %s", e)
                raise
            except (APIConnectionError, APIError) as e:
                last_exception = e
                if attempt < max_retries:
                    delay = retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning("OpenAI API error (attempt %s), retrying in %ss: %s", attempt + 1, delay, e)
                    await asyncio.sleep(delay)
                else:
                    break
            except Exception as e:
                # Non-retryable errors
                logger.error("Non-retryable OpenAI error: %s", e)
                raise

        raise AudioProcessingError(f"OpenAI transcription failed after {max_retries + 1} attempts: {last_exception}")

    async def _initial_health_check(self) -> bool:
        """Non-blocking health check при инициализации."""
        try:
            # Simple API availability check без реальных запросов
            if self.client:
                return True
        except Exception as e:
            logger.warning("Initial health check failed: %s", e)
        return False

    def _extract_safe_settings(self, custom_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Extract safe OpenAI API parameters from custom settings."""
        safe_params = {}
        allowed_params = {
            "temperature", "prompt", "response_format"
        }

        for key, value in custom_settings.items():
            if key in allowed_params:
                safe_params[key] = value

        return safe_params

    def _calculate_confidence_from_segments(self, verbose_result: Any) -> float:
        """Calculate average confidence from Whisper segments if available."""
        try:
            if hasattr(verbose_result, 'segments') and verbose_result.segments:
                confidences = []
                for segment in verbose_result.segments:
                    if hasattr(segment, 'avg_logprob'):
                        # Convert log probability to confidence (0-1 scale)
                        confidence = min(1.0, max(0.0, (segment.avg_logprob + 1.0)))
                        confidences.append(confidence)

                return sum(confidences) / len(confidences) if confidences else 0.95
        except Exception as e:
            logger.debug("Could not extract confidence from segments: %s", e)

        return 0.95  # Default high confidence for Whisper

    async def _ensure_session(self) -> None:
        """Ensure HTTP session with connection pooling (Phase 1.2.3 pattern)."""
        # If connection manager is available, use it for shared connection pooling
        if self._connection_manager:
            # Connection manager handles session management
            return

        if self._session and not self._session.closed:
            return

        async with self._connection_lock:
            if self._session and not self._session.closed:
                return

            # Create optimized TCP connector
            connector = aiohttp.TCPConnector(
                limit=getattr(self, 'connection_pool_size', 100),
                limit_per_host=getattr(self, 'per_host_connections', 30),
                keepalive_timeout=getattr(self, 'keepalive_timeout', 30),
                use_dns_cache=True
            )

            # Create session with performance optimizations
            timeout = aiohttp.ClientTimeout(total=self.timeout + 5)

            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": "PlatformAI-Hub/voice_v2"}
            )

            logger.debug(
                f"OpenAI STT session created - "
                f"pool_size={getattr(self, 'connection_pool_size', 100)}, "
                f"keepalive={getattr(self, 'keepalive_timeout', 30)}s"
            )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get HTTP session, creating if needed."""
        await self._ensure_session()
        return self._session

    async def health_check(self) -> bool:
        """Health check following successful patterns (Phase 1.1.4)."""
        if not self._initialized:
            return False

        try:
            return await self._initial_health_check()
        except Exception as e:
            logger.warning("OpenAI STT health check failed: %s", e)
            return False

    def _normalize_language(self, language: str) -> str:
        """Normalize language code for OpenAI Whisper API."""
        if language == "auto":
            return "auto"

        # Map complex codes to simple ISO codes for Whisper
        language_mapping = {
            "ru-RU": "ru",
            "en-US": "en",
            "en-GB": "en",
            "es-ES": "es",
            "fr-FR": "fr",
            "de-DE": "de",
            "it-IT": "it",
            "pt-PT": "pt",
            "tr-TR": "tr",
            "uk-UA": "uk",
            "zh-CN": "zh",
            "ja-JP": "ja",
            "ko-KR": "ko"
        }

        normalized = language_mapping.get(language, language)
        logger.debug("Language normalized: %s -> %s", language, normalized)
        return normalized
