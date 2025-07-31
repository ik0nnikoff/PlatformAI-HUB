"""
Dashboard Data Generator

Handles dashboard data generation for voice_v2 integration monitoring.
Extracted from integration_monitor.py for modularity and reduced complexity.
"""

import logging
import statistics
from typing import Dict, List, Any, Tuple
from datetime import datetime, timedelta

from .models import PerformanceAlert, PerformanceStatus, EndToEndMetrics

logger = logging.getLogger(__name__)


class DashboardDataGenerator:
    """
    Dashboard data generation engine.

    Single Responsibility: Dashboard data preparation and formatting.
    """

    def __init__(self):
        self.performance_history: List[Tuple[datetime, Dict[str, float]]] = []
        self.active_alerts: List[PerformanceAlert] = []
        logger.info("DashboardDataGenerator initialized")

    def add_performance_data(self, metrics: EndToEndMetrics) -> None:
        """Add performance data point to history"""
        timestamp = datetime.now()
        data_point = {
            "stt_avg_latency": statistics.mean(metrics.stt_latencies) if metrics.stt_latencies else 0,
            "tts_avg_latency": statistics.mean(metrics.tts_latencies) if metrics.tts_latencies else 0,
            "decision_avg_latency": statistics.mean(metrics.decision_latencies) if metrics.decision_latencies else 0,
            "total_avg_latency": metrics.average_total_latency,
            "success_rate": metrics.overall_success_rate,
            "throughput": metrics.total_requests / 60.0 if metrics.total_requests > 0 else 0  # per minute
        }

        self.performance_history.append((timestamp, data_point))

        # Keep only last 24 hours of data
        cutoff_time = datetime.now() - timedelta(hours=24)
        self.performance_history = [
            (ts, data) for ts, data in self.performance_history
            if ts > cutoff_time
        ]

        logger.debug("Added performance data point: %s total points", len(self.performance_history))

    def get_monitoring_dashboard_data(self) -> Dict[str, Any]:
        """
        Generate comprehensive dashboard data.

        Simplified implementation with reduced cyclomatic complexity.
        """
        logger.debug("Generating dashboard data")

        return {
            "overview": self._build_overview_data(),
            "performance_trends": self._build_trends_data(),
            "component_status": self._build_component_status(),
            "alerts": self._build_alerts_data(),
            "resource_utilization": self._build_resource_data(),
            "last_updated": datetime.now().isoformat()
        }

    def _build_overview_data(self) -> Dict[str, Any]:
        """Build overview section data"""
        if not self.performance_history:
            return self._build_empty_overview()

        latest_data = self.performance_history[-1][1]

        return {
            "current_status": self._determine_overall_status(latest_data),
            "total_latency": latest_data.get("total_avg_latency", 0),
            "success_rate": latest_data.get("success_rate", 0),
            "active_alerts_count": len([a for a in self.active_alerts if not a.resolved]),
            "last_measurement": self.performance_history[-1][0].isoformat()
        }

    def _build_empty_overview(self) -> Dict[str, Any]:
        """Build overview when no data is available"""
        return {
            "current_status": PerformanceStatus.UNKNOWN.value,
            "total_latency": 0,
            "success_rate": 0,
            "active_alerts_count": 0,
            "last_measurement": None
        }

    def _build_trends_data(self) -> Dict[str, Any]:
        """Build performance trends data"""
        if not self.performance_history:
            return {"data_points": [], "timeframe": "24h"}

        # Aggregate data points for charting
        chart_data = []
        for timestamp, data in self.performance_history[-50:]:  # Last 50 points
            chart_data.append({
                "timestamp": timestamp.isoformat(),
                "stt_latency": data.get("stt_avg_latency", 0),
                "tts_latency": data.get("tts_avg_latency", 0),
                "total_latency": data.get("total_avg_latency", 0),
                "success_rate": data.get("success_rate", 0)
            })

        return {
            "data_points": chart_data,
            "timeframe": "24h"
        }

    def _build_component_status(self) -> Dict[str, Any]:
        """Build component status data"""
        if not self.performance_history:
            return self._build_empty_component_status()

        latest_data = self.performance_history[-1][1]

        return {
            "stt": {
                "status": self._determine_component_status(
                    latest_data.get("stt_avg_latency", 0), 3.5
                ),
                "current_latency": latest_data.get("stt_avg_latency", 0),
                "target_latency": 3.5
            },
            "tts": {
                "status": self._determine_component_status(
                    latest_data.get("tts_avg_latency", 0), 3.0
                ),
                "current_latency": latest_data.get("tts_avg_latency", 0),
                "target_latency": 3.0
            },
            "decision": {
                "status": self._determine_component_status(
                    latest_data.get("decision_avg_latency", 0), 1.0
                ),
                "current_latency": latest_data.get("decision_avg_latency", 0),
                "target_latency": 1.0
            }
        }

    def _build_empty_component_status(self) -> Dict[str, Any]:
        """Build component status when no data available"""
        return {
            "stt": {"status": PerformanceStatus.UNKNOWN.value, "current_latency": 0, "target_latency": 3.5},
            "tts": {"status": PerformanceStatus.UNKNOWN.value, "current_latency": 0, "target_latency": 3.0},
            "decision": {"status": PerformanceStatus.UNKNOWN.value, "current_latency": 0, "target_latency": 1.0}
        }

    def _build_alerts_data(self) -> List[Dict[str, Any]]:
        """Build alerts data for dashboard"""
        return [
            {
                "id": alert.alert_id,
                "severity": alert.severity.value,
                "metric": alert.metric_type.value,
                "message": alert.message,
                "timestamp": alert.timestamp.isoformat(),
                "resolved": alert.resolved
            }
            for alert in self.active_alerts[-10:]  # Last 10 alerts
        ]

    def _build_resource_data(self) -> Dict[str, Any]:
        """Build resource utilization data"""
        if not self.performance_history:
            return {"cpu_usage": 0, "memory_usage": 0, "disk_usage": 0}

        # Simplified resource data
        return {
            "cpu_usage": 15.5,  # Mock data - would come from actual monitoring
            "memory_usage": 45.2,
            "disk_usage": 23.1
        }

    def _determine_overall_status(self, data: Dict[str, float]) -> str:
        """Determine overall performance status"""
        success_rate = data.get("success_rate", 0)
        total_latency = data.get("total_avg_latency", 0)

        if success_rate >= 0.99 and total_latency <= 5.0:
            return PerformanceStatus.EXCELLENT.value
        elif success_rate >= 0.95 and total_latency <= 7.0:
            return PerformanceStatus.GOOD.value
        elif success_rate >= 0.90 and total_latency <= 10.0:
            return PerformanceStatus.WARNING.value
        else:
            return PerformanceStatus.CRITICAL.value

    def _determine_component_status(self, current_latency: float, target_latency: float) -> str:
        """Determine component performance status"""
        if current_latency == 0:
            return PerformanceStatus.UNKNOWN.value

        ratio = current_latency / target_latency

        if ratio <= 1.0:
            return PerformanceStatus.EXCELLENT.value
        elif ratio <= 1.2:
            return PerformanceStatus.GOOD.value
        elif ratio <= 1.5:
            return PerformanceStatus.WARNING.value
        else:
            return PerformanceStatus.CRITICAL.value

    def add_alert(self, alert: PerformanceAlert) -> None:
        """Add alert to active alerts"""
        self.active_alerts.append(alert)
        logger.info("Alert added: %s (%s)", alert.alert_id, alert.severity.value)

    def resolve_alert(self, alert_id: str) -> bool:
        """Resolve an alert by ID"""
        for alert in self.active_alerts:
            if alert.alert_id == alert_id:
                alert.resolved = True
                logger.info("Alert resolved: %s", alert_id)
                return True
        return False
