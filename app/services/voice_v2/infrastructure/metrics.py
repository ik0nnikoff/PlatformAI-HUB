"""
Voice V2 Metrics Collection Infrastructure

Высокопроизводительная система сбора метрик для voice_v2 компонентов.
Performance target: ≤1ms/record (vs 1.85ms в reference system - 46% улучшение)

SOLID Principles:
- SRP: MetricsCollector только сбор метрик, MetricsStorage только хранение
- OCP: Легкое добавление новых metric types через registry
- LSP: MetricsBackend субклассы полностью взаимозаменяемы
- ISP: Раздельные интерфейсы для сбора, хранения, экспорта метрик
- DIP: Зависимости на абстракциях (MetricsBackendInterface)
"""

import asyncio
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Union
from concurrent.futures import ThreadPoolExecutor
import json

from ..core.interfaces import MetricsCollector
from ..core.schemas import VoiceOperationMetric


class MetricType(Enum):
    """Типы метрик voice_v2 системы"""
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"
    TIMER = "timer"


class MetricPriority(Enum):
    """Приоритеты метрик для performance optimization"""
    HIGH = 1    # Real-time metrics (latency, errors)
    MEDIUM = 2  # Performance metrics (throughput)
    LOW = 3     # Diagnostic metrics (detailed traces)


@dataclass
class MetricRecord:
    """Single metric record с оптимизированной структурой данных"""
    name: str
    value: Union[int, float]
    metric_type: MetricType
    priority: MetricPriority
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)
    extra_data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Сериализация для storage backends"""
        return {
            "name": self.name,
            "value": self.value,
            "type": self.metric_type.value,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "tags": self.tags,
            "extra_data": self.extra_data
        }


class MetricsBackendInterface:
    """Interface для storage backends (ISP compliance)"""
    
    async def store_metric(self, record: MetricRecord) -> None:
        """Store single metric record"""
        raise NotImplementedError
    
    async def store_batch(self, records: List[MetricRecord]) -> None:
        """Store batch of metrics for performance"""
        raise NotImplementedError
    
    async def get_metrics(self, 
                         name_pattern: str = "*",
                         start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[MetricRecord]:
        """Retrieve metrics by criteria"""
        raise NotImplementedError
    
    async def health_check(self) -> bool:
        """Check backend health"""
        raise NotImplementedError


class MemoryMetricsBackend(MetricsBackendInterface):
    """In-memory metrics storage для development/testing"""
    
    def __init__(self, max_records: int = 10000):
        self._max_records = max_records
        self._records: deque = deque(maxlen=max_records)
        self._lock = threading.Lock()
    
    async def store_metric(self, record: MetricRecord) -> None:
        """Store single metric в memory"""
        with self._lock:
            self._records.append(record)
    
    async def store_batch(self, records: List[MetricRecord]) -> None:
        """Store batch efficiently"""
        with self._lock:
            self._records.extend(records)
    
    async def get_metrics(self, 
                         name_pattern: str = "*",
                         start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[MetricRecord]:
        """Retrieve metrics by time range"""
        with self._lock:
            filtered_records = []
            for record in self._records:
                # Filter by time range
                if start_time and record.timestamp < start_time:
                    continue
                if end_time and record.timestamp > end_time:
                    continue
                # Simple pattern matching
                if name_pattern != "*" and name_pattern not in record.name:
                    continue
                filtered_records.append(record)
            return filtered_records
    
    async def health_check(self) -> bool:
        """Memory backend всегда healthy"""
        return True


class RedisMetricsBackend(MetricsBackendInterface):
    """Redis-based metrics storage с performance optimization"""
    
    def __init__(self, redis_client, key_prefix: str = "voice_v2_metrics"):
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._batch_size = 100
    
    async def store_metric(self, record: MetricRecord) -> None:
        """Store single metric в Redis"""
        key = f"{self._key_prefix}:{record.name}:{int(record.timestamp)}"
        await self._redis.setex(
            key, 
            86400,  # TTL 24 hours
            json.dumps(record.to_dict())
        )
    
    async def store_batch(self, records: List[MetricRecord]) -> None:
        """Batch storage с Redis pipeline для performance"""
        if not records:
            return
            
        async with self._redis.pipeline() as pipe:
            for record in records:
                key = f"{self._key_prefix}:{record.name}:{int(record.timestamp)}"
                pipe.setex(key, 86400, json.dumps(record.to_dict()))
            await pipe.execute()
    
    async def get_metrics(self, 
                         name_pattern: str = "*",
                         start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[MetricRecord]:
        """Retrieve metrics from Redis"""
        pattern = f"{self._key_prefix}:{name_pattern}:*"
        keys = await self._redis.keys(pattern)
        
        if not keys:
            return []
        
        # Get all metric data
        raw_data = await self._redis.mget(keys)
        metrics = []
        
        for data in raw_data:
            if data:
                try:
                    metric_dict = json.loads(data)
                    # Apply time filtering
                    timestamp = metric_dict["timestamp"]
                    if start_time and timestamp < start_time:
                        continue
                    if end_time and timestamp > end_time:
                        continue
                    
                    # Reconstruct MetricRecord
                    record = MetricRecord(
                        name=metric_dict["name"],
                        value=metric_dict["value"],
                        metric_type=MetricType(metric_dict["type"]),
                        priority=MetricPriority(metric_dict["priority"]),
                        timestamp=timestamp,
                        tags=metric_dict.get("tags", {}),
                        extra_data=metric_dict.get("extra_data", {})
                    )
                    metrics.append(record)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue  # Skip malformed records
        
        return sorted(metrics, key=lambda x: x.timestamp)
    
    async def health_check(self) -> bool:
        """Check Redis connectivity"""
        try:
            await self._redis.ping()
            return True
        except Exception:
            return False


class MetricsBuffer:
    """Буферизованный сбор метрик для performance optimization"""
    
    def __init__(self, 
                 max_size: int = 1000,
                 flush_interval: float = 1.0,
                 priority_thresholds: Dict[MetricPriority, int] = None):
        self._max_size = max_size
        self._flush_interval = flush_interval
        self._priority_thresholds = priority_thresholds or {
            MetricPriority.HIGH: 100,    # Flush high priority quickly
            MetricPriority.MEDIUM: 500,  # Medium priority batching
            MetricPriority.LOW: 1000     # Low priority full buffer
        }
        
        self._buffers: Dict[MetricPriority, List[MetricRecord]] = {
            priority: [] for priority in MetricPriority
        }
        self._lock = threading.Lock()
        self._flush_callbacks: List[Callable] = []
        self._last_flush = time.time()
    
    def add_metric(self, record: MetricRecord) -> None:
        """Add metric to priority buffer"""
        records_to_flush = None
        
        with self._lock:
            self._buffers[record.priority].append(record)
            
            # Check if we need immediate flush
            buffer_size = len(self._buffers[record.priority])
            threshold = self._priority_thresholds[record.priority]
            
            if buffer_size >= threshold:
                # Capture records for flush INSIDE the lock
                records_to_flush = self._buffers[record.priority].copy()
                self._buffers[record.priority].clear()
        
        # Execute flush callbacks OUTSIDE the lock to prevent deadlock
        if records_to_flush:
            for callback in self._flush_callbacks:
                try:
                    callback(records_to_flush)
                except Exception:
                    pass  # Ignore callback errors
    
    def add_flush_callback(self, callback: Callable[[List[MetricRecord]], None]) -> None:
        """Add callback для flush events"""
        self._flush_callbacks.append(callback)
    
    def flush_all(self) -> Dict[MetricPriority, List[MetricRecord]]:
        """Flush all buffers"""
        all_records = {}
        with self._lock:
            for priority in MetricPriority:
                all_records[priority] = self._buffers[priority].copy()
                self._buffers[priority].clear()
            self._last_flush = time.time()
        
        return all_records
    
    def should_flush(self) -> bool:
        """Check if time-based flush needed"""
        return time.time() - self._last_flush >= self._flush_interval


class VoiceMetricsCollector(MetricsCollector):
    """
    Главный collector метрик voice_v2 (SRP compliance)
    Performance target: ≤1ms per metric record
    """
    
    def __init__(self, 
                 backend: MetricsBackendInterface,
                 enable_buffering: bool = True,
                 thread_pool_size: int = 2):
        self._backend = backend
        self._enable_buffering = enable_buffering
        self._thread_pool = ThreadPoolExecutor(max_workers=thread_pool_size)
        
        # Performance optimization components
        self._buffer = MetricsBuffer() if enable_buffering else None
        self._metrics_registry: Dict[str, Callable] = {}
        self._background_tasks: List[asyncio.Task] = []
        
        # Setup background flushing
        if self._buffer:
            self._buffer.add_flush_callback(self._async_flush_callback)
            self._start_background_flush()
    
    async def record_stt_operation(self, 
                                  provider: str,
                                  duration_ms: float,
                                  success: bool,
                                  audio_length_sec: float,
                                  agent_id: str) -> None:
        """Record STT operation metrics"""
        start_time = time.perf_counter()
        
        # Core metrics
        await self._record_metric(
            name=f"voice.stt.duration_ms",
            value=duration_ms,
            metric_type=MetricType.HISTOGRAM,
            priority=MetricPriority.HIGH,
            tags={
                "provider": provider,
                "agent_id": agent_id,
                "success": str(success)
            }
        )
        
        # Success rate counter
        await self._record_metric(
            name=f"voice.stt.requests",
            value=1,
            metric_type=MetricType.COUNTER,
            priority=MetricPriority.MEDIUM,
            tags={
                "provider": provider,
                "status": "success" if success else "error"
            }
        )
        
        # Audio processing efficiency
        if audio_length_sec > 0:
            efficiency = duration_ms / (audio_length_sec * 1000)  # Processing time ratio
            await self._record_metric(
                name=f"voice.stt.efficiency_ratio",
                value=efficiency,
                metric_type=MetricType.GAUGE,
                priority=MetricPriority.LOW,
                tags={"provider": provider}
            )
        
        # Performance monitoring - target ≤1ms
        collection_time_ms = (time.perf_counter() - start_time) * 1000
        if collection_time_ms > 1.0:  # Alert if over target
            await self._record_metric(
                name="voice.metrics.collection_slow",
                value=collection_time_ms,
                metric_type=MetricType.HISTOGRAM,
                priority=MetricPriority.HIGH,
                tags={"operation": "stt_metrics"}
            )
    
    async def record_tts_operation(self,
                                  provider: str,
                                  duration_ms: float,
                                  success: bool,
                                  text_length: int,
                                  agent_id: str) -> None:
        """Record TTS operation metrics"""
        start_time = time.perf_counter()
        
        await self._record_metric(
            name=f"voice.tts.duration_ms",
            value=duration_ms,
            metric_type=MetricType.HISTOGRAM,
            priority=MetricPriority.HIGH,
            tags={
                "provider": provider,
                "agent_id": agent_id,
                "success": str(success)
            }
        )
        
        await self._record_metric(
            name=f"voice.tts.requests",
            value=1,
            metric_type=MetricType.COUNTER,
            priority=MetricPriority.MEDIUM,
            tags={
                "provider": provider,
                "status": "success" if success else "error"
            }
        )
        
        # Text processing efficiency
        if text_length > 0:
            chars_per_ms = text_length / max(duration_ms, 1)
            await self._record_metric(
                name=f"voice.tts.chars_per_ms",
                value=chars_per_ms,
                metric_type=MetricType.GAUGE,
                priority=MetricPriority.LOW,
                tags={"provider": provider}
            )
        
        # Performance monitoring
        collection_time_ms = (time.perf_counter() - start_time) * 1000
        if collection_time_ms > 1.0:
            await self._record_metric(
                name="voice.metrics.collection_slow",
                value=collection_time_ms,
                metric_type=MetricType.HISTOGRAM,
                priority=MetricPriority.HIGH,
                tags={"operation": "tts_metrics"}
            )
    
    async def record_provider_fallback(self,
                                     original_provider: str,
                                     fallback_provider: str,
                                     operation: str,
                                     agent_id: str) -> None:
        """Record provider fallback events"""
        await self._record_metric(
            name="voice.provider.fallback",
            value=1,
            metric_type=MetricType.COUNTER,
            priority=MetricPriority.HIGH,
            tags={
                "original_provider": original_provider,
                "fallback_provider": fallback_provider,
                "operation": operation,
                "agent_id": agent_id
            }
        )
    
    async def record_cache_hit(self, 
                              cache_type: str,
                              hit: bool,
                              agent_id: str) -> None:
        """Record cache performance"""
        await self._record_metric(
            name="voice.cache.requests",
            value=1,
            metric_type=MetricType.COUNTER,
            priority=MetricPriority.MEDIUM,
            tags={
                "cache_type": cache_type,
                "result": "hit" if hit else "miss",
                "agent_id": agent_id
            }
        )
    
    async def _record_metric(self,
                           name: str,
                           value: Union[int, float],
                           metric_type: MetricType,
                           priority: MetricPriority,
                           tags: Dict[str, str] = None,
                           extra_data: Dict[str, Any] = None) -> None:
        """Internal metric recording с performance optimization"""
        record = MetricRecord(
            name=name,
            value=value,
            metric_type=metric_type,
            priority=priority,
            tags=tags or {},
            extra_data=extra_data or {}
        )
        
        if self._enable_buffering and self._buffer:
            # Use buffer для performance
            self._buffer.add_metric(record)
        else:
            # Direct storage
            await self._backend.store_metric(record)
    
    def _async_flush_callback(self, records: List[MetricRecord]) -> None:
        """Callback для async flush from buffer"""
        # Schedule async flush в thread pool
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._backend.store_batch(records))
        self._background_tasks.append(task)
        
        # Cleanup completed tasks
        self._background_tasks = [t for t in self._background_tasks if not t.done()]
    
    def _start_background_flush(self) -> None:
        """Start background periodic flush"""
        async def periodic_flush():
            while True:
                await asyncio.sleep(1.0)  # Flush interval
                if self._buffer and self._buffer.should_flush():
                    all_records = self._buffer.flush_all()
                    for priority_records in all_records.values():
                        if priority_records:
                            await self._backend.store_batch(priority_records)
        
        # Schedule background task
        loop = asyncio.get_event_loop()
        task = loop.create_task(periodic_flush())
        self._background_tasks.append(task)
    
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
        
        # Aggregate metrics
        summary = {
            "total_requests": 0,
            "success_rate": 0.0,
            "average_stt_duration": 0.0,
            "average_tts_duration": 0.0,
            "provider_distribution": defaultdict(int),
            "fallback_rate": 0.0,
            "cache_hit_rate": 0.0
        }
        
        request_counts = defaultdict(int)
        success_counts = defaultdict(int)
        stt_durations = []
        tts_durations = []
        fallback_count = 0
        cache_hits = 0
        cache_total = 0
        
        for metric in metrics:
            name = metric.name
            tags = metric.tags
            
            if "cache" in name:
                cache_total += metric.value
                if tags.get("result") == "hit":
                    cache_hits += metric.value
            
            elif "requests" in name:
                provider = tags.get("provider", "unknown")
                request_counts[provider] += metric.value
                summary["total_requests"] += metric.value
                
                if tags.get("status") == "success":
                    success_counts[provider] += metric.value
            
            elif "duration_ms" in name:
                if "stt" in name:
                    stt_durations.append(metric.value)
                elif "tts" in name:
                    tts_durations.append(metric.value)
            
            elif "fallback" in name:
                fallback_count += metric.value
        
        # Calculate aggregated values
        total_requests = sum(request_counts.values())
        total_success = sum(success_counts.values())
        
        if total_requests > 0:
            summary["success_rate"] = total_success / total_requests
            summary["fallback_rate"] = fallback_count / total_requests
        
        if stt_durations:
            summary["average_stt_duration"] = sum(stt_durations) / len(stt_durations)
        
        if tts_durations:
            summary["average_tts_duration"] = sum(tts_durations) / len(tts_durations)
        
        if cache_total > 0:
            summary["cache_hit_rate"] = cache_hits / cache_total
        
        summary["provider_distribution"] = dict(request_counts)
        
        return summary
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check для metrics system"""
        backend_healthy = await self._backend.health_check()
        
        health_info = {
            "healthy": backend_healthy,
            "backend_type": type(self._backend).__name__,
            "buffering_enabled": self._enable_buffering,
            "background_tasks": len(self._background_tasks)
        }
        
        if self._buffer:
            with self._buffer._lock:
                health_info["buffer_sizes"] = {
                    priority.name: len(records) 
                    for priority, records in self._buffer._buffers.items()
                }
        
        return health_info
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        # Cancel background tasks
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        
        # Flush remaining metrics
        if self._buffer:
            all_records = self._buffer.flush_all()
            for priority_records in all_records.values():
                if priority_records:
                    await self._backend.store_batch(priority_records)
        
        # Shutdown thread pool
        self._thread_pool.shutdown(wait=True)
