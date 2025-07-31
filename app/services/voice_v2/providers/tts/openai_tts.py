"""
OpenAI TTS Provider для Voice_v2 - Phase 3.2.2

Адаптация из app/services/voice/tts/openai_tts.py с улучшениями:
- LSP compliance с BaseTTSProvider (Phase_1_3_1_architecture_review.md)
- Performance optimization через connection pooling (Phase_1_2_3_performance_optimization.md)
- SOLID principles implementation (Phase_1_2_2_solid_principles.md)
- Enhanced voice quality и parallel generation для длинных текстов
- Proper error recovery patterns (Phase_1_1_4_architecture_patterns.md)

SOLID Principles Implementation:
- Single Responsibility: Только OpenAI TTS операции
- Open/Closed: Расширяемый через config, закрытый для модификации
- Liskov Substitution: Полная взаимозаменяемость с BaseTTSProvider
- Interface Segregation: Использует только TTS interface методы
- Dependency Inversion: Depends on abstractions (BaseTTSProvider, models)
"""

import asyncio
import hashlib
import time
from typing import Dict, List, Optional, Any
import logging

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError, AuthenticationError

from app.core.config import settings
from app.services.voice_v2.core.exceptions import (
    AudioProcessingError,
    VoiceServiceTimeout
)
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.core.schemas import TTSRequest
from app.services.voice_v2.providers.tts.models import (
    TTSResult, TTSCapabilities, TTSQuality
)
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.providers.retry_mixin import provider_operation

logger = logging.getLogger(__name__)


class OpenAITTSProvider(BaseTTSProvider):
    """
    OpenAI Text-to-Speech Provider

    Architecture Compliance:
    - LSP: Full substitutability with BaseTTSProvider
    - SRP: Only OpenAI TTS operations, delegated infrastructure concerns
    - OCP: Extensible via config, closed for modification
    - ISP: Uses minimal interface from BaseTTSProvider
    - DIP: Depends on abstractions (BaseTTSProvider, interfaces)

    Performance Features from Phase 1.2.3:
    - Connection pooling for OpenAI client
    - Async patterns throughout
    - Exponential backoff retry logic
    - Circuit breaker for failed connections
    - Voice quality optimization
    - Parallel generation for long texts
    """

    def __init__(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int = 1,
        enabled: bool = True,
        **kwargs
    ):
        """Initialize OpenAI TTS provider with configuration."""
        # Extract configuration first
        self._api_key = config.get("api_key")
        self._model = config.get("model", "tts-1")
        self._voice = config.get("voice", "alloy")
        self._max_retries = config.get("max_retries", 3)
        self._timeout = config.get("timeout", 30.0)

        # Initialize parent with pre-extracted config
        super().__init__(provider_name, config, priority, enabled, **kwargs)

        # Retry configuration
        self._base_delay = config.get("base_delay", 1.0)
        self._max_delay = config.get("max_delay", 60.0)

        # OpenAI client (lazy initialization)
        self._client: Optional[AsyncOpenAI] = None

        # OpenAI TTS limits
        self._max_text_length = 4096
        self._max_concurrent_chunks = 5
        config = config or {}
        super().__init__(provider_name, config, priority, enabled)

        # OpenAI-specific configuration with defaults
        self._api_key = self.config.get('api_key') or (
            settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None
        )
        self._model = self.config.get('model', 'tts-1')
        self._voice = self.config.get('voice', 'alloy')
        self._speed = self.config.get('speed', 1.0)

        # Performance settings from Phase 1.2.3
        self._max_retries = self.config.get('max_retries', 3)
        self._base_delay = self.config.get('base_delay', 1.0)
        self._max_delay = self.config.get('max_delay', 60.0)
        self._timeout = self.config.get('timeout', 30.0)
        self._max_text_length = self.config.get('max_text_length', 4096)

        # Internal state - lazy initialization pattern
        self._client: Optional[AsyncOpenAI] = None

        logger.debug("OpenAITTSProvider initialized: model=%s, voice=%s", self._model, self._voice)

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields for OpenAI TTS."""
        return ["api_key"] if not self._api_key else []

    async def get_capabilities(self) -> TTSCapabilities:
        """
        Get OpenAI TTS provider capabilities.

        Based on OpenAI TTS API documentation and reference system analysis.
        """
        return TTSCapabilities(
            provider_type=ProviderType.OPENAI,
            supported_formats=[
                AudioFormat.MP3,
                AudioFormat.OPUS,
                AudioFormat.FLAC,
                AudioFormat.WAV
            ],
            supported_languages=[
                # OpenAI TTS supports many languages via the underlying model
                "en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh",
                "ar", "hi", "tr", "pl", "nl", "sv", "da", "no", "fi", "cs",
                "sk", "hu", "ro", "bg", "hr", "sl", "et", "lv", "lt", "uk"
            ],
            available_voices=["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
            max_text_length=self._max_text_length,
            supports_ssml=False,  # OpenAI TTS doesn't support SSML
            supports_speed_control=True,  # Speed parameter supported (0.25 to 4.0)
            supports_pitch_control=False,  # No pitch control in OpenAI TTS
            supports_custom_voices=False,  # No custom voice creation
            quality_levels=[TTSQuality.STANDARD, TTSQuality.HIGH]  # tts-1 and tts-1-hd models
        )

    async def initialize(self) -> None:
        """
        Initialize OpenAI client with performance optimization.

        Implements lazy initialization and connection pooling patterns
        from Phase 1.2.3 performance optimization.
        """
        if not self._api_key:
            raise AudioProcessingError("OpenAI API key not configured")

        try:
            # Initialize OpenAI client with performance settings
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                timeout=self._timeout,
                max_retries=self._max_retries
            )

            logger.debug("OpenAI TTS client initialized: model=%s", self._model)

        except Exception as e:
            logger.error("Failed to initialize OpenAI TTS client: %s", e)
            raise AudioProcessingError(f"OpenAI TTS initialization failed: {e}")

    async def cleanup(self) -> None:
        """Clean up OpenAI client resources."""
        if self._client:
            await self._client.close()
            self._client = None
        logger.debug("OpenAI TTS client cleaned up")

    @provider_operation("OpenAI TTS Synthesis")
    async def _synthesize_implementation(self, request: TTSRequest) -> TTSResult:
        """
        Core OpenAI TTS synthesis implementation with ConnectionManager support.

        Phase 3.5.2.3 Enhancement:
        - ConnectionManager integration for enhanced retry logic
        - Backward compatibility with legacy retry fallback
        - SOLID principles maintained
        """
        if not self._client:
            await self.initialize()

        # Prepare synthesis parameters
        synthesis_params = self._prepare_synthesis_params(request)

        start_time = time.time()

        # Use ConnectionManager if available, fallback to legacy retry
        if self._has_connection_manager():
            audio_data = await self._perform_synthesis(synthesis_params)
        else:
            # Legacy fallback for backward compatibility
            audio_data = await self._synthesize_with_retry(synthesis_params)

        processing_time = time.time() - start_time

        # Upload to MinIO and get URL (following reference system pattern)
        audio_url = await self._upload_audio_to_storage(audio_data, request)

        # Create result with both audio_data and URL for compatibility
        return TTSResult(
            audio_data=audio_data,  # Include raw audio data
            audio_url=audio_url,    # Keep URL for storage reference
            text_length=len(request.text),
            audio_duration=await self.estimate_audio_duration(request.text),
            processing_time=processing_time,
            voice_used=synthesis_params.get("voice", self._voice),
            language_used=request.language,
            provider_metadata={
                "model": synthesis_params["model"],
                "voice": synthesis_params["voice"],
                "speed": synthesis_params.get("speed", 1.0),
                "response_format": synthesis_params["response_format"],
                "audio_size_bytes": len(audio_data)
            }
        )

    async def _perform_synthesis(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Enhanced synthesis with ConnectionManager integration

        Phase 3.5.2.3: Uses centralized retry logic from ConnectionManager
        """
        return await self._execute_with_connection_manager(
            operation_name="openai_tts_synthesis",
            request_func=self._execute_openai_synthesis,
            synthesis_params=synthesis_params
        )

    async def _execute_openai_synthesis(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Direct OpenAI API call - used by ConnectionManager

        Single Responsibility: Only API communication
        """
        response = await self._client.audio.speech.create(**synthesis_params)
        return response.content

    def _prepare_synthesis_params(self, request: TTSRequest) -> Dict[str, Any]:
        """
        Prepare OpenAI API parameters from TTS request.

        Implements voice quality optimization features.
        """
        # Determine model - use default (no quality in new schema)
        model = self._model  # Default to standard quality

        # Default response format (no output_format in new schema)
        response_format = "mp3"  # Default format

        # Base parameters
        params = {
            "model": model,
            "input": request.text.strip(),
            "voice": request.voice or self._voice,
            "response_format": response_format
        }

        # Add speed control if supported and specified
        if request.speed != 1.0 and 0.25 <= request.speed <= 4.0:
            params["speed"] = request.speed

        # Skip custom settings (not available in new schema)
        # if request.custom_settings:
        #     # Only add safe custom parameters
        #     safe_params = ["speed", "response_format"]
        #     for key, value in request.custom_settings.items():
        #         if key in safe_params:
        #             params[key] = value

        return params

    async def _synthesize_with_retry(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Legacy synthesis with exponential backoff retry logic.

        Phase 3.5.2.3: Preserved for backward compatibility
        Will be deprecated after ConnectionManager migration is complete
        """
        last_exception = None

        for attempt in range(self._max_retries + 1):
            try:
                # Use direct API call method
                return await self._execute_openai_synthesis(synthesis_params)

            except RateLimitError as e:
                last_exception = e
                if attempt < self._max_retries:
                    delay = min(self._base_delay * (2 ** attempt), self._max_delay)
                    logger.warning("Rate limit hit, retrying in %ss (attempt %s)", delay, attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                raise AudioProcessingError(f"Rate limit exceeded after {self._max_retries} retries: {e}")

            except APIConnectionError as e:
                last_exception = e
                if attempt < self._max_retries:
                    delay = min(self._base_delay * (2 ** attempt), self._max_delay)
                    logger.warning("Connection error, retrying in %ss (attempt %s)", delay, attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                raise AudioProcessingError(f"Connection failed after {self._max_retries} retries: {e}")

            except AuthenticationError as e:
                # Don't retry authentication errors
                raise AudioProcessingError(f"OpenAI authentication failed: {e}")

            except APIError as e:
                # Don't retry client errors (4xx)
                raise AudioProcessingError(f"OpenAI API error: {e}")

            except asyncio.TimeoutError as e:
                last_exception = e
                if attempt < self._max_retries:
                    delay = min(self._base_delay * (2 ** attempt), self._max_delay)
                    logger.warning("Timeout, retrying in %ss (attempt %s)", delay, attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                raise VoiceServiceTimeout("synthesis", self._timeout)

        # If we get here, all retries failed
        raise AudioProcessingError(f"Synthesis failed after {self._max_retries} retries: {last_exception}")

    async def _upload_audio_to_storage(self, audio_data: bytes, request: TTSRequest) -> str:
        """
        Upload generated audio to MinIO storage and return URL.

        Follows reference system pattern for file storage.
        """
        # Generate unique filename
        text_hash = hashlib.sha256(request.text.encode()).hexdigest()[:8]
        format_ext = "mp3"  # Default format (no output_format in new schema)
        filename = f"tts_openai_{text_hash}_{int(time.time())}.{format_ext}"

        # TODO: Implement MinIO upload when MinIO manager is available
        # For now, return a placeholder URL
        # In production, this would upload to MinIO and return presigned URL
        from app.core.config import settings

        # Use real MinIO configuration
        protocol = "https" if getattr(settings, "MINIO_SECURE", False) else "http"
        endpoint = getattr(settings, "MINIO_ENDPOINT", "127.0.0.1:9000")

        return f"{protocol}://{endpoint}/voice-files/{filename}"

    async def synthesize_long_text(self, text: str, **kwargs) -> List[TTSResult]:
        """
        Parallel generation for long texts (Phase 3.2.2 requirement).

        Splits long text into chunks and processes them in parallel
        for better performance and user experience.
        """
        if len(text) <= self._max_text_length:
            # Text is short enough for single request
            request = TTSRequest(text=text, **kwargs)
            result = await self.synthesize_speech(request)
            return [result]

        # Split text into chunks
        chunks = self._split_text_intelligently(text, self._max_text_length - 100)  # Buffer for safety

        # Create requests for each chunk
        tasks = []
        for i, chunk in enumerate(chunks):
            request = TTSRequest(text=chunk, **kwargs)
            tasks.append(self.synthesize_speech(request))

        # Execute in parallel with concurrency limit
        max_concurrent = self.config.get('max_concurrent_requests', 3)
        results = []

        for i in range(0, len(tasks), max_concurrent):
            batch = tasks[i:i + max_concurrent]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error("Chunk synthesis failed: %s", result)
                    # Create error result
                    error_result = TTSResult(
                        audio_url="",
                        text_length=0,
                        provider_metadata={"error": str(result)}
                    )
                    results.append(error_result)
                else:
                    results.append(result)

        return results

    def _split_text_intelligently(self, text: str, max_chunk_size: int) -> List[str]:
        """
        Split text into chunks preserving sentence boundaries.

        Voice quality optimization - avoids cutting mid-sentence.
        """
        if len(text) <= max_chunk_size:
            return [text]

        chunks = []
        current_chunk = ""

        # Split by sentences (periods, exclamation marks, question marks)
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text)

        for sentence in sentences:
            if len(current_chunk) + len(sentence) + 1 <= max_chunk_size:
                current_chunk += (" " + sentence) if current_chunk else sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())

                # If single sentence is too long, split by words
                if len(sentence) > max_chunk_size:
                    word_chunks = self._split_by_words(sentence, max_chunk_size)
                    chunks.extend(word_chunks[:-1])
                    current_chunk = word_chunks[-1]
                else:
                    current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def _split_by_words(self, text: str, max_size: int) -> List[str]:
        """Split text by words when sentence is too long."""
        words = text.split()
        chunks = []
        current_chunk = ""

        for word in words:
            if len(current_chunk) + len(word) + 1 <= max_size:
                current_chunk += (" " + word) if current_chunk else word
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = word

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def __repr__(self) -> str:
        return f"OpenAITTSProvider(model={self._model}, voice={self._voice}, enabled={self.enabled})"
