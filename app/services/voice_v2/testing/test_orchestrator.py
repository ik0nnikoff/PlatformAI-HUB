"""
Voice_v2 Orchestrator Testing Suite

Comprehensive test coverage for VoiceServiceOrchestrator:
- Unit tests с mocked providers
- Fallback логика testing
- Error handling validation
- Performance benchmarking

Following SOLID testing principles and Phase 1.3.3 strategy.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
import time
import tempfile
from pathlib import Path

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.core.interfaces import (
    FullSTTProvider, CacheInterface,
    FileManagerInterface, ProviderType, AudioFormat
)
from app.services.voice_v2.core.schemas import (
    STTRequest, TTSRequest, STTResponse, TTSResponse,
    ProviderCapabilities, VoiceOperation
)
from app.services.voice_v2.core.config import VoiceConfig, BaseProviderConfig
from app.services.voice_v2.core.exceptions import (
    VoiceServiceError, VoiceProviderError
)


@pytest.fixture
def temp_audio_file():
    """Create temporary audio file for testing"""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        # Create fake audio data that's large enough to pass validation
        fake_audio_data = b"RIFF" + b"\x00" * 100 + b"WAVE" + b"\x00" * 1000  # Minimum 1KB of fake WAV data
        tmp.write(fake_audio_data)
        tmp.flush()  # Ensure data is written to disk
        temp_path = tmp.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def mock_stt_provider():
    """Mock STT provider"""
    provider = AsyncMock()
    provider.transcribe_audio.return_value = STTResponse(
        text="Test transcription",
        confidence=0.95,
        language="en-US",
        provider="openai",
        processing_time=1.5
    )
    provider.health_check.return_value = True
    provider.get_capabilities.return_value = ProviderCapabilities(
        provider_type="openai",
        supports_real_time=False,
        supported_languages=["en-US", "ru-RU"],
        max_file_size_mb=100,
        supported_formats=["wav", "mp3"]
    )
    return provider


@pytest.fixture
def mock_tts_provider():
    """Mock TTS provider"""
    provider = AsyncMock()
    provider.synthesize_speech.return_value = TTSResponse(
        audio_data=b"fake_audio_data",
        format=AudioFormat.WAV,
        sample_rate=44100,
        duration=2.0,
        processing_time=2.0,
        provider="openai"
    )
    provider.health_check.return_value = True
    provider.get_capabilities.return_value = ProviderCapabilities(
        provider_type="openai",
        supports_real_time=False,
        supported_languages=["en-US", "ru-RU"],
        max_file_size_mb=50,
        supported_formats=["wav", "mp3"]
    )
    return provider


@pytest.fixture
def mock_cache():
    """Mock cache interface"""
    cache = MagicMock(spec=CacheInterface)
    cache.initialize = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    cache.delete = AsyncMock()
    cache.clear = AsyncMock()
    return cache


@pytest.fixture
def mock_file_manager():
    """Mock file manager interface"""
    manager = MagicMock(spec=FileManagerInterface)
    manager.store_file = AsyncMock(return_value="/tmp/stored_file.wav")
    manager.get_file_url = AsyncMock(return_value="http://example.com/file.wav")
    manager.delete_file = AsyncMock()
    return manager


@pytest.fixture
def test_config():
    """Test configuration"""
    return VoiceConfig(
        stt_providers={
            ProviderType.OPENAI: BaseProviderConfig(
                enabled=True,
                priority=1,
                timeout=30.0,
                max_retries=3,
                api_key="test-openai-key"
            )
        },
        tts_providers={
            ProviderType.OPENAI: BaseProviderConfig(
                enabled=True,
                priority=1,
                timeout=30.0,
                max_retries=3,
                api_key="test-openai-key"
            )
        },
        fallback_enabled=True,
        debug_mode=True
    )


@pytest.fixture
def orchestrator(test_config, mock_cache, mock_file_manager, mock_stt_provider, mock_tts_provider):
    """Create orchestrator instance with mocked dependencies"""
    stt_providers = {ProviderType.OPENAI: mock_stt_provider}
    tts_providers = {ProviderType.OPENAI: mock_tts_provider}

    return VoiceServiceOrchestrator(
        config=test_config,
        stt_providers=stt_providers,
        tts_providers=tts_providers,
        cache_manager=mock_cache,
        file_manager=mock_file_manager
    )


class TestOrchestrator:
    """Test orchestrator initialization and basic functionality"""

    @pytest.mark.asyncio
    async def test_initialization_success(self, orchestrator):
        """Test successful orchestrator initialization"""
        await orchestrator.initialize()

        assert orchestrator._initialized is True

    @pytest.mark.asyncio
    async def test_initialization_with_provider_health_check(self, orchestrator, mock_stt_provider):
        """Test initialization with provider health checks"""
        mock_stt_provider.health_check.return_value = False
        orchestrator._stt_providers = {ProviderType.OPENAI: mock_stt_provider}

        # Initialize should not fail but mark provider as unhealthy
        await orchestrator.initialize()

        # Should still be initialized but provider marked as unhealthy
        assert orchestrator._initialized is True
        mock_stt_provider.health_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup(self, orchestrator):
        """Test resource cleanup"""
        orchestrator._initialized = True

        await orchestrator.cleanup()

        assert orchestrator._initialized is False


class TestSTTOperations:
    """Test STT operations"""

    @pytest.mark.asyncio
    async def test_transcribe_audio_success(self, orchestrator, mock_stt_provider, temp_audio_file):
        """Test successful STT transcription"""
        # Setup
        orchestrator._stt_providers = {ProviderType.OPENAI: mock_stt_provider}
        orchestrator._initialized = True

        # Read audio file as bytes
        with open(temp_audio_file, 'rb') as f:
            audio_data = f.read()

        request = STTRequest(
            audio_data=audio_data,
            language="en-US"
        )

        # Execute
        response = await orchestrator.transcribe_audio(request)

        # Verify
        assert isinstance(response, STTResponse)
        assert response.text == "Test transcription"
        assert response.provider == "openai"
        mock_stt_provider.transcribe_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_transcribe_audio_with_caching(self, orchestrator, mock_stt_provider, mock_cache, temp_audio_file):
        """Test STT with caching"""
        # Setup - cache hit
        cached_response = STTResponse(
            text="Cached transcription",
            confidence=0.98,
            language="en-US",
            provider="openai",
            processing_time=0.1
        )
        mock_cache.get.return_value = cached_response

        orchestrator._stt_providers = {ProviderType.OPENAI: mock_stt_provider}
        orchestrator._initialized = True

        # Read audio file as bytes
        with open(temp_audio_file, 'rb') as f:
            audio_data = f.read()

        request = STTRequest(
            audio_data=audio_data,
            language="en-US"
        )

        # Execute
        response = await orchestrator.transcribe_audio(request)

        # Verify - should return cached result
        assert isinstance(response, STTResponse)
        assert response.text == "Cached transcription"
        mock_stt_provider.transcribe_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_transcribe_audio_provider_fallback(self, orchestrator, mock_cache, temp_audio_file):
        """Test STT provider fallback logic"""
        # Setup multiple providers
        failing_provider = AsyncMock(spec=FullSTTProvider)
        failing_provider.transcribe_audio.side_effect = VoiceProviderError(
            "openai", "stt", Exception("API error")
        )
        failing_provider.health_check.return_value = True

        success_provider = AsyncMock(spec=FullSTTProvider)
        success_provider.transcribe_audio.return_value = STTResponse(
            text="Fallback transcription",
            confidence=0.85,
            language="en-US",
            provider="google",
            processing_time=2.0
        )
        success_provider.health_check.return_value = True

        orchestrator._stt_providers = {
            ProviderType.OPENAI: failing_provider,
            ProviderType.GOOGLE: success_provider
        }
        orchestrator._initialized = True

        # Read audio file as bytes
        with open(temp_audio_file, 'rb') as f:
            audio_data = f.read()

        request = STTRequest(
            audio_data=audio_data,
            language="en-US"
        )

        # Execute
        response = await orchestrator.transcribe_audio(request)

        # Verify fallback worked
        assert isinstance(response, STTResponse)
        assert response.text == "Fallback transcription"
        assert response.provider == "google"
        failing_provider.transcribe_audio.assert_called_once()
        success_provider.transcribe_audio.assert_called_once()


class TestTTSOperations:
    """Test TTS operations"""

    @pytest.mark.asyncio
    async def test_synthesize_speech_success(self, orchestrator, mock_tts_provider):
        """Test successful TTS synthesis"""
        # Setup
        orchestrator._tts_providers = {ProviderType.OPENAI: mock_tts_provider}
        orchestrator._initialized = True

        request = TTSRequest(
            text="Hello world",
            language="en-US",
            voice="alloy"
        )

        # Execute
        response = await orchestrator.synthesize_speech(request)

        # Verify
        assert isinstance(response, TTSResponse)
        assert response.audio_data == b"fake_audio_data"  # Use correct field from schema
        assert response.provider == "openai"  # Use correct field from schema
        mock_tts_provider.synthesize_speech.assert_called_once()


class TestProviderManagement:
    """Test provider management functionality"""

    @pytest.mark.asyncio
    async def test_get_provider_capabilities_stt(self, orchestrator, mock_stt_provider):
        """Test getting STT provider capabilities"""
        orchestrator._stt_providers = {ProviderType.OPENAI: mock_stt_provider}
        orchestrator._initialized = True

        capabilities = await orchestrator.get_provider_capabilities(
            ProviderType.OPENAI, VoiceOperation.STT
        )

        assert isinstance(capabilities, ProviderCapabilities)
        assert "en-US" in capabilities.supported_languages
        mock_stt_provider.get_capabilities.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_provider_health(self, orchestrator, mock_stt_provider):
        """Test provider health checks"""
        orchestrator._stt_providers = {ProviderType.OPENAI: mock_stt_provider}
        orchestrator._initialized = True

        health = await orchestrator.check_provider_health(
            ProviderType.OPENAI, VoiceOperation.STT
        )

        assert health is True
        mock_stt_provider.health_check.assert_called_once()


class TestErrorHandling:
    """Test error handling scenarios"""

    @pytest.mark.asyncio
    async def test_transcribe_audio_all_providers_fail(self, orchestrator, mock_cache, temp_audio_file):
        """Test when all STT providers fail"""
        # Setup failing provider
        failing_provider = AsyncMock(spec=FullSTTProvider)
        failing_provider.transcribe_audio.side_effect = VoiceProviderError(
            "openai", "stt", Exception("All providers failed")
        )
        failing_provider.health_check.return_value = True

        orchestrator._stt_providers = {ProviderType.OPENAI: failing_provider}
        orchestrator._initialized = True

        # Read audio file as bytes
        with open(temp_audio_file, 'rb') as f:
            audio_data = f.read()

        request = STTRequest(
            audio_data=audio_data,
            language="en-US"
        )

        # Execute & Verify exception
        with pytest.raises(VoiceServiceError):
            await orchestrator.transcribe_audio(request)

    @pytest.mark.asyncio
    async def test_uninitialized_orchestrator_error(self, orchestrator, temp_audio_file):
        """Test using uninitialized orchestrator raises error"""
        # Read audio file as bytes
        with open(temp_audio_file, 'rb') as f:
            audio_data = f.read()

        request = STTRequest(
            audio_data=audio_data,
            language="en-US"
        )

        with pytest.raises(VoiceServiceError, match="not initialized"):
            await orchestrator.transcribe_audio(request)


class TestPerformanceBenchmarks:
    """Performance benchmark tests"""

    @pytest.mark.asyncio
    async def test_stt_performance_benchmark(self, orchestrator, mock_stt_provider, temp_audio_file):
        """Benchmark STT performance"""
        orchestrator._stt_providers = {ProviderType.OPENAI: mock_stt_provider}
        orchestrator._initialized = True

        # Read audio file as bytes
        with open(temp_audio_file, 'rb') as f:
            audio_data = f.read()

        request = STTRequest(
            audio_data=audio_data,
            language="en-US"
        )

        # Measure performance
        start_time = time.time()

        tasks = [orchestrator.transcribe_audio(request) for _ in range(5)]
        responses = await asyncio.gather(*tasks)

        end_time = time.time()
        total_time = end_time - start_time

        # Verify all responses
        assert len(responses) == 5
        for response in responses:
            assert isinstance(response, STTResponse)
            assert response.provider == "openai"  # Use correct field from schema

        # Performance assertion (should handle 5 concurrent requests in reasonable time)
        assert total_time < 10.0  # Should complete in under 10 seconds

        print(f"Performance: 5 concurrent STT requests completed in {total_time:.2f}s")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
