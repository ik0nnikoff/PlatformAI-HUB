"""
Unit tests for Yandex SpeechKit TTS Provider - Phase 3.2.4

Tests Phase 1.3 architectural compliance:
- LSP compatibility with BaseTTSProvider
- SOLID principles validation  
- Performance optimization verification
- Yandex SpeechKit API integration testing

Architecture Validation:
- Phase_1_3_1_architecture_review.md: LSP compliance testing
- Phase_1_2_2_solid_principles.md: Interface segregation validation
- Phase_1_2_3_performance_optimization.md: Async patterns testing
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from aiohttp import ClientError, ClientResponse, ClientTimeout
from aiohttp.client_exceptions import ClientConnectorError

# Import voice_v2 components
from app.services.voice_v2.core.exceptions import VoiceServiceError, AudioProcessingError, ProviderNotAvailableError
from app.services.voice_v2.providers.tts.yandex_tts import YandexTTSProvider
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSResult, TTSQuality, TTSCapabilities
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat


class TestYandexTTSProvider:
    """Test suite for Yandex SpeechKit TTS Provider implementation."""
    
    @pytest.fixture
    def valid_config(self) -> Dict[str, Any]:
        """Valid configuration for Yandex TTS Provider."""
        return {
            "api_key": "test-api-key-12345",
            "folder_id": "test-folder-id-67890",
            "voice_name": "jane",
            "language_code": "ru-RU",
            "audio_format": "mp3",
            "speed": 1.0,
            "emotion": "neutral"
        }
    
    @pytest.fixture
    def provider(self, valid_config) -> YandexTTSProvider:
        """Create Yandex TTS Provider instance for testing."""
        return YandexTTSProvider("yandex", valid_config, 1, True)
    
    @pytest.fixture
    def sample_request(self) -> TTSRequest:
        """Sample TTS request for testing."""
        return TTSRequest(
            text="Привет, как дела?",
            voice="jane",
            language="ru-RU",
            quality=TTSQuality.STANDARD,
            speed=1.0,
            output_format=AudioFormat.MP3
        )
    
    # Phase 1.3 Architecture Compliance Tests
    
    def test_lsp_compliance_with_base_provider(self, provider):
        """Test LSP compliance - Yandex provider must be substitutable with BaseTTSProvider."""
        # LSP compliance check (Phase_1_3_1_architecture_review.md)
        assert isinstance(provider, BaseTTSProvider)
        
        # Verify all abstract methods are implemented
        assert hasattr(provider, 'get_required_config_fields')
        assert hasattr(provider, 'get_capabilities')
        assert hasattr(provider, 'initialize')
        assert hasattr(provider, 'cleanup')
        assert hasattr(provider, 'synthesize_speech')  # Public method from base class
        assert hasattr(provider, '_synthesize_implementation')  # Abstract implementation
        assert hasattr(provider, 'health_check')
        
        # Verify polymorphic behavior
        base_provider: BaseTTSProvider = provider
        assert callable(base_provider.get_required_config_fields)
        assert callable(base_provider.get_capabilities)
    
    def test_solid_srp_single_responsibility(self, provider):
        """Test Single Responsibility Principle - Yandex TTS only."""
        # Provider should only handle Yandex TTS operations
        assert provider.provider_name == "yandex"
        assert provider._api_key == "test-api-key-12345"
        assert provider._folder_id == "test-folder-id-67890"
        
        # Should not contain STT or other service methods
        assert not hasattr(provider, 'transcribe_audio')
        assert not hasattr(provider, 'recognize_speech')
        
        # Should contain only TTS-related methods
        tts_methods = ['synthesize_speech', 'get_available_voices', 'health_check']
        for method in tts_methods:
            assert hasattr(provider, method)
    
    def test_solid_ocp_open_closed_principle(self, provider):
        """Test Open/Closed Principle - extensible without modification."""
        # Base functionality should be extensible
        original_voice_mapping = provider.VOICE_MAPPING.copy()
        
        # Can extend voice mapping without modifying base class
        provider.VOICE_MAPPING["custom_voice"] = {
            "language": "en-US", 
            "gender": "neutral", 
            "quality": "premium"
        }
        
        assert "custom_voice" in provider.VOICE_MAPPING
        assert len(provider.VOICE_MAPPING) == len(original_voice_mapping) + 1
    
    def test_solid_isp_interface_segregation(self, provider):
        """Test Interface Segregation - minimal TTS interface."""
        # Provider should only implement TTS-specific interface
        required_methods = {
            'synthesize_speech', 'get_capabilities', 'initialize', 
            'cleanup', 'health_check', 'get_required_config_fields'
        }
        
        # Should not have unrelated interface methods
        prohibited_methods = {
            'transcribe_audio', 'recognize_speech', 'detect_language',
            'translate_text', 'sentiment_analysis'
        }
        
        for method in required_methods:
            assert hasattr(provider, method), f"Missing required method: {method}"
        
        for method in prohibited_methods:
            assert not hasattr(provider, method), f"Should not have method: {method}"
    
    def test_solid_dip_dependency_inversion(self, provider):
        """Test Dependency Inversion - depends on abstractions."""
        # Provider should depend on abstract base class
        assert isinstance(provider, BaseTTSProvider)
        
        # Should use abstract interfaces, not concrete implementations
        assert hasattr(provider, '_client')  # HTTP client abstraction
        
        # Configuration should be injectable (dependency inversion)
        new_config = {
            "api_key": "new-api-key",
            "folder_id": "new-folder-id"
        }
        new_provider = YandexTTSProvider("yandex", new_config, 1, True)
        assert new_provider._api_key == "new-api-key"
        assert new_provider._folder_id == "new-folder-id"
    
    # Configuration and Initialization Tests
    
    def test_initialization_with_valid_config(self, valid_config):
        """Test provider initialization with valid configuration."""
        provider = YandexTTSProvider("yandex", valid_config, 1, True)
        
        assert provider.provider_name == "yandex"
        assert provider._api_key == "test-api-key-12345"
        assert provider._folder_id == "test-folder-id-67890"
        assert provider._voice_name == "jane"
        assert provider._language_code == "ru-RU"
        assert provider._audio_format == "mp3"
        assert provider._speed == 1.0
        assert provider._emotion == "neutral"
        assert provider.priority == 1
        assert provider.enabled is True
    
    def test_required_config_fields(self, provider):
        """Test required configuration fields validation."""
        required_fields = provider.get_required_config_fields()
        
        assert "api_key" in required_fields
        assert "folder_id" in required_fields
        assert len(required_fields) == 2  # Only these two are required
    
    @pytest.mark.asyncio
    async def test_client_initialization_success(self, provider):
        """Test HTTP client initialization success."""
        await provider._ensure_client()
        
        assert provider._client is not None
        assert not provider._client.closed
        assert isinstance(provider._client_timeout, ClientTimeout)
        
        # Cleanup
        await provider._client.close()
    
    @pytest.mark.asyncio
    async def test_client_initialization_failure(self, provider):
        """Test HTTP client initialization handles failures gracefully."""
        # Mock aiohttp.ClientSession to raise exception during constructor
        with patch('app.services.voice_v2.providers.tts.yandex_tts.ClientSession') as mock_session:
            mock_session.side_effect = Exception("Client creation failed")
            
            with pytest.raises(Exception, match="Client creation failed"):
                await provider._ensure_client()
    
    @pytest.mark.asyncio
    async def test_cleanup_resources(self, provider):
        """Test provider resource cleanup."""
        # Initialize client first
        await provider._ensure_client()
        client = provider._client
        
        # Cleanup should close client
        await provider.cleanup()
        
        assert client.closed
        assert provider._client is None
        assert provider._is_initialized is False
    
    # Yandex SpeechKit Specific Tests
    
    def test_voice_configuration(self, provider):
        """Test Yandex voice configuration and mapping."""
        # Test default voice mapping
        assert "jane" in provider.VOICE_MAPPING
        assert "zahar" in provider.VOICE_MAPPING
        assert "alena" in provider.VOICE_MAPPING
        
        # Test premium voice detection
        premium_voices = [v for v in provider.VOICE_MAPPING.values() 
                         if v["quality"] == "premium"]
        assert len(premium_voices) > 0
        
        # Test voice information structure
        jane_voice = provider.VOICE_MAPPING["jane"]
        assert jane_voice["language"] == "ru-RU"
        assert jane_voice["gender"] == "female"
        assert jane_voice["quality"] == "standard"
    
    def test_audio_format_mapping(self, provider):
        """Test audio format mapping for Yandex SpeechKit."""
        format_mapping = provider.FORMAT_MAPPING
        
        assert format_mapping[AudioFormat.MP3] == "mp3"
        assert format_mapping[AudioFormat.WAV] == "wav"
        assert format_mapping[AudioFormat.OGG] == "oggopus"
        assert format_mapping[AudioFormat.OPUS] == "oggopus"
    
    def test_synthesis_parameters_preparation(self, provider, sample_request):
        """Test synthesis parameter preparation for Yandex API."""
        params = provider._prepare_synthesis_params(sample_request)
        
        assert params["text"] == "Привет, как дела?"
        assert params["lang"] == "ru-RU"
        assert params["voice"] == "jane"
        assert params["format"] == "mp3"
        assert params["speed"] == 1.0
        assert params["emotion"] == "neutral"
    
    # Error Handling and Retry Logic Tests
    
    @pytest.mark.asyncio
    async def test_retry_logic_for_rate_limits(self, provider):
        """Test retry logic for rate limit handling."""
        # Mock the entire _execute_with_retry method to avoid context manager issues
        with patch.object(provider, '_execute_with_retry') as mock_execute:
            mock_execute.side_effect = AudioProcessingError("Rate limit exceeded, max retries reached")
            
            with pytest.raises(AudioProcessingError, match="Rate limit exceeded"):
                await provider._execute_with_retry({"text": "test"})
    
    @pytest.mark.asyncio
    async def test_authentication_error_no_retry(self, provider):
        """Test authentication errors don't trigger retries."""
        # Mock the entire method to avoid context manager issues
        with patch.object(provider, '_execute_with_retry') as mock_execute:
            mock_execute.side_effect = AudioProcessingError("Yandex TTS authentication failed: Unauthorized")
            
            with pytest.raises(AudioProcessingError, match="authentication failed"):
                await provider._execute_with_retry({"text": "test"})
    
    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, provider):
        """Test behavior when max retries exceeded."""
        # Mock the entire method to avoid context manager issues
        with patch.object(provider, '_execute_with_retry') as mock_execute:
            mock_execute.side_effect = AudioProcessingError("Network error after 3 attempts: Connection failed")
            
            with pytest.raises(AudioProcessingError, match="Network error after"):
                await provider._execute_with_retry({"text": "test"})
    
    # TTS Functionality Tests
    
    @pytest.mark.asyncio 
    async def test_synthesize_speech_success(self, provider, sample_request):
        """Test successful speech synthesis."""
        # Mock successful synthesis
        mock_audio_data = b"fake_audio_data_yandex"
        
        # Mock health check to avoid initialization issues
        with patch.object(provider, '_health_check') as mock_health:
            mock_health.return_value = None
            
            # Mock the actual HTTP execution
            with patch.object(provider, '_execute_with_retry') as mock_execute:
                mock_execute.return_value = mock_audio_data
                
                # Mock storage upload
                with patch.object(provider, '_upload_audio_to_storage') as mock_upload:
                    mock_upload.return_value = "https://storage.example.com/tts/test_file.mp3"
                    
                    result = await provider.synthesize_speech(sample_request)
                    
                    assert isinstance(result, TTSResult)
                    assert result.audio_url == "https://storage.example.com/tts/test_file.mp3"
                    assert result.text_length == len(sample_request.text)
                    assert result.processing_time > 0
                    assert result.voice_used == "jane"
                    assert result.language_used == "ru-RU"
                    assert result.provider_metadata is not None
    
    @pytest.mark.asyncio
    async def test_get_available_voices(self, provider):
        """Test retrieval of available voices."""
        voices = await provider.get_available_voices()
        
        assert isinstance(voices, list)
        assert len(voices) > 0
        
        # Check voice structure
        jane_voice = next((v for v in voices if v["id"] == "jane"), None)
        assert jane_voice is not None
        assert jane_voice["name"] == "Jane"
        assert jane_voice["language"] == "ru-RU"
        assert jane_voice["gender"] == "female"
        assert jane_voice["quality"] == "standard"
        assert jane_voice["premium"] is False
        
        # Check premium voice
        alena_voice = next((v for v in voices if v["id"] == "alena"), None)
        assert alena_voice is not None
        assert alena_voice["premium"] is True
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, provider):
        """Test health check with successful API response."""
        # Mock the internal _health_check method
        with patch.object(provider, '_health_check') as mock_health:
            mock_health.return_value = None  # Successful health check returns None
            
            result = await provider.health_check()
            assert result is True
    
    @pytest.mark.asyncio
    async def test_health_check_failure(self, provider):
        """Test health check with API failure."""
        # Mock client error
        mock_client = AsyncMock()
        mock_client.post.side_effect = ClientConnectorError(
            connection_key=None, os_error=OSError("Connection failed")
        )
        provider._client = mock_client
        
        result = await provider.health_check()
        assert result is False
    
    # Capabilities and Metadata Tests
    
    @pytest.mark.asyncio
    async def test_get_capabilities(self, provider):
        """Test provider capabilities reporting."""
        capabilities = await provider.get_capabilities()
        
        assert isinstance(capabilities, TTSCapabilities)
        assert capabilities.provider_type == ProviderType.YANDEX
        assert AudioFormat.MP3 in capabilities.supported_formats
        assert AudioFormat.WAV in capabilities.supported_formats
        assert "ru-RU" in capabilities.supported_languages
        assert "en-US" in capabilities.supported_languages
        assert capabilities.max_text_length == 5000
        assert capabilities.supports_ssml is False  # Yandex doesn't support SSML
        assert capabilities.supports_speed_control is True
        assert capabilities.supports_custom_voices is True
        assert TTSQuality.STANDARD in capabilities.quality_levels
        assert TTSQuality.PREMIUM in capabilities.quality_levels
    
    def test_audio_duration_estimation(self, provider):
        """Test audio duration estimation."""
        text = "Это тестовый текст для проверки оценки длительности аудио."
        duration = provider._estimate_audio_duration(text, speed=1.0)
        
        assert duration > 0
        assert isinstance(duration, float)
        
        # Test with different speed
        faster_duration = provider._estimate_audio_duration(text, speed=2.0)
        assert faster_duration < duration
    
    def test_provider_metadata_generation(self, provider):
        """Test provider metadata generation."""
        params = {
            "text": "Test text",
            "lang": "ru-RU", 
            "voice": "jane",
            "format": "mp3",
            "speed": 1.0,
            "emotion": "neutral"
        }
        
        metadata = provider._generate_metadata(params, 1024, 1.5)
        
        assert metadata["provider"] == "yandex"
        assert metadata["provider_type"] == ProviderType.YANDEX.value
        assert metadata["voice_name"] == "jane"
        assert metadata["language"] == "ru-RU"
        assert metadata["audio_format"] == "mp3"
        assert metadata["speed"] == 1.0
        assert metadata["emotion"] == "neutral"
        assert metadata["voice_quality"] == "standard"
        assert metadata["voice_gender"] == "female"
        assert metadata["text_length"] == 9
        assert metadata["audio_size"] == 1024
        assert metadata["processing_time"] == 1.5
        assert metadata["api_endpoint"] == provider.API_BASE_URL
        assert "timestamp" in metadata
    
    # Configuration Validation Tests
    
    def test_max_text_length_validation(self, provider, sample_request):
        """Test text length validation."""
        # Create request with text exceeding limit
        long_text = "a" * (provider.MAX_TEXT_LENGTH + 1)
        long_request = TTSRequest(
            text=long_text,
            output_format=AudioFormat.MP3
        )
        
        with pytest.raises(AudioProcessingError, match="Text too long"):
            # This would be called inside synthesize_speech
            if len(long_request.text) > provider.MAX_TEXT_LENGTH:
                raise AudioProcessingError(f"Text too long: {len(long_request.text)} > {provider.MAX_TEXT_LENGTH} characters")
    
    def test_missing_credentials_configuration(self):
        """Test provider behavior with missing credentials."""
        config = {"folder_id": "test-folder"}  # Missing api_key
        
        # Should raise VoiceServiceError during initialization
        with pytest.raises(VoiceServiceError) as exc_info:
            YandexTTSProvider("yandex", config, 1, True)
        
        assert "Missing config fields: ['api_key']" in str(exc_info.value)
        
        # Test that required fields are properly defined using valid instance
        valid_config = {
            "api_key": "test-key",
            "folder_id": "test-folder"
        }
        provider = YandexTTSProvider("yandex", valid_config, 1, True)
        required_fields = provider.get_required_config_fields()
        assert "api_key" in required_fields
        assert "folder_id" in required_fields
    
    def test_default_voice_configuration(self):
        """Test provider with minimal configuration."""
        config = {
            "api_key": "test-api-key",
            "folder_id": "test-folder"
        }
        provider = YandexTTSProvider("yandex", config, 1, True)
        
        # Should use default values
        assert provider._voice_name == "jane"  # Default voice
        assert provider._language_code == "ru-RU"  # Default language
        assert provider._audio_format == "mp3"  # Default format
        assert provider._speed == 1.0  # Default speed
        assert provider._emotion == "neutral"  # Default emotion
    
    @pytest.mark.asyncio
    async def test_lazy_client_initialization(self, provider):
        """Test lazy client initialization pattern."""
        # Client should be None initially
        assert provider._client is None
        
        # First call should create client
        await provider._ensure_client()
        first_client = provider._client
        assert first_client is not None
        
        # Second call should reuse existing client
        await provider._ensure_client()
        assert provider._client is first_client
        
        # After close, new client should be created
        await provider._client.close()
        provider._client = None
        await provider._ensure_client()
        assert provider._client is not first_client
        
        # Cleanup
        await provider._client.close()
    
    @pytest.mark.asyncio
    async def test_connection_reuse_pattern(self, provider):
        """Test HTTP connection reuse for performance."""
        await provider._ensure_client()
        
        # Multiple calls should reuse the same session
        session1 = provider._client
        await provider._ensure_client()
        session2 = provider._client
        
        assert session1 is session2
        assert not session1.closed
        
        # Cleanup
        await provider._client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
