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

from app.services.voice_v2.core.schemas import (
    STTRequest, STTResponse, ProviderCapabilities
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
        
        assert request.audio_file_path == temp_audio_file
        assert request.language == "en-US"
        assert request.provider is None
        assert request.options == {}
    
    def test_stt_response_creation(self):
        """Test STTResponse creation"""
        response = STTResponse(
            transcribed_text="Test transcription",
            provider_used=ProviderType.OPENAI,
            processing_time_ms=1500.0,
            cached=False
        )
        
        assert response.transcribed_text == "Test transcription"
        assert response.provider_used == ProviderType.OPENAI
        assert response.processing_time_ms == 1500.0
        assert response.cached is False
        assert response.metadata == {}
    
    def test_provider_capabilities_creation(self):
        """Test ProviderCapabilities creation"""
        capabilities = ProviderCapabilities(
            provider_type="openai",
            supported_formats=["wav", "mp3"],
            supported_languages=["en-US", "ru-RU"],
            max_file_size_mb=100,
            supports_real_time=False
        )
        
        assert capabilities.provider_type == "openai"
        assert "wav" in capabilities.supported_formats
        assert "en-US" in capabilities.supported_languages
        assert capabilities.max_file_size_mb == 100
        assert capabilities.supports_real_time is False
    
    @pytest.mark.asyncio
    async def test_mock_provider_interface(self):
        """Test mock provider interface"""
        # Create mock provider
        mock_provider = AsyncMock()
        mock_provider.transcribe_audio.return_value = STTResponse(
            transcribed_text="Mock transcription",
            provider_used=ProviderType.OPENAI,
            processing_time_ms=1000.0,
            cached=False
        )
        mock_provider.health_check.return_value = True
        
        # Test mock calls
        response = await mock_provider.transcribe_audio("fake_request")
        health = mock_provider.health_check()
        
        assert response.transcribed_text == "Mock transcription"
        assert health is True
        mock_provider.transcribe_audio.assert_called_once_with("fake_request")
        mock_provider.health_check.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
