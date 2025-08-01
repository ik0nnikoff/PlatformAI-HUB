"""
Unified Voice V2 Metrics System

Phase 2 Consolidation: Объединение metrics_models.py, metrics_backends.py в единый файл.
Simplified architecture with essential metrics functionality только.

Architecture:
- UNIFIED MODELS: All metric models in single file
- SIMPLIFIED BACKENDS: Basic memory backend only
- REDUCED COMPLEXITY: ~150 lines instead of 720 lines total
- CORE FUNCTIONALITY: Essential metrics collection without enterprise patterns
"""

import logging
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Protocol
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# === UNIFIED MODELS ===

class MetricType(Enum):
    """Типы метрик для voice_v2"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class MetricPriority(Enum):
    """Приоритеты метрик"""
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class MetricRecord:
    """Запись метрики"""
    name: str
    value: float
    metric_type: MetricType
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)
    priority: MetricPriority = MetricPriority.NORMAL


# === UNIFIED BACKENDS ===

class MetricsBackendInterface(Protocol):
    """Interface для metrics backends"""

    def store_metric(self, record: MetricRecord) -> None:
        """Store single metric record"""
        ...

    def get_metrics(self, name: Optional[str] = None, limit: int = 100) -> List[MetricRecord]:
        """Get stored metrics"""
        ...

    def clear_metrics(self) -> None:
        """Clear all stored metrics"""
        ...


class MemoryMetricsBackend:
    """Simple in-memory metrics storage"""

    def __init__(self, max_records: int = 1000):
        self.max_records = max_records
        self.metrics: deque[MetricRecord] = deque(maxlen=max_records)
        self.metrics_by_name: Dict[str, deque[MetricRecord]] = defaultdict(lambda: deque(maxlen=100))

    def store_metric(self, record: MetricRecord) -> None:
        """Store metric in memory"""
        self.metrics.append(record)
        self.metrics_by_name[record.name].append(record)

    def get_metrics(self, name: Optional[str] = None, limit: int = 100) -> List[MetricRecord]:
        """Get metrics from memory"""
        if name:
            return list(self.metrics_by_name[name])[-limit:]
        return list(self.metrics)[-limit:]

    def clear_metrics(self) -> None:
        """Clear all metrics"""
        self.metrics.clear()
        self.metrics_by_name.clear()


# === SIMPLIFIED METRICS COLLECTOR ===

class VoiceMetricsCollector:
    """
    Simplified Voice Metrics Collector

    Single Responsibility: Basic metrics collection without enterprise complexity.
    Removed: Complex async processing, advanced analytics, enterprise monitoring.
    """

    def __init__(self, backend: Optional[MetricsBackendInterface] = None):
        self.backend = backend or MemoryMetricsBackend()
        self.enabled = True
        logger.info("VoiceMetricsCollector initialized with simplified architecture")

    def collect_metric(self, name: str, value: float, metric_type: MetricType = MetricType.GAUGE,
                      labels: Optional[Dict[str, str]] = None, priority: MetricPriority = MetricPriority.NORMAL) -> None:
        """Collect single metric"""
        if not self.enabled:
            return

        record = MetricRecord(
            name=name,
            value=value,
            metric_type=metric_type,
            labels=labels or {},
            priority=priority
        )

        try:
            self.backend.store_metric(record)
            logger.debug(f"Collected metric: {name}={value}")
        except Exception as e:
            logger.error(f"Failed to store metric {name}: {e}")

    def collect_counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        """Collect counter metric"""
        self.collect_metric(name, value, MetricType.COUNTER, labels)

    def collect_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Collect gauge metric"""
        self.collect_metric(name, value, MetricType.GAUGE, labels)

    def collect_timer(self, name: str, duration: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Collect timer metric"""
        self.collect_metric(name, duration, MetricType.TIMER, labels)

    def get_metrics(self, name: Optional[str] = None, limit: int = 100) -> List[MetricRecord]:
        """Get collected metrics"""
        return self.backend.get_metrics(name, limit)

    def clear_metrics(self) -> None:
        """Clear all metrics"""
        self.backend.clear_metrics()
        logger.debug("Metrics cleared")

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable metrics collection"""
        self.enabled = enabled
        logger.info(f"Metrics collection {'enabled' if enabled else 'disabled'}")


def create_metrics_collector(backend: Optional[MetricsBackendInterface] = None) -> VoiceMetricsCollector:
    """Factory function for metrics collector"""
    return VoiceMetricsCollector(backend)
