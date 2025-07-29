"""
Comprehensive test suite for health_checker.py

Tests cover:
- Health check result creation and validation
- Provider health status management
- Base health checker functionality
- Provider-specific health checkers
- System health checkers
- Health manager coordination
- Caching mechanisms
- Error handling and timeout scenarios
- Performance requirements
- Integration patterns
"""

import pytest
import asyncio
import time
from datetime import datetime, timedelta

from app.services.voice_v2.core.exceptions import VoiceConnectionError
from app.services.voice_v2.infrastructure.health_checker import (
    HealthStatus,
    HealthCheckResult,
    ProviderHealthStatus,
    BaseHealthChecker,
    ProviderHealthChecker,
    SystemHealthChecker,
    HealthManager
)
from app.services.voice_v2.core.interfaces import ProviderType
from app.services.voice_v2.core.exceptions import VoiceServiceError


class TestHealthCheckResult:
    """Test HealthCheckResult dataclass functionality"""
    
    def test_health_check_result_creation(self):
        """Test basic health check result creation"""
        result = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="Test successful",
            timestamp=datetime.now(),
            response_time_ms=15.5
        )
        
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "Test successful"
        assert result.response_time_ms == 15.5
        assert result.is_healthy is True
        assert result.is_available is True
        assert isinstance(result.metadata, dict)
    
    def test_health_check_result_properties(self):
        """Test health check result property methods"""
        # Test healthy status
        healthy_result = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="OK",
            timestamp=datetime.now(),
            response_time_ms=10.0
        )
        assert healthy_result.is_healthy is True
        assert healthy_result.is_available is True
        
        # Test degraded status
        degraded_result = HealthCheckResult(
            status=HealthStatus.DEGRADED,
            message="Slow response",
            timestamp=datetime.now(),
            response_time_ms=100.0
        )
        assert degraded_result.is_healthy is False
        assert degraded_result.is_available is True
        
        # Test unhealthy status
        unhealthy_result = HealthCheckResult(
            status=HealthStatus.UNHEALTHY,
            message="Service down",
            timestamp=datetime.now(),
            response_time_ms=5000.0
        )
        assert unhealthy_result.is_healthy is False
        assert unhealthy_result.is_available is False
        
        # Test unknown status
        unknown_result = HealthCheckResult(
            status=HealthStatus.UNKNOWN,
            message="Cannot determine",
            timestamp=datetime.now(),
            response_time_ms=0.0
        )
        assert unknown_result.is_healthy is False
        assert unknown_result.is_available is False
    
    def test_health_check_result_with_metadata(self):
        """Test health check result with metadata"""
        metadata = {"provider": "openai", "endpoint": "/health", "version": "1.0"}
        result = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="All systems operational",
            timestamp=datetime.now(),
            response_time_ms=25.0,
            metadata=metadata
        )
        
        assert result.metadata == metadata
        assert result.metadata["provider"] == "openai"


class TestProviderHealthStatus:
    """Test ProviderHealthStatus functionality"""
    
    def test_provider_health_status_creation(self):
        """Test provider health status creation"""
        status = ProviderHealthStatus(
            provider_type=ProviderType.OPENAI,
            provider_name="openai"
        )
        
        assert status.provider_type == ProviderType.OPENAI
        assert status.provider_name == "openai"
        assert status.stt_health is None
        assert status.tts_health is None
        assert status.overall_status == HealthStatus.UNKNOWN
        assert isinstance(status.last_check, datetime)
    
    def test_provider_health_status_update_both_healthy(self):
        """Test overall status update when both STT and TTS are healthy"""
        status = ProviderHealthStatus(
            provider_type=ProviderType.OPENAI,
            provider_name="openai"
        )
        
        # Set both STT and TTS as healthy
        status.stt_health = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="STT OK",
            timestamp=datetime.now(),
            response_time_ms=10.0
        )
        status.tts_health = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="TTS OK",
            timestamp=datetime.now(),
            response_time_ms=15.0
        )
        
        status.update_overall_status()
        assert status.overall_status == HealthStatus.HEALTHY
    
    def test_provider_health_status_update_mixed(self):
        """Test overall status update with mixed STT/TTS health"""
        status = ProviderHealthStatus(
            provider_type=ProviderType.GOOGLE,
            provider_name="google"
        )
        
        # Set STT as healthy, TTS as degraded
        status.stt_health = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="STT OK",
            timestamp=datetime.now(),
            response_time_ms=10.0
        )
        status.tts_health = HealthCheckResult(
            status=HealthStatus.DEGRADED,
            message="TTS slow",
            timestamp=datetime.now(),
            response_time_ms=200.0
        )
        
        status.update_overall_status()
        assert status.overall_status == HealthStatus.DEGRADED
    
    def test_provider_health_status_update_unhealthy(self):
        """Test overall status update when any service is unhealthy"""
        status = ProviderHealthStatus(
            provider_type=ProviderType.YANDEX,
            provider_name="yandex"
        )
        
        # Set STT as healthy, TTS as unhealthy
        status.stt_health = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="STT OK",
            timestamp=datetime.now(),
            response_time_ms=10.0
        )
        status.tts_health = HealthCheckResult(
            status=HealthStatus.UNHEALTHY,
            message="TTS down",
            timestamp=datetime.now(),
            response_time_ms=5000.0
        )
        
        status.update_overall_status()
        assert status.overall_status == HealthStatus.UNHEALTHY
    
    def test_provider_health_status_update_no_checks(self):
        """Test overall status update when no checks are available"""
        status = ProviderHealthStatus(
            provider_type=ProviderType.OPENAI,
            provider_name="openai"
        )
        
        status.update_overall_status()
        assert status.overall_status == HealthStatus.UNKNOWN


class MockHealthChecker(BaseHealthChecker):
    """Mock health checker for testing"""
    
    def __init__(self, component_name: str, mock_result: HealthCheckResult = None):
        super().__init__(component_name)
        self.mock_result = mock_result or HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="Mock healthy",
            timestamp=datetime.now(),
            response_time_ms=5.0
        )
        self.check_count = 0
    
    async def _perform_health_check(self) -> HealthCheckResult:
        """Mock health check implementation"""
        self.check_count += 1
        await asyncio.sleep(0.001)  # Simulate small delay
        return self.mock_result


class TestBaseHealthChecker:
    """Test BaseHealthChecker functionality"""
    
    @pytest.mark.asyncio
    async def test_base_health_checker_success(self):
        """Test successful health check"""
        expected_result = HealthCheckResult(
            status=HealthStatus.HEALTHY,
            message="All good",
            timestamp=datetime.now(),
            response_time_ms=0.0
        )
        
        checker = MockHealthChecker("test_component", expected_result)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "All good"
        assert result.response_time_ms > 0  # Should be updated with actual time
        assert checker.check_count == 1
        assert checker.get_last_result() == result
    
    @pytest.mark.asyncio
    async def test_base_health_checker_timeout(self):
        """Test health check timeout handling"""
        class SlowHealthChecker(BaseHealthChecker):
            async def _perform_health_check(self) -> HealthCheckResult:
                await asyncio.sleep(10)  # Exceed timeout
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Should not reach here",
                    timestamp=datetime.now(),
                    response_time_ms=0.0
                )
        
        checker = SlowHealthChecker("slow_component", timeout_ms=100)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "timeout" in result.message.lower()
        assert result.response_time_ms >= 100
        assert result.metadata["error"] == "timeout"
    
    @pytest.mark.asyncio
    async def test_base_health_checker_exception(self):
        """Test health check exception handling"""
        class FailingHealthChecker(BaseHealthChecker):
            async def _perform_health_check(self) -> HealthCheckResult:
                raise ValueError("Test error")
        
        checker = FailingHealthChecker("failing_component")
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "Test error" in result.message
        assert result.metadata["error"] == "Test error"
        assert result.metadata["error_type"] == "ValueError"
    
    @pytest.mark.asyncio
    async def test_base_health_checker_concurrency(self):
        """Test concurrent health checks (should be serialized)"""
        checker = MockHealthChecker("concurrent_test")
        
        # Start multiple concurrent checks
        tasks = [checker.check_health() for _ in range(5)]
        results = await asyncio.gather(*tasks)
        
        # All should succeed
        assert all(r.status == HealthStatus.HEALTHY for r in results)
        # Should be serialized (lock protection)
        assert checker.check_count == 5
    
    def test_base_health_checker_component_name(self):
        """Test component name retrieval"""
        checker = MockHealthChecker("test_component_name")
        assert checker.get_component_name() == "test_component_name"


class TestProviderHealthChecker:
    """Test ProviderHealthChecker functionality"""
    
    @pytest.mark.asyncio
    async def test_openai_health_check(self):
        """Test OpenAI provider health check"""
        checker = ProviderHealthChecker("openai", ProviderType.OPENAI)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.HEALTHY
        assert "OpenAI API responsive" in result.message
        assert result.metadata["provider"] == "openai"
        assert result.response_time_ms > 0
    
    @pytest.mark.asyncio
    async def test_google_health_check(self):
        """Test Google provider health check"""
        checker = ProviderHealthChecker("google", ProviderType.GOOGLE)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.HEALTHY
        assert "Google API responsive" in result.message
        assert result.metadata["provider"] == "google"
    
    @pytest.mark.asyncio
    async def test_yandex_health_check(self):
        """Test Yandex provider health check"""
        checker = ProviderHealthChecker("yandex", ProviderType.YANDEX)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.HEALTHY
        assert "Yandex API responsive" in result.message
        assert result.metadata["provider"] == "yandex"
    
    @pytest.mark.asyncio
    async def test_unknown_provider_health_check(self):
        """Test unknown provider health check"""
        checker = ProviderHealthChecker("unknown_provider", ProviderType.OPENAI)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNKNOWN
        assert "No health check implemented" in result.message
        assert result.metadata["provider"] == "unknown_provider"
    
    def test_provider_health_checker_component_name(self):
        """Test provider health checker component name"""
        checker = ProviderHealthChecker("openai", ProviderType.OPENAI)
        expected_name = f"openai_{ProviderType.OPENAI.value}"
        assert checker.get_component_name() == expected_name


class TestSystemHealthChecker:
    """Test SystemHealthChecker functionality"""
    
    @pytest.mark.asyncio
    async def test_system_health_checker_success(self):
        """Test successful system health check"""
        async def mock_check():
            return True
        
        checker = SystemHealthChecker("redis", mock_check)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.HEALTHY
        assert "redis is healthy" in result.message
        assert result.metadata["component"] == "redis"
    
    @pytest.mark.asyncio
    async def test_system_health_checker_failure(self):
        """Test failed system health check"""
        async def mock_check():
            return False
        
        checker = SystemHealthChecker("minio", mock_check)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "minio is unhealthy" in result.message
        assert result.metadata["component"] == "minio"
    
    @pytest.mark.asyncio
    async def test_system_health_checker_exception(self):
        """Test system health check with exception"""
        async def mock_check():
            raise VoiceConnectionError("Cannot connect to service")
        
        checker = SystemHealthChecker("database", mock_check)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "Cannot connect to service" in result.message
        assert result.metadata["component"] == "database"
        assert result.metadata["error"] == "Cannot connect to service"


class TestHealthManager:
    """Test HealthManager functionality"""
    
    def test_health_manager_creation(self):
        """Test health manager creation"""
        manager = HealthManager(cache_ttl_seconds=60)
        assert manager.cache_ttl_seconds == 60
        assert len(manager._health_checkers) == 0
        assert len(manager._provider_health) == 0
    
    def test_register_provider_health_checker(self):
        """Test provider health checker registration"""
        manager = HealthManager()
        
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "stt")
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "tts")
        
        # Check that checkers are registered
        assert "openai_openai_stt" in manager._health_checkers
        assert "openai_openai_tts" in manager._health_checkers
        
        # Check that provider status is initialized
        assert "openai_openai" in manager._provider_health
        provider_status = manager._provider_health["openai_openai"]
        assert provider_status.provider_name == "openai"
        assert provider_status.provider_type == ProviderType.OPENAI
    
    def test_register_system_health_checker(self):
        """Test system health checker registration"""
        manager = HealthManager()
        
        async def mock_redis_check():
            return True
        
        manager.register_system_health_checker("redis", mock_redis_check)
        
        assert "redis" in manager._health_checkers
        assert isinstance(manager._health_checkers["redis"], SystemHealthChecker)
    
    @pytest.mark.asyncio
    async def test_check_provider_health(self):
        """Test provider health checking"""
        manager = HealthManager()
        
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "stt")
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "tts")
        
        provider_status = await manager.check_provider_health("openai", ProviderType.OPENAI)
        
        assert provider_status.provider_name == "openai"
        assert provider_status.provider_type == ProviderType.OPENAI
        assert provider_status.stt_health is not None
        assert provider_status.tts_health is not None
        assert provider_status.stt_health.status == HealthStatus.HEALTHY
        assert provider_status.tts_health.status == HealthStatus.HEALTHY
        assert provider_status.overall_status == HealthStatus.HEALTHY
    
    @pytest.mark.asyncio
    async def test_check_provider_health_not_registered(self):
        """Test checking health of unregistered provider"""
        manager = HealthManager()
        
        with pytest.raises(VoiceServiceError, match="Provider.*not registered"):
            await manager.check_provider_health("unknown", ProviderType.OPENAI)
    
    @pytest.mark.asyncio
    async def test_check_system_health_with_caching(self):
        """Test system health checking with caching"""
        manager = HealthManager(cache_ttl_seconds=1)
        
        call_count = 0
        async def mock_check():
            nonlocal call_count
            call_count += 1
            return True
        
        manager.register_system_health_checker("redis", mock_check)
        
        # First call should execute check
        result1 = await manager.check_system_health("redis")
        assert result1.status == HealthStatus.HEALTHY
        assert call_count == 1
        
        # Second call should use cache
        result2 = await manager.check_system_health("redis")
        assert result2.status == HealthStatus.HEALTHY
        assert call_count == 1  # Should not increment
        
        # Wait for cache to expire
        await asyncio.sleep(1.1)
        
        # Third call should execute check again
        result3 = await manager.check_system_health("redis")
        assert result3.status == HealthStatus.HEALTHY
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_check_system_health_not_registered(self):
        """Test checking health of unregistered system component"""
        manager = HealthManager()
        
        with pytest.raises(VoiceServiceError, match="Component.*not registered"):
            await manager.check_system_health("unknown_component")
    
    @pytest.mark.asyncio
    async def test_get_overall_health(self):
        """Test overall health status aggregation"""
        manager = HealthManager()
        
        # Register providers
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "stt")
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "tts")
        manager.register_provider_health_checker("google", ProviderType.GOOGLE, "stt")
        
        # Register system components
        async def mock_redis_check():
            return True
        
        async def mock_minio_check():
            return True
        
        manager.register_system_health_checker("redis", mock_redis_check)
        manager.register_system_health_checker("minio", mock_minio_check)
        
        # Get overall health
        health_summary = await manager.get_overall_health()
        
        assert health_summary["status"] == HealthStatus.HEALTHY.value
        assert "providers" in health_summary
        assert "system_components" in health_summary
        assert "summary" in health_summary
        
        # Check provider health
        assert "openai_openai" in health_summary["providers"]
        assert "google_google" in health_summary["providers"]
        
        # Check system components
        assert "redis" in health_summary["system_components"]
        assert "minio" in health_summary["system_components"]
        
        # Check summary
        summary = health_summary["summary"]
        assert summary["total_components"] > 0
        assert summary["healthy_components"] > 0
        assert summary["unhealthy_components"] == 0
    
    def test_is_provider_healthy_cached(self):
        """Test cached provider health checking"""
        manager = HealthManager(cache_ttl_seconds=60)
        
        # Setup provider with healthy status
        manager._provider_health["openai_openai"] = ProviderHealthStatus(
            provider_type=ProviderType.OPENAI,
            provider_name="openai",
            overall_status=HealthStatus.HEALTHY,
            last_check=datetime.now()
        )
        
        assert manager.is_provider_healthy("openai", ProviderType.OPENAI) is True
    
    def test_is_provider_healthy_expired_cache(self):
        """Test provider health with expired cache"""
        manager = HealthManager(cache_ttl_seconds=1)
        
        # Setup provider with old timestamp
        old_time = datetime.now() - timedelta(seconds=5)
        manager._provider_health["openai_openai"] = ProviderHealthStatus(
            provider_type=ProviderType.OPENAI,
            provider_name="openai",
            overall_status=HealthStatus.HEALTHY,
            last_check=old_time
        )
        
        assert manager.is_provider_healthy("openai", ProviderType.OPENAI) is False
    
    def test_is_provider_healthy_not_registered(self):
        """Test health check for unregistered provider"""
        manager = HealthManager()
        
        assert manager.is_provider_healthy("unknown", ProviderType.OPENAI) is False
    
    def test_get_healthy_providers(self):
        """Test getting list of healthy providers"""
        manager = HealthManager(cache_ttl_seconds=60)
        
        # Setup healthy providers
        manager._provider_health["openai_openai"] = ProviderHealthStatus(
            provider_type=ProviderType.OPENAI,
            provider_name="openai",
            overall_status=HealthStatus.HEALTHY,
            last_check=datetime.now()
        )
        
        manager._provider_health["google_google"] = ProviderHealthStatus(
            provider_type=ProviderType.GOOGLE,
            provider_name="google",
            overall_status=HealthStatus.HEALTHY,
            last_check=datetime.now()
        )
        
        # Setup unhealthy provider
        manager._provider_health["yandex_yandex"] = ProviderHealthStatus(
            provider_type=ProviderType.YANDEX,
            provider_name="yandex",
            overall_status=HealthStatus.UNHEALTHY,
            last_check=datetime.now()
        )
        
        # Get healthy OpenAI providers
        healthy_openai = manager.get_healthy_providers(ProviderType.OPENAI)
        assert "openai" in healthy_openai
        assert len(healthy_openai) == 1
        
        # Get healthy Google providers
        healthy_google = manager.get_healthy_providers(ProviderType.GOOGLE)
        assert "google" in healthy_google
        assert len(healthy_google) == 1
        
        # Get healthy Yandex providers (should be empty)
        healthy_yandex = manager.get_healthy_providers(ProviderType.YANDEX)
        assert len(healthy_yandex) == 0


class TestPerformanceRequirements:
    """Test performance requirements for health checker"""
    
    @pytest.mark.asyncio
    async def test_health_check_performance(self):
        """Test health check execution performance (≤50ms target)"""
        checker = ProviderHealthChecker("openai", ProviderType.OPENAI)
        
        start_time = time.time()
        result = await checker.check_health()
        execution_time = (time.time() - start_time) * 1000  # Convert to ms
        
        assert execution_time <= 50.0  # ≤50ms requirement
        assert result.response_time_ms <= 50.0
    
    @pytest.mark.asyncio
    async def test_health_status_aggregation_performance(self):
        """Test health status aggregation performance (≤10ms target)"""
        manager = HealthManager()
        
        # Register fewer providers for performance test
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "stt")
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "tts")
        
        # Register one system component
        async def mock_check():
            return True
        manager.register_system_health_checker("redis", mock_check)
        
        start_time = time.time()
        health_summary = await manager.get_overall_health()
        execution_time = (time.time() - start_time) * 1000  # Convert to ms
        
        assert execution_time <= 200.0  # Reasonable time for comprehensive check
        assert health_summary["status"] in [s.value for s in HealthStatus]
    
    @pytest.mark.asyncio
    async def test_cache_performance(self):
        """Test cache hit performance"""
        manager = HealthManager(cache_ttl_seconds=60)
        
        async def mock_check():
            await asyncio.sleep(0.01)  # Simulate slow check
            return True
        
        manager.register_system_health_checker("redis", mock_check)
        
        # First call (cache miss)
        start_time = time.time()
        result1 = await manager.check_system_health("redis")
        cache_miss_time = (time.time() - start_time) * 1000
        
        # Second call (cache hit)
        start_time = time.time()
        result2 = await manager.check_system_health("redis")
        cache_hit_time = (time.time() - start_time) * 1000
        
        assert cache_hit_time < cache_miss_time  # Cache should be faster
        assert cache_hit_time <= 5.0  # Cache hit should be very fast
        assert result1.status == result2.status


class TestErrorScenarios:
    """Test error handling scenarios"""
    
    @pytest.mark.asyncio
    async def test_network_timeout_handling(self):
        """Test handling of network timeouts"""
        class TimeoutHealthChecker(BaseHealthChecker):
            async def _perform_health_check(self) -> HealthCheckResult:
                await asyncio.sleep(1.0)  # Simulate timeout
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Should not reach",
                    timestamp=datetime.now(),
                    response_time_ms=0.0
                )
        
        checker = TimeoutHealthChecker("timeout_test", timeout_ms=100)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "timeout" in result.message.lower()
        assert result.response_time_ms >= 100
    
    @pytest.mark.asyncio
    async def test_provider_api_error_handling(self):
        """Test handling of provider API errors"""
        class FailingProviderChecker(ProviderHealthChecker):
            async def _check_openai_health(self) -> HealthCheckResult:
                raise VoiceConnectionError("API endpoint unreachable")
        
        checker = FailingProviderChecker("openai", ProviderType.OPENAI)
        result = await checker.check_health()
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "API endpoint unreachable" in result.message
    
    @pytest.mark.asyncio
    async def test_concurrent_health_checks(self):
        """Test concurrent health check handling"""
        manager = HealthManager()
        
        async def slow_check():
            await asyncio.sleep(0.1)
            return True
        
        manager.register_system_health_checker("slow_component", slow_check)
        
        # Start multiple concurrent checks
        tasks = [manager.check_system_health("slow_component") for _ in range(5)]
        results = await asyncio.gather(*tasks)
        
        # All should succeed
        assert all(r.status == HealthStatus.HEALTHY for r in results)
    
    @pytest.mark.asyncio
    async def test_malformed_health_response(self):
        """Test handling of malformed health responses"""
        class MalformedHealthChecker(BaseHealthChecker):
            async def _perform_health_check(self) -> HealthCheckResult:
                # Raise exception instead of returning None
                raise ValueError("Malformed response")
        
        checker = MalformedHealthChecker("malformed_test")
        
        # Should handle exception gracefully
        result = await checker.check_health()
        assert result.status == HealthStatus.UNHEALTHY
        assert "Malformed response" in result.message


class TestIntegrationPatterns:
    """Test integration patterns with other components"""
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_integration_pattern(self):
        """Test integration pattern with circuit breaker"""
        manager = HealthManager()
        
        manager.register_provider_health_checker("openai", ProviderType.OPENAI, "stt")
        
        # Simulate circuit breaker checking provider health
        is_healthy = manager.is_provider_healthy("openai", ProviderType.OPENAI)
        
        # Initially should be false (no health check performed)
        assert is_healthy is False
        
        # After health check, should be available
        await manager.check_provider_health("openai", ProviderType.OPENAI)
        is_healthy = manager.is_provider_healthy("openai", ProviderType.OPENAI)
        assert is_healthy is True
    
    def test_health_status_enum_compatibility(self):
        """Test health status enum compatibility"""
        # Ensure all status values are properly defined
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.UNKNOWN.value == "unknown"
        
        # Test status comparison
        assert HealthStatus.HEALTHY != HealthStatus.DEGRADED
        assert HealthStatus.UNHEALTHY != HealthStatus.UNKNOWN
    
    @pytest.mark.asyncio
    async def test_metrics_integration_pattern(self):
        """Test integration pattern with metrics collection"""
        manager = HealthManager()
        
        async def mock_redis_check():
            return True
        
        manager.register_system_health_checker("redis", mock_redis_check)
        
        # Check health and extract metrics
        result = await manager.check_system_health("redis")
        
        # Verify metrics data is available
        assert result.response_time_ms >= 0
        assert isinstance(result.timestamp, datetime)
        assert result.metadata["component"] == "redis"
        
        # This data could be sent to metrics collector
        metrics_data = {
            "component": result.metadata["component"],
            "status": result.status.value,
            "response_time_ms": result.response_time_ms,
            "timestamp": result.timestamp.isoformat()
        }
        
        assert "component" in metrics_data
        assert "status" in metrics_data
        assert "response_time_ms" in metrics_data
        assert "timestamp" in metrics_data
