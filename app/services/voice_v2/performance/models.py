"""
Performance Monitor Data Models

Dataclasses and enums for integration performance monitoring.
Extracted from integration_monitor.py for better modularity.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Any
from datetime import datetime


class PerformanceStatus(Enum):
    """Performance status levels"""
    EXCELLENT = "excellent"    # >95% target compliance
    GOOD = "good"             # 85-95% target compliance
    WARNING = "warning"       # 70-85% target compliance
    CRITICAL = "critical"     # <70% target compliance
    UNKNOWN = "unknown"       # Insufficient data


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MetricType(Enum):
    """Metric types for monitoring"""
    STT_LATENCY = "stt_latency"
    TTS_LATENCY = "tts_latency"
    DECISION_LATENCY = "decision_latency"
    TOTAL_LATENCY = "total_latency"
    SUCCESS_RATE = "success_rate"
    MEMORY_USAGE = "memory_usage"
    CPU_USAGE = "cpu_usage"


@dataclass
class PerformanceAlert:
    """Performance alert data structure"""
    alert_id: str
    severity: AlertSeverity
    metric_type: MetricType
    current_value: float
    threshold_value: float
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    resolved: bool = False


@dataclass
class LoadTestConfig:
    """Load test configuration"""
    concurrent_users: int = 10
    test_duration_seconds: int = 60
    ramp_up_seconds: int = 10
    target_stt_latency: float = 3.5
    target_tts_latency: float = 3.0
    target_success_rate: float = 0.99
    voice_message_ratio: float = 0.7


@dataclass
class PerformanceBaseline:
    """Performance baseline data"""
    baseline_id: str
    timestamp: datetime
    stt_baseline_latency: float
    tts_baseline_latency: float
    decision_baseline_latency: float
    total_baseline_latency: float
    baseline_success_rate: float
    test_conditions: Dict[str, Any]
    confidence_score: float = 0.0


@dataclass
class EndToEndMetrics:
    """End-to-end performance metrics collection"""
    # Request counts
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Latency measurements
    stt_latencies: List[float] = field(default_factory=list)
    tts_latencies: List[float] = field(default_factory=list)
    decision_latencies: List[float] = field(default_factory=list)
    total_latencies: List[float] = field(default_factory=list)

    # Resource usage
    memory_usage_mb: List[float] = field(default_factory=list)
    cpu_usage_percent: List[float] = field(default_factory=list)

    @property
    def overall_success_rate(self) -> float:
        """Calculate overall success rate"""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests

    @property
    def stt_success_rate(self) -> float:
        """Calculate STT success rate"""
        return len(self.stt_latencies) / max(self.total_requests, 1)

    @property
    def tts_success_rate(self) -> float:
        """Calculate TTS success rate"""
        return len(self.tts_latencies) / max(self.total_requests, 1)

    @property
    def decision_success_rate(self) -> float:
        """Calculate decision success rate"""
        return len(self.decision_latencies) / max(self.total_requests, 1)

    @property
    def average_total_latency(self) -> float:
        """Calculate average total latency"""
        import statistics
        return statistics.mean(self.total_latencies) if self.total_latencies else 0.0

    @property
    def p95_total_latency(self) -> float:
        """Calculate 95th percentile total latency"""
        if not self.total_latencies:
            return 0.0
        sorted_latencies = sorted(self.total_latencies)
        index = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[index] if index < len(sorted_latencies) else 0.0
