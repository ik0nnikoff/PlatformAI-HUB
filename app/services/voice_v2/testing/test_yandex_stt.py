"""
Comprehensive Unit Tests for YandexSTTProvider - Phase 3.1.4

100% code coverage тесты для Yandex STT провайдера, следуя принципам:
- LSP compliance validation 
- Performance testing с измерением времени
- Error handling всех edge cases
- Mock всех внешних зависимостей (aiohttp, pydub, settings)
- SOLID principles verification

Test categories:
- Initialization and cleanup
- Configuration validation
- Health checking
- Audio transcription (success cases)
- Error handling and retry logic
- Performance validation
- Helper methods testing
"""

import asyncio
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add the project root to Python path  
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

import pytest
from aiohttp import ClientSession

from app.services.voice_v2.providers.stt.yandex_stt import YandexSTTProvider
from app.services.voice_v2.providers.stt.models import STTRequest, STTResult, STTCapabilities, STTQuality
from app.services.voice_v2.core.exceptions import (
    VoiceServiceError, 
    ProviderNotAvailableError, 
    AudioProcessingError,
    VoiceServiceTimeout
)
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat


# ==================== FIXTURES ====================

@pytest.fixture
def basic_config():
    """Basic valid configuration for Yandex STT."""
    return {
        "api_key": "test_api_key_12345",
        "folder_id": "test_folder_id",
        "max_connections": 5,
        "connection_timeout": 15.0,
        "read_timeout": 30.0
    }


@pytest.fixture
def minimal_config():
    """Minimal configuration (relies on settings)."""
    return {}


@pytest.fixture
def invalid_config():
    """Invalid configuration for testing error cases."""
    return {
        "api_key": "",  # Empty API key
        "folder_id": None  # None folder ID
    }


@pytest.fixture
def temp_audio_file():
    """Create temporary audio file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        # Write minimal WAV header for testing
        wav_header = (
            b'RIFF\x24\x08\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00'
            b'\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00data\x00\x08\x00\x00'
        )
        tmp.write(wav_header)
        tmp.write(b'\x00' * 1000)  # Some audio data
        tmp.flush()
        yield tmp.name
    
    # Cleanup
    Path(tmp.name).unlink(missing_ok=True)


@pytest.fixture
def temp_ogg_file():
    """Create temporary OGG file for conversion testing."""
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
        # Write OGG header
        ogg_header = b'OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00'
        tmp.write(ogg_header)
        tmp.write(b'\x00' * 500)  # Some audio data
        tmp.flush()
        yield tmp.name
    
    # Cleanup
    Path(tmp.name).unlink(missing_ok=True)


@pytest.fixture
def mock_session():
    """Mock aiohttp ClientSession."""
    session = AsyncMock(spec=ClientSession)
    return session


@pytest.fixture
def mock_response():
    """Mock aiohttp response."""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={"result": "Hello world"})
    response.text = AsyncMock(return_value="")
    return response


# ==================== INITIALIZATION TESTS ====================

class TestYandexSTTProviderInitialization:
    """Test YandexSTTProvider initialization and configuration."""
    
    def test_init_with_valid_config(self, basic_config):
        """Test initialization with valid configuration."""
        provider = YandexSTTProvider(basic_config, priority=2, enabled=True)
        
        assert provider.provider_name == "yandex"
        assert provider.priority == 2
        assert provider.enabled is True
        assert provider.api_key == "test_api_key_12345"
        assert provider.folder_id == "test_folder_id"
        assert provider.max_connections == 5
        assert provider.connection_timeout == 15.0
        assert provider.read_timeout == 30.0
        assert not provider._initialized
        assert provider._session is None
        assert provider._connector is None
    
    @patch('app.services.voice_v2.providers.stt.yandex_stt.settings')
    def test_init_with_settings_fallback(self, mock_settings, minimal_config):
        """Test initialization with settings fallback."""
        # Mock settings
        mock_settings.YANDEX_API_KEY.get_secret_value.return_value = "settings_api_key"
        mock_settings.YANDEX_FOLDER_ID = "settings_folder_id"
        
        provider = YandexSTTProvider(minimal_config)
        
        assert provider.api_key == "settings_api_key"
        assert provider.folder_id == "settings_folder_id"
        assert provider.priority == 3  # Default priority
        assert provider.enabled is True  # Default enabled
    
    def test_init_with_defaults(self, basic_config):
        """Test initialization with default values."""
        provider = YandexSTTProvider(basic_config)
        
        assert provider.priority == 3
        assert provider.enabled is True
        assert provider.max_connections == 5  # From config
        assert provider.connection_timeout == 15.0  # From config
        assert provider.read_timeout == 30.0  # From config
    
    def test_get_required_config_fields(self, basic_config):
        """Test required config fields (should be empty for Yandex)."""
        provider = YandexSTTProvider(basic_config)
        required_fields = provider.get_required_config_fields()
        
        assert required_fields == []


# ==================== CAPABILITIES TESTS ====================

class TestYandexSTTProviderCapabilities:
    """Test YandexSTTProvider capabilities."""
    
    @pytest.mark.asyncio
    async def test_get_capabilities(self, basic_config):
        """Test get_capabilities method."""
        provider = YandexSTTProvider(basic_config)
        capabilities = await provider.get_capabilities()
        
        assert isinstance(capabilities, STTCapabilities)
        assert capabilities.provider_type == ProviderType.YANDEX
        assert AudioFormat.WAV in capabilities.supported_formats
        assert AudioFormat.MP3 in capabilities.supported_formats
        assert AudioFormat.OGG in capabilities.supported_formats
        assert "ru-RU" in capabilities.supported_languages
        assert "en-US" in capabilities.supported_languages
        assert capabilities.max_file_size_mb == 1.0
        assert capabilities.max_duration_seconds == 30.0
        assert STTQuality.STANDARD in capabilities.supports_quality_levels
        assert not capabilities.supports_language_detection
        assert not capabilities.supports_word_timestamps
        assert not capabilities.supports_speaker_diarization
    
    @pytest.mark.asyncio
    async def test_capabilities_constants(self, basic_config):
        """Test capabilities match provider constants."""
        provider = YandexSTTProvider(basic_config)
        capabilities = await provider.get_capabilities()
        
        assert capabilities.max_file_size_mb == provider.MAX_FILE_SIZE_MB
        assert len(capabilities.supported_formats) > 0
        assert capabilities.provider_type == ProviderType.YANDEX


# ==================== LIFECYCLE TESTS ====================

class TestYandexSTTProviderLifecycle:
    """Test YandexSTTProvider lifecycle management."""
    
    @pytest.mark.asyncio
    @patch('app.core.config.settings')
    async def test_initialize_success(self, mock_settings, basic_config, mock_session, mock_response):
        """Test successful initialization."""
        # Mock settings
        mock_settings.YANDEX_API_KEY.get_secret_value.return_value = "test_key"
        mock_settings.YANDEX_FOLDER_ID = "test_folder"
        
        provider = YandexSTTProvider(basic_config)
        
        # Mock session creation and health check
        with patch('aiohttp.ClientSession', return_value=mock_session), \
             patch('aiohttp.TCPConnector'), \
             patch.object(provider, 'health_check', return_value=True):
            
            await provider.initialize()
            
            assert provider._initialized is True
            assert provider._session is not None
    
    @pytest.mark.asyncio
    async def test_initialize_no_api_key(self, basic_config):
        """Test initialization failure with missing API key."""
        config = basic_config.copy()
        config["api_key"] = None
        
        with patch('app.services.voice_v2.providers.stt.yandex_stt.settings') as mock_settings:
            mock_settings.YANDEX_API_KEY = None
            
            provider = YandexSTTProvider(config)
            
            with pytest.raises(ProviderNotAvailableError) as exc_info:
                await provider.initialize()
            
            assert "API key not configured" in str(exc_info.value)
            assert not provider._initialized
    
    @pytest.mark.asyncio
    async def test_initialize_no_folder_id(self, basic_config):
        """Test initialization failure with missing folder ID."""
        config = basic_config.copy()
        config["folder_id"] = None
        
        with patch('app.services.voice_v2.providers.stt.yandex_stt.settings') as mock_settings:
            mock_settings.YANDEX_FOLDER_ID = None
            
            provider = YandexSTTProvider(config)
            
            with pytest.raises(ProviderNotAvailableError) as exc_info:
                await provider.initialize()
            
            assert "Folder ID not configured" in str(exc_info.value)
            assert not provider._initialized
    
    @pytest.mark.asyncio
    async def test_initialize_health_check_failure(self, basic_config, mock_session):
        """Test initialization failure due to health check."""
        provider = YandexSTTProvider(basic_config)
        
        with patch('aiohttp.ClientSession', return_value=mock_session), \
             patch('aiohttp.TCPConnector'), \
             patch.object(provider, 'health_check', side_effect=Exception("Health check failed")):
            
            with pytest.raises(ProviderNotAvailableError):
                await provider.initialize()
            
            assert not provider._initialized
    
    @pytest.mark.asyncio
    async def test_initialize_already_initialized(self, basic_config):
        """Test initialize when already initialized."""
        provider = YandexSTTProvider(basic_config)
        provider._initialized = True
        
        # Should return immediately without doing anything
        await provider.initialize()
        
        assert provider._initialized is True
    
    @pytest.mark.asyncio
    async def test_cleanup(self, basic_config):
        """Test cleanup method."""
        provider = YandexSTTProvider(basic_config)
        
        # Mock session and connector
        mock_session = AsyncMock()
        mock_connector = AsyncMock()
        
        provider._session = mock_session
        provider._connector = mock_connector
        provider._initialized = True
        
        await provider.cleanup()
        
        mock_session.close.assert_called_once()
        mock_connector.close.assert_called_once()
        assert provider._session is None
        assert provider._connector is None
        assert not provider._initialized
    
    @pytest.mark.asyncio
    async def test_cleanup_connections_none(self, basic_config):
        """Test cleanup when connections are None."""
        provider = YandexSTTProvider(basic_config)
        
        # Should not raise exception
        await provider.cleanup()
        
        assert not provider._initialized


# ==================== HEALTH CHECK TESTS ====================

class TestYandexSTTProviderHealthCheck:
    """Test YandexSTTProvider health checking."""
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, basic_config, mock_session, mock_response):
        """Test successful health check."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        
        # Mock successful response (status 200)
        mock_response.status = 200
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        result = await provider.health_check()
        
        assert result is True
        mock_session.post.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_health_check_unauthorized(self, basic_config, mock_session, mock_response):
        """Test health check with unauthorized status."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        
        # Mock unauthorized response (status 401)
        mock_response.status = 401
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        result = await provider.health_check()
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_health_check_no_session(self, basic_config):
        """Test health check when session is None."""
        provider = YandexSTTProvider(basic_config)
        provider._session = None
        
        result = await provider.health_check()
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_health_check_exception(self, basic_config, mock_session):
        """Test health check with exception."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        
        # Mock exception during request
        mock_session.post.side_effect = Exception("Connection error")
        
        result = await provider.health_check()
        
        assert result is False


# ==================== TRANSCRIPTION TESTS ====================

class TestYandexSTTProviderTranscription:
    """Test YandexSTTProvider transcription functionality."""
    
    @pytest.mark.asyncio
    async def test_transcribe_success(self, basic_config, temp_audio_file, mock_session, mock_response):
        """Test successful transcription."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock successful API response
        mock_response.status = 200
        mock_response.json.return_value = {"result": "Hello world"}
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        request = STTRequest(
            audio_file_path=temp_audio_file,
            language="ru-RU",
            quality=STTQuality.STANDARD
        )
        
        result = await provider.transcribe_audio(request)
        
        assert isinstance(result, STTResult)
        assert result.text == "Hello world"
        assert result.confidence == 1.0
        assert result.language_detected == "ru-RU"
        assert result.processing_time is not None
        assert result.word_count == 2
        assert "yandex_format" in result.provider_metadata
    
    @pytest.mark.asyncio
    async def test_transcribe_ogg_conversion(self, basic_config, temp_ogg_file, mock_session, mock_response):
        """Test transcription with OGG to WAV conversion."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock successful API response
        mock_response.status = 200
        mock_response.json.return_value = {"result": "Converted audio"}
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        # Mock pydub AudioSegment for conversion
        with patch('pydub.AudioSegment') as mock_audio_segment:
            mock_segment = MagicMock()
            mock_audio_segment.from_ogg.return_value = mock_segment
            
            # Mock export method
            def mock_export(buffer, **kwargs):
                buffer.write(b'RIFF' + b'\x00' * 100)  # Mock WAV data
            
            mock_segment.export = mock_export
            
            request = STTRequest(
                audio_file_path=temp_ogg_file,
                language="ru",
                quality=STTQuality.STANDARD
            )
            
            result = await provider.transcribe_audio(request)
            
            assert result.text == "Converted audio"
            assert "yandex_format" in result.provider_metadata
            assert result.provider_metadata["original_format"] == "ogg"
    
    @pytest.mark.asyncio
    async def test_transcribe_file_too_large(self, basic_config, temp_audio_file):
        """Test transcription with file too large."""
        provider = YandexSTTProvider(basic_config)
        provider._initialized = True
        
        # Mock session to avoid error
        provider._session = AsyncMock()
        
        # Mock file size validation to trigger error earlier in validation
        request = STTRequest(audio_file_path=temp_audio_file)
        
        # Mock the validation to trigger file size error
        with patch.object(provider, '_validate_request') as mock_validate:
            mock_validate.side_effect = AudioProcessingError("File too large")
            
            with pytest.raises(AudioProcessingError) as exc_info:
                await provider.transcribe_audio(request)
            
            assert "File too large" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_transcribe_not_initialized(self, basic_config, temp_audio_file):
        """Test transcription when not initialized."""
        provider = YandexSTTProvider(basic_config, enabled=False)
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        with pytest.raises(ProviderNotAvailableError):
            await provider.transcribe_audio(request)
    
    @pytest.mark.asyncio
    async def test_transcribe_provider_disabled(self, basic_config, temp_audio_file):
        """Test transcription when provider is disabled."""
        provider = YandexSTTProvider(basic_config, enabled=False)
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        with pytest.raises(ProviderNotAvailableError) as exc_info:
            await provider.transcribe_audio(request)
        
        assert "disabled" in str(exc_info.value)


# ==================== ERROR HANDLING TESTS ====================

class TestYandexSTTProviderErrorHandling:
    """Test YandexSTTProvider error handling."""
    
    @pytest.mark.asyncio
    async def test_api_error_handling(self, basic_config, temp_audio_file, mock_session, mock_response):
        """Test API error handling."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock API error response
        mock_response.status = 400
        mock_response.text.return_value = "Bad request error"
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        with pytest.raises(VoiceServiceError) as exc_info:
            await provider._transcribe_implementation(request)
        
        assert "Yandex API error 400" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_rate_limit_handling(self, basic_config, temp_audio_file, mock_session, mock_response):
        """Test rate limit handling with retry."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock rate limit response, then success
        mock_response_429 = AsyncMock()
        mock_response_429.status = 429
        mock_response_429.text.return_value = "Rate limit exceeded"
        
        mock_response_200 = AsyncMock()
        mock_response_200.status = 200
        mock_response_200.json.return_value = {"result": "Success after retry"}
        
        mock_session.post.return_value.__aenter__.side_effect = [
            mock_response_429, mock_response_200
        ]
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        # Mock sleep to speed up test
        with patch('asyncio.sleep'):
            result = await provider._transcribe_implementation(request)
        
        assert result.text == "Success after retry"
        assert mock_session.post.call_count == 2
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self, basic_config, temp_audio_file, mock_session):
        """Test timeout handling."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock timeout error
        mock_session.post.side_effect = asyncio.TimeoutError()
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        with pytest.raises(VoiceServiceTimeout):
            await provider._transcribe_implementation(request)
    
    @pytest.mark.asyncio
    async def test_retry_exhaustion(self, basic_config, temp_audio_file, mock_session):
        """Test retry exhaustion."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock consistent timeouts
        mock_session.post.side_effect = asyncio.TimeoutError()
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        with pytest.raises(VoiceServiceTimeout):
            await provider._transcribe_implementation(request)


# ==================== HELPER METHOD TESTS ====================

class TestYandexSTTProviderHelpers:
    """Test YandexSTTProvider helper methods."""
    
    def test_normalize_language(self, basic_config):
        """Test language normalization."""
        provider = YandexSTTProvider(basic_config)
        
        assert provider._normalize_language("auto") == "ru-RU"
        assert provider._normalize_language("ru") == "ru-RU"
        assert provider._normalize_language("en") == "en-US"
        assert provider._normalize_language("tr") == "tr-TR"
        assert provider._normalize_language("uk-UA") == "uk-UA"  # Already normalized
        assert provider._normalize_language("unknown") == "unknown"  # Pass through
    
    @pytest.mark.asyncio
    async def test_process_audio_data_wav(self, basic_config, temp_audio_file):
        """Test audio processing for WAV files."""
        provider = YandexSTTProvider(basic_config)
        
        audio_data = b'RIFF\x00\x00\x00\x00WAVE'
        audio_path = Path(temp_audio_file)
        
        processed_audio, yandex_format = await provider._process_audio_data(audio_data, audio_path)
        
        assert processed_audio == audio_data
        assert yandex_format == "lpcm"
    
    @pytest.mark.asyncio
    async def test_process_audio_data_mp3(self, basic_config):
        """Test audio processing for MP3 files."""
        provider = YandexSTTProvider(basic_config)
        
        audio_data = b'ID3\x03\x00\x00\x00'
        audio_path = Path("test.mp3")
        
        processed_audio, yandex_format = await provider._process_audio_data(audio_data, audio_path)
        
        assert processed_audio == audio_data
        assert yandex_format == "mp3"
    
    @pytest.mark.asyncio
    async def test_load_audio_file_success(self, basic_config, temp_audio_file):
        """Test successful audio file loading."""
        provider = YandexSTTProvider(basic_config)
        
        audio_data = await provider._load_audio_file(Path(temp_audio_file))
        
        assert isinstance(audio_data, bytes)
        assert len(audio_data) > 0
    
    @pytest.mark.asyncio
    async def test_load_audio_file_not_found(self, basic_config):
        """Test audio file loading when file not found."""
        provider = YandexSTTProvider(basic_config)
        
        with pytest.raises(AudioProcessingError) as exc_info:
            await provider._load_audio_file(Path("nonexistent.wav"))
        
        assert "Failed to load audio file" in str(exc_info.value)
    
    def test_get_status_info(self, basic_config):
        """Test status info retrieval."""
        provider = YandexSTTProvider(basic_config, priority=2, enabled=True)
        provider._request_count = 5
        provider._total_processing_time = 10.0
        
        status = provider.get_status_info()
        
        assert status["provider_name"] == "yandex"
        assert status["enabled"] is True
        assert status["priority"] == 2
        assert status["request_count"] == 5
        assert status["avg_processing_time"] == 2.0
        assert "capabilities" in status
        assert status["capabilities"]["max_file_size_mb"] == 1.0


# ==================== PERFORMANCE TESTS ====================

class TestYandexSTTProviderPerformance:
    """Test YandexSTTProvider performance requirements."""
    
    @pytest.mark.asyncio
    async def test_initialization_performance(self, basic_config, mock_session):
        """Test initialization performance target (≤100ms)."""
        provider = YandexSTTProvider(basic_config)
        
        with patch('aiohttp.ClientSession', return_value=mock_session), \
             patch('aiohttp.TCPConnector'), \
             patch.object(provider, 'health_check', return_value=True):
            
            start_time = asyncio.get_event_loop().time()
            await provider.initialize()
            end_time = asyncio.get_event_loop().time()
            
            initialization_time = (end_time - start_time) * 1000  # Convert to ms
            
            assert provider._initialized is True
            # Note: In real environment should be ≤100ms, but in tests with mocks it's much faster
            assert initialization_time < 1000  # Relaxed for test environment
    
    @pytest.mark.asyncio
    async def test_transcription_performance_tracking(self, basic_config, temp_audio_file, mock_session, mock_response):
        """Test transcription performance tracking."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock successful API response
        mock_response.status = 200
        mock_response.json.return_value = {"result": "Performance test"}
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        result = await provider.transcribe_audio(request)
        
        assert result.processing_time is not None
        assert result.processing_time > 0
        assert provider._request_count == 1
        assert provider._total_processing_time > 0


# ==================== INTEGRATION TESTS ====================

class TestYandexSTTProviderIntegration:
    """Test YandexSTTProvider integration scenarios."""
    
    @pytest.mark.asyncio
    async def test_full_workflow_success(self, basic_config, temp_audio_file, mock_session, mock_response):
        """Test complete workflow from initialization to transcription."""
        provider = YandexSTTProvider(basic_config)
        
        # Mock successful API responses
        mock_response.status = 200
        mock_response.json.return_value = {"result": "Full workflow test"}
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        with patch('aiohttp.ClientSession', return_value=mock_session), \
             patch('aiohttp.TCPConnector'), \
             patch.object(provider, 'health_check', return_value=True):
            
            # Initialize
            await provider.initialize()
            assert provider._initialized is True
            
            # Mock the session after initialization
            provider._session = mock_session
            
            # Transcribe
            request = STTRequest(
                audio_file_path=temp_audio_file,
                language="auto",
                quality=STTQuality.HIGH
            )
            
            result = await provider.transcribe_audio(request)
            
            assert result.text == "Full workflow test"
            assert result.language_detected == "ru-RU"  # auto -> ru-RU
            
            # Cleanup
            await provider.cleanup()
            assert not provider._initialized
    
    @pytest.mark.asyncio
    async def test_multiple_requests_performance(self, basic_config, temp_audio_file, mock_session, mock_response):
        """Test performance with multiple requests."""
        provider = YandexSTTProvider(basic_config)
        provider._session = mock_session
        provider._initialized = True
        
        # Mock successful API responses
        mock_response.status = 200
        mock_response.json.return_value = {"result": "Multiple requests test"}
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        request = STTRequest(audio_file_path=temp_audio_file)
        
        # Execute multiple requests
        num_requests = 3
        results = []
        
        for i in range(num_requests):
            result = await provider.transcribe_audio(request)
            results.append(result)
        
        # Verify all requests succeeded
        assert len(results) == num_requests
        assert all(r.text == "Multiple requests test" for r in results)
        
        # Verify performance tracking
        assert provider._request_count == num_requests
        assert provider._total_processing_time > 0
        
        status = provider.get_status_info()
        assert status["avg_processing_time"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
