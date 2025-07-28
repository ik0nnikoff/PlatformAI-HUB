# Phase 3.2.6 - TTS Testing and Validation

"""
Comprehensive testing strategy для TTS Provider system
включая unit tests, integration tests, и voice quality validation
с полным соблюдением Phase 1.3 архитектурных требований.
"""

import pytest
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call
import os
import hashlib
from io import BytesIO

# Imports для voice_v2 TTS system
from app.services.voice_v2.providers.tts.openai_tts import OpenAITTSProvider
from app.services.voice_v2.providers.tts.google_tts import GoogleTTSProvider  
from app.services.voice_v2.providers.tts.yandex_tts import YandexTTSProvider
from app.services.voice_v2.providers.tts.factory import TTSProviderFactory
from app.services.voice_v2.providers.tts.orchestrator import TTSOrchestrator
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSResponse, TTSCapabilities
from app.services.voice_v2.core.exceptions import VoiceServiceError
from app.services.voice_v2.core.interfaces import BaseTTSProvider


logger = logging.getLogger(__name__)


class TestTTSProviderValidation:
    """
    Phase 3.2.6.1 - Unit tests для каждого TTS провайдера
    
    Comprehensive validation каждого TTS provider:
    - OpenAI TTS Provider functionality
    - Google Cloud TTS Provider functionality  
    - Yandex SpeechKit TTS Provider functionality
    
    Phase 1.3 Requirements:
    - LSP compliance через BaseTTSProvider interface
    - SOLID principles validation
    - Performance optimization patterns
    """
    
    @pytest.fixture
    def openai_config(self):
        """OpenAI TTS provider configuration для testing."""
        return {
            "api_key": "test-openai-key",
            "model": "tts-1-hd",
            "voice": "nova",
            "response_format": "mp3",
            "speed": 1.0
        }
    
    @pytest.fixture  
    def google_config(self):
        """Google Cloud TTS provider configuration для testing."""
        return {
            "service_account_path": "/path/to/service-account.json",
            "language_code": "ru-RU",
            "voice_name": "ru-RU-Standard-A",
            "ssml_gender": "FEMALE",
            "audio_encoding": "MP3"
        }
    
    @pytest.fixture
    def yandex_config(self):
        """Yandex SpeechKit TTS provider configuration для testing."""
        return {
            "api_key": "test-yandex-key",
            "voice": "jane",
            "emotion": "good",
            "speed": 1.0,
            "format": "mp3",
            "lang": "ru-RU"
        }

    @pytest.mark.asyncio
    async def test_openai_tts_provider_functionality(self, openai_config):
        """Test OpenAI TTS Provider core functionality."""
        # Arrange
        provider = OpenAITTSProvider(openai_config)
        test_request = TTSRequest(
            text="Привет мир!",
            voice="nova",
            language="ru",
            speed=1.0
        )
        
        # Mock OpenAI client response
        mock_audio_data = b"fake_audio_data_openai"
        
        with patch.object(provider, '_client') as mock_client:
            mock_response = MagicMock()
            mock_response.content = mock_audio_data
            mock_client.audio.speech.create = AsyncMock(return_value=mock_response)
            
            # Act
            response = await provider.synthesize_speech(test_request)
            
            # Assert - Phase 1.3 LSP Compliance
            assert isinstance(response, TTSResponse)
            assert response.audio_data == mock_audio_data
            assert response.format == "mp3"
            assert response.provider_name == "openai"
            
            # Verify API call
            mock_client.audio.speech.create.assert_called_once()
            call_kwargs = mock_client.audio.speech.create.call_args.kwargs
            assert call_kwargs["input"] == test_request.text
            assert call_kwargs["voice"] == test_request.voice
            assert call_kwargs["model"] == openai_config["model"]

    @pytest.mark.asyncio
    async def test_google_tts_provider_functionality(self, google_config):
        """Test Google Cloud TTS Provider core functionality."""
        # Arrange
        provider = GoogleTTSProvider(google_config)
        test_request = TTSRequest(
            text="Привет мир!",
            voice="ru-RU-Standard-A",
            language="ru-RU"
        )
        
        # Mock Google client response
        mock_audio_data = b"fake_audio_data_google"
        
        with patch.object(provider, '_client') as mock_client:
            mock_response = MagicMock()
            mock_response.audio_content = mock_audio_data
            mock_client.synthesize_speech = AsyncMock(return_value=mock_response)
            
            # Act
            response = await provider.synthesize_speech(test_request)
            
            # Assert - Phase 1.3 LSP Compliance
            assert isinstance(response, TTSResponse)
            assert response.audio_data == mock_audio_data
            assert response.format == "mp3"
            assert response.provider_name == "google"
            
            # Verify API call
            mock_client.synthesize_speech.assert_called_once()

    @pytest.mark.asyncio
    async def test_yandex_tts_provider_functionality(self, yandex_config):
        """Test Yandex SpeechKit TTS Provider core functionality."""
        # Arrange
        provider = YandexTTSProvider(yandex_config)
        test_request = TTSRequest(
            text="Привет мир!",
            voice="jane",
            language="ru-RU",
            speed=1.0
        )
        
        # Mock Yandex API response
        mock_audio_data = b"fake_audio_data_yandex"
        
        with patch('aiohttp.ClientSession.post') as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.read = AsyncMock(return_value=mock_audio_data)
            mock_post.return_value.__aenter__.return_value = mock_response
            
            # Act
            response = await provider.synthesize_speech(test_request)
            
            # Assert - Phase 1.3 LSP Compliance
            assert isinstance(response, TTSResponse)
            assert response.audio_data == mock_audio_data
            assert response.format == "mp3"  
            assert response.provider_name == "yandex"
            
            # Verify API call
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_providers_lsp_compliance(self, openai_config, google_config, yandex_config):
        """
        Test Liskov Substitution Principle compliance для всех TTS providers.
        Phase 1.3.1 Architecture Review requirement.
        """
        # Arrange
        providers = [
            OpenAITTSProvider(openai_config),
            GoogleTTSProvider(google_config), 
            YandexTTSProvider(yandex_config)
        ]
        
        test_request = TTSRequest(
            text="Test LSP compliance",
            voice="default",
            language="en"
        )
        
        # Act & Assert - все providers должны implement BaseTTSProvider interface
        for provider in providers:
            assert isinstance(provider, BaseTTSProvider)
            
            # Test interface methods existence
            assert hasattr(provider, 'synthesize_speech')
            assert hasattr(provider, 'get_capabilities')
            assert hasattr(provider, 'health_check')
            assert hasattr(provider, 'cleanup')
            
            # Test capabilities method
            capabilities = await provider.get_capabilities()
            assert isinstance(capabilities, TTSCapabilities)
            assert capabilities.provider_name == provider.provider_name

    @pytest.mark.asyncio
    async def test_provider_error_handling_consistency(self, openai_config, google_config, yandex_config):
        """Test consistent error handling across всех providers."""
        providers = [
            ("openai", OpenAITTSProvider(openai_config)),
            ("google", GoogleTTSProvider(google_config)),
            ("yandex", YandexTTSProvider(yandex_config))
        ]
        
        invalid_request = TTSRequest(
            text="",  # Empty text should cause error
            voice="invalid_voice",
            language="invalid_lang"
        )
        
        for provider_name, provider in providers:
            with pytest.raises(VoiceServiceError) as exc_info:
                await provider.synthesize_speech(invalid_request)
            
            # Assert consistent error structure
            assert provider_name in str(exc_info.value)
            assert hasattr(exc_info.value, 'provider_name')


class TestTTSIntegrationWithMockedAPIs:
    """
    Phase 3.2.6.2 - Integration tests с mocked APIs
    
    Testing multi-provider integration scenarios:
    - Factory + Orchestrator integration
    - Fallback mechanisms 
    - Circuit breaker patterns
    - Real-world usage scenarios
    """
    
    @pytest.fixture
    def factory_config(self):
        """Complete factory configuration для всех providers."""
        return {
            "openai": {
                "api_key": "test-openai-key",
                "model": "tts-1-hd",
                "voice": "nova"
            },
            "google": {
                "service_account_path": "/path/to/service-account.json",
                "language_code": "ru-RU",
                "voice_name": "ru-RU-Standard-A"
            },
            "yandex": {
                "api_key": "test-yandex-key",
                "voice": "jane",
                "emotion": "good"
            }
        }

    @pytest.mark.asyncio
    async def test_factory_orchestrator_integration(self, factory_config):
        """Test complete Factory + Orchestrator integration."""
        # Arrange
        factory = TTSProviderFactory()
        await factory.initialize(factory_config)
        
        orchestrator = TTSOrchestrator(factory)
        await orchestrator.initialize()
        
        test_request = TTSRequest(
            text="Integration test",
            voice="nova",
            language="en"
        )
        
        # Mock successful response from первого provider
        mock_audio_data = b"integration_test_audio"
        
        with patch.object(factory, 'get_provider') as mock_get_provider:
            mock_provider = AsyncMock()
            mock_provider.synthesize_speech.return_value = TTSResponse(
                audio_data=mock_audio_data,
                format="mp3",
                provider_name="openai",
                metadata={"duration": 2.5}
            )
            mock_provider.health_check.return_value = True
            mock_get_provider.return_value = mock_provider
            
            # Act
            response = await orchestrator.synthesize_speech(test_request)
            
            # Assert
            assert isinstance(response, TTSResponse)
            assert response.audio_data == mock_audio_data
            assert response.provider_name == "openai"

    @pytest.mark.asyncio
    async def test_multi_provider_fallback_scenario(self, factory_config):
        """Test fallback mechanism между providers."""
        # Arrange
        factory = TTSProviderFactory()
        await factory.initialize(factory_config)
        
        orchestrator = TTSOrchestrator(factory)
        await orchestrator.initialize()
        
        test_request = TTSRequest(
            text="Fallback test",
            voice="default",
            language="en"
        )
        
        # Mock первый provider fails, второй succeeds
        mock_audio_data = b"fallback_success_audio"
        
        with patch.object(factory, 'get_available_providers') as mock_get_providers:
            # Setup provider mocks
            failing_provider = AsyncMock()
            failing_provider.synthesize_speech.side_effect = VoiceServiceError("Provider 1 failed", "openai")
            failing_provider.provider_name = "openai"
            
            success_provider = AsyncMock()
            success_provider.synthesize_speech.return_value = TTSResponse(
                audio_data=mock_audio_data,
                format="mp3", 
                provider_name="google",
                metadata={"duration": 3.0}
            )
            success_provider.provider_name = "google"
            
            mock_get_providers.return_value = [failing_provider, success_provider]
            
            # Act
            response = await orchestrator.synthesize_speech(test_request)
            
            # Assert
            assert isinstance(response, TTSResponse)
            assert response.audio_data == mock_audio_data
            assert response.provider_name == "google"
            
            # Verify fallback occurred
            failing_provider.synthesize_speech.assert_called_once()
            success_provider.synthesize_speech.assert_called_once()

    @pytest.mark.asyncio
    async def test_circuit_breaker_integration(self, factory_config):
        """Test circuit breaker pattern в multi-provider scenario."""
        # Arrange
        factory = TTSProviderFactory()
        await factory.initialize(factory_config)
        
        orchestrator = TTSOrchestrator(factory)
        await orchestrator.initialize()
        
        # Configure circuit breaker для тестирования
        await orchestrator.configure_circuit_breaker(
            failure_threshold=2,
            recovery_timeout=1  # 1 second для faster testing
        )
        
        test_request = TTSRequest(
            text="Circuit breaker test",
            voice="default",
            language="en"
        )
        
        with patch.object(factory, 'get_available_providers') as mock_get_providers:
            # Setup failing provider
            failing_provider = AsyncMock()
            failing_provider.synthesize_speech.side_effect = VoiceServiceError("Consistent failure", "openai")
            failing_provider.provider_name = "openai"
            
            mock_get_providers.return_value = [failing_provider]
            
            # Act - trigger circuit breaker
            for i in range(3):  # 3 failures to trigger circuit breaker
                with pytest.raises(VoiceServiceError):
                    await orchestrator.synthesize_speech(test_request)
            
            # Verify circuit breaker is now blocking calls
            health_status = await orchestrator.get_provider_health_status()
            assert "openai" in health_status
            assert not health_status["openai"]["is_healthy"]

    @pytest.mark.asyncio  
    async def test_concurrent_synthesis_requests(self, factory_config):
        """Test concurrent synthesis requests handling."""
        # Arrange
        factory = TTSProviderFactory()
        await factory.initialize(factory_config)
        
        orchestrator = TTSOrchestrator(factory)
        await orchestrator.initialize()
        
        # Create multiple concurrent requests
        requests = [
            TTSRequest(text=f"Concurrent test {i}", voice="nova", language="en")
            for i in range(5)
        ]
        
        mock_audio_data = b"concurrent_test_audio"
        
        with patch.object(factory, 'get_provider') as mock_get_provider:
            mock_provider = AsyncMock()
            mock_provider.synthesize_speech.return_value = TTSResponse(
                audio_data=mock_audio_data,
                format="mp3",
                provider_name="openai",
                metadata={"duration": 1.5}
            )
            mock_get_provider.return_value = mock_provider
            
            # Act - concurrent requests
            tasks = [orchestrator.synthesize_speech(req) for req in requests]
            responses = await asyncio.gather(*tasks)
            
            # Assert
            assert len(responses) == 5
            for response in responses:
                assert isinstance(response, TTSResponse)
                assert response.audio_data == mock_audio_data
                assert response.provider_name == "openai"


class TestVoiceQualityValidation:
    """
    Phase 3.2.6.3 - Voice quality validation
    
    Testing voice output quality и consistency:
    - Audio format validation
    - Voice parameter consistency  
    - Quality metrics collection
    - Performance benchmarking
    """
    
    def test_audio_format_validation(self):
        """Test audio format consistency across providers."""
        # Test data
        test_audio_formats = ["mp3", "wav", "ogg"]
        
        for format_type in test_audio_formats:
            response = TTSResponse(
                audio_data=b"test_audio_data",
                format=format_type,
                provider_name="test_provider",
                metadata={"duration": 2.0}
            )
            
            # Assert
            assert response.format in test_audio_formats
            assert isinstance(response.audio_data, bytes)
            assert len(response.audio_data) > 0

    def test_voice_parameter_consistency(self):
        """Test voice parameter validation across providers."""
        # Test voice parameters
        test_voices = ["nova", "alloy", "echo", "fable", "onyx", "shimmer"]
        test_languages = ["en", "ru", "es", "fr", "de"]
        test_speeds = [0.25, 0.5, 1.0, 1.5, 2.0]
        
        for voice in test_voices:
            request = TTSRequest(
                text="Voice parameter test",
                voice=voice,
                language="en",
                speed=1.0
            )
            
            assert request.voice == voice
            assert request.language in test_languages or request.language == "en"
            assert 0.25 <= request.speed <= 2.0

    @pytest.mark.asyncio
    async def test_quality_metrics_collection(self):
        """Test quality metrics collection для voice synthesis."""
        # Arrange
        mock_provider = AsyncMock()
        mock_provider.provider_name = "test_provider"
        
        test_request = TTSRequest(
            text="Quality metrics test with longer text to measure processing time",
            voice="nova",
            language="en",
            speed=1.0
        )
        
        # Mock response с metadata
        mock_response = TTSResponse(
            audio_data=b"quality_test_audio_data",
            format="mp3",
            provider_name="test_provider",
            metadata={
                "duration": 3.5,
                "processing_time": 0.8,
                "audio_quality": "high",
                "sample_rate": 22050
            }
        )
        
        mock_provider.synthesize_speech.return_value = mock_response
        
        # Act
        response = await mock_provider.synthesize_speech(test_request)
        
        # Assert quality metrics
        assert "duration" in response.metadata
        assert "processing_time" in response.metadata
        assert response.metadata["duration"] > 0
        assert response.metadata["processing_time"] > 0
        assert response.metadata["sample_rate"] > 0

    def test_audio_data_integrity(self):
        """Test audio data integrity и validation."""
        # Test различных audio data sizes
        test_audio_sizes = [1024, 4096, 16384, 65536]  # Different audio data sizes
        
        for size in test_audio_sizes:
            audio_data = b"a" * size  # Simulate audio data
            
            response = TTSResponse(
                audio_data=audio_data,
                format="mp3",
                provider_name="integrity_test",
                metadata={"size_bytes": len(audio_data)}
            )
            
            # Assert
            assert len(response.audio_data) == size
            assert response.metadata["size_bytes"] == size
            assert isinstance(response.audio_data, bytes)

    @pytest.mark.asyncio
    async def test_performance_benchmarking(self):
        """Test performance benchmarking для TTS operations."""
        import time
        
        # Mock providers с different performance characteristics
        fast_provider = AsyncMock()
        fast_provider.provider_name = "fast_provider"
        fast_provider.synthesize_speech = AsyncMock()
        
        slow_provider = AsyncMock()  
        slow_provider.provider_name = "slow_provider"
        slow_provider.synthesize_speech = AsyncMock()
        
        # Simulate different response times
        async def fast_synthesis(request):
            await asyncio.sleep(0.1)  # 100ms
            return TTSResponse(
                audio_data=b"fast_audio",
                format="mp3", 
                provider_name="fast_provider",
                metadata={"processing_time": 0.1}
            )
        
        async def slow_synthesis(request):
            await asyncio.sleep(0.5)  # 500ms  
            return TTSResponse(
                audio_data=b"slow_audio",
                format="mp3",
                provider_name="slow_provider", 
                metadata={"processing_time": 0.5}
            )
        
        fast_provider.synthesize_speech.side_effect = fast_synthesis
        slow_provider.synthesize_speech.side_effect = slow_synthesis
        
        test_request = TTSRequest(
            text="Performance benchmark test",
            voice="default",
            language="en"
        )
        
        # Benchmark fast provider
        start_time = time.time()
        fast_response = await fast_provider.synthesize_speech(test_request)
        fast_duration = time.time() - start_time
        
        # Benchmark slow provider
        start_time = time.time()
        slow_response = await slow_provider.synthesize_speech(test_request)
        slow_duration = time.time() - start_time
        
        # Assert performance differences
        assert fast_duration < slow_duration
        assert fast_response.metadata["processing_time"] < slow_response.metadata["processing_time"]
        assert fast_response.provider_name == "fast_provider"
        assert slow_response.provider_name == "slow_provider"


# Integration test fixtures и utilities
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop для async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_audio_data():
    """Generate consistent mock audio data для testing."""
    return b"mock_audio_data_" + b"a" * 1000  # 1KB of mock audio


def generate_test_audio_hash(audio_data: bytes) -> str:
    """Generate hash для audio data integrity validation."""
    return hashlib.md5(audio_data).hexdigest()


class TTSTestingUtilities:
    """
    Utility functions для TTS testing infrastructure.
    Phase 1.3 compliant testing helpers.
    """
    
    @staticmethod
    async def create_mock_provider(
        provider_name: str,
        success_response: Optional[TTSResponse] = None,
        should_fail: bool = False
    ) -> AsyncMock:
        """Create consistent mock provider для testing."""
        mock_provider = AsyncMock()
        mock_provider.provider_name = provider_name
        
        if should_fail:
            mock_provider.synthesize_speech.side_effect = VoiceServiceError(
                f"Mock failure for {provider_name}", provider_name
            )
            mock_provider.health_check.return_value = False
        else:
            if success_response is None:
                success_response = TTSResponse(
                    audio_data=b"mock_audio_data",
                    format="mp3",
                    provider_name=provider_name,
                    metadata={"duration": 2.0}
                )
            
            mock_provider.synthesize_speech.return_value = success_response
            mock_provider.health_check.return_value = True
        
        return mock_provider
    
    @staticmethod
    def validate_tts_response(response: TTSResponse) -> bool:
        """Validate TTS response structure и content."""
        try:
            assert isinstance(response, TTSResponse)
            assert isinstance(response.audio_data, bytes)
            assert len(response.audio_data) > 0
            assert response.format in ["mp3", "wav", "ogg", "flac"]
            assert isinstance(response.provider_name, str)
            assert len(response.provider_name) > 0
            assert isinstance(response.metadata, dict)
            return True
        except AssertionError:
            return False
    
    @staticmethod
    async def run_provider_stress_test(
        provider: BaseTTSProvider,
        num_requests: int = 10,
        concurrent: bool = True
    ) -> Dict[str, Any]:
        """Run stress test на provider с performance metrics."""
        start_time = time.time()
        
        test_request = TTSRequest(
            text="Stress test message",
            voice="default",
            language="en"
        )
        
        if concurrent:
            tasks = [provider.synthesize_speech(test_request) for _ in range(num_requests)]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            responses = []
            for _ in range(num_requests):
                try:
                    response = await provider.synthesize_speech(test_request)
                    responses.append(response)
                except Exception as e:
                    responses.append(e)
        
        total_time = time.time() - start_time
        successful_responses = [r for r in responses if isinstance(r, TTSResponse)]
        failed_responses = [r for r in responses if isinstance(r, Exception)]
        
        return {
            "total_requests": num_requests,
            "successful_requests": len(successful_responses),
            "failed_requests": len(failed_responses),
            "total_time": total_time,
            "average_time_per_request": total_time / num_requests,
            "success_rate": len(successful_responses) / num_requests,
            "concurrent": concurrent
        }


if __name__ == "__main__":
    """
    Run TTS testing suite.
    
    Usage:
        python -m pytest app/services/voice_v2/testing/test_tts_validation.py -v
        python -m pytest app/services/voice_v2/testing/test_tts_validation.py::TestTTSProviderValidation -v
        python -m pytest app/services/voice_v2/testing/test_tts_validation.py::TestTTSIntegrationWithMockedAPIs -v
        python -m pytest app/services/voice_v2/testing/test_tts_validation.py::TestVoiceQualityValidation -v
    """
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
