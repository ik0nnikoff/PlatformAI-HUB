"""
Circuit Breaker Pattern Implementation for voice_v2

This module implements the Circuit Breaker pattern to protect voice_v2 system
from cascade failures and provide automatic recovery mechanisms for providers.

Circuit breaker prevents overwhelming failed services and provides graceful degradation.

Key features:
- Three states: CLOSED, OPEN, HALF_OPEN
- Automatic failure detection and recovery
- Provider-specific circuit breakers
- Performance metrics integration
- Async-first implementation

Architecture follows SOLID principles:
- SRP: Each circuit breaker handles one provider
- OCP: Extensible for new failure conditions
- LSP: All circuit breakers implement same interface
- ISP: Separate interfaces for different circuit breaker types
- DIP: Depends on abstractions, not concrete implementations

Performance targets:
- Circuit breaker decision: ≤1µs
- State transitions: ≤5µs
- Recovery checks: ≤10µs
"""

import asyncio
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional, Callable, Any, TypeVar, Generic
from dataclasses import dataclass
from contextlib import asynccontextmanager

from app.services.voice_v2.core.interfaces import ProviderType
from app.services.voice_v2.core.exceptions import VoiceServiceError
from app.services.voice_v2.core.config import get_config


class CircuitBreakerState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior"""
    failure_threshold: int = 5          # Failures to trigger OPEN
    recovery_timeout: float = 60.0      # Seconds before trying HALF_OPEN
    success_threshold: int = 3          # Successes to close from HALF_OPEN
    timeout_duration: float = 30.0      # Request timeout
    sliding_window_size: int = 100      # Window for failure rate calculation
    failure_rate_threshold: float = 0.5  # Failure rate to trigger OPEN


@dataclass
class CircuitBreakerMetrics:
    """Metrics for circuit breaker monitoring"""
    total_requests: int = 0
    failed_requests: int = 0
    successful_requests: int = 0
    state_transitions: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    average_response_time: float = 0.0

    @property
    def failure_rate(self) -> float:
        """Calculate current failure rate"""
        if self.total_requests == 0:
            return 0.0
        return self.failed_requests / self.total_requests

    @property
    def success_rate(self) -> float:
        """Calculate current success rate"""
        return 1.0 - self.failure_rate


class CircuitBreakerInterface(ABC):
    """Abstract interface for circuit breaker implementations"""

    @abstractmethod
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function through circuit breaker"""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if circuit breaker allows requests"""
        pass

    @abstractmethod
    def get_state(self) -> CircuitBreakerState:
        """Get current circuit breaker state"""
        pass

    @abstractmethod
    def get_metrics(self) -> CircuitBreakerMetrics:
        """Get circuit breaker metrics"""
        pass

    @abstractmethod
    async def reset(self) -> None:
        """Reset circuit breaker to CLOSED state"""
        pass


T = TypeVar('T')


class BaseCircuitBreaker(CircuitBreakerInterface, Generic[T]):
    """
    Base circuit breaker implementation following SOLID principles

    SRP: Handles only circuit breaker logic
    OCP: Extensible through configuration
    LSP: Substitutable with other circuit breaker implementations
    """

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState.CLOSED
        self._metrics = CircuitBreakerMetrics()
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._state_lock = asyncio.Lock()
        self._request_history: List[tuple[float, bool]] = []  # (timestamp, success)

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Execute function through circuit breaker with failure protection

        Implementation follows performance targets:
        - Circuit breaker decision: ≤1µs
        - State transitions: ≤5µs
        """
        start_time = time.perf_counter()

        # Fast path: check availability without lock
        if not await self._fast_availability_check():
            raise VoiceServiceError(
                f"Circuit breaker '{self.name}' is OPEN"
            )

        try:
            # Execute the function with timeout
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=self.config.timeout_duration
            )

            # Record success
            await self._record_success(time.perf_counter() - start_time)
            return result

        except (asyncio.TimeoutError, TimeoutError):
            await self._record_failure(time.perf_counter() - start_time)
            raise VoiceServiceError(
                f"Circuit breaker '{self.name}' timeout"
            )
        except Exception:
            await self._record_failure(time.perf_counter() - start_time)
            raise

    async def _fast_availability_check(self) -> bool:
        """Fast availability check without lock for performance"""
        current_time = time.time()

        if self._state == CircuitBreakerState.CLOSED:
            return True
        elif self._state == CircuitBreakerState.OPEN:
            # Check if recovery timeout has passed
            if (self._last_failure_time and
                    current_time - self._last_failure_time >= self.config.recovery_timeout):
                # Transition to HALF_OPEN (with lock for safety)
                async with self._state_lock:
                    if self._state == CircuitBreakerState.OPEN:
                        await self._transition_to_half_open()
                return True
            return False
        else:  # HALF_OPEN
            return True

    async def _record_success(self, response_time: float) -> None:
        """Record successful operation"""
        async with self._state_lock:
            self._success_count += 1
            self._metrics.successful_requests += 1
            self._metrics.total_requests += 1
            self._metrics.last_success_time = time.time()

            # Reset failure count on success in CLOSED state
            if self._state == CircuitBreakerState.CLOSED:
                self._failure_count = 0

            # Update average response time
            self._update_average_response_time(response_time)

            # Update request history
            self._update_request_history(True)

            # State transition logic
            if self._state == CircuitBreakerState.HALF_OPEN:
                if self._success_count >= self.config.success_threshold:
                    await self._transition_to_closed()

    async def _record_failure(self, response_time: float) -> None:
        """Record failed operation"""
        async with self._state_lock:
            self._failure_count += 1
            self._metrics.failed_requests += 1
            self._metrics.total_requests += 1
            self._metrics.last_failure_time = time.time()
            self._last_failure_time = self._metrics.last_failure_time

            # Update average response time
            self._update_average_response_time(response_time)

            # Update request history
            self._update_request_history(False)

            # State transition logic
            if self._state == CircuitBreakerState.CLOSED:
                # Check failure threshold (consecutive failures)
                if self._failure_count >= self.config.failure_threshold:
                    await self._transition_to_open()
                # Also check failure rate in sliding window
                elif (self._metrics.total_requests >= 10 and  # Minimum requests for rate calculation
                      self._calculate_sliding_window_failure_rate() >= self.config.failure_rate_threshold):
                    await self._transition_to_open()
            elif self._state == CircuitBreakerState.HALF_OPEN:
                # Any failure in HALF_OPEN state reopens the circuit
                await self._transition_to_open()

    def _update_average_response_time(self, response_time: float) -> None:
        """Update running average of response time"""
        total_requests = self._metrics.total_requests
        if total_requests == 1:
            self._metrics.average_response_time = response_time
        else:
            # Exponential moving average
            alpha = 0.1  # Smoothing factor
            self._metrics.average_response_time = (
                alpha * response_time +
                (1 - alpha) * self._metrics.average_response_time
            )

    def _update_request_history(self, success: bool) -> None:
        """Update sliding window request history"""
        current_time = time.time()
        self._request_history.append((current_time, success))

        # Remove old entries outside sliding window
        cutoff_time = current_time - 300  # 5 minutes sliding window
        self._request_history = [
            (timestamp, success) for timestamp, success in self._request_history
            if timestamp > cutoff_time
        ]

        # Limit history size for performance
        if len(self._request_history) > self.config.sliding_window_size:
            self._request_history = self._request_history[-self.config.sliding_window_size:]

    def _calculate_sliding_window_failure_rate(self) -> float:
        """Calculate failure rate in sliding window"""
        if not self._request_history:
            return 0.0

        total_requests = len(self._request_history)
        failed_requests = sum(1 for _, success in self._request_history if not success)

        return failed_requests / total_requests

    async def _transition_to_open(self) -> None:
        """Transition to OPEN state"""
        if self._state != CircuitBreakerState.OPEN:
            self._state = CircuitBreakerState.OPEN
            self._metrics.state_transitions += 1
            self._success_count = 0  # Reset success count

    async def _transition_to_half_open(self) -> None:
        """Transition to HALF_OPEN state"""
        if self._state != CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.HALF_OPEN
            self._metrics.state_transitions += 1
            self._success_count = 0  # Reset success count for testing
            self._failure_count = 0  # Reset failure count for fresh start

    async def _transition_to_closed(self) -> None:
        """Transition to CLOSED state"""
        if self._state != CircuitBreakerState.CLOSED:
            self._state = CircuitBreakerState.CLOSED
            self._metrics.state_transitions += 1
            self._failure_count = 0  # Reset failure count
            self._success_count = 0  # Reset success count

    async def is_available(self) -> bool:
        """Check if circuit breaker allows requests"""
        return await self._fast_availability_check()

    def get_state(self) -> CircuitBreakerState:
        """Get current circuit breaker state"""
        return self._state

    def get_metrics(self) -> CircuitBreakerMetrics:
        """Get circuit breaker metrics"""
        return self._metrics

    async def reset(self) -> None:
        """Reset circuit breaker to CLOSED state"""
        async with self._state_lock:
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            self._metrics = CircuitBreakerMetrics()
            self._request_history.clear()


class ProviderCircuitBreaker(BaseCircuitBreaker):
    """
    Circuit breaker specifically for voice providers

    ISP: Specialized interface for provider-specific circuit breaking
    """

    def __init__(self, provider_type: ProviderType, config: Optional[CircuitBreakerConfig] = None):
        super().__init__(f"provider_{provider_type.value}", config)
        self.provider_type = provider_type

    @asynccontextmanager
    async def protect(self, operation_name: str = "unknown"):
        """Context manager for protecting provider operations"""
        if not await self.is_available():
            raise VoiceServiceError(
                f"Provider {self.provider_type.value} circuit breaker is OPEN"
            )

        start_time = time.perf_counter()
        try:
            yield
            await self._record_success(time.perf_counter() - start_time)
        except Exception:
            await self._record_failure(time.perf_counter() - start_time)
            raise


class CircuitBreakerManager:
    """
    Manager for multiple circuit breakers following DIP principle

    DIP: Depends on CircuitBreakerInterface abstraction
    SRP: Manages circuit breaker lifecycle and coordination
    """

    def __init__(self):
        self._circuit_breakers: Dict[str, CircuitBreakerInterface] = {}
        self._provider_breakers: Dict[ProviderType, ProviderCircuitBreaker] = {}
        self._global_config = get_config().circuit_breaker if hasattr(
            get_config(), 'circuit_breaker') else CircuitBreakerConfig()

    def register_provider_circuit_breaker(
        self,
        provider_type: ProviderType,
        config: Optional[CircuitBreakerConfig] = None
    ) -> ProviderCircuitBreaker:
        """Register circuit breaker for a provider"""
        circuit_breaker = ProviderCircuitBreaker(provider_type, config or self._global_config)
        self._provider_breakers[provider_type] = circuit_breaker
        self._circuit_breakers[f"provider_{provider_type.value}"] = circuit_breaker
        return circuit_breaker

    def get_provider_circuit_breaker(
            self, provider_type: ProviderType) -> Optional[ProviderCircuitBreaker]:
        """Get circuit breaker for a provider"""
        return self._provider_breakers.get(provider_type)

    def register_circuit_breaker(self, name: str, circuit_breaker: CircuitBreakerInterface) -> None:
        """Register a custom circuit breaker"""
        self._circuit_breakers[name] = circuit_breaker

    def get_circuit_breaker(self, name: str) -> Optional[CircuitBreakerInterface]:
        """Get circuit breaker by name"""
        return self._circuit_breakers.get(name)

    async def get_all_available_providers(self) -> List[ProviderType]:
        """Get list of providers with available circuit breakers"""
        available_providers = []
        for provider_type, circuit_breaker in self._provider_breakers.items():
            if await circuit_breaker.is_available():
                available_providers.append(provider_type)
        return available_providers

    async def get_system_health(self) -> Dict[str, Dict[str, Any]]:
        """Get health status of all circuit breakers"""
        health_status = {}

        for name, circuit_breaker in self._circuit_breakers.items():
            metrics = circuit_breaker.get_metrics()
            health_status[name] = {
                "state": circuit_breaker.get_state().value,
                "available": await circuit_breaker.is_available(),
                "success_rate": metrics.success_rate,
                "failure_rate": metrics.failure_rate,
                "total_requests": metrics.total_requests,
                "average_response_time": metrics.average_response_time,
                "state_transitions": metrics.state_transitions
            }

        return health_status

    async def reset_all(self) -> None:
        """Reset all circuit breakers"""
        for circuit_breaker in self._circuit_breakers.values():
            await circuit_breaker.reset()


# Global circuit breaker manager instance
_circuit_breaker_manager: Optional[CircuitBreakerManager] = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Get global circuit breaker manager instance"""
    global _circuit_breaker_manager
    if _circuit_breaker_manager is None:
        _circuit_breaker_manager = CircuitBreakerManager()
    return _circuit_breaker_manager


async def initialize_provider_circuit_breakers() -> None:
    """Initialize circuit breakers for all voice providers"""
    manager = get_circuit_breaker_manager()

    # Initialize circuit breakers for all provider types
    for provider_type in ProviderType:
        manager.register_provider_circuit_breaker(provider_type)


# Convenience decorators for circuit breaker protection
def circuit_breaker_protect(provider_type: ProviderType):
    """Decorator to protect functions with circuit breaker"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            manager = get_circuit_breaker_manager()
            circuit_breaker = manager.get_provider_circuit_breaker(provider_type)

            if circuit_breaker is None:
                # No circuit breaker configured, execute directly
                return await func(*args, **kwargs)

            return await circuit_breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator
