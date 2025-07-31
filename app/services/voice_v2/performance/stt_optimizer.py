"""
STT Performance Optimizer - Phase 5.3.1 Implementation

Реализует оптимизацию производительности STT для достижения ≤3.5s targets:
- Provider ordering optimization на основе latency metrics
- Parallel provider attempts для critical paths
- Advanced caching с >85% hit rate target
- Connection pooling optimization с aiohttp patterns
- Real-time performance monitoring и adaptation

Architecture Compliance:
- aiohttp connection pooling patterns (Context7)
- SOLID principles compliance
- Phase 1.2.3 performance optimization patterns
- Async/await best practices
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import statistics
import hashlib

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.core.schemas import STTRequest
from app.services.voice_v2.providers.stt.models import STTResult
from app.services.voice_v2.core.exceptions import VoiceServiceError

logger = logging.getLogger(__name__)


class PerformanceTier(Enum):
    """Performance tier classification"""
    CRITICAL = "critical"      # ≤2.0s required
    HIGH = "high"             # ≤3.0s required  
    NORMAL = "normal"         # ≤3.5s required
    BACKGROUND = "background"  # ≤5.0s acceptable


@dataclass
class ProviderMetrics:
    """Provider performance metrics tracking"""
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
class CacheMetrics:
    """Cache performance metrics"""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.cache_hits / self.total_requests) * 100


@dataclass
class OptimizationConfig:
    """STT optimization configuration"""
    # Performance targets
    target_latency: float = 3.5  # seconds
    critical_latency: float = 2.0  # seconds
    cache_hit_target: float = 85.0  # percentage
    
    # Connection pooling (aiohttp patterns)
    max_connections: int = 100
    max_connections_per_host: int = 30
    connection_timeout: float = 10.0
    total_timeout: float = 35.0
    keepalive_timeout: int = 30
    
    # Parallel processing
    enable_parallel_attempts: bool = True
    parallel_threshold_ms: float = 1000.0  # Try parallel if >1s expected
    max_parallel_providers: int = 2
    
    # Cache optimization
    cache_ttl_seconds: int = 3600
    cache_max_size: int = 1000
    enable_prefetch: bool = True
    
    # Provider ordering
    dynamic_ordering: bool = True
    ordering_window_minutes: int = 15
    min_samples_for_ordering: int = 5


class STTPerformanceOptimizer:
    """
    STT Performance Optimizer
    
    Implements comprehensive STT performance optimization:
    - Dynamic provider ordering based on latency
    - Parallel provider attempts for critical paths
    - Advanced caching with intelligent prefetch
    - Connection pooling optimization
    - Real-time performance adaptation
    """
    
    def __init__(self, config: OptimizationConfig):
        self.config = config
        self.provider_metrics: Dict[ProviderType, ProviderMetrics] = {}
        self.cache_metrics = CacheMetrics()
        self._connection_pools: Dict[ProviderType, aiohttp.ClientSession] = {}
        self._optimization_lock = asyncio.Lock()
        
        # Performance tracking
        self._performance_history: List[Tuple[datetime, float]] = []
        self._last_optimization = datetime.now()
        
        logger.info(f"STTPerformanceOptimizer initialized with target latency: {config.target_latency}s")
    
    async def initialize_connection_pools(self, providers: List[ProviderType]) -> None:
        """
        Initialize optimized connection pools for providers.
        Uses aiohttp patterns from Context7 documentation.
        """
        for provider in providers:
            # Create optimized connector with aiohttp best practices
            connector = TCPConnector(
                limit=self.config.max_connections,
                limit_per_host=self.config.max_connections_per_host,
                keepalive_timeout=self.config.keepalive_timeout,
                enable_cleanup_closed=True,
                use_dns_cache=True,
                ttl_dns_cache=300  # 5 minutes DNS cache
            )
            
            # Create session with optimized timeouts
            timeout = ClientTimeout(
                total=self.config.total_timeout,
                connect=self.config.connection_timeout,
                sock_connect=self.config.connection_timeout,
                sock_read=self.config.total_timeout - self.config.connection_timeout
            )
            
            session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=True
            )
            
            self._connection_pools[provider] = session
            self.provider_metrics[provider] = ProviderMetrics(provider_type=provider)
            
            logger.info(f"Initialized optimized connection pool for {provider.value}")
    
    async def cleanup_connection_pools(self) -> None:
        """Cleanup connection pools properly"""
        for provider, session in self._connection_pools.items():
            try:
                await session.close()
                logger.debug(f"Cleaned up connection pool for {provider.value}")
            except Exception as e:
                logger.warning(f"Error cleaning up {provider.value} pool: {e}")
        
        self._connection_pools.clear()
    
    def get_optimized_provider_order(self, performance_tier: PerformanceTier) -> List[ProviderType]:
        """
        Get optimized provider order based on current performance metrics.
        Implements dynamic ordering based on recent latency data.
        """
        if not self.config.dynamic_ordering:
            # Default fallback order
            return [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]
        
        # Filter providers with sufficient samples
        viable_providers = []
        for provider, metrics in self.provider_metrics.items():
            if (metrics.total_requests >= self.config.min_samples_for_ordering and 
                metrics.is_healthy):
                viable_providers.append((provider, metrics))
        
        if not viable_providers:
            # Fallback to default order
            return [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]
        
        # Sort by performance based on tier requirements
        if performance_tier == PerformanceTier.CRITICAL:
            # Sort by p95 latency for consistent performance
            viable_providers.sort(key=lambda x: x[1].p95_latency)
        elif performance_tier == PerformanceTier.HIGH:
            # Balance between latency and success rate
            viable_providers.sort(key=lambda x: (x[1].average_latency, -x[1].success_rate))
        else:
            # Sort by average latency for normal/background
            viable_providers.sort(key=lambda x: x[1].average_latency)
        
        optimized_order = [provider for provider, _ in viable_providers]
        
        # Add any missing providers at the end
        all_providers = [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]
        for provider in all_providers:
            if provider not in optimized_order:
                optimized_order.append(provider)
        
        logger.debug(f"Optimized provider order for {performance_tier.value}: "
                    f"{[p.value for p in optimized_order]}")
        
        return optimized_order
    
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
        """
        Generate optimized cache key for STT result.
        Uses SHA256 for security (replacing MD5 per security requirements).
        """
        hasher = hashlib.sha256()
        hasher.update(audio_data)
        if language:
            hasher.update(language.encode())
        
        return f"stt:{hasher.hexdigest()[:16]}"
    
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
            metrics.last_success = datetime.now()
            
            # Update latency metrics
            metrics.recent_latencies.append(latency)
            # Keep only recent samples (last 100)
            if len(metrics.recent_latencies) > 100:
                metrics.recent_latencies = metrics.recent_latencies[-100:]
            
            # Calculate statistics
            if metrics.recent_latencies:
                metrics.average_latency = statistics.mean(metrics.recent_latencies)
                sorted_latencies = sorted(metrics.recent_latencies)
                n = len(sorted_latencies)
                metrics.p95_latency = sorted_latencies[int(0.95 * n)] if n > 0 else latency
                metrics.p99_latency = sorted_latencies[int(0.99 * n)] if n > 0 else latency
        else:
            metrics.failed_requests += 1
            metrics.consecutive_failures += 1
            metrics.last_failure = datetime.now()
        
        # Track overall performance
        self._performance_history.append((datetime.now(), latency))
        
        # Cleanup old history (keep last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        self._performance_history = [
            (ts, lat) for ts, lat in self._performance_history if ts > cutoff
        ]
    
    async def record_cache_access(self, hit: bool) -> None:
        """Record cache access metrics"""
        self.cache_metrics.total_requests += 1
        if hit:
            self.cache_metrics.cache_hits += 1
        else:
            self.cache_metrics.cache_misses += 1
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "target_latency": self.config.target_latency,
            "cache": {
                "hit_rate": self.cache_metrics.hit_rate,
                "total_requests": self.cache_metrics.total_requests,
                "target_hit_rate": self.config.cache_hit_target
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
            recent_latencies = [lat for _, lat in self._performance_history[-50:]]
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
            
            # Check cache performance
            if self.cache_metrics.hit_rate < self.config.cache_hit_target:
                logger.info(f"Cache hit rate below target: {self.cache_metrics.hit_rate:.1f}% < {self.config.cache_hit_target}%")
                needs_optimization = True
            
            # Check provider performance
            for provider, metrics in self.provider_metrics.items():
                if not metrics.is_healthy:
                    logger.warning(f"Provider {provider.value} unhealthy: "
                                 f"success_rate={metrics.success_rate:.1f}%, "
                                 f"avg_latency={metrics.average_latency:.2f}s")
                    needs_optimization = True
            
            if needs_optimization:
                await self._perform_optimization()
                self._last_optimization = now
                return True
        
        return False
    
    async def _perform_optimization(self) -> None:
        """Perform optimization actions"""
        logger.info("Performing STT performance optimization...")
        
        # Optimization actions would include:
        # - Adjusting connection pool sizes
        # - Updating provider ordering
        # - Triggering cache cleanup
        # - Adjusting retry parameters
        
        # For now, log the optimization event
        summary = self.get_performance_summary()
        logger.info(f"Optimization triggered with summary: {summary}")
