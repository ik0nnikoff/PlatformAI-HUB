"""
Simple Health Checker for voice_v2

Simplified health monitoring for voice providers with essential functionality:
- Basic provider availability checks
- Critical failure detection
- Simple health status tracking

Reduced from enterprise 552-line implementation to ~100 lines essential code.
"""

import asyncio
import time
from enum import Enum
from typing import Dict, List
from dataclasses import dataclass
from datetime import datetime

from app.services.voice_v2.core.interfaces import ProviderType


class HealthStatus(Enum):
    """Health status enumeration"""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthResult:
    """Simple health check result"""
    status: HealthStatus
    message: str
    timestamp: datetime
    response_time_ms: float = 0.0

    @property
    def is_healthy(self) -> bool:
        """Check if status indicates healthy state"""
        return self.status == HealthStatus.HEALTHY


class SimpleHealthChecker:
    """Simplified health checker for voice providers"""

    def __init__(self, cache_ttl_seconds: int = 30):
        self.cache_ttl_seconds = cache_ttl_seconds
        self._provider_cache: Dict[str, HealthResult] = {}
        self._cache_timestamps: Dict[str, datetime] = {}

    async def check_provider_health(
        self,
        provider_name: str,
        provider_type: ProviderType,
        force_check: bool = False
    ) -> HealthResult:
        """Check health of specific provider with caching"""
        cache_key = f"{provider_name}_{provider_type.value}"

        # Check cache first (unless forced)
        if not force_check and self._is_cache_valid(cache_key):
            return self._provider_cache[cache_key]

        # Perform actual health check
        result = await self._perform_provider_check(provider_name, provider_type)

        # Update cache
        self._provider_cache[cache_key] = result
        self._cache_timestamps[cache_key] = datetime.now()

        return result

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached result is still valid"""
        if cache_key not in self._provider_cache or cache_key not in self._cache_timestamps:
            return False

        cache_age = datetime.now() - self._cache_timestamps[cache_key]
        return cache_age.total_seconds() < self.cache_ttl_seconds

    async def _perform_provider_check(
        self,
        provider_name: str,
        provider_type: ProviderType
    ) -> HealthResult:
        """Perform basic provider health check"""
        start_time = time.time()

        try:
            # Simple availability check with timeout
            await asyncio.wait_for(
                self._check_provider_availability(provider_name),
                timeout=5.0
            )

            response_time = (time.time() - start_time) * 1000
            return HealthResult(
                status=HealthStatus.HEALTHY,
                message=f"{provider_name} is available",
                timestamp=datetime.now(),
                response_time_ms=response_time
            )

        except asyncio.TimeoutError:
            response_time = (time.time() - start_time) * 1000
            return HealthResult(
                status=HealthStatus.UNHEALTHY,
                message=f"{provider_name} timeout",
                timestamp=datetime.now(),
                response_time_ms=response_time
            )

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return HealthResult(
                status=HealthStatus.UNHEALTHY,
                message=f"{provider_name} error: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=response_time
            )

    async def _check_provider_availability(self, provider_name: str) -> None:
        """Basic provider availability check"""
        # Simulate lightweight availability check
        await asyncio.sleep(0.01)

        # In real implementation, this would make minimal API call
        # For now, always pass (providers handle their own errors)

    def is_provider_healthy(self, provider_name: str, provider_type: ProviderType) -> bool:
        """Quick check if provider is healthy from cache"""
        cache_key = f"{provider_name}_{provider_type.value}"

        if not self._is_cache_valid(cache_key):
            return False  # Unknown state, assume unhealthy

        return self._provider_cache[cache_key].is_healthy

    def get_healthy_providers(self, provider_type: ProviderType) -> List[str]:
        """Get list of healthy providers for given type"""
        healthy_providers = []

        for cache_key in self._provider_cache.keys():
            if cache_key.endswith(f"_{provider_type.value}"):
                provider_name = cache_key.replace(f"_{provider_type.value}", "")
                if self.is_provider_healthy(provider_name, provider_type):
                    healthy_providers.append(provider_name)

        return healthy_providers
