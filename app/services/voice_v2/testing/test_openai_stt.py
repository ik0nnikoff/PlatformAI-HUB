"""Comprehensive tests for OpenAI STT Provider - Phase 3.1.2."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from openai import APIConnectionError, RateLimitError, AuthenticationError

from app.services.voice_v2.providers.stt.openai_stt import OpenAISTTProvider
from app.services.voice_v2.providers.stt.models import STTRequest, STTResult, STTCapabilities, STTQuality
from app.services.voice_v2.core.exceptions import (
    ProviderNotAvailableError,
    AudioProcessingError,
    VoiceServiceTimeout
)
from app.services.voice_v2.core.interfaces import ProviderType


@pytest.fixture
def openai_config():
    """Standard OpenAI configuration for tests."""
    return {
        "api_key": "test-api-key-12345",
        "model": "whisper-1",
        "timeout": 30,
        "max_retries": 3,
        "retry_delay": 1.0,
        "connection_pool_size": 100,
        "per_host_connections": 30,
        "keepalive_timeout": 30
    }


@pytest.fixture
def stt_request(tmp_path):
    """Standard STT request for tests."""
    # Create test audio file
    audio_file = tmp_path / "test_audio.wav"
    audio_file.write_bytes(b"fake audio data")

    return STTRequest(
        audio_file_path=str(audio_file),
        language="en",
        quality=STTQuality.STANDARD,
        custom_settings={}
    )


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client."""
    client = AsyncMock()
    client.close = AsyncMock()
    return client


class TestOpenAISTTProviderInitialization:
    """Test OpenAI STT Provider initialization."""

    def test_init_valid_config(self, openai_config):
        """Test initialization with valid configuration."""
        provider = OpenAISTTProvider("openai", openai_config)

        assert provider.provider_name == "openai"
        assert provider.api_key == "test-api-key-12345"
        assert provider.model == "whisper-1"
        assert provider.timeout == 30
        # ConnectionManager architecture - check retry config через RetryMixin
        if hasattr(provider, 'max_retries'):
            assert provider.max_retries == 3
        # Legacy fallback path когда ConnectionManager недоступен
        assert provider.client is None
        assert provider._session is None

    def test_init_missing_api_key(self):
        """Test initialization with missing API key."""
        config = {"model": "whisper-1"}

        with pytest.raises(Exception):  # Should fail during validation
            provider = OpenAISTTProvider("openai", config)

    def test_init_default_values(self):
        """Test initialization with default values."""
        config = {"api_key": "test-key"}
        provider = OpenAISTTProvider("openai", config)

        assert provider.model == "whisper-1"
        assert provider.timeout == 30
        assert provider.max_retries == 3
        assert provider.connection_pool_size == 100
        assert provider.per_host_connections == 30
        assert provider.keepalive_timeout == 30

    def test_get_required_config_fields(self, openai_config):
        """Test required configuration fields."""
        provider = OpenAISTTProvider("openai", openai_config)
        required_fields = provider.get_required_config_fields()

        assert required_fields == ["api_key"]


class TestOpenAISTTProviderCapabilities:
    """Test OpenAI STT Provider capabilities."""

    @pytest.mark.asyncio
    async def test_get_capabilities(self, openai_config):
        """Test provider capabilities."""
        provider = OpenAISTTProvider("openai", openai_config)
        capabilities = await provider.get_capabilities()

        assert isinstance(capabilities, STTCapabilities)
        assert capabilities.provider_type == ProviderType.OPENAI
        assert len(capabilities.supported_formats) > 0
        assert "wav" in [fmt.value for fmt in capabilities.supported_formats]
        assert "mp3" in [fmt.value for fmt in capabilities.supported_formats]
        assert len(capabilities.supported_languages) >= 50  # Whisper supports many languages
        assert "en" in capabilities.supported_languages
        assert "ru" in capabilities.supported_languages
        assert capabilities.max_file_size_mb == 25.0
        assert capabilities.max_duration_seconds == 3600.0
        assert STTQuality.STANDARD in capabilities.supports_quality_levels
        assert STTQuality.HIGH in capabilities.supports_quality_levels
        assert capabilities.supports_language_detection is True


class TestOpenAISTTProviderLifecycle:
    """Test OpenAI STT Provider lifecycle management."""

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    @patch('aiohttp.ClientSession')
    async def test_initialize_success(self, mock_session, mock_openai_class, openai_config):
        """Test successful initialization."""
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client

        mock_session_instance = AsyncMock()
        mock_session_instance.closed = False
        mock_session.return_value = mock_session_instance

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        assert provider._initialized is True
        assert provider.client is not None
        mock_openai_class.assert_called_once()

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_initialize_missing_api_key(self, mock_openai_class):
        """Test initialization with missing API key."""
        config = {"api_key": None}
        provider = OpenAISTTProvider("openai", config)

        with pytest.raises(ProviderNotAvailableError) as exc_info:
            await provider.initialize()

        assert "OpenAI API key не настроен" in str(exc_info.value)
        assert not provider._initialized

    @pytest.mark.asyncio
    async def test_cleanup(self, openai_config, mock_openai_client):
        """Test resource cleanup."""
        provider = OpenAISTTProvider("openai", openai_config)
        provider.client = mock_openai_client
        provider._session = AsyncMock()

        await provider.cleanup()

        mock_openai_client.close.assert_called_once()
        provider._session.close.assert_called_once()
        assert provider.client is None
        assert provider._session is None

    @pytest.mark.asyncio
    async def test_cleanup_with_errors(self, openai_config):
        """Test cleanup handles errors gracefully."""
        provider = OpenAISTTProvider("openai", openai_config)

        # Mock clients that raise errors on close
        provider.client = AsyncMock()
        provider.client.close.side_effect = Exception("Close error")
        provider._session = AsyncMock()
        provider._session.close.side_effect = Exception("Session close error")

        # Should not raise exception
        await provider.cleanup()

        assert provider.client is None
        assert provider._session is None


class TestOpenAISTTProviderTranscription:
    """Test OpenAI STT Provider transcription functionality."""

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_transcribe_success_text_format(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test successful transcription with text format response."""
        # Setup mocks
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client

        mock_response = "Hello, this is a test transcription."
        mock_client.audio.transcriptions.create.return_value = mock_response

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        # Execute transcription
        result = await provider.transcribe_audio(stt_request)

        # Verify result
        assert isinstance(result, STTResult)
        assert result.text == "Hello, this is a test transcription."
        assert result.confidence == 0.95
        assert result.language_detected == "en"
        assert result.processing_time > 0
        assert result.word_count == 6
        assert result.provider_metadata["model"] == "whisper-1"
        assert result.provider_metadata["quality_level"] == "standard"

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_transcribe_success_verbose_format(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test successful transcription with verbose JSON format."""
        # Setup request for high quality (verbose JSON)
        stt_request.quality = STTQuality.HIGH

        # Setup mocks
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = "Hello, this is a verbose test."
        mock_response.language = "en"
        mock_response.segments = [
            MagicMock(avg_logprob=-0.1),
            MagicMock(avg_logprob=-0.2)
        ]
        mock_client.audio.transcriptions.create.return_value = mock_response

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        # Execute transcription
        result = await provider.transcribe_audio(stt_request)

        # Verify result
        assert result.text == "Hello, this is a verbose test."
        assert result.language_detected == "en"
        assert 0.8 <= result.confidence <= 1.0  # Calculated from segments
        assert result.provider_metadata["response_format"] == "verbose_json"

    @pytest.mark.asyncio
    async def test_transcribe_file_not_found(self, openai_config):
        """Test transcription with non-existent file."""
        request = STTRequest(
            audio_file_path="/non/existent/file.wav",
            language="en",
            quality=STTQuality.STANDARD
        )

        provider = OpenAISTTProvider("openai", openai_config)
        provider.client = AsyncMock()  # Mock to avoid initialization

        with pytest.raises(AudioProcessingError) as exc_info:
            await provider._transcribe_implementation(request)

        assert "Аудиофайл не найден" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"x" * (26 * 1024 * 1024))  # 26MB
    async def test_transcribe_file_too_large(self, mock_file, openai_config, stt_request):
        """Test transcription with file exceeding size limit."""
        provider = OpenAISTTProvider("openai", openai_config)
        provider.client = AsyncMock()

        with pytest.raises(AudioProcessingError) as exc_info:
            await provider._transcribe_implementation(stt_request)

        assert "слишком большой" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_transcribe_empty_result(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test transcription with empty result."""
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.return_value = ""

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        with pytest.raises(AudioProcessingError) as exc_info:
            await provider._transcribe_implementation(stt_request)

        assert "Не удалось распознать речь" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_transcribe_timeout_error(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test transcription timeout handling."""
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.side_effect = asyncio.TimeoutError()

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        with pytest.raises(VoiceServiceTimeout):
            await provider._transcribe_implementation(stt_request)

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_transcribe_authentication_error(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test authentication error handling."""
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.side_effect = AuthenticationError(
            "Invalid API key", response=MagicMock(), body=None
        )

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        with pytest.raises(ProviderNotAvailableError) as exc_info:
            await provider._transcribe_implementation(stt_request)

        assert "аутентификации" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_transcribe_rate_limit_error(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test rate limit error handling."""
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.side_effect = RateLimitError(
            "Rate limit exceeded", response=MagicMock(), body=None
        )

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        with pytest.raises(AudioProcessingError) as exc_info:
            await provider._transcribe_implementation(stt_request)

        assert "лимит запросов" in str(exc_info.value)


class TestOpenAISTTProviderRetryLogic:
    """Test OpenAI STT Provider retry logic."""

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('asyncio.sleep', new_callable=AsyncMock)
    async def test_retry_on_connection_error(self, mock_sleep, mock_file, openai_config, tmp_path):
        """Test retry logic on connection errors."""
        # Create test file
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio data")

        provider = OpenAISTTProvider("openai", openai_config)
        provider.client = AsyncMock()

        # First two calls fail, third succeeds
        provider.client.audio.transcriptions.create.side_effect = [
            APIConnectionError("Connection failed"),
            APIConnectionError("Connection failed again"),
            "Success transcription"
        ]

        transcription_params = {"model": "whisper-1", "response_format": "text"}

        result = await provider._transcribe_with_retry(audio_file, transcription_params)

        assert result == "Success transcription"
        assert provider.client.audio.transcriptions.create.call_count == 3
        assert mock_sleep.call_count == 2  # Two retries

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('asyncio.sleep', new_callable=AsyncMock)
    async def test_retry_exhausted(self, mock_sleep, mock_file, openai_config, tmp_path):
        """Test retry logic when all attempts fail."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio data")

        provider = OpenAISTTProvider("openai", openai_config)
        provider.client = AsyncMock()

        # All calls fail
        provider.client.audio.transcriptions.create.side_effect = APIConnectionError("Persistent error")

        transcription_params = {"model": "whisper-1", "response_format": "text"}

        with pytest.raises(AudioProcessingError) as exc_info:
            await provider._transcribe_with_retry(audio_file, transcription_params)

        assert "failed after" in str(exc_info.value)
        assert provider.client.audio.transcriptions.create.call_count == 4  # 1 + 3 retries


class TestOpenAISTTProviderHelperMethods:
    """Test OpenAI STT Provider helper methods."""

    def test_extract_safe_settings(self, openai_config):
        """Test safe settings extraction."""
        provider = OpenAISTTProvider("openai", openai_config)

        custom_settings = {
            "temperature": 0.5,
            "prompt": "Custom prompt",
            "response_format": "json",
            "dangerous_param": "should_be_filtered",
            "another_bad_param": True
        }

        safe_settings = provider._extract_safe_settings(custom_settings)

        assert safe_settings == {
            "temperature": 0.5,
            "prompt": "Custom prompt",
            "response_format": "json"
        }

    def test_calculate_confidence_from_segments(self, openai_config):
        """Test confidence calculation from segments."""
        provider = OpenAISTTProvider("openai", openai_config)

        # Mock verbose result with segments
        mock_result = MagicMock()
        mock_result.segments = [
            MagicMock(avg_logprob=-0.1),  # High confidence
            MagicMock(avg_logprob=-0.5),  # Medium confidence
            MagicMock(avg_logprob=-0.8),  # Lower confidence
        ]

        confidence = provider._calculate_confidence_from_segments(mock_result)

        assert 0.0 <= confidence <= 1.0
        assert confidence < 0.95  # Should be calculated, not default

    def test_calculate_confidence_no_segments(self, openai_config):
        """Test confidence calculation with no segments."""
        provider = OpenAISTTProvider("openai", openai_config)

        mock_result = MagicMock()
        mock_result.segments = []

        confidence = provider._calculate_confidence_from_segments(mock_result)

        assert confidence == 0.95  # Default value

    @pytest.mark.asyncio
    async def test_health_check_success(self, openai_config):
        """Test successful health check."""
        provider = OpenAISTTProvider("openai", openai_config)
        provider._initialized = True

        with patch.object(provider, '_initial_health_check', return_value=True):
            result = await provider.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self, openai_config):
        """Test health check when not initialized."""
        provider = OpenAISTTProvider("openai", openai_config)
        provider._initialized = False

        result = await provider.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_failure(self, openai_config):
        """Test health check failure."""
        provider = OpenAISTTProvider("openai", openai_config)
        provider._initialized = True

        with patch.object(provider, '_initial_health_check', side_effect=Exception("Health check failed")):
            result = await provider.health_check()
            assert result is False


class TestOpenAISTTProviderIntegration:
    """Integration tests for OpenAI STT Provider."""

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_full_transcription_workflow(
        self, mock_openai_class, mock_file, openai_config, stt_request
    ):
        """Test complete transcription workflow."""
        # Setup mock
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.return_value = "Complete workflow test."

        # Initialize and transcribe
        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        result = await provider.transcribe_audio(stt_request)

        # Verify complete workflow
        assert result.text == "Complete workflow test."
        assert result.processing_time > 0
        assert result.word_count == 3
        assert "model" in result.provider_metadata

        # Cleanup
        await provider.cleanup()
        mock_client.close.assert_called_once()

    def test_provider_string_representation(self, openai_config):
        """Test provider string representation."""
        provider = OpenAISTTProvider("openai", openai_config)

        repr_str = repr(provider)

        assert "OpenAISTTProvider" in repr_str
        assert "whisper-1" in repr_str
        assert "priority=" in repr_str
        assert "enabled=" in repr_str
        assert "initialized=" in repr_str


# Performance and stress tests
class TestOpenAISTTProviderPerformance:
    """Performance tests for OpenAI STT Provider."""

    @pytest.mark.asyncio
    @patch('builtins.open', new_callable=mock_open, read_data=b"fake audio data")
    @patch('app.services.voice_v2.providers.stt.openai_stt.AsyncOpenAI')
    async def test_concurrent_transcriptions(
        self, mock_openai_class, mock_file, openai_config, tmp_path
    ):
        """Test concurrent transcription requests."""
        # Setup
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.return_value = "Concurrent test."

        provider = OpenAISTTProvider("openai", openai_config)
        await provider.initialize()

        # Create multiple requests
        requests = []
        for i in range(5):
            audio_file = tmp_path / f"test_{i}.wav"
            audio_file.write_bytes(b"fake audio data")

            request = STTRequest(
                audio_file_path=str(audio_file),
                language="en",
                quality=STTQuality.STANDARD
            )
            requests.append(request)

        # Execute concurrent transcriptions
        start_time = asyncio.get_event_loop().time()
        results = await asyncio.gather(*[
            provider.transcribe_audio(req) for req in requests
        ])
        end_time = asyncio.get_event_loop().time()

        # Verify results
        assert len(results) == 5
        for result in results:
            assert result.text == "Concurrent test."

        # Verify performance (concurrent execution should be faster than sequential)
        total_time = end_time - start_time
        assert total_time < 1.0  # Should be much faster with mocked client

        await provider.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
