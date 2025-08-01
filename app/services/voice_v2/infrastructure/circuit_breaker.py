"""
Simple Circuit Breaker for voice_v2

Basic failure detection and provider failover with essential functionality:
- Simple failure counting
- Basic provider failover
- Critical failure detection for provider fallback chains

Reduced from enterprise 460-line implementation to ~150 lines essential code.
"""

import asyncio
import time
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass

from app.services.voice_v2.core.exceptions import VoiceServiceError


class CircuitState(Enum):
    """Simplified circuit states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking requests


@dataclass
class SimpleCircuitConfig:
    """Basic circuit configuration"""
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    timeout_duration: float = 30.0


class SimpleCircuitBreaker:
    """Simplified circuit breaker for provider fallback"""

    def __init__(self, name: str, config: Optional[SimpleCircuitConfig] = None):
        self.name = name
        self.config = config or SimpleCircuitConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state_lock = asyncio.Lock()

    async def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        # Fast check if circuit is open
        if not await self._is_available():
            raise VoiceServiceError(f"Circuit breaker '{self.name}' is OPEN")

        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=self.config.timeout_duration
            )

            # Record success
            await self._record_success()
            return result

        except (asyncio.TimeoutError, TimeoutError):
            await self._record_failure()
            raise VoiceServiceError(f"Circuit breaker '{self.name}' timeout")

        except Exception as e:
            await self._record_failure()
            raise e

    async def _is_available(self) -> bool:
        """Check if circuit breaker allows requests"""
        if self._state == CircuitState.CLOSED:
            return True

        # Check if recovery timeout has passed
        if self._last_failure_time:
            time_since_failure = time.time() - self._last_failure_time
            if time_since_failure >= self.config.recovery_timeout:
                # Reset to closed state
                async with self._state_lock:
                    if self._state == CircuitState.OPEN:
                        self._state = CircuitState.CLOSED
                        self._failure_count = 0
                return True

        return False

    async def _record_success(self) -> None:
        """Record successful operation"""
        async with self._state_lock:
            if self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    async def _record_failure(self) -> None:
        """Record failed operation"""
        async with self._state_lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            # Open circuit if failure threshold reached
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN

    def get_state(self) -> CircuitState:
        """Get current circuit state"""
        return self._state

    def is_healthy(self) -> bool:
        """Quick health check"""
        return self._state == CircuitState.CLOSED

    async def reset(self) -> None:
        """Reset circuit breaker to closed state"""
        async with self._state_lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None


class ProviderCircuitManager:
    """Manages circuit breakers for provider failover chains"""

    def __init__(self):
        self._circuit_breakers: dict[str, SimpleCircuitBreaker] = {}

    def get_circuit_breaker(self, provider_name: str) -> SimpleCircuitBreaker:
        """Get or create circuit breaker for provider"""
        if provider_name not in self._circuit_breakers:
            self._circuit_breakers[provider_name] = SimpleCircuitBreaker(provider_name)
        return self._circuit_breakers[provider_name]

    def get_healthy_providers(self, provider_names: list[str]) -> list[str]:
        """Get list of healthy providers from given list"""
        healthy = []
        for name in provider_names:
            circuit = self.get_circuit_breaker(name)
            if circuit.is_healthy():
                healthy.append(name)
        return healthy

    async def reset_all(self) -> None:
        """Reset all circuit breakers"""
        for circuit in self._circuit_breakers.values():
            await circuit.reset()


# Global instance for provider circuit management
provider_circuits = ProviderCircuitManager()
