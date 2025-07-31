"""
Base Performance Optimizer - Common functionality for STT/TTS optimizers

Содержит общую логику для всех performance оптимизаторов:
- Connection pool management с aiohttp patterns
- Common performance metrics tracking
- Shared cache functionality
- Base optimization algorithms
"""

import asyncio
import logging
import statistics
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, TypeVar, Generic
from dataclasses import dataclass, field

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

from app.services.voice_v2.core.interfaces import ProviderType

logger = logging.getLogger(__name__)

T = TypeVar('T')  # Generic type for metrics classes


@dataclass
class BaseOptimizationConfig:
    """Base configuration for performance optimizers"""
    # Performance targets
    target_latency: float = 3.0  # seconds

    # Connection pooling (aiohttp patterns)
    max_connections: int = 100
    max_connections_per_host: int = 30
    connection_timeout: float = 10.0
    total_timeout: float = 30.0
    keepalive_timeout: int = 30

    # Cache configuration
    cache_ttl_seconds: int = 3600
    enable_response_caching: bool = True

    # Provider ordering
    dynamic_ordering: bool = True
    min_samples_for_ordering: int = 5


@dataclass
class BaseProviderMetrics:
    """Base provider performance metrics"""
    provider_type: ProviderType
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    average_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0
    recent_latencies: List[float] = field(default_factory=list)

    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    consecutive_failures: int = 0

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100

    @property
    def is_healthy(self) -> bool:
        """Check if provider is healthy"""
        return (
            self.success_rate >= 95.0 and
            self.consecutive_failures < 3 and
            self.average_latency <= 4.0
        )


@dataclass
class BaseCacheMetrics:
    """Base cache performance metrics"""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.cache_hits / self.total_requests) * 100


class BasePerformanceOptimizer(Generic[T], ABC):
    """
    Base Performance Optimizer

    Provides common functionality for STT/TTS performance optimization:
    - Connection pool management with aiohttp best practices
    - Common performance metrics tracking
    - Base optimization algorithms
    - Shared caching functionality
    """

    def __init__(self, config: BaseOptimizationConfig):
        self.config = config
        self.provider_metrics: Dict[ProviderType, T] = {}
        self.cache_metrics = BaseCacheMetrics()
        self._connection_pools: Dict[ProviderType, aiohttp.ClientSession] = {}
        self._optimization_lock = asyncio.Lock()

        # Performance tracking
        self._performance_history: List[Tuple[datetime, float, str]] = []
        self._last_optimization = datetime.now()

        logger.info("BasePerformanceOptimizer initialized with target latency: %ss",
                   config.target_latency)

    async def initialize_connection_pools(self, providers: List[ProviderType]) -> None:
        """
        Initialize optimized connection pools for providers.
        Uses aiohttp patterns optimized for voice workloads.
        """
        for provider in providers:
            connector = TCPConnector(
                limit=self.config.max_connections,
                limit_per_host=self.config.max_connections_per_host,
                keepalive_timeout=self.config.keepalive_timeout,
                enable_cleanup_closed=True,
                use_dns_cache=True,
                ttl_dns_cache=600  # 10 minutes DNS cache
            )

            timeout = ClientTimeout(
                total=self.config.total_timeout,
                connect=self.config.connection_timeout,
                sock_connect=self.config.connection_timeout,
                sock_read=self.config.total_timeout - 5  # Leave buffer for processing
            )

            session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=True
            )

            self._connection_pools[provider] = session
            self.provider_metrics[provider] = self._create_provider_metrics(provider)

            logger.info("Initialized optimized connection pool for %s", provider.value)

    async def cleanup_connection_pools(self) -> None:
        """Cleanup connection pools properly"""
        for provider, session in self._connection_pools.items():
            try:
                await session.close()
                logger.debug("Cleaned up connection pool for %s", provider.value)
            except Exception as e:
                logger.warning("Error cleaning up %s pool: %s", provider.value, e)

        self._connection_pools.clear()

    def _update_latency_metrics(self, metrics: BaseProviderMetrics, latency: float) -> None:
        """Update latency statistics"""
        metrics.recent_latencies.append(latency)
        if len(metrics.recent_latencies) > 100:
            metrics.recent_latencies = metrics.recent_latencies[-100:]

        # Calculate statistics
        if metrics.recent_latencies:
            metrics.average_latency = statistics.mean(metrics.recent_latencies)
            sorted_latencies = sorted(metrics.recent_latencies)
            n = len(sorted_latencies)
            metrics.p95_latency = sorted_latencies[int(0.95 * n)] if n > 0 else latency
            metrics.p99_latency = sorted_latencies[int(0.99 * n)] if n > 0 else latency

    async def _update_performance_history(self, latency: float, strategy: str = "unknown") -> None:
        """Update and cleanup performance history"""
        self._performance_history.append((datetime.now(), latency, strategy))

        # Cleanup old history (keep last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        self._performance_history = [
            (ts, lat, strat) for ts, lat, strat in self._performance_history if ts > cutoff
        ]

    async def record_cache_access(self, hit: bool) -> None:
        """Record cache access metrics"""
        self.cache_metrics.total_requests += 1
        if hit:
            self.cache_metrics.cache_hits += 1
        else:
            self.cache_metrics.cache_misses += 1

    def _get_default_provider_order(self) -> List[ProviderType]:
        """Get default provider order"""
        return [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]

    def _get_viable_providers(self) -> List[Tuple[ProviderType, T]]:
        """Get providers with sufficient samples and healthy status"""
        viable_providers = []
        for provider, metrics in self.provider_metrics.items():
            if (metrics.total_requests >= self.config.min_samples_for_ordering and
                metrics.is_healthy):
                viable_providers.append((provider, metrics))
        return viable_providers

    def _complete_provider_order(self, optimized_order: List[ProviderType]) -> List[ProviderType]:
        """Add any missing providers at the end"""
        all_providers = [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]
        for provider in all_providers:
            if provider not in optimized_order:
                optimized_order.append(provider)
        return optimized_order

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "target_latency": self.config.target_latency,
            "cache": {
                "hit_rate": self.cache_metrics.hit_rate,
                "total_requests": self.cache_metrics.total_requests,
            },
            "providers": {}
        }

        for provider, metrics in self.provider_metrics.items():
            summary["providers"][provider.value] = {
                "total_requests": metrics.total_requests,
                "success_rate": metrics.success_rate,
                "average_latency": metrics.average_latency,
                "p95_latency": metrics.p95_latency,
                "is_healthy": metrics.is_healthy,
                "consecutive_failures": metrics.consecutive_failures
            }

        # Overall performance
        if self._performance_history:
            recent_latencies = [lat for _, lat, _ in self._performance_history[-50:]]
            summary["overall"] = {
                "recent_average_latency": statistics.mean(recent_latencies),
                "meeting_target": statistics.mean(recent_latencies) <= self.config.target_latency
            }

        return summary

    async def optimize_if_needed(self) -> bool:
        """
        Trigger optimization if performance degrades.
        Returns True if optimization was performed.
        """
        now = datetime.now()
        if (now - self._last_optimization).total_seconds() < 300:  # 5 minutes cooldown
            return False

        async with self._optimization_lock:
            needs_optimization = False

            # Check provider performance
            for provider, metrics in self.provider_metrics.items():
                if not metrics.is_healthy:
                    logger.warning("Provider %s unhealthy: success_rate=%s%%, avg_latency=%ss",
                                 provider.value, f"{metrics.success_rate:.1f}",
                                 f"{metrics.average_latency:.2f}")
                    needs_optimization = True

            if needs_optimization:
                await self._perform_optimization()
                self._last_optimization = now
                return True

        return False

    async def _perform_optimization(self) -> None:
        """Perform optimization actions"""
        logger.info("Performing performance optimization...")

        # Log optimization event with summary
        summary = self.get_performance_summary()
        logger.info("Optimization triggered with summary: %s", summary)

    # Abstract methods that must be implemented by subclasses
    @abstractmethod
    def _create_provider_metrics(self, provider: ProviderType) -> T:
        """Create provider-specific metrics instance"""

    @abstractmethod
    async def record_performance(self, provider: ProviderType, latency: float,
                               success: bool, **kwargs) -> None:
        """Record performance metrics for provider"""

    @abstractmethod
    def get_optimized_provider_order(self, **kwargs) -> List[ProviderType]:
        """Get optimized provider order based on specific criteria"""
