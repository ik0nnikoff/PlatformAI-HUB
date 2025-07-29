"""
Comprehensive tests for OpenAI TTS Provider - Phase 3.2.2

Tests follow Phase 1.3 testing strategies:
- LSP compliance validation
- SOLID principles testing 
- Performance patterns verification
- Error handling and recovery
- Voice quality optimization
- Parallel generation testing
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from openai import RateLimitError, APIConnectionError, AuthenticationError, APIError

from app.services.voice_v2.providers.tts.openai_tts import OpenAITTSProvider
from app.services.voice_v2.providers.tts.models import (
    TTSRequest, TTSResult, TTSCapabilities, TTSQuality
)
from app.services.voice_v2.core.exceptions import (
    AudioProcessingError, VoiceServiceTimeout, VoiceServiceError
)
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat


class TestOpenAITTSProvider:
    """Comprehensive test suite for OpenAI TTS Provider."""
    
    @pytest.fixture
    def valid_config(self):
        """Valid OpenAI configuration."""
        return {
            "api_key": "test_openai_key",
            "model": "tts-1",
            "voice": "alloy",
            "max_retries": 2,
            "timeout": 30.0
        }
    
    @pytest.fixture
    def provider(self, valid_config):
        """Create OpenAI TTS provider instance."""
        return OpenAITTSProvider("openai_tts", valid_config)
    
    @pytest.fixture
    def mock_openai_client(self):
        """Mock OpenAI client for testing."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"fake_audio_data"
        mock_client.audio.speech.create.return_value = mock_response
        return mock_client
    
    @pytest.fixture
    def standard_request(self):
        """Standard TTS request for testing."""
        return TTSRequest(
            text="Hello, this is a test message for TTS synthesis.",
            voice="alloy",
            language="en",
            quality=TTSQuality.STANDARD,
            output_format=AudioFormat.MP3
        )
    
    # LSP Compliance Tests (Phase_1_3_1_architecture_review.md)
    
    def test_provider_initialization_with_config(self, valid_config):
        """Test provider follows LSP initialization contract."""
        provider = OpenAITTSProvider("test_openai", valid_config)
        
        assert provider.provider_name == "test_openai"
        assert provider._api_key == "test_openai_key"
        assert provider._model == "tts-1"
        assert provider._voice == "alloy"
        assert provider.enabled is True
        assert provider._client is None  # Lazy initialization
    
    def test_missing_api_key_validation(self):
        """Test API key validation on initialization."""
        config = {"model": "tts-1"}  # Missing api_key
        
        # Should raise VoiceServiceError due to missing config field
        with pytest.raises(VoiceServiceError, match="Missing config fields"):
            OpenAITTSProvider("test", config)
    
    @pytest.mark.asyncio
    async def test_capabilities_structure(self, provider):
        """Test capabilities follow expected structure."""
        caps = await provider.get_capabilities()
        
        assert isinstance(caps, TTSCapabilities)
        assert caps.provider_type == ProviderType.OPENAI
        assert AudioFormat.MP3 in caps.supported_formats
        assert AudioFormat.OPUS in caps.supported_formats
        assert "en" in caps.supported_languages
        assert "ru" in caps.supported_languages
        assert len(caps.available_voices) == 6  # alloy, echo, fable, onyx, nova, shimmer
        assert caps.max_text_length == 4096
        assert caps.supports_speed_control is True
        assert caps.supports_ssml is False  # OpenAI doesn't support SSML
    
    # SOLID Principles Tests (Phase_1_2_2_solid_principles.md)
    
    @pytest.mark.asyncio
    async def test_single_responsibility_principle(self, provider):
        """Test SRP - provider only handles OpenAI TTS operations."""
        # Should not have methods for caching, metrics, file management
        methods = [method for method in dir(provider) if not method.startswith('_') and callable(getattr(provider, method))]
        allowed_methods = [
            'get_required_config_fields', 'get_capabilities', 'initialize', 
            'cleanup', 'synthesize_speech', 'health_check', 
            'estimate_audio_duration', 'synthesize_long_text'
        ]
        
        for method in methods:
            assert method in allowed_methods, f"Method {method} violates SRP"
    
    @pytest.mark.asyncio 
    async def test_liskov_substitution_principle(self, provider, standard_request):
        """Test LSP - provider is substitutable with BaseTTSProvider."""
        # Mock the client creation from the beginning
        with patch('app.services.voice_v2.providers.tts.openai_tts.AsyncOpenAI') as mock_openai_class:
            mock_client = AsyncMock()
            mock_openai_class.return_value = mock_client
            
            mock_response = MagicMock()
            mock_response.content = b"audio_data"
            mock_client.audio.speech.create.return_value = mock_response
            
            # Initialize with mocked client
            await provider.initialize()
            
            with patch.object(provider, '_upload_audio_to_storage', return_value="http://test.url"):
                result = await provider.synthesize_speech(standard_request)
                
                # Should return TTSResult with expected structure
                assert isinstance(result, TTSResult)
                assert result.audio_url == "http://test.url"
                assert result.text_length == len(standard_request.text)
    
    # Performance Tests (Phase_1_2_3_performance_optimization.md)
    
    @pytest.mark.asyncio
    async def test_lazy_initialization(self, provider):
        """Test lazy initialization pattern."""
        assert provider._client is None
        
        # Mock the OpenAI client creation
        with patch('app.services.voice_v2.providers.tts.openai_tts.AsyncOpenAI') as mock_openai:
            await provider.initialize()
            
            mock_openai.assert_called_once_with(
                api_key="test_openai_key",
                timeout=30.0,
                max_retries=2
            )
            assert provider._client is not None
    
    @pytest.mark.asyncio
    async def test_synthesis_parameters_optimization(self, provider):
        """Test synthesis parameter optimization for different quality levels."""
        # High quality request should use tts-1-hd model
        high_quality_request = TTSRequest(
            text="Test text",
            quality=TTSQuality.HIGH,
            speed=1.2,
            output_format=AudioFormat.WAV
        )
        
        params = provider._prepare_synthesis_params(high_quality_request)
        
        assert params["model"] == "tts-1-hd"
        assert params["speed"] == 1.2
        assert params["response_format"] == "wav"
        assert params["input"] == "Test text"
        assert params["voice"] == "alloy"  # Default voice
    
    @pytest.mark.asyncio
    async def test_retry_logic_with_rate_limiting(self, provider):
        """Test exponential backoff retry logic for rate limits."""
        provider._max_retries = 2
        provider._base_delay = 0.1  # Fast test
        
        # Initialize provider first
        await provider.initialize()
        provider._client = AsyncMock()
        
        # Create mock response for RateLimitError
        mock_response = MagicMock()
        mock_response.request = MagicMock()
        
        # First two calls fail with rate limit, third succeeds
        provider._client.audio.speech.create.side_effect = [
            RateLimitError("Rate limit", response=mock_response, body=None),
            RateLimitError("Rate limit", response=mock_response, body=None),
            MagicMock(content=b"success_audio")
        ]
        
        with patch('asyncio.sleep') as mock_sleep:
            # Test synthesis operation с использованием legacy retry метода (ConnectionManager недоступен в тестах)
            synthesis_params = {"input": "test", "model": "tts-1", "voice": "alloy"}
            result = await provider._synthesize_with_retry(synthesis_params)
            
            assert result == b"success_audio"
            assert provider._client.audio.speech.create.call_count == 3
            # Legacy retry fallback должен срабатывать при отсутствии ConnectionManager
            assert mock_sleep.call_count >= 2  # Two retry delays
    
    @pytest.mark.asyncio
    async def test_connection_pooling_cleanup(self, provider):
        """Test proper resource cleanup."""
        mock_client = AsyncMock()
        provider._client = mock_client
        
        await provider.cleanup()
        
        mock_client.close.assert_called_once()
        assert provider._client is None
    
    # Voice Quality Optimization Tests
    
    @pytest.mark.asyncio
    async def test_voice_quality_model_selection(self, provider):
        """Test model selection based on quality settings."""
        # Standard quality
        standard_req = TTSRequest(text="test", quality=TTSQuality.STANDARD)
        params = provider._prepare_synthesis_params(standard_req)
        assert params["model"] == "tts-1"
        
        # High quality  
        high_req = TTSRequest(text="test", quality=TTSQuality.HIGH)
        params = provider._prepare_synthesis_params(high_req)
        assert params["model"] == "tts-1-hd"
        
        # Premium quality
        premium_req = TTSRequest(text="test", quality=TTSQuality.PREMIUM)
        params = provider._prepare_synthesis_params(premium_req)
        assert params["model"] == "tts-1-hd"
    
    @pytest.mark.asyncio
    async def test_audio_format_mapping(self, provider):
        """Test audio format mapping to OpenAI response formats."""
        format_tests = [
            (AudioFormat.MP3, "mp3"),
            (AudioFormat.OPUS, "opus"),
            (AudioFormat.FLAC, "flac"),
            (AudioFormat.WAV, "wav")
        ]
        
        for audio_format, expected_response_format in format_tests:
            request = TTSRequest(text="test", output_format=audio_format)
            params = provider._prepare_synthesis_params(request)
            assert params["response_format"] == expected_response_format
    
    # Parallel Generation Tests (Phase 3.2.2 requirement)
    
    @pytest.mark.asyncio
    async def test_long_text_parallel_generation(self, provider):
        """Test parallel generation for long texts."""
        # Create a long text that exceeds max_text_length
        long_text = "This is a test sentence. " * 200  # ~5000 chars, exceeds 4096 limit
        
        with patch.object(provider, 'synthesize_speech') as mock_synthesize:
            mock_result = TTSResult(
                audio_url="http://test.url",
                text_length=100,
                audio_duration=5.0
            )
            mock_synthesize.return_value = mock_result
            
            results = await provider.synthesize_long_text(long_text, voice="echo")
            
            # Should be split into multiple chunks
            assert len(results) > 1
            assert mock_synthesize.call_count > 1
            
            # Verify each call had reasonable chunk size
            for call in mock_synthesize.call_args_list:
                request = call[0][0]  # First argument is TTSRequest
                assert len(request.text) <= provider._max_text_length
    
    def test_intelligent_text_splitting(self, provider):
        """Test intelligent text splitting preserves sentence boundaries."""
        text = "First sentence. Second sentence! Third sentence? Fourth sentence."
        chunks = provider._split_text_intelligently(text, 30)  # Force splitting
        
        # Should split preserving sentences
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 30
            # Each chunk should end with punctuation (sentence boundary)
            assert chunk.strip()[-1] in ".!?"
    
    def test_word_level_splitting_fallback(self, provider):
        """Test fallback to word-level splitting for very long sentences."""
        long_sentence = "This is a very long sentence without any punctuation that exceeds the maximum chunk size"
        chunks = provider._split_by_words(long_sentence, 20)
        
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 20
    
    # Error Handling Tests (Phase_1_1_4_architecture_patterns.md)
    
    @pytest.mark.asyncio
    async def test_authentication_error_handling(self, provider):
        """Test authentication error handling."""
        provider._client = AsyncMock()
        
        # Create mock response for AuthenticationError
        mock_response = MagicMock()
        mock_response.request = MagicMock()
        
        provider._client.audio.speech.create.side_effect = AuthenticationError("Invalid API key", response=mock_response, body=None)
        
        with pytest.raises(AudioProcessingError, match="authentication failed"):
            synthesis_params = {"input": "test", "model": "tts-1", "voice": "alloy"}
            await provider._synthesize_with_retry(synthesis_params)
    
    @pytest.mark.asyncio
    async def test_api_error_handling(self, provider):
        """Test API error handling."""
        provider._client = AsyncMock()
        
        # Create mock request object for APIError
        mock_request = MagicMock()
        provider._client.audio.speech.create.side_effect = APIError(
            "Bad request", 
            request=mock_request, 
            body=None
        )
        
        with pytest.raises(AudioProcessingError, match="API error"):
            synthesis_params = {"input": "test", "model": "tts-1", "voice": "alloy"}
            await provider._synthesize_with_retry(synthesis_params)
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self, provider):
        """Test timeout error handling with retries."""
        provider._max_retries = 1
        provider._client = AsyncMock()
        provider._client.audio.speech.create.side_effect = asyncio.TimeoutError()
        
        with patch('asyncio.sleep'):
            with pytest.raises(VoiceServiceTimeout):
                synthesis_params = {"input": "test", "model": "tts-1", "voice": "alloy"}
                await provider._synthesize_with_retry(synthesis_params)
    
    @pytest.mark.asyncio
    async def test_connection_error_retry(self, provider):
        """Test connection error retry logic."""
        provider._max_retries = 1
        provider._base_delay = 0.01  # Fast test
        provider._client = AsyncMock()
        
        # Create mock request object for APIConnectionError
        mock_request = MagicMock()
        
        # First call fails, second succeeds
        provider._client.audio.speech.create.side_effect = [
            APIConnectionError(message="Connection failed", request=mock_request),
            MagicMock(content=b"success")
        ]
        
        with patch('asyncio.sleep'):
            synthesis_params = {"input": "test", "model": "tts-1", "voice": "alloy"}
            result = await provider._synthesize_with_retry(synthesis_params)
            assert result == b"success"
    
    # Integration Tests
    
    @pytest.mark.asyncio
    async def test_full_synthesis_workflow(self, provider, standard_request):
        """Test complete synthesis workflow."""
        # Mock the client creation from the beginning
        with patch('app.services.voice_v2.providers.tts.openai_tts.AsyncOpenAI') as mock_openai_class:
            mock_client = AsyncMock()
            mock_openai_class.return_value = mock_client
            
            mock_response = MagicMock()
            mock_response.content = b"synthesized_audio_data"
            mock_client.audio.speech.create.return_value = mock_response
            
            # Initialize with mocked client
            await provider.initialize()
            
            with patch.object(provider, '_upload_audio_to_storage', return_value="https://storage.url/audio.mp3"):
                result = await provider.synthesize_speech(standard_request)
                
                assert isinstance(result, TTSResult)
                assert result.audio_url == "https://storage.url/audio.mp3"
                assert result.text_length == len(standard_request.text)
                assert result.processing_time > 0
                assert result.voice_used == "alloy"
                assert result.provider_metadata["model"] == "tts-1"
                assert result.provider_metadata["audio_size_bytes"] == len(b"synthesized_audio_data")
    
    @pytest.mark.asyncio
    async def test_custom_settings_integration(self, provider):
        """Test custom settings integration."""
        request = TTSRequest(
            text="Custom test",
            voice="nova",
            speed=1.5,
            custom_settings={"response_format": "flac"}
        )
        
        params = provider._prepare_synthesis_params(request)
        
        assert params["voice"] == "nova"
        assert params["speed"] == 1.5
        assert params["response_format"] == "flac"
    
    def test_provider_representation(self, provider):
        """Test string representation."""
        repr_str = repr(provider)
        assert "OpenAITTSProvider" in repr_str
        assert "model=tts-1" in repr_str
        assert "voice=alloy" in repr_str
        assert "enabled=True" in repr_str
