"""
Metrics Backend Implementations

Различные backend реализации для хранения метрик.
Extracted from metrics.py для модульности.
"""

import asyncio
import json
import logging
from typing import List, Optional
from abc import ABC, abstractmethod

from .metrics_models import MetricRecord, MetricPriority

logger = logging.getLogger(__name__)


class MetricsBackendInterface(ABC):
    """Interface для storage backends (ISP compliance)"""

    @abstractmethod
    async def store_metric(self, record: MetricRecord) -> None:
        """Store single metric record"""

    @abstractmethod
    async def store_batch(self, records: List[MetricRecord]) -> None:
        """Store batch of metrics for performance"""

    @abstractmethod
    async def get_metrics(self,
                         name_pattern: str = "*",
                         start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[MetricRecord]:
        """Retrieve metrics by criteria"""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check backend health"""


class MemoryMetricsBackend(MetricsBackendInterface):
    """In-memory metrics storage для testing и development"""

    def __init__(self, max_records: int = 10000):
        self.max_records = max_records
        self.records: List[MetricRecord] = []
        self._lock = asyncio.Lock()

    async def store_metric(self, record: MetricRecord) -> None:
        """Store single metric"""
        async with self._lock:
            self.records.append(record)
            # Maintain size limit
            if len(self.records) > self.max_records:
                self.records = self.records[-self.max_records:]

    async def store_batch(self, records: List[MetricRecord]) -> None:
        """Store batch of metrics"""
        async with self._lock:
            self.records.extend(records)
            if len(self.records) > self.max_records:
                self.records = self.records[-self.max_records:]

    async def get_metrics(self,
                         name_pattern: str = "*",
                         start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[MetricRecord]:
        """Get metrics with filtering"""
        async with self._lock:
            results = []
            for record in self.records:
                # Time filtering
                if start_time and record.timestamp < start_time:
                    continue
                if end_time and record.timestamp > end_time:
                    continue

                # Pattern matching (simple implementation)
                if name_pattern != "*":
                    pattern = name_pattern.replace("*", "")
                    if pattern not in record.name:
                        continue

                results.append(record)

            return results

    async def health_check(self) -> bool:
        """Always healthy for memory backend"""
        return True


class RedisMetricsBackend(MetricsBackendInterface):
    """Redis-based metrics storage для production"""

    def __init__(self, redis_client, key_prefix: str = "voice_metrics"):
        self.redis = redis_client
        self.key_prefix = key_prefix
        self.ttl_seconds = 86400 * 7  # 7 days retention

    async def store_metric(self, record: MetricRecord) -> None:
        """Store metric in Redis"""
        try:
            key = f"{self.key_prefix}:{record.name}:{int(record.timestamp)}"
            data = {
                "value": record.value,
                "tags": record.tags,
                "priority": record.priority.value
            }

            await self.redis.setex(
                key,
                self.ttl_seconds,
                json.dumps(data)
            )
        except Exception as exc:
            logger.error("Failed to store metric in Redis: %s", exc)

    async def store_batch(self, records: List[MetricRecord]) -> None:
        """Store batch in Redis pipeline"""
        try:
            pipe = self.redis.pipeline()

            for record in records:
                key = f"{self.key_prefix}:{record.name}:{int(record.timestamp)}"
                data = {
                    "value": record.value,
                    "tags": record.tags,
                    "priority": record.priority.value
                }
                pipe.setex(key, self.ttl_seconds, json.dumps(data))

            await pipe.execute()
        except Exception as exc:
            logger.error("Failed to store batch in Redis: %s", exc)

    async def get_metrics(self,
                         name_pattern: str = "*",
                         start_time: Optional[float] = None,
                         end_time: Optional[float] = None) -> List[MetricRecord]:
        """Get metrics from Redis"""
        try:
            pattern = f"{self.key_prefix}:{name_pattern}:*"
            keys = await self.redis.keys(pattern)

            if not keys:
                return []

            values = await self.redis.mget(keys)
            results = []

            for key, value in zip(keys, values):
                if not value:
                    continue

                record = self._parse_redis_record(key, value, start_time, end_time)
                if record:
                    results.append(record)

            return results

        except Exception as exc:
            logger.error("Failed to get metrics from Redis: %s", exc)
            return []

    def _parse_redis_record(self, key, value, start_time, end_time) -> Optional[MetricRecord]:
        """Parse single Redis record"""
        try:
            # Parse key to extract name and timestamp
            key_parts = key.decode().split(':')
            name = ':'.join(key_parts[1:-1])  # Everything except prefix and timestamp
            timestamp = float(key_parts[-1])

            # Time filtering
            if start_time and timestamp < start_time:
                return None
            if end_time and timestamp > end_time:
                return None

            # Parse value
            data = json.loads(value)
            return MetricRecord(
                name=name,
                value=data["value"],
                timestamp=timestamp,
                tags=data.get("tags", {}),
                priority=MetricPriority(data.get("priority", "normal"))
            )

        except (ValueError, KeyError) as exc:
            logger.warning("Failed to parse metric from Redis: %s", exc)
            return None

    async def health_check(self) -> bool:
        """Check Redis connectivity"""
        try:
            await self.redis.ping()
            return True
        except Exception:
            return False


class MetricsBuffer:
    """Buffer для batch processing метрик"""

    def __init__(self, max_size: int = 100, flush_interval: float = 5.0):
        self.max_size = max_size
        self.flush_interval = flush_interval
        self.buffer: List[MetricRecord] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._backend: Optional[MetricsBackendInterface] = None

    def set_backend(self, backend: MetricsBackendInterface) -> None:
        """Set storage backend"""
        self._backend = backend

    async def add(self, record: MetricRecord) -> None:
        """Add metric to buffer"""
        async with self._lock:
            self.buffer.append(record)

            # Auto-flush if buffer is full
            if len(self.buffer) >= self.max_size:
                await self._flush_buffer()

    async def start_auto_flush(self) -> None:
        """Start automatic buffer flushing"""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._auto_flush_loop())

    async def stop_auto_flush(self) -> None:
        """Stop automatic flushing"""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

    async def flush(self) -> None:
        """Manually flush buffer"""
        async with self._lock:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Internal flush implementation"""
        if not self.buffer or not self._backend:
            return

        try:
            await self._backend.store_batch(self.buffer.copy())
            self.buffer.clear()
        except Exception as exc:
            logger.error("Failed to flush metrics buffer: %s", exc)

    async def _auto_flush_loop(self) -> None:
        """Automatic flush loop"""
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
        except asyncio.CancelledError:
            # Final flush on cancellation
            await self.flush()
