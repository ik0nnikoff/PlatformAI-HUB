"""
STT Performance Optimizer

Оптимизатор производительности для Speech-to-Text сервисов с:
- Dynamic provider ordering на основе latency
- Parallel provider attempts для критических путей
- Advanced caching с intelligent prefetch
- STT-specific performance адаптация
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

from app.services.voice_v2.core.interfaces import ProviderType, PerformanceTier
from app.services.voice_v2.performance.base_optimizer import (
    BasePerformanceOptimizer, BaseOptimizationConfig
)
from app.services.voice_v2.performance.utils import CacheKeyGenerator

logger = logging.getLogger(__name__)


@dataclass
class STTOptimizationConfig(BaseOptimizationConfig):
    """STT-specific optimization configuration"""
    # STT-specific parameters
    critical_latency: float = 2.0  # seconds
    enable_parallel_attempts: bool = True
    parallel_threshold_ms: int = 1500

    # STT Provider ordering
    ordering_window_minutes: int = 15


@dataclass
class STTProviderMetrics:
    """STT Provider performance metrics tracking"""
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


class STTPerformanceOptimizer(BasePerformanceOptimizer[STTProviderMetrics]):
    """
    STT Performance Optimizer

    Оптимизатор производительности для Speech-to-Text сервисов с:
    - Dynamic provider ordering на основе latency
    - Parallel provider attempts для критических путей
    - Advanced caching с intelligent prefetch
    - STT-specific performance адаптация
    """

    def __init__(self, config: Optional[STTOptimizationConfig] = None):
        """Initialize STT optimizer with configuration"""
        if config is None:
            config = STTOptimizationConfig()

        super().__init__(config)

        logger.info("STTPerformanceOptimizer initialized with target latency: %ss",
                    config.target_latency)

    def _create_provider_metrics(self, provider: ProviderType) -> STTProviderMetrics:
        """Create STT-specific provider metrics instance"""
        return STTProviderMetrics(provider_type=provider)

    def get_optimized_provider_order(self, performance_tier: PerformanceTier) -> List[ProviderType]:
        """Get optimized provider order based on current performance metrics."""
        if not self.config.dynamic_ordering:
            return self._get_default_provider_order()

        viable_providers = self._get_viable_providers()

        if not viable_providers:
            return self._get_default_provider_order()

        # Sort by performance based on tier requirements
        sorted_providers = self._sort_providers_by_tier(viable_providers, performance_tier)
        optimized_order = [provider for provider, _ in sorted_providers]

        # Add missing providers at the end
        complete_order = self._complete_provider_order(optimized_order)

        logger.debug("Optimized STT provider order for %s: %s", performance_tier.value,
                     [p.value for p in complete_order])

        return complete_order

    def _sort_providers_by_tier(self,
                                providers: List[Tuple[ProviderType,
                                                      STTProviderMetrics]],
                                performance_tier: PerformanceTier) -> List[Tuple[ProviderType,
                                                                                 STTProviderMetrics]]:
        """Sort STT providers based on performance tier requirements"""
        if performance_tier == PerformanceTier.CRITICAL:
            return sorted(providers, key=lambda x: x[1].p95_latency)
        elif performance_tier == PerformanceTier.HIGH:
            return sorted(providers, key=lambda x: (x[1].average_latency, -x[1].success_rate))
        else:
            return sorted(providers, key=lambda x: x[1].average_latency)

    def should_use_parallel_processing(self, performance_tier: PerformanceTier) -> bool:
        """
        Determine if parallel processing should be used.
        Based on performance tier and expected latency.
        """
        if not self.config.enable_parallel_attempts:
            return False

        if performance_tier == PerformanceTier.CRITICAL:
            return True

        # Check if any provider is expected to exceed threshold
        for metrics in self.provider_metrics.values():
            if metrics.average_latency > (self.config.parallel_threshold_ms / 1000):
                return True

        return False

    def generate_cache_key(self, audio_data: bytes, language: Optional[str] = None) -> str:
        """Generate optimized cache key for STT result"""
        return CacheKeyGenerator.generate_stt_key(audio_data, language)

    async def record_performance(self, provider: ProviderType, latency: float,
                                 success: bool) -> None:
        """Record performance metrics for provider"""
        metrics = self.provider_metrics.get(provider)
        if not metrics:
            return

        metrics.total_requests += 1

        if success:
            metrics.successful_requests += 1
            metrics.consecutive_failures = 0
            metrics.last_success = self._get_current_time()

            # Update latency metrics using base class method
            self._update_latency_metrics(metrics, latency)
        else:
            metrics.failed_requests += 1
            metrics.consecutive_failures += 1
            metrics.last_failure = self._get_current_time()

        # Track overall performance using base class method
        await self._update_performance_history(latency, "stt")

    def _get_current_time(self):
        """Get current datetime - helper for testing"""
        return datetime.now()

    def get_performance_summary(self) -> dict:
        """Get STT-specific performance summary"""
        summary = super().get_performance_summary()

        # Add STT-specific fields
        summary["cache"]["target_hit_rate"] = self.config.cache_hit_target
        summary["stt_config"] = {
            "critical_latency": self.config.critical_latency,
            "enable_parallel": self.config.enable_parallel_attempts,
            "parallel_threshold_ms": self.config.parallel_threshold_ms
        }

        return summary
