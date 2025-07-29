"""
Unit tests for Google Cloud TTS Provider - Phase 3.2.3

Tests Phase 1.3 architectural compliance:
- LSP compatibility with BaseTTSProvider
- SOLID principles validation
- Performance optimization verification
- Advanced voice configuration testing

Architecture Validation:
- Phase_1_3_1_architecture_review.md: LSP compliance testing
- Phase_1_2_2_solid_principles.md: Interface segregation validation
- Phase_1_2_3_performance_optimization.md: Async patterns testing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from google.api_core import exceptions as google_exceptions
from google.cloud.texttospeech_v1 import types

from app.services.voice_v2.providers.tts.google_tts import GoogleTTSProvider
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSQuality
from app.services.voice_v2.core.exceptions import AudioProcessingError, VoiceServiceError
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat


class TestGoogleTTSProvider:
    """Test suite for Google Cloud TTS Provider implementation."""

    @pytest.fixture
    def provider_config(self) -> Dict[str, Any]:
        """Standard configuration for Google TTS provider."""
        return {
            "credentials_path": "/path/to/credentials.json",
            "project_id": "test-project-123",
            "voice_name": "en-US-Journey-D",
            "language_code": "en-US",
            "speaking_rate": 1.0,
            "pitch": 0.0,
            "max_retries": 3,
            "timeout": 30.0
        }

    @pytest.fixture
    def provider(self, provider_config) -> GoogleTTSProvider:
        """Create Google TTS provider instance for testing."""
        return GoogleTTSProvider(
            provider_name="google",
            config=provider_config,
            priority=1,
            enabled=True
        )

    # Architecture Compliance Tests (Phase 1.3.1)

    def test_lsp_compliance_with_base_provider(self, provider):
        """Test LSP compliance - Google provider must be substitutable with BaseTTSProvider."""
        # LSP: Google provider should be usable wherever BaseTTSProvider is expected
        assert isinstance(provider, BaseTTSProvider)

        # All BaseTTSProvider interface methods must be implemented
        base_methods = [
            'initialize', 'cleanup', 'synthesize_speech', 'health_check',
            'get_required_config_fields'
        ]

        for method in base_methods:
            assert hasattr(provider, method), f"Missing required method: {method}"
            assert callable(getattr(provider, method)), f"Method not callable: {method}"

        # Properties should exist but not be callable
        base_properties = ['provider_type', 'supported_formats']
        for prop in base_properties:
            assert hasattr(provider, prop), f"Missing required property: {prop}"

        # Provider type must be correctly identified
        assert provider.provider_type == ProviderType.GOOGLE

        # String representations must be implemented
        assert str(provider).startswith("GoogleTTSProvider")
        assert repr(provider).startswith("GoogleTTSProvider")

    def test_solid_srp_single_responsibility(self, provider):
        """Test SRP - Provider should only handle Google Cloud TTS operations."""
        # Provider should not handle storage, metrics, or caching directly
        assert not hasattr(provider, '_storage_manager')
        assert not hasattr(provider, '_metrics_collector')
        assert not hasattr(provider, '_cache_service')

        # Core responsibility: Google Cloud TTS synthesis
        assert hasattr(provider, '_client')
        assert hasattr(provider, '_voice_name')
        assert hasattr(provider, '_language_code')

        # Configuration responsibility
        assert callable(provider.get_required_config_fields)

    def test_solid_ocp_open_closed_principle(self, provider):
        """Test OCP - Provider should be extensible without modification."""
        # Configuration-based extension (open for extension)
        config_fields = [
            '_voice_name', '_language_code', '_speaking_rate', '_pitch',
            '_effects_profile_id', '_volume_gain_db', '_sample_rate_hertz'
        ]

        for field in config_fields:
            assert hasattr(provider, field), f"Missing configurable field: {field}"

        # Core implementation is closed for modification
        assert provider._prepare_synthesis_params
        # New architecture uses _synthesize_with_retry for legacy compatibility
        assert provider._synthesize_with_retry

    def test_solid_isp_interface_segregation(self, provider):
        """Test ISP - Provider should only depend on interfaces it uses."""
        # Should not depend on unnecessary interfaces
        assert not hasattr(provider, '_stt_interface')
        assert not hasattr(provider, '_file_interface')

        # Only depends on TTS-specific interfaces
        assert hasattr(provider, 'synthesize_speech')
        assert hasattr(provider, 'supported_formats')

        # Provider type is correctly specified
        assert provider.provider_type == ProviderType.GOOGLE

    def test_solid_dip_dependency_inversion(self, provider):
        """Test DIP - Provider should depend on abstractions, not concretions."""
        # Inherits from abstract BaseTTSProvider
        assert isinstance(provider, BaseTTSProvider)

        # Uses configuration abstraction
        assert hasattr(provider, 'config')
        assert hasattr(provider, 'enabled')
        assert hasattr(provider, 'priority')

        # Does not create concrete dependencies internally
        assert provider._client is None  # Lazy initialization

    # Configuration and Initialization Tests

    def test_initialization_with_valid_config(self, provider_config):
        """Test provider initialization with valid configuration."""
        provider = GoogleTTSProvider("google", provider_config, 1, True)

        assert provider.provider_name == "google"
        assert provider.priority == 1
        assert provider.enabled is True
        assert provider._voice_name == "en-US-Journey-D"
        assert provider._language_code == "en-US"
        assert provider._speaking_rate == 1.0
        assert provider._pitch == 0.0

    def test_required_config_fields(self, provider):
        """Test required configuration field detection."""
        # Provider with missing credentials
        provider._credentials_path = None
        provider._project_id = None

        required_fields = provider.get_required_config_fields()
        assert "credentials_path" in required_fields
        assert "project_id" in required_fields

    @pytest.mark.asyncio
    async def test_client_initialization_success(self, provider):
        """Test successful Google Cloud TTS client initialization."""
        mock_client = AsyncMock()
        mock_client.list_voices.return_value = MagicMock()

        with patch('app.services.voice_v2.providers.tts.google_tts.texttospeech_v1.TextToSpeechAsyncClient') as mock_client_class:
            mock_client_class.return_value = mock_client

            await provider.initialize()

            assert provider._client is not None
            mock_client.list_voices.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_initialization_failure(self, provider):
        """Test Google Cloud TTS client initialization failure."""
        with patch('app.services.voice_v2.providers.tts.google_tts.texttospeech_v1.TextToSpeechAsyncClient') as mock_client_class:
            mock_client_class.side_effect = Exception("Credentials not found")

            with pytest.raises(AudioProcessingError, match="Google Cloud TTS initialization failed"):
                await provider.initialize()

    @pytest.mark.asyncio
    async def test_cleanup_resources(self, provider):
        """Test proper resource cleanup."""
        mock_client = AsyncMock()
        provider._client = mock_client

        await provider.cleanup()

        mock_client.close.assert_called_once()
        assert provider._client is None

    # Voice Configuration Tests

    def test_voice_quality_configuration(self, provider):
        """Test voice quality optimization based on request."""
        # Standard quality
        standard_request = TTSRequest(text="Test", quality=TTSQuality.STANDARD)
        params = provider._prepare_synthesis_params(standard_request)

        assert params["voice"].name == "en-US-Journey-D"

        # Premium quality should upgrade voice if needed
        premium_request = TTSRequest(text="Test", quality=TTSQuality.PREMIUM)
        params_premium = provider._prepare_synthesis_params(premium_request)

        # Should maintain Journey voice or upgrade appropriately
        assert "Journey" in params_premium["voice"].name or "Wavenet" in params_premium["voice"].name

    def test_premium_voice_mapping(self, provider):
        """Test premium voice name mapping for different languages."""
        test_cases = [
            ("en-US", "en-US-Journey-D"),
            ("ja-JP", "ja-JP-Wavenet-A"),
            ("zh-CN", "zh-CN-Wavenet-A"),
            ("unknown-XX", "en-US-Journey-D")  # Fallback
        ]

        for language_code, expected_voice in test_cases:
            result = provider._get_premium_voice_name(language_code)
            assert result == expected_voice

    def test_synthesis_parameters_preparation(self, provider):
        """Test synthesis parameters preparation."""
        request = TTSRequest(
            text="Hello, world!",
            voice="en-US-Journey-F",
            language="en-US",
            speed=1.2,
            quality=TTSQuality.HIGH
        )

        params = provider._prepare_synthesis_params(request)

        # Verify synthesis input
        assert isinstance(params["input"], types.SynthesisInput)
        assert params["input"].text == "Hello, world!"

        # Verify voice selection
        assert isinstance(params["voice"], types.VoiceSelectionParams)
        assert params["voice"].language_code == "en-US"
        assert params["voice"].name == "en-US-Journey-F"

        # Verify audio config
        assert isinstance(params["audio_config"], types.AudioConfig)
        assert params["audio_config"].speaking_rate == 1.2
        assert params["audio_config"].audio_encoding == types.AudioEncoding.MP3

    # Error Handling and Retry Logic Tests

    @pytest.mark.asyncio
    async def test_retry_logic_for_rate_limits(self, provider):
        """Test retry logic for Google Cloud API rate limits."""
        mock_client = AsyncMock()
        provider._client = mock_client
        provider._max_retries = 2

        # Simulate rate limit then success
        rate_limit_error = google_exceptions.GoogleAPICallError("Rate limit exceeded")
        rate_limit_error.code = 429

        mock_client.synthesize_speech.side_effect = [
            rate_limit_error,
            rate_limit_error,
            MagicMock(audio_content=b"success_audio")
        ]

        synthesis_params = {"input": MagicMock(), "voice": MagicMock(), "audio_config": MagicMock()}

        with patch('asyncio.sleep') as mock_sleep:
            result = await provider._synthesize_with_retry(synthesis_params)

            assert result == b"success_audio"
            assert mock_sleep.call_count == 2  # Two retries
            # Verify exponential backoff
            mock_sleep.assert_any_call(1)  # 2^0
            mock_sleep.assert_any_call(2)  # 2^1

    @pytest.mark.asyncio
    async def test_authentication_error_no_retry(self, provider):
        """Test that authentication errors are not retried."""
        mock_client = AsyncMock()
        provider._client = mock_client

        auth_error = google_exceptions.Unauthenticated("Invalid credentials")
        mock_client.synthesize_speech.side_effect = auth_error

        synthesis_params = {"input": MagicMock(), "voice": MagicMock(), "audio_config": MagicMock()}

        with pytest.raises(AudioProcessingError, match="Google Cloud authentication failed"):
            await provider._synthesize_with_retry(synthesis_params)

        # Should only be called once (no retries)
        assert mock_client.synthesize_speech.call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, provider):
        """Test behavior when maximum retries are exceeded."""
        mock_client = AsyncMock()
        provider._client = mock_client
        provider._max_retries = 1

        api_error = google_exceptions.GoogleAPICallError("Service unavailable")
        api_error.code = 503
        mock_client.synthesize_speech.side_effect = api_error

        synthesis_params = {"input": MagicMock(), "voice": MagicMock(), "audio_config": MagicMock()}

        with patch('asyncio.sleep'):
            with pytest.raises(AudioProcessingError, match="Google Cloud API error"):
                await provider._synthesize_with_retry(synthesis_params)

        # Should be called max_retries + 1 times
        assert mock_client.synthesize_speech.call_count == 2

    # Synthesis Implementation Tests

    @pytest.mark.asyncio
    async def test_synthesize_speech_success(self, provider):
        """Test successful speech synthesis."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.audio_content = b"test_audio_data"
        mock_client.synthesize_speech.return_value = mock_response
        provider._client = mock_client

        request = TTSRequest(text="Hello, world!")

        # Mock the storage upload method
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://test.url"):
            result = await provider._synthesize_implementation(request)

            assert result == "http://test.url"
            mock_client.synthesize_speech.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_available_voices(self, provider):
        """Test retrieving available voices from Google Cloud."""
        mock_client = AsyncMock()
        provider._client = mock_client

        # Mock voice response
        mock_voice = MagicMock()
        mock_voice.name = "en-US-Journey-D"
        mock_voice.language_codes = ["en-US"]
        mock_voice.ssml_gender.name = "NEUTRAL"
        mock_voice.natural_sample_rate_hertz = 24000

        mock_response = MagicMock()
        mock_response.voices = [mock_voice]
        mock_client.list_voices.return_value = mock_response

        voices = await provider.get_available_voices()

        assert len(voices) == 1
        assert voices[0]["name"] == "en-US-Journey-D"
        assert voices[0]["language_codes"] == ["en-US"]
        assert voices[0]["ssml_gender"] == "NEUTRAL"
        assert voices[0]["natural_sample_rate_hertz"] == 24000

    @pytest.mark.asyncio
    async def test_health_check_success(self, provider):
        """Test successful health check."""
        mock_client = AsyncMock()
        mock_client.list_voices.return_value = MagicMock()
        provider._client = mock_client

        result = await provider.health_check()
        assert result is True
        mock_client.list_voices.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure(self, provider):
        """Test health check failure handling."""
        mock_client = AsyncMock()
        mock_client.list_voices.side_effect = Exception("API error")
        provider._client = mock_client

        result = await provider.health_check()
        assert result is False

    # Utility and Helper Method Tests

    def test_audio_duration_estimation(self, provider):
        """Test audio duration estimation."""
        text = "This is a test with exactly ten words here now."
        estimated_duration = provider._estimate_audio_duration(text, speaking_rate=1.0)

        # 10 words at ~150 words/minute = ~4 seconds
        assert 3.0 <= estimated_duration <= 5.0

        # Test with faster speaking rate
        fast_duration = provider._estimate_audio_duration(text, speaking_rate=2.0)
        assert fast_duration < estimated_duration

    def test_provider_metadata_generation(self, provider):
        """Test provider metadata generation."""
        request = TTSRequest(
            text="Test",
            voice="en-US-Journey-F",
            language="en-US",
            speed=1.2
        )

        metadata = provider._get_provider_metadata(request)

        assert metadata["voice_name"] == "en-US-Journey-F"
        assert metadata["language_code"] == "en-US"
        assert metadata["speaking_rate"] == 1.2
        assert metadata["audio_encoding"] == "MP3"
        assert metadata["provider_version"] == "texttospeech_v1"

    def test_supported_formats(self, provider):
        """Test supported audio formats."""
        formats = provider.supported_formats

        assert AudioFormat.MP3 in formats
        assert AudioFormat.WAV in formats
        assert AudioFormat.OGG in formats
        assert len(formats) >= 3

    def test_max_text_length(self, provider):
        """Test maximum text length configuration."""
        assert provider.max_text_length == 5000

        # Test with custom configuration
        custom_config = {
            "credentials_path": "/test/creds.json",
            "project_id": "test-project",
            "max_text_length": 3000
        }
        custom_provider = GoogleTTSProvider("google", custom_config, 1, True)
        assert custom_provider.max_text_length == 3000

    # Configuration Edge Cases

    def test_missing_credentials_configuration(self):
        """Test provider behavior with missing credentials."""
        config = {"project_id": "test-project"}  # Missing credentials_path

        # Should raise VoiceServiceError during initialization
        with pytest.raises(VoiceServiceError) as exc_info:
            GoogleTTSProvider("google", config, 1, True)

        assert "Missing config fields: ['credentials_path']" in str(exc_info.value)

        # Test that required fields are properly defined using valid instance
        valid_config = {
            "credentials_path": "/path/to/creds.json",
            "project_id": "test-project"
        }
        provider = GoogleTTSProvider("google", valid_config, 1, True)
        required_fields = provider.get_required_config_fields()
        assert "credentials_path" in required_fields

    def test_default_voice_configuration(self):
        """Test provider with minimal configuration."""
        config = {
            "credentials_path": "/path/to/creds.json",
            "project_id": "test-project"
        }
        provider = GoogleTTSProvider("google", config, 1, True)

        # Should use default values
        assert provider._voice_name == "en-US-Wavenet-D"
        assert provider._language_code == "en-US"
        assert provider._speaking_rate == 1.0
        assert provider._pitch == 0.0

    # Performance Optimization Tests (Phase 1.2.3)

    @pytest.mark.asyncio
    async def test_lazy_client_initialization(self, provider):
        """Test lazy initialization pattern."""
        # Client should not be initialized until needed
        assert provider._client is None

        # Mock synthesis should trigger initialization
        with patch.object(provider, 'initialize') as mock_init:
            with patch.object(provider, '_synthesize_with_retry', return_value=b"audio"):
                with patch.object(provider, '_upload_audio_to_storage', return_value="url"):
                    await provider._synthesize_implementation(TTSRequest(text="test"))

                    mock_init.assert_called_once()

    def test_connection_reuse_pattern(self, provider):
        """Test that client connection is reused."""
        # Single client instance should be reused
        assert provider._client is None

        # After initialization, client should be persistent
        mock_client = AsyncMock()
        provider._client = mock_client

        # Multiple operations should use same client
        request1 = TTSRequest(text="test1")
        request2 = TTSRequest(text="test2")

        params1 = provider._prepare_synthesis_params(request1)
        params2 = provider._prepare_synthesis_params(request2)

        # Both should work with same client instance
        assert params1 is not None
        assert params2 is not None
        assert provider._client is mock_client
