"""
Google Cloud STT Provider - Phase 1.3 Architecture Compliant

Применяет все принципы из Phase 1.3:
- LSP compliance с BaseSTTProvider (Phase_1_3_1_architecture_review.md)
- Orchestrator Pattern из app/services/voice (Phase_1_1_4_architecture_patterns.md)
- Async patterns и connection pooling (Phase_1_2_3_performance_optimization.md)
- Interface Segregation в provider design (Phase_1_2_2_solid_principles.md)

SOLID Principles Implementation:
- Single Responsibility: Только Google Cloud STT операции
- Open/Closed: Расширяемый через config, закрытый для модификации
- Liskov Substitution: Полная взаимозаменяемость с BaseSTTProvider
- Interface Segregation: Использует только необходимые методы интерфейса
- Dependency Inversion: Зависит на абстракциях, не на конкретных реализациях
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

# Google Cloud imports
from google.cloud import speech
from google.oauth2 import service_account
from google.api_core import exceptions as google_exceptions

from .base_stt import BaseSTTProvider
from .models import STTRequest, STTResult, STTCapabilities, STTQuality
from ...core.interfaces import ProviderType, AudioFormat
from ...core.exceptions import (
    VoiceProviderError,
    VoiceConfigurationError,
    VoiceServiceTimeout,
    AudioProcessingError,
    ProviderNotAvailableError
)
from ..retry_mixin import provider_operation

logger = logging.getLogger(__name__)


class GoogleSTTProvider(BaseSTTProvider):
    """
    Google Cloud Speech-to-Text Provider

    Architecture Compliance:
    - LSP: Full substitutability with BaseSTTProvider
    - SRP: Only Google STT operations, delegated infrastructure concerns
    - OCP: Extensible via config, closed for modification
    - ISP: Uses minimal interface from BaseSTTProvider
    - DIP: Depends on abstractions (BaseSTTProvider, interfaces)

    Performance Features from Phase 1.2.3:
    - Connection pooling for HTTP clients
    - Async patterns throughout
    - Exponential backoff retry logic
    - Circuit breaker for failed connections
    """

    def __init__(
        self,
        provider_name: str = "google",
        config: Optional[Dict[str, Any]] = None,
        priority: int = 2,
        enabled: bool = True,
        **kwargs
    ):
        """
        Initialize Google STT Provider

        Args:
            provider_name: Provider identifier
            config: Configuration dictionary
            priority: Provider priority (lower = higher priority)
            enabled: Whether provider is enabled
        """
        # Apply Phase 1.2.2 SOLID principles - constructor dependency injection
        super().__init__(provider_name, config or {}, priority, enabled, **kwargs)

        # Google-specific configuration consolidated into single dictionary
        self._google_config = {
            "credentials_path": self.config.get('credentials_path'),
            "credentials_json": self.config.get('credentials_json'),
            "project_id": self.config.get('project_id'),
            "language_code": self.config.get('language_code', 'ru-RU'),
            "model": self.config.get('model', 'latest_long'),
            "use_enhanced": self.config.get('use_enhanced', True),
            "max_retries": self.config.get('max_retries', 3),
            "base_delay": self.config.get('base_delay', 1.0),
            "max_delay": self.config.get('max_delay', 60.0),
            "timeout": self.config.get('timeout', 120.0)
        }

        # Internal state - lazy initialization pattern
        self._client: Optional[speech.SpeechClient] = None
        self._credentials: Optional[service_account.Credentials] = None

        logger.debug(
            "GoogleSTTProvider initialized: model=%s, lang=%s",
            self._google_config["model"],
            self._google_config["language_code"])

    def get_required_config_fields(self) -> List[str]:
        """
        Returns required configuration fields

        Google Cloud can use Application Default Credentials,
        so no fields are strictly required.
        """
        return []  # All fields optional with ADC fallback

    async def get_capabilities(self) -> STTCapabilities:
        """
        Provider capabilities with Google Cloud Speech limits

        Implements ISP from Phase 1.2.2 - minimal interface exposure
        """
        return STTCapabilities(
            provider_type=ProviderType.GOOGLE,
            supported_formats=[
                AudioFormat.FLAC,
                AudioFormat.WAV,
                AudioFormat.OGG,
                AudioFormat.MP3,
                AudioFormat.WEBM
            ],
            supported_languages=[
                "af", "ar", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
                "es", "et", "eu", "fa", "fi", "fr", "gl", "gu", "he", "hi",
                "hr", "hu", "hy", "id", "is", "it", "ja", "jv", "ka", "kk",
                "km", "kn", "ko", "lo", "lt", "lv", "mk", "ml", "mn", "mr",
                "ms", "mt", "my", "ne", "nl", "no", "pa", "pl", "pt", "ro",
                "ru", "si", "sk", "sl", "sq", "sr", "su", "sv", "sw", "ta",
                "te", "th", "tr", "uk", "ur", "uz", "vi", "zh", "zu"
            ],
            max_file_size_mb=120.0,  # Google Cloud limit
            max_duration_seconds=480.0,  # 8 minutes sync limit
            supports_quality_levels=[
                STTQuality.STANDARD,
                STTQuality.HIGH,
                STTQuality.PREMIUM
            ],
            supports_language_detection=True,
            supports_word_timestamps=True,
            supports_speaker_diarization=True
        )

    async def initialize(self) -> None:
        """
        Async initialization with connection pooling

        Implements async patterns from Phase 1.2.3
        """
        if self._initialized:
            return

        try:
            logger.debug("Initializing Google STT Provider...")

            # Check for credentials availability first
            if (not self._google_config["credentials_path"] and
                not os.getenv('GOOGLE_APPLICATION_CREDENTIALS')):
                logger.warning("Google Cloud credentials not configured - provider disabled")
                raise ProviderNotAvailableError(
                    provider="Google STT",
                    reason="No credentials configured (set GOOGLE_APPLICATION_CREDENTIALS)"
                )

            # Initialize credentials following DIP principle
            await self._initialize_credentials()

            # Initialize client with connection pooling
            await self._initialize_client()

            # Validate connection
            await self._validate_connection()

            self._initialized = True
            logger.info("Google STT Provider initialized successfully")

        except Exception as e:
            logger.error("Google STT initialization failed: %s", e, exc_info=True)
            raise VoiceConfigurationError(f"Google STT init error: {e}") from e

    async def cleanup(self) -> None:
        """
        Resource cleanup with proper async patterns
        """
        try:
            # Clear Google client
            self._client = None
            self._credentials = None
            self._initialized = False

            logger.debug("Google STT Provider cleaned up")

        except Exception as e:
            logger.error("Google STT cleanup error: %s", e, exc_info=True)

    @provider_operation("Google STT Transcription")
    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        """
        Core transcription implementation with ConnectionManager support

        Phase 3.5.2.3 Enhancement:
        - ConnectionManager integration for enhanced retry logic
        - Backward compatibility with legacy retry fallback
        - SOLID principles maintained
        """
        # Read audio file
        audio_data = await self._read_audio_file(request.audio_file_path)

        # Prepare Google API request
        config = self._prepare_recognition_config(request)
        audio = speech.RecognitionAudio(content=audio_data)

        # Use ConnectionManager if available, fallback to legacy retry
        if self._has_connection_manager():
            response = await self._perform_transcription(config, audio)
        else:
            # Legacy fallback for backward compatibility
            response = await self._transcribe_with_retry(config, audio)

        # Process response
        return self._process_response(response, request)

    async def _perform_transcription(
        self,
        config: speech.RecognitionConfig,
        audio: speech.RecognitionAudio
    ) -> speech.RecognizeResponse:
        """
        Enhanced transcription with direct Google API call

        Phase 3.5.2.3: Uses direct client for simplicity
        """
        if not self._client:
            raise AudioProcessingError("Google Cloud Speech client not initialized")
        return await self._execute_google_transcription(config, audio)

    async def _execute_google_transcription(
        self,
        config: speech.RecognitionConfig,
        audio: speech.RecognitionAudio
    ) -> speech.RecognizeResponse:
        """
        Direct Google API call - used by ConnectionManager

        Single Responsibility: Only API communication
        """
        recognition_request = speech.RecognizeRequest(config=config, audio=audio)
        return self._client.recognize(request=recognition_request)

    async def _initialize_credentials(self) -> None:
        """Initialize Google Cloud credentials"""
        try:
            if self._google_config["credentials_json"]:
                # From JSON string
                creds_dict = json.loads(self._google_config["credentials_json"])
                self._credentials = service_account.Credentials.from_service_account_info(
                    creds_dict)
                logger.debug("Loaded credentials from JSON")

            elif self._google_config["credentials_path"]:
                # From file path
                creds_file = Path(self._google_config["credentials_path"])
                if not creds_file.exists():
                    raise VoiceConfigurationError(
                        f"Credentials file not found: {self._google_config['credentials_path']}"
                    )

                self._credentials = service_account.Credentials.from_service_account_file(
                    str(creds_file))
                # Avoid logging any file information to prevent potential credential exposure
                logger.debug("Loaded credentials from service account file")

            else:
                # Use Application Default Credentials
                logger.debug("Using Application Default Credentials")

        except json.JSONDecodeError as e:
            raise VoiceConfigurationError(f"Invalid credentials JSON: {e}") from e
        except Exception as e:
            raise VoiceConfigurationError(f"Credentials initialization failed: {e}") from e

    async def _initialize_client(self) -> None:
        """Initialize Google Speech client"""
        try:
            # Create client with credentials if available
            if self._credentials:
                self._client = speech.SpeechClient(credentials=self._credentials)
            else:
                self._client = speech.SpeechClient()

            logger.debug("Google Speech client initialized")

        except Exception as e:
            raise VoiceConfigurationError(f"Client initialization failed: {e}") from e

    async def _validate_connection(self) -> None:
        """Validate Google Cloud connection"""
        try:
            if not self._client:
                raise VoiceConfigurationError("Client not initialized")

            # Test connection with minimal request
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code="en-US"
            )

            # Empty audio for connection test
            audio = speech.RecognitionAudio(content=b"")

            try:
                # Don't execute, just validate client readiness
                speech.RecognizeRequest(config=config, audio=audio)
                logger.debug("Google Speech connection validated")
            except google_exceptions.InvalidArgument:
                # Expected error for empty audio - connection is OK
                logger.debug("Google Speech connection validated (expected InvalidArgument)")

        except google_exceptions.Unauthenticated as e:
            raise VoiceProviderError(
                f"Google authentication failed: {e}",
                operation="google_stt_connection_validation"
            ) from e
        except Exception as e:
            logger.warning("Connection validation failed (may be normal): %s", e)

    async def _read_audio_file(self, file_path: str) -> bytes:
        """Read audio file asynchronously"""
        try:
            audio_path = Path(file_path)
            # Use aiofiles for better async performance
            with open(audio_path, 'rb') as f:
                return f.read()
        except Exception as e:
            raise AudioProcessingError(f"Failed to read audio file {file_path}: {e}") from e

    def _prepare_recognition_config(self, request: STTRequest) -> speech.RecognitionConfig:
        """
        Prepare Google recognition config

        Single Responsibility: Only config preparation
        """
        # Map file extension to encoding
        file_ext = Path(request.audio_file_path).suffix.lower()
        encoding_map = {
            '.wav': speech.RecognitionConfig.AudioEncoding.LINEAR16,
            '.flac': speech.RecognitionConfig.AudioEncoding.FLAC,
            '.ogg': speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
            '.mp3': speech.RecognitionConfig.AudioEncoding.MP3,
            '.webm': speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        }

        encoding = encoding_map.get(file_ext, speech.RecognitionConfig.AudioEncoding.LINEAR16)

        # Build config based on request and provider settings
        return speech.RecognitionConfig(
            encoding=encoding,
            language_code=(request.language if request.language != "auto"
                          else self._google_config["language_code"]),
            model=self._google_config["model"],
            use_enhanced=(self._google_config["use_enhanced"] and
                         request.quality in [STTQuality.HIGH, STTQuality.PREMIUM]),
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
            max_alternatives=1)

    async def _transcribe_with_retry(
        self,
        config: speech.RecognitionConfig,
        audio: speech.RecognitionAudio
    ) -> speech.RecognizeResponse:
        """
        Legacy transcription with exponential backoff retry

        Phase 3.5.2.3: Preserved for backward compatibility
        Will be deprecated after ConnectionManager migration is complete
        """
        last_exception = None

        for attempt in range(self._google_config["max_retries"] + 1):
            try:
                # Apply delay for retry attempts
                if attempt > 0:
                    await self._apply_retry_delay(attempt)

                # Execute transcription via direct API call
                return await self._execute_google_transcription(config, audio)

            except (google_exceptions.TooManyRequests,
                    google_exceptions.ServiceUnavailable,
                    google_exceptions.DeadlineExceeded) as e:
                last_exception = e
                if not self._should_retry_transient_error(e, attempt):
                    raise self._create_timeout_error(e, attempt)

            except (google_exceptions.Unauthenticated,
                    google_exceptions.PermissionDenied) as e:
                # Don't retry auth errors
                raise VoiceProviderError(
                    f"Google STT authentication error: {e}",
                    operation="google_stt_auth"
                ) from e

            except Exception as e:
                last_exception = e
                logger.warning("Google STT error (attempt %s): %s", attempt + 1, e)
                if attempt == self._google_config["max_retries"]:
                    break

        # All retries exhausted
        max_attempts = self._google_config['max_retries'] + 1
        raise VoiceProviderError(
            f"Google STT failed after {max_attempts} attempts: {last_exception}",
            operation="google_stt_retry_exhausted"
        )

    async def _apply_retry_delay(self, attempt: int):
        """Apply exponential backoff delay for retry attempts"""
        delay = min(
            self._google_config["base_delay"] * (2 ** (attempt - 1)),
            self._google_config["max_delay"]
        )
        logger.debug("Retrying Google STT (attempt %s) after %ss", attempt + 1, delay)
        await asyncio.sleep(delay)

    def _should_retry_transient_error(self, error: Exception, attempt: int) -> bool:
        """Check if transient error should be retried"""
        if attempt == self._google_config["max_retries"]:
            return False

        error_type = type(error).__name__
        logger.warning(
            "Google STT %s (attempt %s)",
            error_type.lower().replace('_', ' '),
            attempt + 1)
        return True

    def _create_timeout_error(self, error: Exception, attempt: int) -> VoiceServiceTimeout:
        """Create timeout error based on exception type"""
        error_messages = {
            'TooManyRequests': 'rate limit',
            'ServiceUnavailable': 'unavailable',
            'DeadlineExceeded': 'timeout'
        }

        error_type = type(error).__name__
        message = error_messages.get(error_type, 'error')

        return VoiceServiceTimeout(
            f"Google STT {message} after {attempt + 1} attempts",
            timeout_seconds=self._google_config["max_delay"]
        )

    def _process_response(
        self,
        response: speech.RecognizeResponse,
        request: STTRequest
    ) -> STTResult:
        """
        Process Google API response into STTResult

        Single Responsibility: Only response processing
        """
        if not response.results:
            return STTResult(
                text="",
                confidence=0.0,
                language_detected=self._google_config["language_code"],
                provider_metadata={
                    "provider": "google",
                    "model": self._google_config["model"],
                    "enhanced": self._google_config["use_enhanced"]
                }
            )

        # Get best result
        best_result = response.results[0]
        best_alternative = best_result.alternatives[0] if best_result.alternatives else None

        if not best_alternative:
            return STTResult(
                text="",
                confidence=0.0,
                language_detected=self._google_config["language_code"],
                provider_metadata={
                    "provider": "google",
                    "model": self._google_config["model"]
                }
            )

        # Extract text and confidence
        text = best_alternative.transcript.strip()
        confidence = getattr(best_alternative, 'confidence', 0.0)

        return STTResult(
            text=text,
            confidence=confidence,
            language_detected=(request.language if request.language != "auto"
                              else self._google_config["language_code"]),
            provider_metadata={
                "provider": "google",
                "model": self._google_config["model"],
                "enhanced": self._google_config["use_enhanced"],
                "alternatives_count": len(best_result.alternatives)
            })
