"""
Yandex SpeechKit TTS Provider для Voice_v2 - Phase 3.2.4

Применяет архитектурные принципы из Phase 1.3:
- LSP compliance (Phase_1_3_1_architecture_review.md)
- SOLID principles (Phase_1_2_2_solid_principles.md)  
- Performance patterns (Phase_1_2_3_performance_optimization.md)
- Reference system patterns (Phase_1_1_4_architecture_patterns.md)

SOLID Principles Implementation:
- Single Responsibility: Только Yandex TTS functionality
- Open/Closed: Extensible через наследование, закрыт для модификации
- Liskov Substitution: Полная взаимозаменяемость с BaseTTSProvider
- Interface Segregation: Минимальный interface, только TTS методы
- Dependency Inversion: Зависит от абстракций, не от конкретных реализаций

Архитектурные Patterns:
- Async/await patterns (Phase_1_2_3_performance_optimization.md)
- Lazy client initialization для performance optimization
- Connection reuse patterns с HTTP session management
- Error handling с exponential backoff retry logic
- Provider metadata generation для monitoring
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession, ClientTimeout, ClientError

from .base_tts import BaseTTSProvider
from .models import TTSRequest, TTSResult, TTSCapabilities, TTSQuality
from ...core.exceptions import VoiceServiceError, AudioProcessingError, ProviderNotAvailableError
from ...core.interfaces import ProviderType, AudioFormat

logger = logging.getLogger(__name__)


class YandexTTSProvider(BaseTTSProvider):
    """
    Yandex SpeechKit TTS Provider - Phase 3.2.4
    
    Архитектурные принципы (Phase 1.3):
    - LSP compliance: Полностью совместим с BaseTTSProvider
    - SOLID: Single responsibility только для Yandex TTS operations
    - Performance: Async patterns, lazy initialization, connection reuse
    - Error Handling: Exponential backoff, comprehensive error recovery
    """

    # Yandex SpeechKit API Configuration
    API_BASE_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
    MAX_TEXT_LENGTH = 5000  # Yandex SpeechKit limit
    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds
    
    # Yandex Voice Configuration
    VOICE_MAPPING = {
        # Russian voices
        "jane": {"language": "ru-RU", "gender": "female", "quality": "standard"},
        "oksana": {"language": "ru-RU", "gender": "female", "quality": "standard"},
        "omazh": {"language": "ru-RU", "gender": "female", "quality": "standard"},
        "zahar": {"language": "ru-RU", "gender": "male", "quality": "standard"},
        "ermil": {"language": "ru-RU", "gender": "male", "quality": "standard"},
        "silaerkan": {"language": "ru-RU", "gender": "male", "quality": "standard"},
        
        # English voices
        "john": {"language": "en-US", "gender": "male", "quality": "standard"},
        "alyss": {"language": "en-US", "gender": "female", "quality": "standard"},
        
        # Premium voices (neural synthesis)
        "alena": {"language": "ru-RU", "gender": "female", "quality": "premium"},
        "filipp": {"language": "ru-RU", "gender": "male", "quality": "premium"},
    }
    
    # Audio format mapping
    FORMAT_MAPPING = {
        AudioFormat.MP3: "mp3",
        AudioFormat.WAV: "wav",
        AudioFormat.OGG: "oggopus",
        AudioFormat.OPUS: "oggopus",
    }

    def __init__(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int,
        enabled: bool,
        **kwargs
    ):
        """
        Initialize Yandex TTS Provider.
        
        Args:
            provider_name: Provider identifier ("yandex")
            config: Configuration dictionary with api_key, folder_id
            priority: Provider priority (lower = higher priority)
            enabled: Whether provider is enabled
        """
        super().__init__(provider_name, config, priority, enabled, **kwargs)
        
        # Yandex SpeechKit Configuration
        self._api_key = config.get("api_key")
        self._folder_id = config.get("folder_id")
        self._voice_name = config.get("voice_name", "jane")
        self._language_code = config.get("language_code", "ru-RU")
        self._audio_format = config.get("audio_format", "mp3")
        self._speed = config.get("speed", 1.0)
        self._emotion = config.get("emotion", "neutral")
        
        # HTTP Session for connection reuse (Performance pattern)
        self._client: Optional[ClientSession] = None
        self._client_timeout = ClientTimeout(total=60, connect=10)
        
        logger.debug(f"YandexTTSProvider initialized with voice: {self._voice_name}")

    def get_required_config_fields(self) -> List[str]:
        """Required configuration fields for Yandex SpeechKit TTS."""
        return ["api_key", "folder_id"]

    async def get_capabilities(self) -> TTSCapabilities:
        """Get Yandex TTS provider capabilities."""
        # Prepare available voices list
        available_voices = []
        for voice_id, voice_info in self.VOICE_MAPPING.items():
            available_voices.append({
                "id": voice_id,
                "name": voice_id.title(),
                "language": voice_info["language"],
                "gender": voice_info["gender"],
                "quality": voice_info["quality"]
            })
        
        return TTSCapabilities(
            provider_type=ProviderType.YANDEX,
            supported_formats=[AudioFormat.MP3, AudioFormat.WAV, AudioFormat.OGG, AudioFormat.OPUS],
            supported_languages=["ru-RU", "en-US", "tr-TR", "uk-UA", "kk-KZ"],
            available_voices=available_voices,
            max_text_length=self.MAX_TEXT_LENGTH,
            supports_ssml=False,  # Yandex TTS doesn't support SSML
            supports_speed_control=True,
            supports_pitch_control=False,
            supports_custom_voices=True,
            quality_levels=[TTSQuality.STANDARD, TTSQuality.PREMIUM]
        )

    async def initialize(self) -> None:
        """
        Initialize Yandex TTS provider resources.
        
        Performance pattern: Lazy initialization - client created only when needed.
        Error handling: Comprehensive validation and clear error messages.
        """
        try:
            logger.debug("Initializing Yandex TTS Provider...")
            
            # Validate configuration
            if not self._api_key:
                raise VoiceServiceError(
                    "Yandex API key is required (api_key in config)"
                )
            
            if not self._folder_id:
                raise VoiceServiceError(
                    "Yandex folder ID is required (folder_id in config)"
                )
            
            # Validate voice configuration
            if self._voice_name not in self.VOICE_MAPPING:
                logger.warning(f"Unknown voice '{self._voice_name}', using default 'jane'")
                self._voice_name = "jane"
            
            # Initialize HTTP client (connection reuse pattern)
            await self._ensure_client()
            
            # Test API connectivity
            await self._health_check()
            
            self._is_initialized = True
            logger.info(f"Yandex TTS Provider initialized successfully with voice: {self._voice_name}")
            
        except Exception as e:
            logger.error(f"Failed to initialize Yandex TTS Provider: {e}", exc_info=True)
            raise AudioProcessingError(f"Yandex TTS initialization failed: {str(e)}")

    async def cleanup(self) -> None:
        """Clean up provider resources with proper session closure."""
        try:
            if self._client and not self._client.closed:
                await self._client.close()
                logger.debug("Yandex TTS HTTP client closed")
            
            self._client = None
            self._is_initialized = False
            
            logger.info("Yandex TTS Provider cleaned up successfully")
            
        except Exception as e:
            logger.error(f"Error during Yandex TTS cleanup: {e}", exc_info=True)

    async def _synthesize_implementation(self, request: TTSRequest) -> TTSResult:
        """
        Core synthesis implementation using Yandex SpeechKit TTS.
        
        Args:
            request: TTS request with text and configuration
            
        Returns:
            TTSResult with audio data and metadata
            
        Raises:
            AudioProcessingError: If synthesis fails
            VoiceServiceError: If provider is not available
        """
        start_time = time.time()
        
        try:
            logger.debug(f"Synthesizing speech for text length: {len(request.text)}")
            
            # Validate request
            if len(request.text) > self.MAX_TEXT_LENGTH:
                raise AudioProcessingError(
                    f"Text too long: {len(request.text)} > {self.MAX_TEXT_LENGTH} characters"
                )
            
            # Ensure client is available
            await self._ensure_client()
            
            # Prepare synthesis parameters
            synthesis_params = self._prepare_synthesis_params(request)
            
            # Use ConnectionManager if available, fallback to legacy retry
            if self._has_connection_manager():
                audio_data = await self._perform_synthesis(synthesis_params)
            else:
                # Legacy fallback for backward compatibility
                audio_data = await self._synthesize_with_retry(synthesis_params)
            
            # Upload audio to storage
            file_path = await self._upload_audio_to_storage(
                audio_data, request.agent_id if hasattr(request, 'agent_id') else "default", 
                request.user_id if hasattr(request, 'user_id') else "default"
            )
            
            processing_time = time.time() - start_time
            
            # Generate provider metadata
            metadata = self._generate_metadata(synthesis_params, len(audio_data), processing_time)
            
            return TTSResult(
                audio_url=file_path,
                text_length=len(request.text),
                audio_duration=self._estimate_audio_duration(request.text, request.speed or 1.0),
                processing_time=processing_time,
                voice_used=self._voice_name,
                language_used=synthesis_params.get("lang"),
                provider_metadata=metadata
            )
            
        except AudioProcessingError:
            raise
        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"Yandex TTS synthesis failed: {e}", exc_info=True)
            
            # Return TTSResult with minimal required fields and metadata in provider_metadata
            return TTSResult(
                audio_url="",  # Empty URL for failed synthesis
                text_length=len(request.text),
                audio_duration=None,
                processing_time=processing_time,
                voice_used=self._voice_name,
                language_used=request.language or self._language_code,
                provider_metadata={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "provider": self.provider_name,
                    "success": False
                }
            )

    async def _perform_synthesis(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Enhanced synthesis with ConnectionManager integration
        
        Phase 3.5.2.3: Uses centralized retry logic from ConnectionManager
        """
        return await self._execute_with_connection_manager(
            operation_name="yandex_tts_synthesis",
            request_func=self._execute_yandex_synthesis,
            synthesis_params=synthesis_params
        )
    
    async def _execute_yandex_synthesis(self, synthesis_params: Dict[str, Any]) -> bytes:
        """
        Direct Yandex API call - used by ConnectionManager
        
        Single Responsibility: Only API communication
        """
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = "&".join([f"{k}={v}" for k, v in synthesis_params.items()])
        
        async with self._session.post(
            self.TTS_API_URL,
            headers=headers,
            data=data.encode('utf-8')
        ) as response:
            if response.status == 200:
                return await response.read()
            else:
                error_text = await response.text()
                raise VoiceServiceError(f"Yandex TTS API error {response.status}: {error_text}")

    async def get_available_voices(self) -> List[Dict[str, Any]]:
        """Get list of available voices with their characteristics."""
        voices = []
        
        for voice_id, voice_info in self.VOICE_MAPPING.items():
            voices.append({
                "id": voice_id,
                "name": voice_id.title(),
                "language": voice_info["language"],
                "gender": voice_info["gender"],
                "quality": voice_info["quality"],
                "premium": voice_info["quality"] == "premium",
                "description": f"{voice_info['gender'].title()} {voice_info['language']} voice"
            })
        
        return voices

    async def health_check(self) -> bool:
        """
        Check provider health and availability.
        
        Returns:
            bool: True if provider is healthy and available
        """
        try:
            await self._health_check()
            return True
        except Exception:
            return False

    # Private methods for internal operations

    async def _ensure_client(self) -> None:
        """Ensure HTTP client is available (lazy initialization pattern)."""
        if self._client is None or self._client.closed:
            self._client = ClientSession(timeout=self._client_timeout)
            logger.debug("Created new Yandex TTS HTTP client")

    async def _health_check(self) -> None:
        """Perform health check with minimal API request."""
        await self._ensure_client()
        
        # Test API availability with minimal request
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "x-folder-id": self._folder_id,
        }
        
        data = {
            "text": "test",
            "lang": "ru-RU",
            "voice": "jane",
            "format": "mp3"
        }
        
        try:
            async with self._client.post(
                self.API_BASE_URL,
                headers=headers,
                data=data
            ) as response:
                if response.status == 401:
                    raise ProviderNotAvailableError("Invalid Yandex API credentials")
                elif response.status == 403:
                    raise ProviderNotAvailableError("Yandex API access forbidden")
                # Note: We don't require 200 for health check, just valid auth
                
        except (ClientError, asyncio.TimeoutError) as e:
            raise ProviderNotAvailableError(f"Yandex TTS API unreachable: {str(e)}")

    def _prepare_synthesis_params(self, request: TTSRequest) -> Dict[str, Any]:
        """Prepare synthesis parameters for Yandex SpeechKit API."""
        voice_config = self.VOICE_MAPPING.get(self._voice_name, self.VOICE_MAPPING["jane"])
        
        # Use request language if provided, otherwise use voice default
        language = request.language or voice_config["language"]
        
        # Map audio format
        format_name = self.FORMAT_MAPPING.get(request.output_format, "mp3")
        
        params = {
            "text": request.text,
            "lang": language,
            "voice": self._voice_name,
            "format": format_name,
            "speed": request.speed or self._speed,
            "emotion": self._emotion
        }
        
        logger.debug(f"Prepared synthesis params: {params}")
        return params

    async def _synthesize_with_retry(self, params: Dict[str, Any]) -> bytes:
        """
        Legacy synthesis with exponential backoff retry logic.
        
        Phase 3.5.2.3: Preserved for backward compatibility
        Will be deprecated after ConnectionManager migration is complete
        """
        headers = {
            "Authorization": f"Api-Key {self._api_key}",
            "x-folder-id": self._folder_id,
        }
        
        last_exception = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Use direct API call method
                return await self._execute_yandex_synthesis(params)
                
            except VoiceServiceError as e:
                # Check for rate limit specifically
                if "429" in str(e) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"Rate limited, retrying in {delay}s (attempt {attempt + 1})")
                    await asyncio.sleep(delay)
                    continue
                # Re-raise other VoiceServiceErrors
                if attempt == self.MAX_RETRIES - 1:
                    raise AudioProcessingError(f"Yandex TTS failed: {e}")
                
            except Exception as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"Unexpected error, retrying in {delay}s (attempt {attempt + 1}): {e}")
                    await asyncio.sleep(delay)
                    continue
                break
        
        # If we get here, all retries failed
        raise AudioProcessingError(f"TTS synthesis failed after {self.MAX_RETRIES} attempts: {str(last_exception)}")

    async def _upload_audio_to_storage(
        self, 
        audio_data: bytes, 
        agent_id: str, 
        user_id: str
    ) -> str:
        """
        Upload synthesized audio to storage and return URL.
        
        Follows the same pattern as Google TTS for consistency.
        """
        try:
            # Generate unique filename
            timestamp = int(time.time() * 1000)  # milliseconds
            filename = f"tts_yandex_{agent_id}_{user_id}_{timestamp}.{self._audio_format}"
            
            # TODO: Implement MinIO upload when MinIO manager is available
            # For now, return a placeholder URL
            # In production, this would upload to MinIO and return presigned URL
            return f"https://storage.example.com/tts/{filename}"
            
        except Exception as e:
            logger.error(f"Failed to upload audio to storage: {e}")
            raise AudioProcessingError(f"Audio storage upload failed: {e}")

    def _generate_metadata(
        self, 
        params: Dict[str, Any], 
        audio_size: int, 
        processing_time: float
    ) -> Dict[str, Any]:
        """Generate comprehensive metadata for monitoring and debugging."""
        voice_config = self.VOICE_MAPPING.get(self._voice_name, {})
        
        return {
            "provider": self.provider_name,
            "provider_type": ProviderType.YANDEX.value,
            "voice_name": self._voice_name,
            "language": params.get("lang"),
            "audio_format": params.get("format"),
            "speed": params.get("speed"),
            "emotion": params.get("emotion"),
            "voice_quality": voice_config.get("quality", "standard"),
            "voice_gender": voice_config.get("gender", "unknown"),
            "text_length": len(params.get("text", "")),
            "audio_size": audio_size,
            "processing_time": processing_time,
            "api_endpoint": self.API_BASE_URL,
            "timestamp": time.time()
        }

    def _estimate_audio_duration(self, text: str, speed: float = 1.0) -> float:
        """
        Estimate audio duration based on text length and speech speed.
        
        Based on average speech rate: ~150 words per minute for Russian
        """
        words = len(text.split())
        base_duration = (words / 150) * 60  # seconds
        return base_duration / speed
