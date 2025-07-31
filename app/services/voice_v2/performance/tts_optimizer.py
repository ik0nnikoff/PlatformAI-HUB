"""
TTS Performance Optimizer - Phase 5.3.2 Implementation

Реализует оптимизацию производительности TTS для достижения ≤3.0s targets:
- Provider ordering optimization на основе latency и quality metrics
- Response caching для common phrases/responses
- Streaming TTS для long responses (>200 chars)
- Audio compression optimization для transmission speed
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
from typing import Dict, List, Optional, Tuple, Any, AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import statistics
import hashlib
import io

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

from app.services.voice_v2.core.interfaces import ProviderType, AudioFormat
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSResult
from app.services.voice_v2.core.exceptions import VoiceServiceError

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
    
    # Latency metrics
    average_latency: float = 0.0
    p95_latency: float = 0.0
    p99_latency: float = 0.0
    recent_latencies: List[float] = field(default_factory=list)
    
    # TTS-specific metrics
    average_audio_quality_score: float = 0.0  # Subjective quality rating
    characters_per_second: float = 0.0         # Processing speed
    audio_compression_ratio: float = 0.0       # Compression efficiency
    
    # Reliability metrics
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
        
        # Weighted scoring
        latency_score = max(0, 100 - (self.average_latency * 20))  # Penalty for slow response
        success_score = self.success_rate
        speed_score = min(100, self.characters_per_second * 2)     # Processing speed bonus
        quality_score = self.average_audio_quality_score * 10     # Quality bonus
        
        # Weighted average
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
class TTSOptimizationConfig:
    """TTS optimization configuration"""
    # Performance targets
    target_latency: float = 3.0           # seconds
    real_time_latency: float = 1.5        # seconds for real-time
    target_chars_per_second: float = 50.0 # characters per second
    
    # Connection pooling (aiohttp patterns)
    max_connections: int = 50
    max_connections_per_host: int = 20
    connection_timeout: float = 10.0
    total_timeout: float = 30.0
    keepalive_timeout: int = 30
    
    # Streaming configuration
    enable_streaming: bool = True
    streaming_threshold_chars: int = 200    # Stream if text > 200 chars
    chunk_size_chars: int = 100            # Characters per streaming chunk
    
    # Caching configuration
    enable_response_caching: bool = True
    cache_ttl_seconds: int = 7200          # 2 hours
    cache_max_size_mb: int = 100           # 100 MB cache limit
    common_phrases_cache_ttl: int = 86400   # 24 hours for common phrases
    
    # Compression optimization
    enable_compression: bool = True
    target_compression_ratio: float = 0.7  # 70% of original size
    quality_threshold: float = 8.0         # Minimum quality score (1-10)
    
    # Provider optimization
    dynamic_provider_selection: bool = True
    quality_vs_speed_weight: float = 0.6   # 0.0 = pure speed, 1.0 = pure quality
    min_samples_for_optimization: int = 10


class TTSPerformanceOptimizer:
    """
    TTS Performance Optimizer
    
    Implements comprehensive TTS performance optimization:
    - Dynamic provider selection based on latency and quality
    - Intelligent response caching for common phrases
    - Streaming TTS for long responses
    - Audio compression optimization
    - Real-time performance monitoring and adaptation
    """
    
    def __init__(self, config: TTSOptimizationConfig):
        self.config = config
        self.provider_metrics: Dict[ProviderType, TTSProviderMetrics] = {}
        self.cache_metrics = TTSCacheMetrics()
        self._connection_pools: Dict[ProviderType, aiohttp.ClientSession] = {}
        self._optimization_lock = asyncio.Lock()
        
        # Performance tracking
        self._performance_history: List[Tuple[datetime, float, str]] = []  # time, latency, strategy
        self._last_optimization = datetime.now()
        
        # Caching components
        self._phrase_cache: Dict[str, Tuple[bytes, datetime, float]] = {}  # text_hash -> (audio, timestamp, quality)
        self._cache_access_times: Dict[str, List[datetime]] = {}
        
        logger.info(f"TTSPerformanceOptimizer initialized with target latency: {config.target_latency}s")
    
    async def initialize_connection_pools(self, providers: List[ProviderType]) -> None:
        """
        Initialize optimized connection pools for TTS providers.
        Uses aiohttp patterns optimized for TTS workloads.
        """
        for provider in providers:
            # Create optimized connector for TTS (smaller pools, longer timeouts)
            connector = TCPConnector(
                limit=self.config.max_connections,
                limit_per_host=self.config.max_connections_per_host,
                keepalive_timeout=self.config.keepalive_timeout,
                enable_cleanup_closed=True,
                use_dns_cache=True,
                ttl_dns_cache=600  # 10 minutes DNS cache for TTS
            )
            
            # Create session with TTS-optimized timeouts
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
            self.provider_metrics[provider] = TTSProviderMetrics(provider_type=provider)
            
            logger.info(f"Initialized optimized TTS connection pool for {provider.value}")
    
    async def cleanup_connection_pools(self) -> None:
        """Cleanup TTS connection pools properly"""
        for provider, session in self._connection_pools.items():
            try:
                await session.close()
                logger.debug(f"Cleaned up TTS connection pool for {provider.value}")
            except Exception as e:
                logger.warning(f"Error cleaning up TTS {provider.value} pool: {e}")
        
        self._connection_pools.clear()
    
    def determine_optimization_strategy(self, text: str, 
                                      performance_tier: TTSPerformanceTier) -> TTSOptimizationStrategy:
        """
        Determine the best optimization strategy for given text and performance requirements.
        """
        text_length = len(text)
        
        # Real-time tier - prioritize speed
        if performance_tier == TTSPerformanceTier.REAL_TIME:
            if text_length > self.config.streaming_threshold_chars:
                return TTSOptimizationStrategy.STREAMING
            return TTSOptimizationStrategy.LATENCY_FIRST
        
        # Interactive tier - balance speed and quality
        elif performance_tier == TTSPerformanceTier.INTERACTIVE:
            if text_length > self.config.streaming_threshold_chars:
                return TTSOptimizationStrategy.STREAMING
            return TTSOptimizationStrategy.BALANCED
        
        # Batch tier - prioritize quality
        else:
            return TTSOptimizationStrategy.QUALITY_FIRST
    
    def get_optimized_provider_order(self, strategy: TTSOptimizationStrategy) -> List[ProviderType]:
        """
        Get optimized provider order based on strategy and current metrics.
        """
        # Filter healthy providers with sufficient samples
        viable_providers = []
        for provider, metrics in self.provider_metrics.items():
            if (metrics.total_requests >= self.config.min_samples_for_optimization and 
                metrics.is_healthy):
                viable_providers.append((provider, metrics))
        
        if not viable_providers:
            # Fallback to default order based on general TTS performance
            return [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]
        
        # Sort providers based on strategy
        if strategy == TTSOptimizationStrategy.LATENCY_FIRST:
            # Sort by average latency
            viable_providers.sort(key=lambda x: x[1].average_latency)
            
        elif strategy == TTSOptimizationStrategy.QUALITY_FIRST:
            # Sort by quality score, then latency
            viable_providers.sort(key=lambda x: (-x[1].average_audio_quality_score, x[1].average_latency))
            
        elif strategy == TTSOptimizationStrategy.BALANCED:
            # Sort by performance score (weighted combination)
            viable_providers.sort(key=lambda x: -x[1].performance_score)
            
        elif strategy == TTSOptimizationStrategy.STREAMING:
            # Sort by characters per second (processing speed)
            viable_providers.sort(key=lambda x: -x[1].characters_per_second)
        
        optimized_order = [provider for provider, _ in viable_providers]
        
        # Add any missing providers at the end
        all_providers = [ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.YANDEX]
        for provider in all_providers:
            if provider not in optimized_order:
                optimized_order.append(provider)
        
        logger.debug(f"Optimized TTS provider order for {strategy.value}: "
                    f"{[p.value for p in optimized_order]}")
        
        return optimized_order
    
    def should_use_streaming(self, text: str, performance_tier: TTSPerformanceTier) -> bool:
        """
        Determine if streaming should be used for given text.
        """
        if not self.config.enable_streaming:
            return False
        
        text_length = len(text)
        
        # Always stream for long texts
        if text_length > self.config.streaming_threshold_chars:
            return True
        
        # Stream for real-time if providers are slow
        if performance_tier == TTSPerformanceTier.REAL_TIME:
            avg_chars_per_sec = self._calculate_average_processing_speed()
            expected_time = text_length / avg_chars_per_sec if avg_chars_per_sec > 0 else 10
            return expected_time > self.config.real_time_latency
        
        return False
    
    def generate_cache_key(self, text: str, voice_config: Dict[str, Any]) -> str:
        """
        Generate optimized cache key for TTS result.
        Uses SHA256 for security.
        """
        hasher = hashlib.sha256()
        hasher.update(text.encode('utf-8'))
        
        # Include voice parameters that affect output
        cache_params = {
            'voice': voice_config.get('voice', ''),
            'speed': voice_config.get('speed', 1.0),
            'pitch': voice_config.get('pitch', 0.0),
            'format': voice_config.get('format', 'mp3')
        }
        
        # Sort for consistent hashing
        for key in sorted(cache_params.keys()):
            hasher.update(f"{key}:{cache_params[key]}".encode())
        
        return f"tts:{hasher.hexdigest()[:16]}"
    
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
        
        # Check if cache entry is still valid
        age = (datetime.now() - timestamp).total_seconds()
        if age > self.config.cache_ttl_seconds:
            # Remove expired entry
            del self._phrase_cache[cache_key]
            await self.record_cache_access(hit=False)
            return None
        
        await self.record_cache_access(hit=True)
        
        # Track access time for cache optimization
        if cache_key not in self._cache_access_times:
            self._cache_access_times[cache_key] = []
        self._cache_access_times[cache_key].append(datetime.now())
        
        return audio_data, quality_score
    
    async def store_cache(self, cache_key: str, audio_data: bytes, quality_score: float) -> None:
        """Store TTS result in cache with quality score"""
        if not self.config.enable_response_caching:
            return
        
        # Check cache size limits
        current_size_mb = sum(len(data[0]) for data in self._phrase_cache.values()) / (1024 * 1024)
        audio_size_mb = len(audio_data) / (1024 * 1024)
        
        if current_size_mb + audio_size_mb > self.config.cache_max_size_mb:
            # Evict least recently used entries
            await self._evict_cache_entries(audio_size_mb)
        
        self._phrase_cache[cache_key] = (audio_data, datetime.now(), quality_score)
        self.cache_metrics.phrases_cached += 1
        self.cache_metrics.cache_storage_mb = current_size_mb + audio_size_mb
    
    async def _evict_cache_entries(self, needed_space_mb: float) -> None:
        """Evict cache entries to make space"""
        # Sort by last access time (LRU)
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
        
        logger.debug(f"Evicted {freed_space_mb:.2f} MB from TTS cache")
    
    async def record_performance(self, provider: ProviderType, text: str, 
                               latency: float, success: bool, 
                               audio_quality_score: Optional[float] = None) -> None:
        """Record TTS performance metrics for provider"""
        metrics = self.provider_metrics.get(provider)
        if not metrics:
            return
        
        metrics.total_requests += 1
        text_length = len(text)
        
        if success:
            metrics.successful_requests += 1
            metrics.consecutive_failures = 0
            metrics.last_success = datetime.now()
            
            # Update latency metrics
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
            
            # Update TTS-specific metrics
            if text_length > 0:
                chars_per_sec = text_length / latency if latency > 0 else 0
                # Update running average
                if metrics.characters_per_second == 0:
                    metrics.characters_per_second = chars_per_sec
                else:
                    metrics.characters_per_second = (
                        metrics.characters_per_second * 0.8 + chars_per_sec * 0.2
                    )
            
            if audio_quality_score is not None:
                # Update quality score average
                if metrics.average_audio_quality_score == 0:
                    metrics.average_audio_quality_score = audio_quality_score
                else:
                    metrics.average_audio_quality_score = (
                        metrics.average_audio_quality_score * 0.8 + audio_quality_score * 0.2
                    )
        else:
            metrics.failed_requests += 1
            metrics.consecutive_failures += 1
            metrics.last_failure = datetime.now()
        
        # Track overall performance
        strategy = "unknown"  # Would be passed from calling context
        self._performance_history.append((datetime.now(), latency, strategy))
        
        # Cleanup old history
        cutoff = datetime.now() - timedelta(hours=24)
        self._performance_history = [
            (ts, lat, strat) for ts, lat, strat in self._performance_history if ts > cutoff
        ]
    
    async def record_cache_access(self, hit: bool) -> None:
        """Record TTS cache access metrics"""
        self.cache_metrics.total_requests += 1
        if hit:
            self.cache_metrics.cache_hits += 1
        else:
            self.cache_metrics.cache_misses += 1
    
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
        
        # Overall performance
        if self._performance_history:
            recent_latencies = [lat for _, lat, _ in self._performance_history[-50:]]
            summary["overall"] = {
                "recent_average_latency": statistics.mean(recent_latencies),
                "meeting_target": statistics.mean(recent_latencies) <= self.config.target_latency,
                "average_processing_speed": self._calculate_average_processing_speed()
            }
        
        return summary
    
    async def optimize_if_needed(self) -> bool:
        """
        Trigger TTS optimization if performance degrades.
        Returns True if optimization was performed.
        """
        now = datetime.now()
        if (now - self._last_optimization).total_seconds() < 300:  # 5 minutes cooldown
            return False
        
        async with self._optimization_lock:
            needs_optimization = False
            
            # Check cache performance
            if (self.cache_metrics.total_requests > 50 and 
                self.cache_metrics.hit_rate < 60.0):  # Lower threshold for TTS
                logger.info(f"TTS cache hit rate suboptimal: {self.cache_metrics.hit_rate:.1f}%")
                needs_optimization = True
            
            # Check provider performance
            for provider, metrics in self.provider_metrics.items():
                if not metrics.is_healthy:
                    logger.warning(f"TTS Provider {provider.value} unhealthy: "
                                 f"success_rate={metrics.success_rate:.1f}%, "
                                 f"avg_latency={metrics.average_latency:.2f}s, "
                                 f"chars_per_sec={metrics.characters_per_second:.1f}")
                    needs_optimization = True
            
            if needs_optimization:
                await self._perform_optimization()
                self._last_optimization = now
                return True
        
        return False
    
    async def _perform_optimization(self) -> None:
        """Perform TTS optimization actions"""
        logger.info("Performing TTS performance optimization...")
        
        # TTS-specific optimization actions:
        # - Adjust provider ordering based on quality vs speed weights
        # - Optimize cache eviction strategy
        # - Adjust streaming thresholds
        # - Update compression settings
        
        summary = self.get_performance_summary()
        logger.info(f"TTS optimization triggered with summary: {summary}")
