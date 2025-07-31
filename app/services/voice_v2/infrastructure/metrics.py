"""
Voice V2 Metrics Collection System - Simplified Version

Упрощенная система сбора метрик с модульной архитектурой.
Reduced from 650+ to ~350 lines через extraction специализированных модулей.

Architecture:
- metrics_models: Data models and enums
- metrics_backends: Storage backend implementations
- metrics: Core collector and orchestration
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Any, Callable, Union
from concurrent.futures import ThreadPoolExecutor

from ..core.interfaces import MetricsCollector
from .metrics_models import MetricRecord, MetricType, MetricPriority
from .metrics_backends import MetricsBackendInterface, MemoryMetricsBackend, RedisMetricsBackend, MetricsBuffer

logger = logging.getLogger(__name__)


class VoiceMetricsCollector(MetricsCollector):
    """
    Simplified Voice Metrics Collector

    Основная ответственность: координация сбора метрик
    Делегирует storage в backends, buffering в MetricsBuffer
    """

    def __init__(self,
                 backend: MetricsBackendInterface = None,
                 buffer_size: int = 100,
                 flush_interval: float = 5.0):
        super().__init__()

        # Backend and buffering
        self._backend = backend or MemoryMetricsBackend()
        self._buffer = MetricsBuffer(buffer_size, flush_interval)
        self._buffer.set_backend(self._backend)

        # State tracking
        self._active = False
        self._background_tasks: List[asyncio.Task] = []

        # Performance optimization
        self._thread_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="metrics")

        logger.info("VoiceMetricsCollector initialized with simplified architecture")

    async def start(self) -> None:
        """Start metrics collection"""
        if self._active:
            return

        self._active = True
        await self._buffer.start_auto_flush()
        logger.info("Metrics collection started")

    async def stop(self) -> None:
        """Stop metrics collection"""
        if not self._active:
            return

        self._active = False

        # Stop buffer flushing
        await self._buffer.stop_auto_flush()

        # Cancel background tasks
        for task in self._background_tasks:
            task.cancel()

        # Wait for tasks completion
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        # Shutdown thread pool
        self._thread_pool.shutdown(wait=True)

        logger.info("Metrics collection stopped")

    # Core metric collection methods

    async def increment_counter(self, name: str, value: float = 1.0, **tags) -> None:
        """Increment counter metric"""
        await self._record_metric(
            name=name,
            value=value,
            metric_type=MetricType.COUNTER,
            priority=MetricPriority.HIGH,
            tags=tags
        )

    async def record_gauge(self, name: str, value: float, **tags) -> None:
        """Record gauge metric"""
        await self._record_metric(
            name=name,
            value=value,
            metric_type=MetricType.GAUGE,
            priority=MetricPriority.NORMAL,
            tags=tags
        )

    async def record_timer(self, name: str, duration_ms: float, **tags) -> None:
        """Record timer metric"""
        await self._record_metric(
            name=name,
            value=duration_ms,
            metric_type=MetricType.TIMER,
            priority=MetricPriority.HIGH,
            tags=tags
        )

    async def record_histogram(self, name: str, value: float, **tags) -> None:
        """Record histogram metric"""
        await self._record_metric(
            name=name,
            value=value,
            metric_type=MetricType.HISTOGRAM,
            priority=MetricPriority.NORMAL,
            tags=tags
        )

    async def _record_metric(self,
                             name: str,
                             value: float,
                             metric_type: MetricType,
                             priority: MetricPriority,
                             tags: Dict[str, str]) -> None:
        """Internal method to record metric"""
        if not self._active:
            return

        try:
            record = MetricRecord(
                name=name,
                value=value,
                timestamp=time.time(),
                tags=tags,
                priority=priority
            )

            await self._buffer.add(record)

        except Exception as e:
            logger.error("Failed to record metric %s: %s", name, e)

    # Voice-specific convenience methods

    async def record_stt_duration(self, provider: str, duration_ms: float,
                                  success: bool = True, **extra_tags) -> None:
        """Record STT processing duration"""
        tags = {
            "provider": provider,
            "status": "success" if success else "error",
            **extra_tags
        }
        await self.record_timer("voice.stt.duration_ms", duration_ms, **tags)

    async def record_tts_duration(self, provider: str, duration_ms: float,
                                  success: bool = True, **extra_tags) -> None:
        """Record TTS processing duration"""
        tags = {
            "provider": provider,
            "status": "success" if success else "error",
            **extra_tags
        }
        await self.record_timer("voice.tts.duration_ms", duration_ms, **tags)

    async def record_voice_request(self, request_type: str, success: bool = True,
                                   **extra_tags) -> None:
        """Record voice request"""
        tags = {
            "type": request_type,
            "status": "success" if success else "error",
            **extra_tags
        }
        await self.increment_counter("voice.requests", 1.0, **tags)

    async def record_cache_hit(self, cache_type: str, hit: bool = True, **extra_tags) -> None:
        """Record cache hit/miss"""
        tags = {
            "cache_type": cache_type,
            "result": "hit" if hit else "miss",
            **extra_tags
        }
        await self.increment_counter("voice.cache", 1.0, **tags)

    async def record_fallback(self, from_provider: str, to_provider: str, **extra_tags) -> None:
        """Record provider fallback"""
        tags = {
            "from_provider": from_provider,
            "to_provider": to_provider,
            **extra_tags
        }
        await self.increment_counter("voice.fallback", 1.0, **tags)

    # Metrics retrieval and summary methods (simplified from original)

    async def get_metrics_summary(self,
                                  agent_id: Optional[str] = None,
                                  time_window_minutes: int = 60) -> Dict[str, Any]:
        """Get metrics summary для monitoring"""
        end_time = time.time()
        start_time = end_time - (time_window_minutes * 60)

        metrics = await self._backend.get_metrics(
            name_pattern="voice.*",
            start_time=start_time,
            end_time=end_time
        )

        # Filter by agent if specified
        if agent_id:
            metrics = [m for m in metrics if m.tags.get("agent_id") == agent_id]

        # Initialize summary structure
        summary = self._init_summary_structure()

        # Collect raw data from metrics
        raw_data = self._collect_raw_metrics_data(metrics)

        # Calculate aggregated values
        summary = self._calculate_aggregated_summary(summary, raw_data)

        return summary

    def _init_summary_structure(self) -> Dict[str, Any]:
        """Initialize summary structure with default values"""
        return {
            "total_requests": 0,
            "success_rate": 0.0,
            "average_stt_duration": 0.0,
            "average_tts_duration": 0.0,
            "provider_distribution": defaultdict(int),
            "fallback_rate": 0.0,
            "cache_hit_rate": 0.0
        }

    def _collect_raw_metrics_data(self, metrics: List) -> Dict[str, Any]:
        """Collect raw data from metrics for processing"""
        raw_data = {
            "request_counts": defaultdict(int),
            "success_counts": defaultdict(int),
            "stt_durations": [],
            "tts_durations": [],
            "fallback_count": 0,
            "cache_hits": 0,
            "cache_total": 0
        }

        for metric in metrics:
            self._process_single_metric(metric, raw_data)

        return raw_data

    def _process_single_metric(self, metric, raw_data: Dict[str, Any]) -> None:
        """Process a single metric and update raw data"""
        name = metric.name
        tags = metric.tags

        if "cache" in name:
            raw_data["cache_total"] += metric.value
            if tags.get("result") == "hit":
                raw_data["cache_hits"] += metric.value

        elif "requests" in name:
            self._process_request_metric(metric, raw_data)

        elif "duration_ms" in name:
            self._process_duration_metric(metric, raw_data)

        elif "fallback" in name:
            raw_data["fallback_count"] += metric.value

    def _process_request_metric(self, metric, raw_data: Dict[str, Any]) -> None:
        """Process request-related metrics"""
        provider = metric.tags.get("provider", "unknown")
        raw_data["request_counts"][provider] += metric.value

        if metric.tags.get("status") == "success":
            raw_data["success_counts"][provider] += metric.value

    def _process_duration_metric(self, metric, raw_data: Dict[str, Any]) -> None:
        """Process duration-related metrics"""
        if "stt" in metric.name:
            raw_data["stt_durations"].append(metric.value)
        elif "tts" in metric.name:
            raw_data["tts_durations"].append(metric.value)

    def _calculate_aggregated_summary(
            self, summary: Dict[str, Any], raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate final aggregated values"""
        total_requests = sum(raw_data["request_counts"].values())
        total_success = sum(raw_data["success_counts"].values())

        summary["total_requests"] = total_requests

        # Calculate rates
        if total_requests > 0:
            summary["success_rate"] = total_success / total_requests
            summary["fallback_rate"] = raw_data["fallback_count"] / total_requests

        # Calculate durations
        if raw_data["stt_durations"]:
            summary["average_stt_duration"] = sum(
                raw_data["stt_durations"]) / len(raw_data["stt_durations"])

        if raw_data["tts_durations"]:
            summary["average_tts_duration"] = sum(
                raw_data["tts_durations"]) / len(raw_data["tts_durations"])

        # Calculate cache hit rate
        if raw_data["cache_total"] > 0:
            summary["cache_hit_rate"] = raw_data["cache_hits"] / raw_data["cache_total"]

        summary["provider_distribution"] = dict(raw_data["request_counts"])

        return summary

    async def health_check(self) -> Dict[str, Any]:
        """Health check for metrics system"""
        return {
            "status": "healthy" if self._active else "stopped",
            "backend_healthy": await self._backend.health_check(),
            "buffer_size": len(self._buffer.buffer) if hasattr(self._buffer, 'buffer') else 0,
            "background_tasks": len(self._background_tasks),
            "thread_pool_active": not self._thread_pool._shutdown
        }

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        return {
            "active": self._active,
            "backend_type": type(self._backend).__name__,
            "background_tasks_count": len(self._background_tasks),
            "thread_pool_active": not self._thread_pool._shutdown
        }

    async def flush_metrics(self) -> None:
        """Manually flush pending metrics"""
        await self._buffer.flush()

    def set_backend(self, backend: MetricsBackendInterface) -> None:
        """Change storage backend"""
        self._backend = backend
        self._buffer.set_backend(backend)
        logger.info("Metrics backend changed to %s", type(backend).__name__)


# Factory function for easy instantiation
def create_voice_metrics_collector(
    backend_type: str = "memory",
    redis_client=None,
    **kwargs
) -> VoiceMetricsCollector:
    """
    Factory function для создания metrics collector

    Args:
        backend_type: "memory" или "redis"
        redis_client: Redis client instance для redis backend
        **kwargs: Additional arguments for collector
    """
    if backend_type == "redis":
        if not redis_client:
            raise ValueError("Redis client required for redis backend")
        backend = RedisMetricsBackend(redis_client)
    else:
        backend = MemoryMetricsBackend()

    return VoiceMetricsCollector(backend=backend, **kwargs)
