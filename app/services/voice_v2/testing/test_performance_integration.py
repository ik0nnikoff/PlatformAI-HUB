"""
Performance Integration Tests - Phase 5.3.5 Implementation

Тесты для интеграции системы оптимизации производительности в voice_v2:
- Configuration-driven activation testing
- Component initialization testing
- Monitoring system integration testing
- Performance manager lifecycle testing
- Environment variable configuration testing

Test Coverage:
- Performance manager creation и initialization
- Environment configuration parsing
- Component activation based on settings
- Monitoring activation и deactivation
- Validation suite integration
- Error handling и graceful degradation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.voice_v2.core.performance_manager import (
    VoicePerformanceManager, PerformanceConfig, PerformanceLevel,
    create_performance_manager
)
from app.services.voice_v2.core.interfaces import ProviderType


class TestPerformanceConfig:
    """Test performance configuration from environment"""

    def test_default_config(self):
        """Test default configuration values"""
        config = PerformanceConfig()

        assert config.enabled is False
        assert config.level == PerformanceLevel.STANDARD
        assert config.monitoring_enabled is True
        assert config.monitoring_interval == 30
        assert config.stt_target_latency == 3.5
        assert config.tts_target_latency == 3.0
        assert config.decision_target_latency == 0.5

    @patch('app.services.voice_v2.core.performance_manager.settings')
    def test_config_from_env(self, mock_settings):
        """Test configuration from centralized settings"""
        mock_settings.VOICE_V2_PERFORMANCE_ENABLED = True
        mock_settings.VOICE_V2_MONITORING_ENABLED = True
        mock_settings.VOICE_V2_STT_OPTIMIZATION_ENABLED = True
        mock_settings.VOICE_V2_TTS_STREAMING_ENABLED = True
        mock_settings.VOICE_V2_TTS_COMPRESSION_ENABLED = True
        mock_settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED = True
        mock_settings.VOICE_V2_LOAD_TESTING_ENABLED = False

        config = PerformanceConfig.from_env()

        assert config.enabled is True
        assert config.monitoring_enabled is True
        assert config.stt_parallel_enabled is True
        assert config.tts_streaming_enabled is True
        assert config.load_test_enabled is False  # Default from centralized config

    @patch('app.services.voice_v2.core.performance_manager.settings')
    def test_disabled_config(self, mock_settings):
        """Test disabled performance configuration"""
        mock_settings.VOICE_V2_PERFORMANCE_ENABLED = False
        mock_settings.VOICE_V2_MONITORING_ENABLED = False
        mock_settings.VOICE_V2_STT_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_TTS_STREAMING_ENABLED = False
        mock_settings.VOICE_V2_TTS_COMPRESSION_ENABLED = False
        mock_settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_LOAD_TESTING_ENABLED = False

        config = PerformanceConfig.from_env()

        assert config.enabled is False

    @patch('app.services.voice_v2.core.performance_manager.settings')
    def test_monitoring_only_config(self, mock_settings):
        """Test monitoring-only configuration"""
        mock_settings.VOICE_V2_PERFORMANCE_ENABLED = True
        mock_settings.VOICE_V2_MONITORING_ENABLED = True
        mock_settings.VOICE_V2_STT_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_TTS_STREAMING_ENABLED = False
        mock_settings.VOICE_V2_TTS_COMPRESSION_ENABLED = False
        mock_settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_LOAD_TESTING_ENABLED = False

        config = PerformanceConfig.from_env()

        assert config.monitoring_enabled is True


class TestVoicePerformanceManager:
    """Test voice performance manager functionality"""

    @pytest.fixture
    def default_config(self):
        """Default test configuration"""
        return PerformanceConfig(
            enabled=True,
            level=PerformanceLevel.STANDARD,
            monitoring_enabled=True,
            stt_cache_enabled=True,
            tts_streaming_enabled=True,
            decision_cache_enabled=True
        )

    @pytest.fixture
    def disabled_config(self):
        """Disabled configuration for testing"""
        return PerformanceConfig(enabled=False)

    @pytest.fixture
    def monitoring_only_config(self):
        """Monitoring-only configuration"""
        return PerformanceConfig(
            enabled=True,
            level=PerformanceLevel.MONITORING_ONLY,
            monitoring_enabled=True
        )

    def test_manager_creation(self, default_config):
        """Test performance manager creation"""
        manager = VoicePerformanceManager(default_config)

        assert manager.config == default_config
        assert manager.is_enabled is True
        assert manager.is_monitoring_only is False
        assert manager.stt_optimizer is None
        assert manager.tts_optimizer is None
        assert manager.decision_optimizer is None

    def test_disabled_manager(self, disabled_config):
        """Test disabled performance manager"""
        manager = VoicePerformanceManager(disabled_config)

        assert manager.is_enabled is False
        assert manager.is_monitoring_only is False

    def test_monitoring_only_manager(self, monitoring_only_config):
        """Test monitoring-only performance manager"""
        manager = VoicePerformanceManager(monitoring_only_config)

        assert manager.is_enabled is True
        assert manager.is_monitoring_only is True

    @pytest.mark.asyncio
    async def test_disabled_initialization(self, disabled_config):
        """Test initialization with disabled config"""
        manager = VoicePerformanceManager(disabled_config)

        await manager.initialize([ProviderType.OPENAI, ProviderType.GOOGLE])

        assert manager.stt_optimizer is None
        assert manager.tts_optimizer is None
        assert manager.decision_optimizer is None
        assert manager.integration_monitor is None

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.STTPerformanceOptimizer')
    @patch('app.services.voice_v2.core.performance_manager.TTSPerformanceOptimizer')
    @patch('app.services.voice_v2.core.performance_manager.VoiceDecisionOptimizer')
    @patch('app.services.voice_v2.core.performance_manager.IntegrationPerformanceMonitor')
    @patch('app.services.voice_v2.core.performance_manager.PerformanceValidationSuite')
    async def test_full_initialization(self, mock_validation, mock_monitor, mock_decision,
                                       mock_tts, mock_stt, default_config):
        """Test full initialization with all components"""
        # Setup mocks
        mock_stt_instance = AsyncMock()
        mock_stt.return_value = mock_stt_instance

        mock_tts_instance = AsyncMock()
        mock_tts.return_value = mock_tts_instance

        mock_decision_instance = MagicMock()
        mock_decision.return_value = mock_decision_instance

        mock_monitor_instance = AsyncMock()
        mock_monitor_instance.set_optimizers = MagicMock()  # sync method
        mock_monitor.return_value = mock_monitor_instance

        mock_validation_instance = MagicMock()
        mock_validation.return_value = mock_validation_instance

        manager = VoicePerformanceManager(default_config)
        providers = [ProviderType.OPENAI, ProviderType.GOOGLE]

        await manager.initialize(providers)

        # Verify component initialization
        assert manager._initialized is True
        assert manager.stt_optimizer == mock_stt_instance
        assert manager.tts_optimizer == mock_tts_instance
        assert manager.decision_optimizer == mock_decision_instance
        assert manager.integration_monitor == mock_monitor_instance
        assert manager.validation_suite == mock_validation_instance

        # Verify initialization calls
        mock_stt_instance.initialize_connection_pools.assert_called_once_with(providers)
        mock_tts_instance.initialize_connection_pools.assert_called_once_with(providers)
        mock_monitor_instance.start_monitoring.assert_called_once()
        mock_validation_instance.configure_components.assert_called_once()

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.IntegrationPerformanceMonitor')
    @patch('app.services.voice_v2.core.performance_manager.PerformanceValidationSuite')
    async def test_monitoring_only_initialization(self, mock_validation, mock_monitor,
                                                  monitoring_only_config):
        """Test initialization with monitoring-only config"""
        mock_monitor_instance = AsyncMock()
        mock_monitor.return_value = mock_monitor_instance

        mock_validation_instance = MagicMock()
        mock_validation.return_value = mock_validation_instance

        manager = VoicePerformanceManager(monitoring_only_config)

        await manager.initialize([ProviderType.OPENAI])

        # Verify no optimizers created
        assert manager.stt_optimizer is None
        assert manager.tts_optimizer is None
        assert manager.decision_optimizer is None

        # Verify monitoring components created
        assert manager.integration_monitor == mock_monitor_instance
        assert manager.validation_suite == mock_validation_instance

    @pytest.mark.asyncio
    async def test_get_optimizers_when_disabled(self, disabled_config):
        """Test getting optimizers when system is disabled"""
        manager = VoicePerformanceManager(disabled_config)

        stt_optimizer = await manager.get_stt_optimizer()
        tts_optimizer = await manager.get_tts_optimizer()
        decision_optimizer = await manager.get_decision_optimizer()

        assert stt_optimizer is None
        assert tts_optimizer is None
        assert decision_optimizer is None

    @pytest.mark.asyncio
    async def test_performance_validation_disabled(self, disabled_config):
        """Test performance validation when system is disabled"""
        manager = VoicePerformanceManager(disabled_config)

        result = await manager.run_performance_validation()

        assert result == {}

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.PerformanceValidationSuite')
    async def test_performance_validation_load_test_disabled(self, mock_validation, default_config):
        """Test performance validation when load testing is disabled"""
        default_config.load_test_enabled = False

        mock_validation_instance = MagicMock()
        mock_validation.return_value = mock_validation_instance

        manager = VoicePerformanceManager(default_config)
        manager.validation_suite = mock_validation_instance

        result = await manager.run_performance_validation()

        assert result == {"status": "load_test_disabled"}
        mock_validation_instance.run_full_validation.assert_not_called()

    @pytest.mark.asyncio
    async def test_performance_metrics_disabled(self, disabled_config):
        """Test getting performance metrics when disabled"""
        manager = VoicePerformanceManager(disabled_config)

        metrics = await manager.get_performance_metrics()

        assert metrics == {"status": "disabled"}

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.STTPerformanceOptimizer')
    async def test_performance_metrics_with_components(self, mock_stt, default_config):
        """Test getting performance metrics with active components"""
        from unittest.mock import Mock

        mock_stt_instance = Mock()
        mock_stt_instance.get_performance_summary.return_value = {"test": "stt_metrics"}
        mock_stt.return_value = mock_stt_instance

        manager = VoicePerformanceManager(default_config)
        manager.stt_optimizer = mock_stt_instance
        manager._components_status = {"stt_optimizer": True}

        metrics = await manager.get_performance_metrics()

        assert "system_status" in metrics
        assert metrics["system_status"]["enabled"] is True
        assert metrics["system_status"]["level"] == "standard"
        assert "stt_optimizer" in metrics
        assert metrics["stt_optimizer"] == {"test": "stt_metrics"}

    @pytest.mark.asyncio
    async def test_status_reporting(self, default_config):
        """Test status reporting functionality"""
        manager = VoicePerformanceManager(default_config)
        manager._components_status = {"test_component": True}
        manager._monitoring_active = True
        manager._initialized = True

        status = await manager.get_status()

        assert status.enabled is True
        assert status.level == PerformanceLevel.STANDARD
        assert status.components_initialized == {"test_component": True}
        assert status.monitoring_active is True
        assert status.last_optimization is not None

    def test_connection_pool_sizing(self, default_config):
        """Test connection pool sizing based on performance level"""
        manager = VoicePerformanceManager(default_config)

        # Test standard level
        assert manager._get_connection_pool_size() == 100
        assert manager._get_host_connection_limit() == 30
        assert manager._get_cache_size() == 1000

        # Test aggressive level
        manager.config.level = PerformanceLevel.AGGRESSIVE
        assert manager._get_connection_pool_size() == 150
        assert manager._get_host_connection_limit() == 50
        assert manager._get_cache_size() == 2000

        # Test basic level
        manager.config.level = PerformanceLevel.BASIC
        assert manager._get_connection_pool_size() == 50
        assert manager._get_host_connection_limit() == 20
        assert manager._get_cache_size() == 500

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.STTPerformanceOptimizer')
    @patch('app.services.voice_v2.core.performance_manager.TTSPerformanceOptimizer')
    @patch('app.services.voice_v2.core.performance_manager.IntegrationPerformanceMonitor')
    async def test_shutdown(self, mock_monitor, mock_tts, mock_stt, default_config):
        """Test graceful shutdown"""
        # Setup mocks
        mock_stt_instance = AsyncMock()
        mock_stt.return_value = mock_stt_instance

        mock_tts_instance = AsyncMock()
        mock_tts.return_value = mock_tts_instance

        mock_monitor_instance = AsyncMock()
        mock_monitor.return_value = mock_monitor_instance

        manager = VoicePerformanceManager(default_config)
        manager.stt_optimizer = mock_stt_instance
        manager.tts_optimizer = mock_tts_instance
        manager.integration_monitor = mock_monitor_instance
        manager._monitoring_active = True
        manager._initialized = True
        manager._components_status = {"test": True}

        await manager.shutdown()

        # Verify shutdown calls
        mock_monitor_instance.stop_monitoring.assert_called_once()
        mock_stt_instance.cleanup_connection_pools.assert_called_once()
        mock_tts_instance.cleanup_connection_pools.assert_called_once()

        # Verify state reset
        assert manager._initialized is False
        assert manager._monitoring_active is False
        assert len(manager._components_status) == 0

    def test_repr(self, default_config):
        """Test string representation"""
        manager = VoicePerformanceManager(default_config)
        manager._monitoring_active = True

        repr_str = repr(manager)

        assert "VoicePerformanceManager" in repr_str
        assert "enabled=True" in repr_str
        assert "level=standard" in repr_str
        assert "monitoring=True" in repr_str


class TestPerformanceManagerFactory:
    """Test performance manager factory function"""

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.PerformanceConfig.from_env')
    @patch('app.services.voice_v2.core.performance_manager.VoicePerformanceManager')
    async def test_factory_creation(self, mock_manager_class, mock_config):
        """Test factory function creation"""
        mock_config.return_value = PerformanceConfig(enabled=True)
        mock_manager_instance = AsyncMock()
        mock_manager_class.return_value = mock_manager_instance

        providers = [ProviderType.OPENAI, ProviderType.GOOGLE]

        manager = await create_performance_manager(providers)

        # Verify creation and initialization
        mock_config.assert_called_once()
        mock_manager_class.assert_called_once()
        mock_manager_instance.initialize.assert_called_once_with(providers)
        assert manager == mock_manager_instance

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.PerformanceConfig.from_env')
    @patch('app.services.voice_v2.core.performance_manager.VoicePerformanceManager')
    async def test_factory_without_providers(self, mock_manager_class, mock_config):
        """Test factory function without providers"""
        mock_config.return_value = PerformanceConfig(enabled=False)
        mock_manager_instance = AsyncMock()
        mock_manager_class.return_value = mock_manager_instance

        manager = await create_performance_manager()

        # Verify creation without initialization
        mock_manager_class.assert_called_once()
        mock_manager_instance.initialize.assert_not_called()
        assert manager == mock_manager_instance


class TestEnvironmentIntegration:
    """Test environment variable integration"""

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.settings')
    async def test_env_integration(self, mock_settings):
        """Test full environment variable integration"""
        # Mock centralized settings
        mock_settings.VOICE_V2_PERFORMANCE_ENABLED = True
        mock_settings.VOICE_V2_MONITORING_ENABLED = True
        mock_settings.VOICE_V2_STT_OPTIMIZATION_ENABLED = True
        mock_settings.VOICE_V2_TTS_STREAMING_ENABLED = True
        mock_settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED = True
        mock_settings.VOICE_V2_LOAD_TESTING_ENABLED = False
        mock_settings.VOICE_V2_STT_CACHE_TTL = 3600
        mock_settings.VOICE_V2_STT_MAX_CONNECTIONS = 100
        mock_settings.VOICE_V2_STT_PARALLEL_REQUESTS = 5
        mock_settings.VOICE_V2_STT_CACHE_SIZE = 1000
        mock_settings.VOICE_V2_TTS_CACHE_SIZE = 500

        manager = await create_performance_manager()

        assert manager.config.enabled is True
        assert manager.config.monitoring_enabled is True
        assert manager.config.stt_parallel_enabled is True
        assert manager.config.tts_streaming_enabled is True

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.settings')
    async def test_disabled_env_integration(self, mock_settings):
        """Test disabled system via environment"""
        # Mock disabled settings
        mock_settings.VOICE_V2_PERFORMANCE_ENABLED = False
        mock_settings.VOICE_V2_MONITORING_ENABLED = False
        mock_settings.VOICE_V2_STT_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_TTS_STREAMING_ENABLED = False
        mock_settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_LOAD_TESTING_ENABLED = False

        manager = await create_performance_manager([ProviderType.OPENAI])

        assert manager.is_enabled is False
        assert manager.stt_optimizer is None
        assert manager.tts_optimizer is None

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.core.performance_manager.settings')
    async def test_monitoring_only_env_integration(self, mock_settings):
        """Test monitoring-only mode via environment"""
        # Mock monitoring-only settings
        mock_settings.VOICE_V2_PERFORMANCE_ENABLED = True
        mock_settings.VOICE_V2_MONITORING_ENABLED = True
        mock_settings.VOICE_V2_STT_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_TTS_STREAMING_ENABLED = False
        mock_settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED = False
        mock_settings.VOICE_V2_LOAD_TESTING_ENABLED = False

        # Create manager with monitoring-only config
        config = PerformanceConfig.from_env()
        config.level = PerformanceLevel.MONITORING_ONLY  # Manually set to monitoring only
        manager = VoicePerformanceManager(config)

        assert manager.is_enabled is True
        assert manager.is_monitoring_only is True


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
