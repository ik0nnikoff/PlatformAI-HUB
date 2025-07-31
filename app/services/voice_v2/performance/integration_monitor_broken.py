"""
Integration Performance Monitor - Refactored Version

Simplified integration performance monitor with modular architecture.
Reduced from 684 to ~280 lines through extraction of specialized engines.

Architecture Compliance:
- SOLID principles compliance
- Reduced cyclomatic complexity (CCN ≤8)
- Single Responsibility: coordination and monitoring orchestration
- Open/Closed: extensible through engine composition
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime

try:
    from app.services.voice_v2.performance.stt_optimizer import STTPerformanceOptimizer
    from app.services.voice_v2.performance.tts_optimizer import TTSPerformanceOptimizer
    from app.services.voice_v2.performance.langgraph_optimizer import VoiceDecisionOptimizer
except ImportError:
    # Mock classes for development
    STTPerformanceOptimizer = None
    TTSPerformanceOptimizer = None
    VoiceDecisionOptimizer = None

from .models import (
    LoadTestConfig, PerformanceAlert, PerformanceBaseline,
    EndToEndMetrics, PerformanceStatus, AlertSeverity, MetricType
)
from .load_tester import LoadTestEngine
from .baseline_engine import BaselineEngine
from .dashboard_generator import DashboardDataGenerator

logger = logging.getLogger(__name__)


class IntegrationPerformanceMonitor:
    """
    Integration Performance Monitor - Simplified Architecture

    Orchestrates performance monitoring through specialized engines:
    - LoadTestEngine: Load testing operations
    - BaselineEngine: Baseline establishment and comparison
    - DashboardDataGenerator: Dashboard data preparation

    Reduced cyclomatic complexity through delegation pattern.
    """

    def __init__(self, load_test_config: LoadTestConfig):
        self.config = load_test_config

        # Component optimizers
        self.stt_optimizer: Optional[STTPerformanceOptimizer] = None
        self.tts_optimizer: Optional[TTSPerformanceOptimizer] = None
        self.decision_optimizer: Optional[VoiceDecisionOptimizer] = None

        # Specialized engines
        self.load_tester = LoadTestEngine(load_test_config)
        self.baseline_engine = BaselineEngine()
        self.dashboard_generator = DashboardDataGenerator()

        # Monitoring state
        self.monitoring_active = False
        self.monitoring_task: Optional[asyncio.Task] = None

        # Current metrics
        self.current_metrics = EndToEndMetrics()

        # Alert callbacks
        self.alert_callbacks: List[Callable[[PerformanceAlert], None]] = []

        logger.info("IntegrationPerformanceMonitor initialized with modular architecture")

    def set_optimizers(self,
                      stt_optimizer: STTPerformanceOptimizer,
                      tts_optimizer: TTSPerformanceOptimizer,
                      decision_optimizer: VoiceDecisionOptimizer) -> None:
        """Set component optimizers for monitoring"""
        self.stt_optimizer = stt_optimizer
        self.tts_optimizer = tts_optimizer
        self.decision_optimizer = decision_optimizer
        logger.info("Performance optimizers configured")

    def add_alert_callback(self, callback: Callable[[PerformanceAlert], None]) -> None:
        """Add alert callback function"""
        self.alert_callbacks.append(callback)
        logger.info("Alert callback added")

    async def start_monitoring(self) -> None:
        """Start continuous performance monitoring"""
        if self.monitoring_active:
            logger.warning("Monitoring already active")
            return

        self.monitoring_active = True
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info("Performance monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop continuous performance monitoring"""
        if not self.monitoring_active:
            return

        self.monitoring_active = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass

        logger.info("Performance monitoring stopped")

    async def _monitoring_loop(self) -> None:
        """Main monitoring loop"""
        try:
            while self.monitoring_active:
                await self._collect_performance_metrics()
                await asyncio.sleep(30)  # Collect metrics every 30 seconds
        except asyncio.CancelledError:
            logger.info("Monitoring loop cancelled")
        except Exception as e:
            logger.error(f"Monitoring loop error: {e}", exc_info=True)

    async def _collect_performance_metrics(self) -> None:
        """Collect current performance metrics"""
        try:
            # Simulate metric collection from optimizers
            if self.stt_optimizer and self.tts_optimizer and self.decision_optimizer:
                # Get recent performance data from optimizers
                await self._update_current_metrics()

                # Add to dashboard data
                self.dashboard_generator.add_performance_data(self.current_metrics)

                # Check for alerts
                await self._check_performance_alerts()

        except Exception as e:
            logger.error(f"Error collecting performance metrics: {e}", exc_info=True)

    async def _update_current_metrics(self) -> None:
        """Update current metrics from optimizers"""
        # This would integrate with actual optimizer metrics
        # For now, using mock data structure
        self.current_metrics = EndToEndMetrics()

        # Add mock recent performance data
        self.current_metrics.stt_latencies = [2.1, 2.3, 2.0]
        self.current_metrics.tts_latencies = [2.8, 3.1, 2.9]
        self.current_metrics.decision_latencies = [0.5, 0.6, 0.4]
        self.current_metrics.total_latencies = [5.4, 6.0, 5.3]
        self.current_metrics.total_requests = 3
        self.current_metrics.successful_requests = 3

    async def _check_performance_alerts(self) -> None:
        """Check for performance alerts"""
        if not self.current_metrics.total_latencies:
            return

        avg_total_latency = self.current_metrics.average_total_latency
        success_rate = self.current_metrics.overall_success_rate

        # Check latency alert
        if avg_total_latency > 8.0:
            alert = PerformanceAlert(
                alert_id=f"latency_alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                severity=AlertSeverity.WARNING if avg_total_latency < 10.0 else AlertSeverity.CRITICAL,
                metric_type=MetricType.TOTAL_LATENCY,
                current_value=avg_total_latency,
                threshold_value=8.0,
                message=f"Total latency exceeded threshold: {avg_total_latency:.2f}s > 8.0s"
            )
            await self._trigger_alert(alert)

        # Check success rate alert
        if success_rate < 0.95:
            alert = PerformanceAlert(
                alert_id=f"success_alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                severity=AlertSeverity.CRITICAL,
                metric_type=MetricType.SUCCESS_RATE,
                current_value=success_rate,
                threshold_value=0.95,
                message=f"Success rate below threshold: {success_rate:.2f} < 0.95"
            )
            await self._trigger_alert(alert)

    async def _trigger_alert(self, alert: PerformanceAlert) -> None:
        """Trigger performance alert"""
        self.dashboard_generator.add_alert(alert)

        # Call alert callbacks
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}", exc_info=True)

        logger.warning(f"Performance alert triggered: {alert.message}")

    # Delegation to specialized engines

    async def run_load_test(self) -> Dict[str, Any]:
        """Run load test (delegated to LoadTestEngine)"""
        return await self.load_tester.run_load_test()

    def establish_baseline(self) -> PerformanceBaseline:
        """Establish performance baseline (delegated to BaselineEngine)"""
        test_conditions = {
            "config": self.config.__dict__,
            "timestamp": datetime.now().isoformat()
        }
        return self.baseline_engine.establish_baseline(self.current_metrics, test_conditions)

    def get_monitoring_dashboard_data(self) -> Dict[str, Any]:
        """Get dashboard data (delegated to DashboardDataGenerator)"""
        return self.dashboard_generator.get_monitoring_dashboard_data()

    def compare_to_baseline(self, baseline_id: str = None) -> Dict[str, Any]:
        """Compare current performance to baseline"""
        return self.baseline_engine.compare_to_baseline(self.current_metrics, baseline_id)

    # Utility methods

    def get_current_status(self) -> PerformanceStatus:
        """Get current overall performance status"""
        if not self.current_metrics.total_latencies:
            return PerformanceStatus.UNKNOWN

        avg_latency = self.current_metrics.average_total_latency
        success_rate = self.current_metrics.overall_success_rate

        if success_rate >= 0.99 and avg_latency <= 5.0:
            return PerformanceStatus.EXCELLENT
        elif success_rate >= 0.95 and avg_latency <= 7.0:
            return PerformanceStatus.GOOD
        elif success_rate >= 0.90 and avg_latency <= 10.0:
            return PerformanceStatus.WARNING
        else:
            return PerformanceStatus.CRITICAL

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        return {
            "current_status": self.get_current_status().value,
            "load_test_active": self.load_tester.is_active,
            "monitoring_active": self.monitoring_active,
            "total_baselines": len(self.baseline_engine.baselines),
            "active_alerts": len([a for a in self.dashboard_generator.active_alerts if not a.resolved]),
            "last_metrics": {
                "total_latency": self.current_metrics.average_total_latency,
                "success_rate": self.current_metrics.overall_success_rate,
                "total_requests": self.current_metrics.total_requests
            }
        }

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check"""
        return {
            "monitor_status": "healthy" if not self.monitoring_task or not self.monitoring_task.done() else "stopped",
            "load_tester": "ready" if not self.load_tester.is_active else "running",
            "baseline_engine": "ready",
            "dashboard_generator": "ready",
            "optimizers_configured": all([
                self.stt_optimizer is not None,
                self.tts_optimizer is not None,
                self.decision_optimizer is not None
            ])
        }

    def cleanup(self) -> None:
        """Cleanup resources"""
        if self.monitoring_task:
            self.monitoring_task.cancel()

        self.monitoring_active = False
        logger.info("IntegrationPerformanceMonitor cleanup completed")
