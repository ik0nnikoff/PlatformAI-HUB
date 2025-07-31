"""
TTS Performance Optimizer - Phase 5.3.2 Implementation

Реализует оптимизацию производительности TTS для достижения ≤3.0s targets:
- Provider ordering optimization на основе latency и quality metrics
- Response caching для common phrases/responses
- Streaming TTS для long responses (>200 chars)
- Audio compression optimization для transmission speed
- Real-time performance monitoring и adaptation
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import statistics

from app.services.voice_v2.core.interfaces import ProviderType
from app.services.voice_v2.performance.base_optimizer import (
    BasePerformanceOptimizer, BaseOptimizationConfig
)
from app.services.voice_v2.performance.utils import CacheKeyGenerator

logger = logging.getLogger(__name__)
class TTSPerformanceTier(Enum):
    """TTS Performance tier classification"""
    REAL_TIME = "real_time"    # ≤1.5s required (immediate response)
    INTERACTIVE = "interactive" # ≤3.0s required (conversational)
    BATCH = "batch"            # ≤5.0s acceptable (background processing)
class TTSOptimizationStrategy(Enum):
    """TTS optimization strategies"""
    LATENCY_FIRST = "latency_first"      # Prioritize speed
    QUALITY_FIRST = "quality_first"      # Prioritize audio quality
    BALANCED = "balanced"                # Balance speed and quality
    STREAMING = "streaming"              # Use streaming for long text
@dataclass
class TTSProviderMetrics:
    """TTS Provider performance metrics tracking"""
    provider_type: ProviderType
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    average_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0
    recent_latencies: List[float] = field(default_factory=list)

    average_audio_quality_score: float = 0.0  # Subjective quality rating
    characters_per_second: float = 0.0         # Processing speed
    audio_compression_ratio: float = 0.0       # Compression efficiency

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
        """Check if provider is healthy for TTS"""
        return (
            self.success_rate >= 95.0 and
            self.consecutive_failures < 3 and
            self.average_latency <= 4.0 and
            self.characters_per_second >= 20.0  # Minimum processing speed
        )

    @property
    def performance_score(self) -> float:
        """Calculate overall performance score (0-100)"""
        if self.total_requests == 0:
            return 0.0

        latency_score = max(0, 100 - (self.average_latency * 20))  # Penalty for slow response
        success_score = self.success_rate
        speed_score = min(100, self.characters_per_second * 2)     # Processing speed bonus
        quality_score = self.average_audio_quality_score * 10     # Quality bonus

        return (
            latency_score * 0.4 +
            success_score * 0.3 +
            speed_score * 0.2 +
            quality_score * 0.1
        )
@dataclass
class TTSCacheMetrics:
    """TTS Cache performance metrics"""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    phrases_cached: int = 0
    cache_storage_mb: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.cache_hits / self.total_requests) * 100

    @property
    def cache_efficiency(self) -> float:
        """Calculate cache efficiency (hits per MB)"""
        if self.cache_storage_mb == 0:
            return 0.0
        return self.cache_hits / self.cache_storage_mb
@dataclass
class TTSOptimizationConfig(BaseOptimizationConfig):
    """TTS optimization configuration"""
    # TTS-specific parameters (inherit base from BaseOptimizationConfig)
    real_time_latency: float = 1.5        # seconds for real-time
    target_chars_per_second: float = 50.0 # characters per second

    enable_streaming: bool = True
    streaming_threshold_chars: int = 200    # Stream if text > 200 chars
    chunk_size_chars: int = 100            # Characters per streaming chunk

    cache_max_size_mb: int = 100           # 100 MB cache limit
    common_phrases_cache_ttl: int = 86400   # 24 hours for common phrases

    enable_compression: bool = True
    target_compression_ratio: float = 0.7  # 70% of original size
    quality_threshold: float = 8.0         # Minimum quality score (1-10)

    dynamic_provider_selection: bool = True
    quality_vs_speed_weight: float = 0.6   # 0.0 = pure speed, 1.0 = pure quality
    min_samples_for_optimization: int = 10
class TTSPerformanceOptimizer(BasePerformanceOptimizer[TTSProviderMetrics]):
    """
    TTS Performance Optimizer

    Implements comprehensive TTS performance optimization:
    - Dynamic provider selection based on latency and quality
    - Intelligent response caching for common phrases
    - Streaming TTS for long responses
    - Audio compression optimization
    - Real-time performance monitoring and adaptation
    """

    def __init__(self, config: Optional[TTSOptimizationConfig] = None):
        """Initialize TTS optimizer with configuration"""
        if config is None:
            config = TTSOptimizationConfig()

        super().__init__(config)

        # TTS-specific cache
        self._phrase_cache: Dict[str, Tuple[bytes, datetime, float]] = {}  # text_hash -> (audio, timestamp, quality)
        self._cache_access_times: Dict[str, List[datetime]] = {}

        logger.info("TTSPerformanceOptimizer initialized with target latency: %ss", config.target_latency)

    def _create_provider_metrics(self, provider: ProviderType) -> TTSProviderMetrics:
        """Create TTS-specific provider metrics instance"""
        return TTSProviderMetrics(provider_type=provider)

    def determine_optimization_strategy(self, text: str,
                                      performance_tier: TTSPerformanceTier) -> TTSOptimizationStrategy:
        """
        Determine the best optimization strategy for given text and performance requirements.
        """
        text_length = len(text)

        if performance_tier == TTSPerformanceTier.REAL_TIME:
            if text_length > self.config.streaming_threshold_chars:
                return TTSOptimizationStrategy.STREAMING
            return TTSOptimizationStrategy.LATENCY_FIRST

        elif performance_tier == TTSPerformanceTier.INTERACTIVE:
            if text_length > self.config.streaming_threshold_chars:
                return TTSOptimizationStrategy.STREAMING
            return TTSOptimizationStrategy.BALANCED

        else:
            return TTSOptimizationStrategy.QUALITY_FIRST

    def get_optimized_provider_order(self, strategy: Optional[TTSOptimizationStrategy] = None, **kwargs) -> List[ProviderType]:
        """Get optimized provider order based on strategy and current metrics."""
        if strategy is None:
            strategy = TTSOptimizationStrategy.BALANCED

        viable_providers = self._get_viable_providers()

        if not viable_providers:
            return self._get_default_provider_order()

        sorted_providers = self._sort_providers_by_strategy(viable_providers, strategy)
        optimized_order = [provider for provider, _ in sorted_providers]

        complete_order = self._complete_provider_order(optimized_order)

        logger.debug("Optimized TTS provider order for %s: %s",
                    strategy.value, [p.value for p in complete_order])

        return complete_order

    def _sort_providers_by_strategy(self, providers: List[Tuple],
                                   strategy: TTSOptimizationStrategy) -> List[Tuple]:
        """Sort providers based on optimization strategy"""
        if strategy == TTSOptimizationStrategy.LATENCY_FIRST:
            return sorted(providers, key=lambda x: x[1].average_latency)
        elif strategy == TTSOptimizationStrategy.QUALITY_FIRST:
            return sorted(providers, key=lambda x: (-x[1].average_audio_quality_score, x[1].average_latency))
        elif strategy == TTSOptimizationStrategy.BALANCED:
            return sorted(providers, key=lambda x: -x[1].performance_score)
        elif strategy == TTSOptimizationStrategy.STREAMING:
            return sorted(providers, key=lambda x: -x[1].characters_per_second)
        return providers

    def should_use_streaming(self, text: str, performance_tier: TTSPerformanceTier) -> bool:
        """
        Determine if streaming should be used for given text.
        """
        if not self.config.enable_streaming:
            return False

        text_length = len(text)

        if text_length > self.config.streaming_threshold_chars:
            return True

        if performance_tier == TTSPerformanceTier.REAL_TIME:
            avg_chars_per_sec = self._calculate_average_processing_speed()
            expected_time = text_length / avg_chars_per_sec if avg_chars_per_sec > 0 else 10
            return expected_time > self.config.real_time_latency

        return False

    def generate_cache_key(self, text: str, voice_config: Dict[str, Any]) -> str:
        """Generate optimized cache key for TTS result"""
        return CacheKeyGenerator.generate_tts_key(text, voice_config)

    async def check_cache(self, cache_key: str) -> Optional[Tuple[bytes, float]]:
        """
        Check if TTS result is cached.
        Returns (audio_data, quality_score) if found.
        """
        if not self.config.enable_response_caching:
            return None

        cached_data = self._phrase_cache.get(cache_key)
        if not cached_data:
            await self.record_cache_access(hit=False)
            return None

        audio_data, timestamp, quality_score = cached_data

        age = (datetime.now() - timestamp).total_seconds()
        if age > self.config.cache_ttl_seconds:
            del self._phrase_cache[cache_key]
            await self.record_cache_access(hit=False)
            return None

        await self.record_cache_access(hit=True)

        if cache_key not in self._cache_access_times:
            self._cache_access_times[cache_key] = []
        self._cache_access_times[cache_key].append(datetime.now())

        return audio_data, quality_score

    async def store_cache(self, cache_key: str, audio_data: bytes, quality_score: float) -> None:
        """Store TTS result in cache with quality score"""
        if not self.config.enable_response_caching:
            return

        current_size_mb = sum(len(data[0]) for data in self._phrase_cache.values()) / (1024 * 1024)
        audio_size_mb = len(audio_data) / (1024 * 1024)

        if current_size_mb + audio_size_mb > self.config.cache_max_size_mb:
            await self._evict_cache_entries(audio_size_mb)

        self._phrase_cache[cache_key] = (audio_data, datetime.now(), quality_score)
        self.cache_metrics.phrases_cached += 1
        self.cache_metrics.cache_storage_mb = current_size_mb + audio_size_mb

    async def _evict_cache_entries(self, needed_space_mb: float) -> None:
        """Evict cache entries to make space"""
        entries_by_access = []
        for cache_key in self._phrase_cache.keys():
            last_access = max(self._cache_access_times.get(cache_key, [datetime.min]))
            entries_by_access.append((last_access, cache_key))

        entries_by_access.sort()  # Oldest first

        freed_space_mb = 0.0
        for _, cache_key in entries_by_access:
            if freed_space_mb >= needed_space_mb:
                break

            if cache_key in self._phrase_cache:
                audio_data, _, _ = self._phrase_cache[cache_key]
                freed_space_mb += len(audio_data) / (1024 * 1024)
                del self._phrase_cache[cache_key]

                if cache_key in self._cache_access_times:
                    del self._cache_access_times[cache_key]

        logger.debug("Evicted %.2f MB from TTS cache", freed_space_mb)

    async def record_performance(self, provider: ProviderType, latency: float,
                               success: bool, text: Optional[str] = None,
                               audio_quality_score: Optional[float] = None, **kwargs) -> None:
        """Record TTS performance metrics for provider"""
        metrics = self.provider_metrics.get(provider)
        if not metrics:
            return

        metrics.total_requests += 1
        text_length = len(text) if text else 0

        if success:
            await self._record_successful_performance(metrics, latency, text_length, audio_quality_score)
        else:
            self._record_failed_performance(metrics)

        await self._update_performance_history(latency, "tts")

    async def _record_successful_performance(self, metrics, latency: float, text_length: int,
                                           audio_quality_score: Optional[float]) -> None:
        """Record successful TTS performance"""
        metrics.successful_requests += 1
        metrics.consecutive_failures = 0
        metrics.last_success = datetime.now()

        self._update_latency_metrics(metrics, latency)

        if text_length > 0:
            self._update_characters_per_second(metrics, text_length, latency)

        if audio_quality_score is not None:
            self._update_audio_quality_score(metrics, audio_quality_score)

    def _record_failed_performance(self, metrics) -> None:
        """Record failed TTS performance"""
        metrics.failed_requests += 1
        metrics.consecutive_failures += 1
        metrics.last_failure = datetime.now()

    def _update_characters_per_second(self, metrics, text_length: int, latency: float) -> None:
        """Update characters per second metric"""
        chars_per_sec = text_length / latency if latency > 0 else 0
        if metrics.characters_per_second == 0:
            metrics.characters_per_second = chars_per_sec
        else:
            metrics.characters_per_second = (
                metrics.characters_per_second * 0.8 + chars_per_sec * 0.2
            )

    def _update_audio_quality_score(self, metrics, audio_quality_score: float) -> None:
        """Update audio quality score"""
        if metrics.average_audio_quality_score == 0:
            metrics.average_audio_quality_score = audio_quality_score
        else:
            metrics.average_audio_quality_score = (
                metrics.average_audio_quality_score * 0.8 + audio_quality_score * 0.2
            )

    def _calculate_average_processing_speed(self) -> float:
        """Calculate average TTS processing speed across all providers"""
        total_speed = 0.0
        provider_count = 0

        for metrics in self.provider_metrics.values():
            if metrics.characters_per_second > 0:
                total_speed += metrics.characters_per_second
                provider_count += 1

        return total_speed / provider_count if provider_count > 0 else 0.0

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive TTS performance summary"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "target_latency": self.config.target_latency,
            "cache": {
                "hit_rate": self.cache_metrics.hit_rate,
                "total_requests": self.cache_metrics.total_requests,
                "phrases_cached": self.cache_metrics.phrases_cached,
                "storage_mb": self.cache_metrics.cache_storage_mb,
                "efficiency": self.cache_metrics.cache_efficiency
            },
            "providers": {}
        }

        for provider, metrics in self.provider_metrics.items():
            summary["providers"][provider.value] = {
                "total_requests": metrics.total_requests,
                "success_rate": metrics.success_rate,
                "average_latency": metrics.average_latency,
                "p95_latency": metrics.p95_latency,
                "chars_per_second": metrics.characters_per_second,
                "audio_quality_score": metrics.average_audio_quality_score,
                "performance_score": metrics.performance_score,
                "is_healthy": metrics.is_healthy
            }

        if self._performance_history:
            recent_latencies = [lat for _, lat, _ in self._performance_history[-50:]]
            summary["overall"] = {
                "recent_average_latency": statistics.mean(recent_latencies),
                "meeting_target": statistics.mean(recent_latencies) <= self.config.target_latency,
                "average_processing_speed": self._calculate_average_processing_speed()
            }

        return summary
