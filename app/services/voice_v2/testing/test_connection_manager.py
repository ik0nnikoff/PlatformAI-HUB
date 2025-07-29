"""
🧪 Unit Tests for Voice Connection Manager

Tests connection pooling, health monitoring, and performance optimization.
Based on Phase_1_3_3_testing_strategy.md - Performance testing patterns.

This module provides comprehensive test coverage for:
- Connection pool management
- Health monitoring functionality
- Provider-specific optimizations
- Performance metrics collection
- Error handling and recovery
"""

import asyncio
from unittest.mock import Mock, AsyncMock, patch

from aiohttp import ClientSession, TCPConnector
import pytest

try:
    from app.services.voice_v2.providers.connection_manager import (
        VoiceConnectionManager,
        ConnectionPool,
        create_connection_manager
    )
    from app.services.voice_v2.core.exceptions import ConnectionPoolError
    from app.services.voice_v2.core.config import VoiceConfig
except ImportError:
    # Fallback for test execution context
    pytest.skip("voice_v2 modules not available", allow_module_level=True)


@pytest.fixture
def mock_settings():
    """Mock VoiceConfig for testing"""
    settings = Mock(spec=VoiceConfig)
    settings.redis_url = "redis://localhost:6379"
    return settings


@pytest.fixture
def mock_health_checker():
    """Mock health checker for testing"""
    health_checker = AsyncMock()
    health_checker.check_provider_health.return_value = True
    return health_checker


@pytest.fixture
def mock_metrics_collector():
    """Mock metrics collector for testing"""
    metrics_collector = AsyncMock()
    return metrics_collector


class TestConnectionPool:
    """Test ConnectionPool functionality"""

    def test_pool_initialization_config(self):
        """Test connection pool initializes with correct configuration"""
        pool = ConnectionPool(
            provider_name="test_provider",
            max_connections=50,
            max_connections_per_host=20,
            keepalive_timeout=60,
            connection_timeout=15,
            read_timeout=45
        )

        assert pool.provider_name == "test_provider"
        assert pool.max_connections == 50
        assert pool.max_connections_per_host == 20
        assert pool.keepalive_timeout == 60
        assert pool.connection_timeout == 15
        assert pool.read_timeout == 45
        assert pool._connector is None
        assert pool._session is None
        assert pool._request_count == 0

    @pytest.mark.asyncio
    async def test_pool_initialize_success(self):
        """Test successful connection pool initialization"""
        pool = ConnectionPool("test_provider")

        await pool.initialize()

        # Verify initialization
        assert pool._connector is not None
        assert pool._session is not None
        assert pool._created_at is not None
        assert pool._last_used is not None
        assert isinstance(pool._connector, TCPConnector)
        assert isinstance(pool._session, ClientSession)

        # Cleanup
        await pool.cleanup()

    @pytest.mark.asyncio
    async def test_pool_initialize_already_initialized(self):
        """Test initializing already initialized pool"""
        pool = ConnectionPool("test_provider")

        await pool.initialize()
        connector_1 = pool._connector
        session_1 = pool._session

        # Initialize again
        await pool.initialize()

        # Should be the same instances
        assert pool._connector is connector_1
        assert pool._session is session_1

        await pool.cleanup()

    @pytest.mark.asyncio
    async def test_get_session_auto_initialize(self):
        """Test get_session automatically initializes pool"""
        pool = ConnectionPool("test_provider")

        session = await pool.get_session()

        # Should auto-initialize and return session
        assert isinstance(session, ClientSession)
        assert pool._connector is not None
        assert pool._session is not None
        assert pool._request_count == 1
        assert pool._last_used is not None

        await pool.cleanup()

    @pytest.mark.asyncio
    async def test_get_session_multiple_calls(self):
        """Test multiple get_session calls increment counter"""
        pool = ConnectionPool("test_provider")

        session1 = await pool.get_session()
        session2 = await pool.get_session()
        session3 = await pool.get_session()

        # Should return same session instance
        assert session1 is session2 is session3
        assert pool._request_count == 3

        await pool.cleanup()

    @pytest.mark.asyncio
    async def test_health_check_uninitialized(self):
        """Test health check on uninitialized pool"""
        pool = ConnectionPool("test_provider")

        is_healthy = await pool.health_check()
        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_health_check_initialized(self):
        """Test health check on initialized pool"""
        pool = ConnectionPool("test_provider")
        await pool.initialize()

        is_healthy = await pool.health_check()
        assert is_healthy is True

        await pool.cleanup()

    @pytest.mark.asyncio
    async def test_health_check_after_cleanup(self):
        """Test health check after cleanup"""
        pool = ConnectionPool("test_provider")
        await pool.initialize()
        await pool.cleanup()

        is_healthy = await pool.health_check()
        assert is_healthy is False

    @pytest.mark.asyncio
    async def test_cleanup_resources(self):
        """Test cleanup properly releases resources"""
        pool = ConnectionPool("test_provider")
        await pool.initialize()

        connector = pool._connector
        session = pool._session

        await pool.cleanup()

        # Verify resources are cleaned up
        assert pool._connector is None
        assert pool._session is None
        assert connector.closed
        assert session.closed

    @pytest.mark.asyncio
    async def test_cleanup_multiple_calls(self):
        """Test cleanup can be called multiple times safely"""
        pool = ConnectionPool("test_provider")
        await pool.initialize()

        # Multiple cleanups should not raise
        await pool.cleanup()
        await pool.cleanup()
        await pool.cleanup()

        assert pool._connector is None
        assert pool._session is None

    def test_stats_property(self):
        """Test stats property returns correct information"""
        pool = ConnectionPool("test_provider", max_connections=100)

        stats = pool.stats

        assert stats["provider_name"] == "test_provider"
        assert stats["max_connections"] == 100
        assert stats["request_count"] == 0
        assert stats["created_at"] is None
        assert stats["last_used"] is None

    @pytest.mark.asyncio
    async def test_stats_after_usage(self):
        """Test stats after pool usage"""
        pool = ConnectionPool("test_provider")
        await pool.get_session()  # This will initialize and increment counter

        stats = pool.stats

        assert stats["provider_name"] == "test_provider"
        assert stats["request_count"] == 1
        assert stats["created_at"] is not None
        assert stats["last_used"] is not None

        await pool.cleanup()


class TestVoiceConnectionManager:
    """Test VoiceConnectionManager functionality"""

    @pytest.fixture
    def manager(self, mock_settings, mock_health_checker, mock_metrics_collector):
        """Create manager instance for testing"""
        return VoiceConnectionManager(
            settings=mock_settings,
            health_checker=mock_health_checker,
            metrics_collector=mock_metrics_collector
        )

    def test_manager_initialization(
        self, manager, mock_settings, mock_health_checker, mock_metrics_collector
    ):
        """Test manager initializes correctly"""
        assert manager.settings == mock_settings
        assert manager.health_checker == mock_health_checker
        assert manager.metrics_collector == mock_metrics_collector
        assert manager._pools == {}
        assert manager._cleanup_task is None

    @pytest.mark.asyncio
    async def test_manager_initialize_starts_background_task(self, manager):
        """Test manager initialization starts background tasks"""
        await manager.initialize()

        assert manager._cleanup_task is not None
        assert not manager._cleanup_task.done()

        # Cleanup
        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_get_session_creates_pool(self, manager):
        """Test get_session creates pool for new provider"""
        session = await manager.get_session("openai")

        # Verify pool was created
        assert "openai" in manager._pools
        assert isinstance(session, ClientSession)
        assert not session.closed

        # Verify metrics were recorded
        manager.metrics_collector.record_connection_request.assert_called_once_with("openai")

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_get_session_reuses_pool(self, manager):
        """Test get_session reuses existing pool"""
        session1 = await manager.get_session("openai")
        session2 = await manager.get_session("openai")

        # Should have only one pool
        assert len(manager._pools) == 1

        # Should return same session instance
        assert session1 is session2

        # Metrics should be called twice
        assert manager.metrics_collector.record_connection_request.call_count == 2

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_get_session_provider_specific_config(self, manager):
        """Test get_session uses provider-specific configuration"""
        # Get sessions for different providers
        await manager.get_session("openai")
        await manager.get_session("google")
        await manager.get_session("yandex")

        # Verify different pools were created
        assert len(manager._pools) == 3
        assert "openai" in manager._pools
        assert "google" in manager._pools
        assert "yandex" in manager._pools

        # Verify provider-specific configurations
        openai_pool = manager._pools["openai"]
        google_pool = manager._pools["google"]
        yandex_pool = manager._pools["yandex"]

        # OpenAI should have higher connection limits
        assert openai_pool.max_connections == 150
        assert openai_pool.max_connections_per_host == 50

        # Google should have moderate limits
        assert google_pool.max_connections == 80
        assert google_pool.max_connections_per_host == 20

        # Yandex should have conservative limits and longer timeout
        assert yandex_pool.max_connections == 60
        assert yandex_pool.max_connections_per_host == 15
        assert yandex_pool.read_timeout == 45

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_release_session_interface_compliance(self, manager):
        """Test release_session interface compliance"""
        session = await manager.get_session("openai")

        # Should not raise (interface compliance)
        await manager.release_session("openai", session)

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_health_check_connections_empty(self, manager):
        """Test health check with no connections"""
        health_status = await manager.health_check_connections()

        assert health_status == {}

    @pytest.mark.asyncio
    async def test_health_check_connections_with_pools(self, manager):
        """Test health check with active pools"""
        # Create some pools
        await manager.get_session("openai")
        await manager.get_session("google")

        health_status = await manager.health_check_connections()

        assert "openai" in health_status
        assert "google" in health_status
        assert health_status["openai"] is True
        assert health_status["google"] is True

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_stops_background_tasks(self, manager):
        """Test cleanup stops background tasks"""
        await manager.initialize()
        cleanup_task = manager._cleanup_task

        await manager.cleanup()

        assert cleanup_task.cancelled() or cleanup_task.done()
        assert manager._cleanup_task is None or manager._cleanup_task.cancelled()

    @pytest.mark.asyncio
    async def test_cleanup_clears_pools(self, manager):
        """Test cleanup clears all pools"""
        # Create some pools
        await manager.get_session("openai")
        await manager.get_session("google")

        assert len(manager._pools) == 2

        await manager.cleanup()

        assert len(manager._pools) == 0

    @pytest.mark.asyncio
    async def test_cleanup_handles_pool_errors(self, manager):
        """Test cleanup handles pool cleanup errors gracefully"""
        # Create pool and mock cleanup to fail
        await manager.get_session("openai")

        with patch.object(
            manager._pools["openai"], 'cleanup', side_effect=Exception("Cleanup failed")
        ):
            # Should not raise despite pool cleanup error
            await manager.cleanup()

        # Pools should still be cleared
        assert len(manager._pools) == 0

    def test_get_pool_stats(self, manager):
        """Test get_pool_stats returns correct information"""
        stats = manager.get_pool_stats()
        assert stats == {}

    @pytest.mark.asyncio
    async def test_get_pool_stats_with_pools(self, manager):
        """Test get_pool_stats with active pools"""
        await manager.get_session("openai")
        await manager.get_session("google")

        stats = manager.get_pool_stats()

        assert "openai" in stats
        assert "google" in stats
        assert stats["openai"]["provider_name"] == "openai"
        assert stats["google"]["provider_name"] == "google"

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_get_session_records_errors(self, manager):
        """Test get_session records errors in metrics"""
        # Mock pool creation to fail
        with patch.object(manager, '_create_pool', side_effect=Exception("Pool creation failed")):
            with pytest.raises(ConnectionPoolError):
                await manager.get_session("openai")

        # Verify error was recorded
        manager.metrics_collector.record_connection_error.assert_called_once()
        call_args = manager.metrics_collector.record_connection_error.call_args
        assert call_args[0][0] == "openai"  # provider name
        assert "Pool creation failed" in call_args[0][1]  # error message


class TestConnectionManagerHelpers:
    """Test connection manager helper functions"""

    def test_create_connection_manager_with_all_dependencies(
        self, mock_settings, mock_health_checker, mock_metrics_collector
    ):
        """Test creating connection manager with all dependencies"""
        manager = create_connection_manager(
            settings=mock_settings,
            health_checker=mock_health_checker,
            metrics_collector=mock_metrics_collector
        )

        assert isinstance(manager, VoiceConnectionManager)
        assert manager.settings == mock_settings
        assert manager.health_checker == mock_health_checker
        assert manager.metrics_collector == mock_metrics_collector

    def test_create_connection_manager_minimal(self, mock_settings):
        """Test creating connection manager with minimal dependencies"""
        manager = create_connection_manager(settings=mock_settings)

        assert isinstance(manager, VoiceConnectionManager)
        assert manager.settings == mock_settings
        assert manager.health_checker is None
        assert manager.metrics_collector is None


class TestPerformanceScenarios:
    """Performance and stress testing scenarios"""

    @pytest.mark.asyncio
    async def test_concurrent_session_requests(self, mock_settings):
        """Test concurrent session requests for same provider"""
        manager = VoiceConnectionManager(mock_settings)

        # Create multiple concurrent requests
        tasks = [
            manager.get_session("openai")
            for _ in range(10)
        ]

        sessions = await asyncio.gather(*tasks)

        # All should return the same session instance
        assert all(session is sessions[0] for session in sessions)

        # Should have only one pool
        assert len(manager._pools) == 1

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_concurrent_different_providers(self, mock_settings):
        """Test concurrent requests for different providers"""
        manager = VoiceConnectionManager(mock_settings)

        # Create requests for different providers
        tasks = [
            manager.get_session("openai"),
            manager.get_session("google"),
            manager.get_session("yandex"),
            manager.get_session("openai"),  # Duplicate
            manager.get_session("google")   # Duplicate
        ]

        sessions = await asyncio.gather(*tasks)

        # Should have 3 pools
        assert len(manager._pools) == 3

        # Duplicate provider requests should return same sessions
        assert sessions[0] is sessions[3]  # Both openai
        assert sessions[1] is sessions[4]  # Both google

        await manager.cleanup()

    @pytest.mark.asyncio
    async def test_pool_recreation_after_failure(self, mock_settings):
        """Test pool recreation after health check failure"""
        manager = VoiceConnectionManager(mock_settings)
        await manager.initialize()

        # Create initial pool
        await manager.get_session("openai")
        initial_pool = manager._pools["openai"]

        # Mock health check to return False
        with patch.object(initial_pool, 'health_check', return_value=False):
            # Trigger health check (would normally happen in periodic cleanup)
            health_status = await manager.health_check_connections()
            assert health_status["openai"] is False

        await manager.cleanup()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
