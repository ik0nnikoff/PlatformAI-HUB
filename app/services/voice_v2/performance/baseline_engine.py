"""
Baseline Establishment Engine

Handles performance baseline establishment for voice_v2 integration.
Extracted from integration_monitor.py for modularity and reduced complexity.
"""

import logging
import statistics
from typing import Dict, List, Any
from datetime import datetime

from .models import PerformanceBaseline, EndToEndMetrics

logger = logging.getLogger(__name__)


class BaselineEngine:
    """
    Performance baseline establishment engine.

    Single Responsibility: Baseline creation and management.
    """

    def __init__(self):
        self.baselines: List[PerformanceBaseline] = []
        logger.info("BaselineEngine initialized")

    def establish_baseline(self, test_metrics: EndToEndMetrics, test_conditions: Dict[str, Any]) -> PerformanceBaseline:
        """
        Establish performance baseline from test metrics.

        Simplified implementation with reduced cyclomatic complexity.
        """
        logger.info("Establishing performance baseline")

        # Calculate baseline metrics
        baseline_data = self._calculate_baseline_metrics(test_metrics)

        # Create baseline object
        baseline = PerformanceBaseline(
            baseline_id=f"baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            timestamp=datetime.now(),
            stt_baseline_latency=baseline_data["stt_latency"],
            tts_baseline_latency=baseline_data["tts_latency"],
            decision_baseline_latency=baseline_data["decision_latency"],
            total_baseline_latency=baseline_data["total_latency"],
            baseline_success_rate=baseline_data["success_rate"],
            test_conditions=test_conditions,
            confidence_score=baseline_data["confidence_score"]
        )

        # Store baseline
        self.baselines.append(baseline)

        logger.info("Baseline established: %s (confidence: %.2f)", baseline.baseline_id, baseline.confidence_score)

        return baseline

    def _calculate_baseline_metrics(self, metrics: EndToEndMetrics) -> Dict[str, float]:
        """Calculate baseline metrics from test data"""
        baseline_data = {
            "stt_latency": self._calculate_robust_average(metrics.stt_latencies),
            "tts_latency": self._calculate_robust_average(metrics.tts_latencies),
            "decision_latency": self._calculate_robust_average(metrics.decision_latencies),
            "total_latency": self._calculate_robust_average(metrics.total_latencies),
            "success_rate": metrics.overall_success_rate
        }

        # Calculate confidence score
        baseline_data["confidence_score"] = self._calculate_confidence_score(metrics)

        return baseline_data

    def _calculate_robust_average(self, values: List[float]) -> float:
        """Calculate robust average (trimmed mean) to exclude outliers"""
        if not values:
            return 0.0

        if len(values) < 10:
            return statistics.mean(values)

        # Use trimmed mean (remove top/bottom 10%)
        sorted_values = sorted(values)
        trim_count = int(len(sorted_values) * 0.1)

        if trim_count > 0:
            trimmed_values = sorted_values[trim_count:-trim_count]
        else:
            trimmed_values = sorted_values

        return statistics.mean(trimmed_values) if trimmed_values else 0.0

    def _calculate_confidence_score(self, metrics: EndToEndMetrics) -> float:
        """Calculate confidence score for baseline reliability"""
        confidence_factors = []

        # Sample size factor
        total_samples = len(metrics.total_latencies)
        size_factor = min(total_samples / 100.0, 1.0)  # Full confidence at 100+ samples
        confidence_factors.append(size_factor)

        # Success rate factor
        success_factor = metrics.overall_success_rate
        confidence_factors.append(success_factor)

        # Consistency factor (lower variance = higher confidence)
        if metrics.total_latencies:
            variance = statistics.variance(metrics.total_latencies)
            mean_latency = statistics.mean(metrics.total_latencies)
            cv = variance / mean_latency if mean_latency > 0 else 1.0
            consistency_factor = max(0.0, 1.0 - cv)
            confidence_factors.append(consistency_factor)

        # Calculate overall confidence
        return statistics.mean(confidence_factors) if confidence_factors else 0.0

    def get_latest_baseline(self) -> PerformanceBaseline:
        """Get the most recent baseline"""
        if not self.baselines:
            raise ValueError("No baselines available")

        return max(self.baselines, key=lambda b: b.timestamp)

    def get_baseline_by_id(self, baseline_id: str) -> PerformanceBaseline:
        """Get baseline by ID"""
        for baseline in self.baselines:
            if baseline.baseline_id == baseline_id:
                return baseline

        raise ValueError(f"Baseline not found: {baseline_id}")

    def compare_to_baseline(self, current_metrics: EndToEndMetrics, baseline_id: str = None) -> Dict[str, Any]:
        """Compare current metrics to baseline"""
        if baseline_id:
            baseline = self.get_baseline_by_id(baseline_id)
        else:
            baseline = self.get_latest_baseline()

        current_data = self._calculate_baseline_metrics(current_metrics)

        return {
            "baseline_id": baseline.baseline_id,
            "comparison": {
                "stt_latency": {
                    "baseline": baseline.stt_baseline_latency,
                    "current": current_data["stt_latency"],
                    "change_percent": self._calculate_change_percent(
                        baseline.stt_baseline_latency,
                        current_data["stt_latency"]
                    )
                },
                "tts_latency": {
                    "baseline": baseline.tts_baseline_latency,
                    "current": current_data["tts_latency"],
                    "change_percent": self._calculate_change_percent(
                        baseline.tts_baseline_latency,
                        current_data["tts_latency"]
                    )
                },
                "total_latency": {
                    "baseline": baseline.total_baseline_latency,
                    "current": current_data["total_latency"],
                    "change_percent": self._calculate_change_percent(
                        baseline.total_baseline_latency,
                        current_data["total_latency"]
                    )
                },
                "success_rate": {
                    "baseline": baseline.baseline_success_rate,
                    "current": current_data["success_rate"],
                    "change_percent": self._calculate_change_percent(
                        baseline.baseline_success_rate,
                        current_data["success_rate"]
                    )
                }
            }
        }

    def _calculate_change_percent(self, baseline_value: float, current_value: float) -> float:
        """Calculate percentage change from baseline"""
        if baseline_value == 0:
            return 0.0 if current_value == 0 else 100.0

        return ((current_value - baseline_value) / baseline_value) * 100.0
