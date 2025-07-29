"""
Unit tests for Enhanced Provider Factory - Phase 3.4.2.3

Tests comprehensive Enhanced Factory functionality:
- Orchestrator compatibility methods
- Connection manager integration
- Provider creation with shared pooling
- Legacy factory replacement validation

Architecture Validation:
- Phase_3_4_2_2: Connection manager integration
- Phase_3_4_2_3: Legacy cleanup and interface compatibility
- Phase_1_3_1: LSP compliance testing
- Phase_1_2_2: SOLID principles validation
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, Mock

from app.services.voice_v2.providers.enhanced_factory import (
    EnhancedVoiceProviderFactory,
    IEnhancedProviderFactory,
    ProviderCategory
)
from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.enhanced_connection_manager import (
    IConnectionManager,
    EnhancedConnectionManager
)
from app.services.voice_v2.core.exceptions import (
    VoiceServiceError,
    ProviderNotFoundError
)


class TestEnhancedProviderFactory:
    """Test suite for Enhanced Provider Factory"""
    
    @pytest.fixture
    async def mock_connection_manager(self):
        """Mock connection manager for testing"""
        mock_cm = AsyncMock(spec=IConnectionManager)
        mock_cm.initialize = AsyncMock()
        mock_cm.register_provider = AsyncMock()
        mock_cm.shutdown = AsyncMock()
        mock_cm.cleanup = AsyncMock()
        mock_cm.execute_request = AsyncMock()
        return mock_cm
    
    @pytest.fixture
    async def enhanced_factory(self, mock_connection_manager):
        """Enhanced factory instance for testing"""
        factory = EnhancedVoiceProviderFactory(connection_manager=mock_connection_manager)
        await factory.initialize()
        return factory
    
    @pytest.fixture
    def sample_stt_config(self):
        """Sample STT configuration"""
        return {
            "api_key": "test_key",
            "model": "whisper-1",
            "timeout": 30.0,
            "max_retries": 3
        }
    
    @pytest.fixture
    def sample_tts_config(self):
        """Sample TTS configuration"""
        return {
            "api_key": "test_key",
            "model": "tts-1",
            "voice": "alloy",
            "timeout": 30.0
        }

    class TestUnifiedProviderCreation:
        """Test unified provider creation method (Phase 3.5.3.2)"""
        
        async def test_create_stt_provider_success(self, enhanced_factory, sample_stt_config):
            """Test successful STT provider creation via unified method"""
            # Directly test real provider creation
            result = await enhanced_factory.create_provider("openai_stt", sample_stt_config)
            
            assert result is not None
            assert isinstance(result, BaseSTTProvider)
            assert result.provider_name == "openai_stt"
        
        async def test_create_stt_provider_auto_suffix(self, enhanced_factory, sample_stt_config):
            """Test provider creation with existing suffix"""
            # Test that provider name with suffix works
            result = await enhanced_factory.create_provider("openai_stt", sample_stt_config)
            
            assert result is not None
            assert isinstance(result, BaseSTTProvider)
            assert result.provider_name == "openai_stt"
        
        async def test_create_tts_provider_success(self, enhanced_factory, sample_tts_config):
            """Test successful TTS provider creation via unified method"""
            # Test real TTS provider creation
            result = await enhanced_factory.create_provider("openai_tts", sample_tts_config)
            
            assert result is not None
            assert isinstance(result, BaseTTSProvider)
            assert result.provider_name == "openai_tts"
        
        async def test_create_provider_not_found_error(self, enhanced_factory, sample_stt_config):
            """Test error when provider is not found"""
            with pytest.raises(ProviderNotFoundError):
                await enhanced_factory.create_provider("nonexistent_provider", sample_stt_config)
        
        async def test_create_provider_with_empty_config(self, enhanced_factory):
            """Test provider creation with empty configuration should raise exception"""
            from app.services.voice_v2.core.exceptions import VoiceServiceError
            
            with pytest.raises(VoiceServiceError) as exc_info:
                await enhanced_factory.create_provider("openai_stt", {})
            
            assert "Missing config" in str(exc_info.value)

    class TestProviderFiltering:
        """Test provider filtering methods"""
        
        def test_get_available_providers_stt(self, enhanced_factory):
            """Test getting available STT providers"""
            with patch.object(enhanced_factory, '_providers_registry') as mock_registry:
                mock_registry.values.return_value = [
                    Mock(category=ProviderCategory.STT, enabled=True, priority=10),
                    Mock(category=ProviderCategory.TTS, enabled=True, priority=20)
                ]
                
                result = enhanced_factory.get_available_providers(category=ProviderCategory.STT)
                
                assert len(result) == 1
                assert result[0].category == ProviderCategory.STT
        
        def test_get_available_providers_tts(self, enhanced_factory):
            """Test getting available TTS providers"""
            with patch.object(enhanced_factory, '_providers_registry') as mock_registry:
                mock_registry.values.return_value = [
                    Mock(category=ProviderCategory.STT, enabled=True, priority=10),
                    Mock(category=ProviderCategory.TTS, enabled=False, priority=20)
                ]
                
                result = enhanced_factory.get_available_providers(
                    category=ProviderCategory.TTS, 
                    enabled_only=False
                )
                
                assert len(result) == 1
                assert result[0].category == ProviderCategory.TTS

    class TestDefaultConfiguration:
        """Test default configuration generation"""
        
        def test_openai_stt_default_config(self, enhanced_factory):
            """Test OpenAI STT default configuration"""
            config = enhanced_factory._get_default_config_for_provider("openai_stt")
            
            assert config["model"] == "whisper-1"
            assert config["api_key"] == ""
            assert config["timeout"] == 30.0
            assert config["max_retries"] == 3
            assert "voice" not in config  # STT doesn't need voice
        
        def test_openai_tts_default_config(self, enhanced_factory):
            """Test OpenAI TTS default configuration"""
            config = enhanced_factory._get_default_config_for_provider("openai_tts")
            
            assert config["model"] == "tts-1"
            assert config["voice"] == "alloy"
            assert config["api_key"] == ""
            assert config["timeout"] == 30.0
        
        def test_google_stt_default_config(self, enhanced_factory):
            """Test Google STT default configuration"""
            config = enhanced_factory._get_default_config_for_provider("google_stt")
            
            assert config["language_code"] == "ru-RU"
            assert config["credentials_path"] == ""
            assert config["project_id"] == ""
            assert config["timeout"] == 30.0
        
        def test_yandex_tts_default_config(self, enhanced_factory):
            """Test Yandex TTS default configuration"""
            config = enhanced_factory._get_default_config_for_provider("yandex_tts")
            
            assert config["language"] == "ru"
            assert config["api_key"] == ""
            assert config["folder_id"] == ""
            assert config["timeout"] == 30.0

    class TestConnectionManagerIntegration:
        """Test connection manager integration (Phase 3.4.2.2)"""
        
        async def test_connection_manager_initialization(self, mock_connection_manager):
            """Test connection manager is initialized during factory startup"""
            factory = EnhancedVoiceProviderFactory(connection_manager=mock_connection_manager)
            await factory.initialize()
            
            mock_connection_manager.initialize.assert_called_once()
        
        async def test_default_connection_manager_creation(self):
            """Test default connection manager is created if none provided"""
            factory = EnhancedVoiceProviderFactory()
            
            assert factory._connection_manager is not None
            assert isinstance(factory._connection_manager, EnhancedConnectionManager)
        
        def test_get_connection_manager(self, enhanced_factory, mock_connection_manager):
            """Test connection manager access"""
            result = enhanced_factory.get_connection_manager()
            
            assert result == mock_connection_manager
        
        async def test_connection_manager_shutdown(self, enhanced_factory, mock_connection_manager):
            """Test connection manager shutdown during factory cleanup"""
            await enhanced_factory.shutdown()
            
            mock_connection_manager.cleanup.assert_called_once()

    class TestModularInterface:
        """Test that Enhanced Factory implements the new modular interface"""
        
        def test_implements_enhanced_interface(self):
            """Test that factory implements IEnhancedProviderFactory"""
            factory = EnhancedVoiceProviderFactory()
            
            assert isinstance(factory, IEnhancedProviderFactory)
        
        async def test_all_required_methods_present(self, enhanced_factory):
            """Test that all required methods are present in new interface"""
            # Test method presence
            assert hasattr(enhanced_factory, 'create_provider')
            assert hasattr(enhanced_factory, 'register_provider')
            assert hasattr(enhanced_factory, 'get_available_providers')
            assert hasattr(enhanced_factory, 'get_provider_info')
            assert hasattr(enhanced_factory, 'health_check')
            
            # Test method signatures
            assert asyncio.iscoroutinefunction(enhanced_factory.create_provider)
            assert callable(enhanced_factory.register_provider)
            assert callable(enhanced_factory.get_available_providers)
            assert callable(enhanced_factory.get_provider_info)
            assert asyncio.iscoroutinefunction(enhanced_factory.health_check)
