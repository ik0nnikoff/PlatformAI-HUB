"""
Metrics Data Models

Базовые модели данных для системы метрик.
Extracted from metrics.py для модульности.
"""

from enum import Enum
from typing import Dict, Any
from dataclasses import dataclass, field
import time


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
    """Record для хранения одной метрики"""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    tags: Dict[str, str] = field(default_factory=dict)
    priority: MetricPriority = MetricPriority.NORMAL

    def __post_init__(self):
        """Validation после создания"""
        if not self.name:
            raise ValueError("Metric name cannot be empty")
        if not isinstance(self.value, (int, float)):
            raise ValueError("Metric value must be numeric")
        if self.timestamp <= 0:
            raise ValueError("Timestamp must be positive")

    def with_tags(self, **additional_tags) -> 'MetricRecord':
        """Create copy with additional tags"""
        new_tags = {**self.tags, **additional_tags}
        return MetricRecord(
            name=self.name,
            value=self.value,
            timestamp=self.timestamp,
            tags=new_tags,
            priority=self.priority
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "name": self.name,
            "value": self.value,
            "timestamp": self.timestamp,
            "tags": self.tags,
            "priority": self.priority.value
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MetricRecord':
        """Create from dictionary"""
        return cls(
            name=data["name"],
            value=data["value"],
            timestamp=data.get("timestamp", time.time()),
            tags=data.get("tags", {}),
            priority=MetricPriority(data.get("priority", "normal"))
        )
