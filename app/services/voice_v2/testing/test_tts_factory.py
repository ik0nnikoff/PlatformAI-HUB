"""
Unit tests for TTS Provider Factory - Phase 3.2.5

Tests Phase 1.3 architectural compliance:
- Factory Pattern implementation validation
- SOLID principles verification
- Provider management и caching testing
- Error handling и configuration validation

Architecture Validation:
- Phase_1_3_1_architecture_review.md: Factory pattern compliance
- Phase_1_2_2_solid_principles.md: Single responsibility validation
- Phase_1_2_3_performance_optimization.md: Caching patterns testing
- Phase_1_1_4_architecture_patterns.md: Provider registry validation
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

# Import voice_v2 components
from app.services.voice_v2.core.exceptions import VoiceServiceError, ProviderNotAvailableError
from app.services.voice_v2.providers.tts.factory import TTSProviderFactory, tts_factory
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.tts.models import TTSCapabilities, TTSQuality
from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat


class TestTTSProviderFactory:
    """Test suite for TTS Provider Factory implementation."""
    
    @pytest.fixture
    def factory(self) -> TTSProviderFactory:
        """Create fresh factory instance for testing."""
        return TTSProviderFactory()
    
    @pytest.fixture
    def valid_providers_config(self) -> List[Dict[str, Any]]:
        """Valid multi-provider configuration."""
        return [
            {
                "provider": "openai",
                "config": {
                    "api_key": "test-openai-key",
                    "voice": "alloy",
                    "model": "tts-1"
                },
                "priority": 1,
                "enabled": True
            },
            {
                "provider": "google",
                "config": {
                    "credentials_path": "/path/to/google-creds.json",
                    "voice_name": "ru-RU-Wavenet-A"
                },
                "priority": 2,
                "enabled": True
            },
            {
                "provider": "yandex",
                "config": {
                    "api_key": "test-yandex-key",
                    "folder_id": "test-folder-id",
                    "voice_name": "jane"
                },
                "priority": 3,
                "enabled": True
            }
        ]
    
    @pytest.fixture
    def single_provider_config(self) -> Dict[str, Any]:
        """Single provider configuration for testing."""
        return {
            "provider": "openai",
            "config": {
                "api_key": "test-api-key",
                "voice": "alloy"
            },
            "priority": 1,
            "enabled": True
        }
    
    # Phase 1.3 Architecture Compliance Tests
    
    def test_factory_pattern_compliance(self, factory):
        """Test Factory Pattern compliance - provider registry и dynamic creation."""
        # Factory should have provider registry
        assert hasattr(factory, 'PROVIDER_REGISTRY')
        assert isinstance(factory.PROVIDER_REGISTRY, dict)
        
        # Should support all expected providers
        expected_providers = {"openai", "google", "yandex"}
        assert set(factory.PROVIDER_REGISTRY.keys()) == expected_providers
        
        # Registry should map to provider classes
        for provider_name, provider_class in factory.PROVIDER_REGISTRY.items():
            assert issubclass(provider_class, BaseTTSProvider)
    
    def test_solid_srp_single_responsibility(self, factory):
        """Test Single Responsibility Principle - only provider factory operations."""
        # Factory should only handle provider creation and management
        factory_methods = {
            'initialize', 'create_provider', 'get_provider', 
            'get_available_providers', 'cleanup', 'get_provider_capabilities'
        }
        
        # Should not contain TTS synthesis methods
        prohibited_methods = {
            'synthesize_speech', 'transcribe_audio', 'process_audio'
        }
        
        for method in factory_methods:
            assert hasattr(factory, method), f"Missing factory method: {method}"
        
        for method in prohibited_methods:
            assert not hasattr(factory, method), f"Should not have method: {method}"
    
    def test_solid_ocp_open_closed_principle(self, factory):
        """Test Open/Closed Principle - extensible without modification."""
        # Base registry should be extensible
        original_registry = factory.PROVIDER_REGISTRY.copy()
        
        # Should be able to extend registry without modifying factory
        class CustomTTSProvider(BaseTTSProvider):
            def get_required_config_fields(self): return []
            async def get_capabilities(self): return None
            async def initialize(self): pass
            async def cleanup(self): pass
            async def _synthesize_implementation(self, request): pass
            async def health_check(self): return True
        
        factory.PROVIDER_REGISTRY["custom"] = CustomTTSProvider
        
        assert "custom" in factory.PROVIDER_REGISTRY
        assert len(factory.PROVIDER_REGISTRY) == len(original_registry) + 1
        
        # Restore original registry to not affect other tests
        factory.PROVIDER_REGISTRY.clear()
        factory.PROVIDER_REGISTRY.update(original_registry)
    
    def test_solid_isp_interface_segregation(self, factory):
        """Test Interface Segregation - minimal factory interface."""
        # Factory should have clean, focused interface
        required_methods = {
            'initialize', 'create_provider', 'get_provider',
            'get_available_providers', 'cleanup'
        }
        
        # Should not mix unrelated functionality
        prohibited_methods = {
            'synthesize_speech', 'save_audio', 'convert_format',
            'analyze_audio', 'process_metrics'
        }
        
        for method in required_methods:
            assert callable(getattr(factory, method, None)), f"Missing method: {method}"
        
        for method in prohibited_methods:
            assert not hasattr(factory, method), f"Should not have method: {method}"
    
    def test_solid_dip_dependency_inversion(self, factory):
        """Test Dependency Inversion - depends on abstractions."""
        # Factory should work with BaseTTSProvider abstraction
        for provider_class in factory.PROVIDER_REGISTRY.values():
            assert issubclass(provider_class, BaseTTSProvider)
        
        # Should not depend on concrete implementations
        assert not hasattr(factory, '_openai_client')
        assert not hasattr(factory, '_google_client')
        assert not hasattr(factory, '_yandex_client')
    
    # Factory Initialization Tests
    
    @pytest.mark.asyncio
    async def test_factory_initialization_success(self, factory, valid_providers_config):
        """Test successful factory initialization with multiple providers."""
        await factory.initialize(valid_providers_config)
        
        assert factory._initialized is True
        assert len(factory._provider_configs) == 3
        
        # Cleanup
        await factory.cleanup()
    
    @pytest.mark.asyncio
    async def test_factory_initialization_empty_config(self, factory):
        """Test factory initialization with empty configuration."""
        await factory.initialize([])
        
        assert factory._initialized is True
        assert len(factory._provider_configs) == 0
        
        await factory.cleanup()
    
    @pytest.mark.asyncio
    async def test_factory_initialization_invalid_providers(self, factory):
        """Test factory initialization with invalid provider configurations."""
        invalid_config = [
            {"provider": "invalid_provider", "config": {}, "priority": 1, "enabled": True},
            {"config": {"api_key": "test"}, "priority": 1, "enabled": True},  # Missing provider
            {"provider": "openai", "config": {"api_key": "test"}, "priority": 1, "enabled": True}  # Valid
        ]
        
        await factory.initialize(invalid_config)
        
        # Should only store valid configuration
        assert factory._initialized is True
        assert len(factory._provider_configs) == 1
        
        await factory.cleanup()
    
    # Provider Creation Tests
    
    @pytest.mark.asyncio
    async def test_create_provider_success(self, factory):
        """Test successful provider creation."""
        config = {
            "api_key": "test-key",
            "voice": "alloy"
        }
        
        # Mock provider to avoid actual initialization
        with patch('app.services.voice_v2.providers.tts.factory.OpenAITTSProvider') as mock_provider_class:
            mock_provider = AsyncMock()
            mock_provider.provider_name = "openai"
            mock_provider.get_required_config_fields.return_value = []  # No missing fields
            mock_provider.initialize = AsyncMock()
            mock_provider_class.return_value = mock_provider
            
            provider = await factory.create_provider("openai", config, 1, True)
            
            assert provider is not None
            assert provider.provider_name == "openai"
            mock_provider.initialize.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_provider_unknown_provider(self, factory):
        """Test provider creation with unknown provider name."""
        config = {"api_key": "test-key"}
        
        provider = await factory.create_provider("unknown", config, 1, True)
        assert provider is None
    
    @pytest.mark.asyncio
    async def test_create_provider_initialization_failure(self, factory):
        """Test provider creation when initialization fails."""
        config = {"api_key": "test-key"}
        
        with patch('app.services.voice_v2.providers.tts.factory.OpenAITTSProvider') as mock_provider_class:
            mock_provider = AsyncMock()
            mock_provider.get_required_config_fields.return_value = []  # No missing fields
            mock_provider.initialize.side_effect = Exception("Initialization failed")
            mock_provider_class.return_value = mock_provider
            
            provider = await factory.create_provider("openai", config, 1, True)
            assert provider is None
    
    # Provider Caching Tests
    
    @pytest.mark.asyncio
    async def test_provider_caching_performance(self, factory, single_provider_config):
        """Test provider caching for performance optimization."""
        # Initialize factory
        await factory.initialize([single_provider_config])
        
        # Mock provider creation
        with patch.object(factory, 'create_provider') as mock_create:
            mock_provider = AsyncMock()
            mock_provider.provider_name = "openai"
            mock_provider.health_check.return_value = True
            mock_create.return_value = mock_provider
            
            # First call should create provider
            provider1 = await factory.get_provider(single_provider_config)
            assert provider1 is not None
            assert mock_create.call_count == 1
            
            # Second call should use cached provider
            provider2 = await factory.get_provider(single_provider_config)
            assert provider2 is provider1  # Same instance
            assert mock_create.call_count == 1  # No additional creation
        
        await factory.cleanup()
    
    @pytest.mark.asyncio
    async def test_cache_invalidation_unhealthy_provider(self, factory, single_provider_config):
        """Test cache invalidation when provider becomes unhealthy."""
        await factory.initialize([single_provider_config])
        
        with patch.object(factory, 'create_provider') as mock_create:
            # Create healthy provider
            healthy_provider = AsyncMock()
            healthy_provider.provider_name = "openai"
            healthy_provider.health_check.return_value = True
            
            # Create unhealthy provider 
            unhealthy_provider = AsyncMock()
            unhealthy_provider.provider_name = "openai"
            unhealthy_provider.health_check.return_value = False
            unhealthy_provider.cleanup = AsyncMock()
            
            mock_create.side_effect = [unhealthy_provider, healthy_provider]
            
            # First call gets unhealthy provider
            provider1 = await factory.get_provider(single_provider_config)
            assert provider1 is unhealthy_provider
            
            # Second call should recreate due to health check failure
            provider2 = await factory.get_provider(single_provider_config)
            assert provider2 is healthy_provider
            assert provider2 is not provider1
            
            # Cleanup should be called on unhealthy provider
            unhealthy_provider.cleanup.assert_called()
        
        await factory.cleanup()
    
    # Multi-Provider Management Tests
    
    @pytest.mark.asyncio
    async def test_get_available_providers_all_healthy(self, factory, valid_providers_config):
        """Test getting available providers when all are healthy."""
        await factory.initialize(valid_providers_config)
        
        # Mock all providers as healthy
        mock_providers = []
        for config in valid_providers_config:
            mock_provider = AsyncMock()
            mock_provider.provider_name = config["provider"]
            mock_provider.priority = config["priority"]
            mock_provider.health_check.return_value = True
            mock_providers.append(mock_provider)
        
        with patch.object(factory, 'get_provider') as mock_get:
            mock_get.side_effect = mock_providers
            
            providers = await factory.get_available_providers(valid_providers_config)
            
            assert len(providers) == 3
            # Should be ordered by priority
            assert providers[0].priority == 1  # OpenAI
            assert providers[1].priority == 2  # Google
            assert providers[2].priority == 3  # Yandex
        
        await factory.cleanup()
    
    @pytest.mark.asyncio
    async def test_get_available_providers_some_unhealthy(self, factory, valid_providers_config):
        """Test getting available providers when some are unhealthy."""
        await factory.initialize(valid_providers_config)
        
        # Mock mixed health status
        healthy_provider = AsyncMock()
        healthy_provider.provider_name = "openai"
        healthy_provider.priority = 1
        healthy_provider.health_check.return_value = True
        
        unhealthy_provider = AsyncMock()
        unhealthy_provider.provider_name = "google"
        unhealthy_provider.priority = 2
        unhealthy_provider.health_check.return_value = False
        
        with patch.object(factory, 'get_provider') as mock_get:
            mock_get.side_effect = [healthy_provider, unhealthy_provider, None]
            
            providers = await factory.get_available_providers(valid_providers_config)
            
            # Should only return healthy provider
            assert len(providers) == 1
            assert providers[0].provider_name == "openai"
        
        await factory.cleanup()
    
    # Health Monitoring Tests
    
    @pytest.mark.asyncio
    async def test_health_check_all_providers(self, factory):
        """Test health check functionality for all cached providers."""
        # Setup cached providers
        healthy_provider = AsyncMock()
        healthy_provider.provider_name = "openai"
        healthy_provider.health_check.return_value = True
        
        unhealthy_provider = AsyncMock()
        unhealthy_provider.provider_name = "google"
        unhealthy_provider.health_check.return_value = False
        
        factory._provider_cache = {
            "cache_key_1": healthy_provider,
            "cache_key_2": unhealthy_provider
        }
        
        health_status = await factory.health_check_all_providers()
        
        assert health_status["openai"] is True
        assert health_status["google"] is False
    
    @pytest.mark.asyncio
    async def test_health_check_with_exceptions(self, factory):
        """Test health check handling of provider exceptions."""
        # Setup provider that raises exception
        failing_provider = AsyncMock()
        failing_provider.provider_name = "yandex"
        failing_provider.health_check.side_effect = Exception("Health check failed")
        
        factory._provider_cache = {"cache_key": failing_provider}
        
        health_status = await factory.health_check_all_providers()
        
        # Should handle exception gracefully
        assert health_status["yandex"] is False
    
    # Configuration and Capabilities Tests
    
    @pytest.mark.asyncio
    async def test_get_provider_capabilities(self, factory):
        """Test provider capabilities retrieval."""
        # Mock capabilities
        mock_capabilities = {
            "provider_type": "openai",
            "supported_formats": ["mp3", "wav"],
            "supported_languages": ["en", "ru"],
            "max_text_length": 4096
        }
        
        with patch('app.services.voice_v2.providers.tts.factory.OpenAITTSProvider') as mock_provider_class:
            mock_provider = AsyncMock()
            mock_capabilities_obj = AsyncMock()
            mock_capabilities_obj.dict.return_value = mock_capabilities
            mock_provider.get_capabilities.return_value = mock_capabilities_obj
            mock_provider_class.return_value = mock_provider
            
            capabilities = await factory.get_provider_capabilities("openai")
            
            assert capabilities == mock_capabilities
    
    @pytest.mark.asyncio
    async def test_get_provider_capabilities_unknown_provider(self, factory):
        """Test capabilities retrieval for unknown provider."""
        capabilities = await factory.get_provider_capabilities("unknown")
        assert capabilities is None
    
    def test_get_supported_providers(self, factory):
        """Test getting list of supported provider names."""
        supported = factory.get_supported_providers()
        
        assert isinstance(supported, list)
        assert "openai" in supported
        assert "google" in supported
        assert "yandex" in supported
        assert len(supported) == 3
    
    # Error Handling Tests
    
    @pytest.mark.asyncio
    async def test_initialization_error_handling(self, factory):
        """Test factory initialization error handling."""
        # Mock configuration that causes error
        with patch.object(factory, '_generate_cache_key', side_effect=Exception("Cache key generation failed")):
            config = [{"provider": "openai", "config": {}, "priority": 1, "enabled": True}]
            
            with pytest.raises(VoiceServiceError, match="TTS Factory initialization failed"):
                await factory.initialize(config)
    
    @pytest.mark.asyncio
    async def test_provider_creation_error_recovery(self, factory):
        """Test error recovery during provider creation."""
        config = {"api_key": "test-key"}
        
        # Mock provider class to raise exception during instantiation
        with patch('app.services.voice_v2.providers.tts.factory.OpenAITTSProvider', side_effect=Exception("Provider creation failed")):
            provider = await factory.create_provider("openai", config, 1, True)
            
            # Should handle error gracefully and return None
            assert provider is None
    
    # Cleanup and Resource Management Tests
    
    @pytest.mark.asyncio
    async def test_factory_cleanup(self, factory, single_provider_config):
        """Test factory cleanup and resource management."""
        # Initialize and create cached provider
        await factory.initialize([single_provider_config])
        
        mock_provider = AsyncMock()
        mock_provider.cleanup = AsyncMock()
        factory._provider_cache["test_key"] = mock_provider
        
        # Cleanup should clean all providers
        await factory.cleanup()
        
        assert len(factory._provider_cache) == 0
        assert len(factory._provider_configs) == 0
        assert factory._initialized is False
        mock_provider.cleanup.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_cleanup_handles_provider_errors(self, factory):
        """Test cleanup handles provider cleanup errors gracefully."""
        # Setup provider that fails during cleanup
        failing_provider = AsyncMock()
        failing_provider.cleanup.side_effect = Exception("Cleanup failed")
        factory._provider_cache["test_key"] = failing_provider
        
        # Should handle cleanup errors gracefully
        await factory.cleanup()
        
        assert len(factory._provider_cache) == 0
    
    # Cache Key Generation Tests
    
    def test_cache_key_generation_consistency(self, factory):
        """Test cache key generation consistency."""
        config1 = {
            "provider": "openai",
            "priority": 1,
            "enabled": True,
            "config": {"api_key": "test", "voice": "alloy"}
        }
        
        config2 = {
            "provider": "openai", 
            "priority": 1,
            "enabled": True,
            "config": {"voice": "alloy", "api_key": "test"}  # Different order
        }
        
        key1 = factory._generate_cache_key("openai", config1)
        key2 = factory._generate_cache_key("openai", config2)
        
        # Should generate same key for equivalent configs
        assert key1 == key2
        assert isinstance(key1, str)
        assert len(key1) == 16  # MD5 hash truncated to 16 chars
    
    def test_cache_key_uniqueness(self, factory):
        """Test cache key uniqueness for different configurations."""
        config1 = {"provider": "openai", "config": {"api_key": "key1"}}
        config2 = {"provider": "openai", "config": {"api_key": "key2"}}
        config3 = {"provider": "google", "config": {"api_key": "key1"}}
        
        key1 = factory._generate_cache_key("openai", config1)
        key2 = factory._generate_cache_key("openai", config2)
        key3 = factory._generate_cache_key("google", config3)
        
        # All keys should be unique
        assert key1 != key2
        assert key1 != key3
        assert key2 != key3
    
    # Module-level Function Tests
    
    @pytest.mark.asyncio
    async def test_module_level_convenience_functions(self, single_provider_config):
        """Test module-level convenience functions."""
        # Test with mocked global factory
        with patch('app.services.voice_v2.providers.tts.factory.tts_factory') as mock_global_factory:
            mock_provider = AsyncMock()
            # Make sure the AsyncMock methods are properly awaitable
            mock_global_factory.get_provider = AsyncMock(return_value=mock_provider)
            mock_global_factory.get_available_providers = AsyncMock(return_value=[mock_provider])
            
            from app.services.voice_v2.providers.tts.factory import get_tts_provider, get_available_tts_providers
            
            # Test get_tts_provider
            provider = await get_tts_provider(single_provider_config)
            assert provider is mock_provider
            mock_global_factory.get_provider.assert_called_with(single_provider_config)
            
            # Test get_available_tts_providers
            providers = await get_available_tts_providers([single_provider_config])
            assert providers == [mock_provider]
            mock_global_factory.get_available_providers.assert_called_with([single_provider_config])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
