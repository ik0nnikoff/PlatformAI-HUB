"""
Enhanced Connection Manager - Phase 3.3.2 Implementation

Реализует advanced connection management с полным соблюдением Phase 1.3 архитектурных требований:
- Phase_1_3_1_architecture_review.md → LSP compliance для connection interfaces
- Phase_1_1_4_architecture_patterns.md → успешные patterns из app/services/voice
- Phase_1_2_3_performance_optimization.md → async patterns и connection pooling
- Phase_1_2_2_solid_principles.md → Interface Segregation в connection design

Enhanced Features (Phase 3.3.2):
- Advanced connection pooling с aiohttp integration
- Retry mechanisms с exponential backoff и jitter
- Health monitoring integration с circuit breaker patterns
- Performance metrics collection и monitoring
- Resource lifecycle management с proper cleanup
- Provider-specific connection optimization
"""

import asyncio
import logging
import time
import random
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiohttp import ClientTimeout, ClientSession, TCPConnector

from app.services.voice_v2.core.exceptions import (
    VoiceServiceError,
    ConfigurationError
)

logger = logging.getLogger(__name__)


class ConnectionStatus(Enum):
    """Connection health status enumeration"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class RetryStrategy(Enum):
    """Retry strategy enumeration"""
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    LINEAR_BACKOFF = "linear_backoff"
    FIXED_DELAY = "fixed_delay"
    NO_RETRY = "no_retry"


@dataclass
class ConnectionMetrics:
    """Connection performance metrics tracking"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    timeout_requests: int = 0
    total_response_time: float = 0.0
    min_response_time: float = float('inf')
    max_response_time: float = 0.0
    last_request_time: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100

    @property
    def average_response_time(self) -> float:
        """Calculate average response time"""
        if self.successful_requests == 0:
            return 0.0
        return self.total_response_time / self.successful_requests

    def record_request(self, response_time: float, success: bool, timeout: bool = False) -> None:
        """Record request metrics"""
        self.total_requests += 1
        self.last_request_time = datetime.now()

        if timeout:
            self.timeout_requests += 1
        elif success:
            self.successful_requests += 1
            self.total_response_time += response_time
            self.min_response_time = min(self.min_response_time, response_time)
            self.max_response_time = max(self.max_response_time, response_time)
        else:
            self.failed_requests += 1


@dataclass
class ConnectionConfig:
    """
    Connection configuration structure.
    Implements Interface Segregation - contains only connection-related configuration.
    """
    # Connection pooling settings
    max_connections: int = 100
    max_connections_per_host: int = 30
    connection_timeout: float = 30.0
    read_timeout: float = 60.0
    total_timeout: float = 120.0

    # Retry settings
    retry_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: bool = True

    # Circuit breaker settings
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout_minutes: int = 5

    # Keep-alive settings
    keepalive_timeout: int = 30
    enable_cleanup_closed: bool = True

    def __post_init__(self):
        """Validate configuration after initialization"""
        if self.max_connections <= 0:
            raise ConfigurationError("max_connections must be positive")
        if self.max_connections_per_host <= 0:
            raise ConfigurationError("max_connections_per_host must be positive")
        if self.connection_timeout <= 0:
            raise ConfigurationError("connection_timeout must be positive")


@dataclass
class CircuitBreakerState:
    """Circuit breaker state tracking"""
    is_open: bool = False
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    next_attempt_time: Optional[datetime] = None

    def record_success(self) -> None:
        """Record successful operation"""
        self.is_open = False
        self.failure_count = 0
        self.last_failure_time = None
        self.next_attempt_time = None

    def record_failure(self, timeout_minutes: int = 5) -> None:
        """Record failed operation"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        self.next_attempt_time = datetime.now() + timedelta(minutes=timeout_minutes)

    def should_open(self, threshold: int) -> bool:
        """Check if circuit breaker should open"""
        return self.failure_count >= threshold

    def can_attempt(self) -> bool:
        """Check if request can be attempted"""
        if not self.is_open:
            return True

        if self.next_attempt_time and datetime.now() >= self.next_attempt_time:
            # Circuit breaker timeout expired, allow half-open state
            return True

        return False


class IConnectionManager(ABC):
    """
    Abstract connection manager interface.
    Implements Interface Segregation Principle - focused interface для connection management.
    Follows LSP - all implementations должны быть fully substitutable.
    """

    @abstractmethod
    async def execute_request(
        self,
        provider_name: str,
        request_func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """Execute request with connection management"""
        raise NotImplementedError

    @abstractmethod
    async def register_provider(
        self, provider_name: str, config: Optional[ConnectionConfig] = None
    ) -> None:
        """Register provider for connection management"""
        raise NotImplementedError

    @abstractmethod
    def get_metrics(self, provider_name: str) -> Optional[ConnectionMetrics]:
        """Get connection metrics for provider"""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self, provider_name: str) -> ConnectionStatus:
        """Check connection health for provider"""
        raise NotImplementedError

    @abstractmethod
    async def shutdown(self) -> None:
        """Graceful shutdown with resource cleanup"""
        raise NotImplementedError


class EnhancedConnectionManager(IConnectionManager):
    """
    Enhanced Connection Manager - Phase 3.3.2 Implementation

    Follows SOLID principles:
    - Single Responsibility: Connection management, pooling, и retry handling
    - Open/Closed: Open for extension (new providers), closed for modification
    - Liskov Substitution: Implements IConnectionManager interface
    - Interface Segregation: Focused interface для connection operations
    - Dependency Inversion: Depends on abstractions, not concrete implementations

    Enhanced features:
    - Advanced connection pooling с aiohttp
    - Sophisticated retry mechanisms с exponential backoff и jitter
    - Circuit breaker patterns для provider isolation
    - Performance metrics collection и monitoring
    - Resource lifecycle management
    """

    def __init__(self, default_config: Optional[ConnectionConfig] = None):
        self._default_config = default_config or ConnectionConfig()
        self._provider_configs: Dict[str, ConnectionConfig] = {}
        self._sessions: Dict[str, ClientSession] = {}
        self._metrics: Dict[str, ConnectionMetrics] = {}
        self._circuit_breakers: Dict[str, CircuitBreakerState] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        logger.info("EnhancedConnectionManager initialized")

    async def initialize(self) -> None:
        """
        Initialize connection manager.
        Implements async initialization pattern from Phase_1_2_3_performance_optimization.md
        """
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                # Initialize default session
                await self._create_default_session()

                self._initialized = True
                logger.info("EnhancedConnectionManager initialized successfully")

            except Exception as e:
                logger.error("Failed to initialize connection manager: %s", e, exc_info=True)
                raise ConfigurationError(f"Connection manager initialization failed: {e}") from e

    async def _create_default_session(self) -> None:
        """Create default HTTP session with optimized settings"""
        connector = TCPConnector(
            limit=self._default_config.max_connections,
            limit_per_host=self._default_config.max_connections_per_host,
            keepalive_timeout=self._default_config.keepalive_timeout,
            enable_cleanup_closed=self._default_config.enable_cleanup_closed,
            use_dns_cache=True
        )

        timeout = ClientTimeout(
            total=self._default_config.total_timeout,
            connect=self._default_config.connection_timeout,
            sock_read=self._default_config.read_timeout
        )

        session = ClientSession(
            connector=connector,
            timeout=timeout,
            raise_for_status=False  # Handle status codes manually
        )

        self._sessions["default"] = session
        logger.debug("Default HTTP session created")

    async def register_provider(
        self, provider_name: str, config: Optional[ConnectionConfig] = None
    ) -> None:
        """
        Register provider with connection configuration.
        Implements provider-specific connection optimization.
        """
        if not self._initialized:
            await self.initialize()

        try:
            # Use provider-specific config or default
            provider_config = config or self._default_config
            self._provider_configs[provider_name] = provider_config

            # Initialize metrics и circuit breaker
            self._metrics[provider_name] = ConnectionMetrics()
            self._circuit_breakers[provider_name] = CircuitBreakerState()

            # Create provider-specific session if needed
            if config and config != self._default_config:
                await self._create_provider_session(provider_name, provider_config)

            logger.info("Registered provider: %s", provider_name)

        except Exception as e:
            logger.error("Failed to register provider %s: %s", provider_name, e)
            raise ConfigurationError(f"Provider registration failed: {e}") from e

    async def _create_provider_session(self, provider_name: str, config: ConnectionConfig) -> None:
        """Create provider-specific HTTP session"""
        connector = TCPConnector(
            limit=config.max_connections,
            limit_per_host=config.max_connections_per_host,
            keepalive_timeout=config.keepalive_timeout,
            enable_cleanup_closed=config.enable_cleanup_closed,
            use_dns_cache=True
        )

        timeout = ClientTimeout(
            total=config.total_timeout,
            connect=config.connection_timeout,
            sock_read=config.read_timeout
        )

        session = ClientSession(
            connector=connector,
            timeout=timeout,
            raise_for_status=False
        )

        self._sessions[provider_name] = session
        logger.debug("Provider-specific session created for: %s", provider_name)

    async def execute_request(
        self,
        provider_name: str,
        request_func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute request with enhanced retry logic и circuit breaker.

        Implements sophisticated retry patterns from Phase_1_2_3_performance_optimization.md
        """
        if not self._initialized:
            await self.initialize()

        # Ensure provider is registered
        if provider_name not in self._metrics:
            await self.register_provider(provider_name)

        config = self._provider_configs.get(provider_name, self._default_config)
        circuit_breaker = self._circuit_breakers[provider_name]
        metrics = self._metrics[provider_name]

        # Check circuit breaker
        if circuit_breaker.is_open and not circuit_breaker.can_attempt():
            raise VoiceServiceError(
                f"Circuit breaker is open for provider {provider_name}. "
                f"Next attempt allowed at: {circuit_breaker.next_attempt_time}"
            )

        # Execute with retry logic
        last_exception = None

        for attempt in range(config.max_retries + 1):
            try:
                start_time = time.time()

                # Get appropriate session
                session = self._sessions.get(provider_name, self._sessions["default"])

                # Execute request
                if asyncio.iscoroutinefunction(request_func):
                    result = await request_func(session, *args, **kwargs)
                else:
                    result = request_func(session, *args, **kwargs)

                # Record success
                response_time = time.time() - start_time
                metrics.record_request(response_time, success=True)
                circuit_breaker.record_success()

                logger.debug(
                    "Request successful for %s (attempt %d, response_time: %.3fs)",
                    provider_name, attempt + 1, response_time
                )

                return result

            except asyncio.TimeoutError as e:
                response_time = time.time() - start_time
                metrics.record_request(response_time, success=False, timeout=True)
                last_exception = e

                logger.warning(
                    "Request timeout for %s (attempt %d/%d)",
                    provider_name, attempt + 1, config.max_retries + 1
                )

            except Exception as e:
                response_time = time.time() - start_time
                metrics.record_request(response_time, success=False)
                last_exception = e

                logger.warning(
                    "Request failed for %s: %s (attempt %d/%d)",
                    provider_name, e, attempt + 1, config.max_retries + 1
                )

            # Check if should retry
            if attempt < config.max_retries:
                delay = self._calculate_retry_delay(attempt, config)
                logger.debug("Retrying in %.3fs...", delay)
                await asyncio.sleep(delay)
            else:
                # All attempts failed, record circuit breaker failure
                circuit_breaker.record_failure(config.circuit_breaker_timeout_minutes)

                if circuit_breaker.should_open(config.circuit_breaker_threshold):
                    circuit_breaker.is_open = True
                    logger.error(
                        "Circuit breaker opened for provider %s (%d consecutive failures)",
                        provider_name, circuit_breaker.failure_count
                    )

        # All retries exhausted
        error_msg = f"All {config.max_retries + 1} attempts failed for provider {provider_name}"
        if last_exception:
            error_msg += f". Last error: {last_exception}"

        raise VoiceServiceError(error_msg)

    def _calculate_retry_delay(self, attempt: int, config: ConnectionConfig) -> float:
        """
        Calculate retry delay based on strategy.
        Implements advanced retry patterns с jitter.
        """
        if config.retry_strategy == RetryStrategy.NO_RETRY:
            return 0.0

        if config.retry_strategy == RetryStrategy.FIXED_DELAY:
            delay = config.base_delay
        elif config.retry_strategy == RetryStrategy.LINEAR_BACKOFF:
            delay = config.base_delay * (attempt + 1)
        elif config.retry_strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
            delay = config.base_delay * (config.backoff_factor ** attempt)
        else:
            delay = config.base_delay

        # Apply maximum delay limit
        delay = min(delay, config.max_delay)

        # Add jitter to prevent thundering herd
        if config.jitter:
            jitter_amount = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_amount, jitter_amount)

        return max(0.0, delay)

    def get_metrics(self, provider_name: str) -> Optional[ConnectionMetrics]:
        """Get connection metrics for provider"""
        return self._metrics.get(provider_name)

    def get_all_metrics(self) -> Dict[str, ConnectionMetrics]:
        """Get metrics for all registered providers"""
        return self._metrics.copy()

    async def health_check(self, provider_name: str) -> ConnectionStatus:
        """
        Check connection health for provider.
        Implements health monitoring patterns from Phase_1_1_4_architecture_patterns.md
        """
        try:
            if provider_name not in self._metrics:
                return ConnectionStatus.UNKNOWN

            metrics = self._metrics[provider_name]
            circuit_breaker = self._circuit_breakers[provider_name]

            # Check circuit breaker status
            if circuit_breaker.is_open:
                return ConnectionStatus.FAILED

            # Check recent performance
            if metrics.total_requests == 0:
                return ConnectionStatus.UNKNOWN

            # Determine health based on success rate
            success_rate = metrics.success_rate
            if success_rate >= 95.0:
                return ConnectionStatus.HEALTHY

            if success_rate >= 80.0:
                return ConnectionStatus.DEGRADED

            return ConnectionStatus.FAILED

        except Exception as e:
            logger.error("Health check failed for %s: %s", provider_name, e)
            return ConnectionStatus.UNKNOWN

    async def health_check_all(self) -> Dict[str, ConnectionStatus]:
        """Check health of all registered providers"""
        results = {}

        for provider_name in self._metrics:
            results[provider_name] = await self.health_check(provider_name)

        logger.debug("Health check completed for %d providers", len(results))
        return results

    def reset_circuit_breaker(self, provider_name: str) -> bool:
        """Manually reset circuit breaker for provider"""
        if provider_name in self._circuit_breakers:
            circuit_breaker = self._circuit_breakers[provider_name]
            circuit_breaker.record_success()
            logger.info("Circuit breaker reset for provider: %s", provider_name)
            return True
        return False

    def get_circuit_breaker_status(self, provider_name: str) -> Optional[CircuitBreakerState]:
        """Get circuit breaker status for provider"""
        return self._circuit_breakers.get(provider_name)

    async def shutdown(self) -> None:
        """
        Graceful shutdown with comprehensive resource cleanup.
        Implements proper resource management from Phase_1_2_3_performance_optimization.md
        """
        try:
            logger.info("Shutting down EnhancedConnectionManager...")

            # Close all HTTP sessions
            for provider_name, session in self._sessions.items():
                try:
                    await session.close()
                    logger.debug("Closed session for: %s", provider_name)
                except Exception as e:
                    logger.error("Error closing session for %s: %s", provider_name, e)

            # Clear all state
            self._sessions.clear()
            self._metrics.clear()
            self._circuit_breakers.clear()
            self._provider_configs.clear()
            self._initialized = False

            logger.info("EnhancedConnectionManager shut down successfully")

        except Exception as e:
            logger.error("Error during connection manager shutdown: %s", e, exc_info=True)


# Global connection manager instance
_connection_manager_instance: Optional[EnhancedConnectionManager] = None
_connection_manager_lock = asyncio.Lock()


async def get_enhanced_connection_manager() -> EnhancedConnectionManager:
    """
    Get singleton connection manager instance.
    Implements singleton pattern с thread safety для performance.
    """
    global _connection_manager_instance

    if _connection_manager_instance is None:
        async with _connection_manager_lock:
            if _connection_manager_instance is None:
                _connection_manager_instance = EnhancedConnectionManager()
                await _connection_manager_instance.initialize()

    return _connection_manager_instance


async def shutdown_connection_manager() -> None:
    """Shutdown connection manager instance"""
    global _connection_manager_instance

    if _connection_manager_instance:
        await _connection_manager_instance.shutdown()
        _connection_manager_instance = None
