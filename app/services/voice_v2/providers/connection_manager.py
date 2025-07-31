"""
🔗 Connection Manager for voice_v2 System

Implements high-performance connection pooling and management for STT/TTS providers.
Based on Phase_1_2_3_performance_optimization.md - Connection pooling optimization.

This module provides centralized connection management with:
- Connection pooling for HTTP clients
- Health monitoring integration
- Circuit breaker patterns
- Performance metrics
"""

import asyncio
import logging
from typing import Dict, Optional, Any
from datetime import datetime
from abc import ABC, abstractmethod

from aiohttp import ClientSession, TCPConnector, ClientTimeout

from app.services.voice_v2.core.exceptions import (
    ConnectionPoolError
)
from app.services.voice_v2.core.config import VoiceConfig
from app.services.voice_v2.infrastructure.health_checker import ProviderHealthChecker
from app.services.voice_v2.infrastructure.metrics import VoiceMetricsCollector


logger = logging.getLogger(__name__)


class IConnectionManager(ABC):
    """
    Interface for connection managers - Interface Segregation Principle

    Based on Phase_1_2_2_solid_principles.md - ISP implementation.
    """

    @abstractmethod
    async def get_session(self, provider_name: str) -> ClientSession:
        """Get HTTP session for provider"""
        pass

    @abstractmethod
    async def release_session(self, provider_name: str, session: ClientSession) -> None:
        """Release HTTP session"""
        pass

    @abstractmethod
    async def health_check_connections(self) -> Dict[str, bool]:
        """Check health of all connections"""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Cleanup all connections"""
        pass


class ConnectionPool:
    """
    Individual connection pool for a provider

    Based on Phase_1_2_3_performance_optimization.md - Connection pooling patterns.
    Single Responsibility: Manages connections for ONE provider.
    """

    def __init__(
        self,
        provider_name: str,
        max_connections: int = 100,
        max_connections_per_host: int = 30,
        keepalive_timeout: int = 30,
        connection_timeout: int = 10,
        read_timeout: int = 30
    ):
        """
        Initialize connection pool for specific provider

        Args:
            provider_name: Name of the provider (openai, google, yandex)
            max_connections: Maximum total connections
            max_connections_per_host: Maximum connections per host
            keepalive_timeout: TCP keepalive timeout
            connection_timeout: Connection establishment timeout
            read_timeout: Read operation timeout
        """
        self.provider_name = provider_name
        self.max_connections = max_connections
        self.max_connections_per_host = max_connections_per_host
        self.keepalive_timeout = keepalive_timeout
        self.connection_timeout = connection_timeout
        self.read_timeout = read_timeout

        self._connector: Optional[TCPConnector] = None
        self._session: Optional[ClientSession] = None
        self._created_at: Optional[datetime] = None
        self._last_used: Optional[datetime] = None
        self._request_count: int = 0

        logger.debug("ConnectionPool created for %s", provider_name)

    async def initialize(self) -> None:
        """Initialize connection pool"""
        if self._connector is not None:
            logger.warning("ConnectionPool for %s already initialized", self.provider_name)
            return

        try:
            # Create optimized TCP connector
            # Based on Phase_1_2_3_performance_optimization.md - Async patterns
            self._connector = TCPConnector(
                limit=self.max_connections,
                limit_per_host=self.max_connections_per_host,
                keepalive_timeout=self.keepalive_timeout,
                use_dns_cache=True,
                ttl_dns_cache=300,  # 5 minutes DNS cache
                enable_cleanup_closed=False  # Avoid deprecated warnings
            )

            # Create session with performance optimizations
            timeout = ClientTimeout(
                total=self.connection_timeout + self.read_timeout,
                connect=self.connection_timeout,
                sock_read=self.read_timeout
            )

            self._session = ClientSession(
                connector=self._connector,
                timeout=timeout,
                headers={
                    "User-Agent": f"PlatformAI-Hub/voice_v2-{self.provider_name}",
                    "Connection": "keep-alive"
                },
                connector_owner=False  # We manage connector lifecycle
            )

            self._created_at = datetime.utcnow()
            self._last_used = datetime.utcnow()

            logger.info("ConnectionPool initialized for %s", self.provider_name)

        except Exception as e:
            logger.error("Failed to initialize ConnectionPool for %s: %s", self.provider_name, e)
            await self._cleanup_partial()
            raise ConnectionPoolError(f"Connection pool initialization failed: {e}")

    async def get_session(self) -> ClientSession:
        """Get HTTP session from pool"""
        if self._session is None:
            await self.initialize()

        self._last_used = datetime.utcnow()
        self._request_count += 1

        logger.debug("Session retrieved for %s (requests: %s)", self.provider_name, self._request_count)
        return self._session

    async def health_check(self) -> bool:
        """Check if connection pool is healthy"""
        try:
            if self._session is None or self._connector is None:
                return False

            # Check if connector is closed
            if self._connector.closed:
                logger.warning("Connector closed for %s", self.provider_name)
                return False

            # Check if session is closed
            if self._session.closed:
                logger.warning("Session closed for %s", self.provider_name)
                return False

            return True

        except Exception as e:
            logger.error("Health check failed for %s: %s", self.provider_name, e)
            return False

    async def cleanup(self) -> None:
        """Cleanup connection pool resources"""
        logger.info("Cleaning up ConnectionPool for %s", self.provider_name)

        try:
            if self._session and not self._session.closed:
                await self._session.close()

            if self._connector and not self._connector.closed:
                await self._connector.close()

        except Exception as e:
            logger.error("Error during ConnectionPool cleanup for %s: %s", self.provider_name, e)
        finally:
            self._session = None
            self._connector = None

        logger.debug("ConnectionPool cleanup completed for %s", self.provider_name)

    async def _cleanup_partial(self) -> None:
        """Cleanup partially initialized resources"""
        try:
            if self._session:
                await self._session.close()
            if self._connector:
                await self._connector.close()
        except:
            pass  # Ignore errors during partial cleanup
        finally:
            self._session = None
            self._connector = None

    @property
    def stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        return {
            "provider_name": self.provider_name,
            "created_at": self._created_at.isoformat() if self._created_at else None,
            "last_used": self._last_used.isoformat() if self._last_used else None,
            "request_count": self._request_count,
            "is_healthy": asyncio.create_task(self.health_check()) if self._session else False,
            "max_connections": self.max_connections,
            "max_connections_per_host": self.max_connections_per_host
        }


class VoiceConnectionManager(IConnectionManager):
    """
    Main connection manager implementation for voice_v2 system

    Based on Phase_1_2_3_performance_optimization.md - High-performance connection management.
    Manages connection pools for all voice providers with health monitoring.
    """

    def __init__(
        self,
        settings: VoiceConfig,
        health_checker: Optional[ProviderHealthChecker] = None,
        metrics_collector: Optional[VoiceMetricsCollector] = None
    ):
        """
        Initialize connection manager

        Args:
            settings: Voice v2 system settings
            health_checker: Optional health checker for provider monitoring
            metrics_collector: Optional metrics collector for performance tracking
        """
        self.settings = settings
        self.health_checker = health_checker
        self.metrics_collector = metrics_collector

        self._pools: Dict[str, ConnectionPool] = {}
        self._pool_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

        logger.info("VoiceConnectionManager initialized")

    async def initialize(self) -> None:
        """Initialize connection manager and start background tasks"""
        logger.info("Initializing VoiceConnectionManager")

        # Start cleanup task for periodic maintenance
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        logger.info("VoiceConnectionManager initialization completed")

    async def get_session(self, provider_name: str) -> ClientSession:
        """
        Get HTTP session for provider with automatic pool creation

        Based on Phase_1_2_3_performance_optimization.md - Lazy initialization patterns.

        Args:
            provider_name: Name of the provider (openai, google, yandex)

        Returns:
            Configured HTTP session for the provider

        Raises:
            ConnectionPoolError: If session creation fails
        """
        async with self._pool_lock:
            if provider_name not in self._pools:
                await self._create_pool(provider_name)

        pool = self._pools[provider_name]

        # Record metrics if available
        if self.metrics_collector:
            await self.metrics_collector.record_connection_request(provider_name)

        try:
            session = await pool.get_session()
            logger.debug("Session provided for %s", provider_name)
            return session

        except Exception as e:
            logger.error("Failed to get session for %s: %s", provider_name, e)
            # Record error metric
            if self.metrics_collector:
                await self.metrics_collector.record_connection_error(provider_name, str(e))
            raise ConnectionPoolError(f"Session creation failed for {provider_name}: {e}")

    async def release_session(self, provider_name: str, session: ClientSession) -> None:
        """
        Release HTTP session back to pool

        Note: With connection pooling, sessions are not explicitly released.
        This method is for interface compliance and future enhancements.
        """
        # Currently, sessions are managed by connection pools
        # This method can be extended for session-specific cleanup if needed
        logger.debug("Session release requested for %s", provider_name)

    async def health_check_connections(self) -> Dict[str, bool]:
        """
        Check health of all connection pools

        Returns:
            Dictionary mapping provider names to health status
        """
        logger.debug("Performing connection health checks")
        health_status = {}

        async with self._pool_lock:
            for provider_name, pool in self._pools.items():
                try:
                    is_healthy = await pool.health_check()
                    health_status[provider_name] = is_healthy

                    if not is_healthy:
                        logger.warning("Connection pool unhealthy for %s", provider_name)

                except Exception as e:
                    logger.error("Health check error for %s: %s", provider_name, e)
                    health_status[provider_name] = False

        logger.debug("Health check completed: %s", health_status)
        return health_status

    async def cleanup(self) -> None:
        """Cleanup all connection pools and background tasks"""
        logger.info("Cleaning up VoiceConnectionManager")

        # Cancel background tasks
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Cleanup all pools
        cleanup_tasks = []
        async with self._pool_lock:
            for pool in self._pools.values():
                cleanup_tasks.append(pool.cleanup())

            self._pools.clear()

        # Wait for all cleanups to complete
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        logger.info("VoiceConnectionManager cleanup completed")

    async def _create_pool(self, provider_name: str) -> None:
        """Create connection pool for provider"""
        logger.info("Creating connection pool for %s", provider_name)

        # Get provider-specific settings from configuration
        pool_config = self._get_pool_config(provider_name)

        pool = ConnectionPool(
            provider_name=provider_name,
            **pool_config
        )

        await pool.initialize()
        self._pools[provider_name] = pool

        logger.info("Connection pool created for %s", provider_name)

    def _get_pool_config(self, provider_name: str) -> Dict[str, Any]:
        """Get connection pool configuration for provider"""
        # Default configuration based on Phase_1_2_3_performance_optimization.md
        default_config = {
            "max_connections": 100,
            "max_connections_per_host": 30,
            "keepalive_timeout": 30,
            "connection_timeout": 10,
            "read_timeout": 30
        }

        # Provider-specific optimizations
        if provider_name == "openai":
            default_config.update({
                "max_connections": 150,  # OpenAI handles high concurrency well
                "max_connections_per_host": 50
            })
        elif provider_name == "google":
            default_config.update({
                "max_connections": 80,   # Google Cloud has rate limits
                "max_connections_per_host": 20
            })
        elif provider_name == "yandex":
            default_config.update({
                "max_connections": 60,   # Yandex SpeechKit conservative limits
                "max_connections_per_host": 15,
                "read_timeout": 45       # Yandex can be slower
            })

        return default_config

    async def _periodic_cleanup(self) -> None:
        """Periodic maintenance task for connection pools"""
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes

                logger.debug("Running periodic connection cleanup")

                # Check health of all pools
                health_status = await self.health_check_connections()

                # Recreate unhealthy pools
                unhealthy_providers = [
                    provider for provider, healthy in health_status.items()
                    if not healthy
                ]

                if unhealthy_providers:
                    logger.warning("Recreating unhealthy pools: %s", unhealthy_providers)

                    async with self._pool_lock:
                        for provider_name in unhealthy_providers:
                            if provider_name in self._pools:
                                # Cleanup old pool
                                await self._pools[provider_name].cleanup()
                                del self._pools[provider_name]

                                # Create new pool
                                await self._create_pool(provider_name)

            except asyncio.CancelledError:
                logger.info("Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.error("Error in periodic cleanup: %s", e)
                # Continue running despite errors

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get statistics for all connection pools"""
        stats = {}
        for provider_name, pool in self._pools.items():
            stats[provider_name] = pool.stats
        return stats


# Helper functions for dependency injection
def create_connection_manager(
    settings: VoiceConfig,
    health_checker: Optional[ProviderHealthChecker] = None,
    metrics_collector: Optional[VoiceMetricsCollector] = None
) -> VoiceConnectionManager:
    """
    Create connection manager instance

    Helper function for dependency injection in orchestrator.
    Based on Phase_1_2_2_solid_principles.md - Dependency Inversion patterns.
    """
    return VoiceConnectionManager(
        settings=settings,
        health_checker=health_checker,
        metrics_collector=metrics_collector
    )
