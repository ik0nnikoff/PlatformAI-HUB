"""
Simple Voice_v2 Orchestrator Test - Phase 2.2.3

Минимальный тест для завершения Phase 2.2.3
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

from app.services.voice_v2.providers.stt.models import (
    STTRequest, STTResult, STTCapabilities, STTQuality
)
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat


@pytest.fixture
def temp_audio_file():
    """Create temporary audio file for testing"""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        tmp.write(b"fake audio data")
        yield tmp.name
    # Cleanup
    Path(tmp.name).unlink(missing_ok=True)


class TestBasicFunctionality:
    """Basic functionality tests"""

    def test_stt_request_creation(self, temp_audio_file):
        """Test STTRequest creation with valid file"""
        request = STTRequest(
            audio_file_path=temp_audio_file,
            language="en-US"
        )

        assert str(request.audio_file_path) == temp_audio_file
        assert request.language == "en-US"
        assert request.quality == STTQuality.STANDARD  # default value

    def test_stt_response_creation(self):
        """Test STTResult creation"""
        result = STTResult(
            text="Test transcription",
            confidence=0.95,
            language_detected="en-US",
            processing_time=1.5,
            word_count=2
        )

        assert result.text == "Test transcription"
        assert result.confidence == 0.95
        assert result.language_detected == "en-US"
        assert result.processing_time == 1.5
        assert result.word_count == 2

    def test_provider_capabilities_creation(self):
        """Test STTCapabilities creation"""
        capabilities = STTCapabilities(
            provider_type=ProviderType.OPENAI,
            supported_formats=[AudioFormat.WAV, AudioFormat.MP3],
            supported_languages=["en-US", "fr-FR", "de-DE"],
            max_file_size_mb=100.0,
            max_duration_seconds=600.0,
            supports_quality_levels=[STTQuality.STANDARD, STTQuality.HIGH],
            supports_language_detection=True
        )

        assert capabilities.provider_type == ProviderType.OPENAI
        assert AudioFormat.WAV in capabilities.supported_formats
        assert "en-US" in capabilities.supported_languages
        assert capabilities.max_file_size_mb == 100.0
        assert capabilities.supports_language_detection is True

    @pytest.mark.asyncio
    async def test_mock_provider_interface(self):
        """Test mock provider interface"""
        # Create mock provider
        mock_provider = AsyncMock()
        mock_provider.transcribe_audio.return_value = STTResult(
            text="Mock transcription",
            confidence=0.95,
            language_detected="en-US",
            processing_time=1.0,
            word_count=2
        )

        # Test mock call
        request = STTRequest(
            audio_file_path="/tmp/test.wav",
            language="en-US"
        )

        result = await mock_provider.transcribe_audio(request)

        assert result.text == "Mock transcription"
        assert result.confidence == 0.95
        assert result.language_detected == "en-US"
        mock_provider.transcribe_audio.assert_called_once_with(request)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
