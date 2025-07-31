"""
Load Testing Engine

Handles load testing operations for voice_v2 integration performance.
Extracted from integration_monitor.py for modularity and reduced complexity.
"""

import asyncio
import logging
import time
import statistics
from typing import Dict, Any

from .models import LoadTestConfig, EndToEndMetrics

logger = logging.getLogger(__name__)


class LoadTestEngine:
    """
    Load testing engine for voice_v2 integration.
    
    Provides isolated load testing functionality with reduced complexity.
    Single Responsibility: Load testing operations only.
    """
    
    def __init__(self, config: LoadTestConfig):
        self.config = config
        self._active = False
        self._results: Dict[str, Any] = {}
        
        logger.info("LoadTestEngine initialized")
    
    @property
    def is_active(self) -> bool:
        """Check if load test is currently running"""
        return self._active
    
    @property
    def last_results(self) -> Dict[str, Any]:
        """Get last load test results"""
        return self._results
    
    async def run_load_test(self) -> Dict[str, Any]:
        """
        Run comprehensive load test for voice_v2 integration.
        
        Simplified implementation with reduced cyclomatic complexity.
        """
        if self._active:
            raise ValueError("Load test already running")
        
        logger.info(f"Starting load test: {self.config.concurrent_users} users, "
                   f"{self.config.test_duration_seconds}s duration")
        
        self._active = True
        start_time = time.time()
        
        try:
            # Initialize test metrics
            test_metrics = EndToEndMetrics()
            
            # Execute load test phases
            await self._execute_concurrent_load(test_metrics)
            
            # Calculate and store results
            test_duration = time.time() - start_time
            results = self._build_results(test_metrics, test_duration)
            
            self._results = results
            logger.info(f"Load test completed: {results['test_status']['meeting_targets']}")
            
            return results
            
        finally:
            self._active = False
    
    async def _execute_concurrent_load(self, test_metrics: EndToEndMetrics) -> None:
        """Execute concurrent user load simulation"""
        tasks = []
        
        # Create user simulation tasks
        for user_id in range(self.config.concurrent_users):
            task = asyncio.create_task(
                self._simulate_user_load(user_id, test_metrics)
            )
            tasks.append(task)
            
            # Stagger user ramp-up
            if self.config.ramp_up_seconds > 0:
                await asyncio.sleep(
                    self.config.ramp_up_seconds / self.config.concurrent_users
                )
        
        # Wait for all users to complete
        await asyncio.gather(*tasks, return_exceptions=True)
    
    def _build_results(self, test_metrics: EndToEndMetrics, duration: float) -> Dict[str, Any]:
        """Build comprehensive test results"""
        return {
            "test_config": self._build_config_results(),
            "performance_results": self._build_performance_results(test_metrics, duration),
            "component_performance": self._build_component_results(test_metrics),
            "resource_usage": self._build_resource_results(test_metrics),
            "test_status": self._build_status_results(test_metrics)
        }
    
    def _build_config_results(self) -> Dict[str, Any]:
        """Build test configuration results"""
        return {
            "concurrent_users": self.config.concurrent_users,
            "duration_seconds": self.config.test_duration_seconds,
            "target_stt_latency": self.config.target_stt_latency,
            "target_tts_latency": self.config.target_tts_latency,
            "target_success_rate": self.config.target_success_rate
        }
    
    def _build_performance_results(self, metrics: EndToEndMetrics, duration: float) -> Dict[str, Any]:
        """Build overall performance results"""
        return {
            "total_requests": metrics.total_requests,
            "successful_requests": metrics.successful_requests,
            "failed_requests": metrics.failed_requests,
            "overall_success_rate": metrics.overall_success_rate,
            "average_total_latency": metrics.average_total_latency,
            "p95_total_latency": metrics.p95_total_latency,
            "throughput_rps": metrics.total_requests / duration if duration > 0 else 0
        }
    
    def _build_component_results(self, metrics: EndToEndMetrics) -> Dict[str, Any]:
        """Build component-specific performance results"""
        return {
            "stt": self._build_component_stats(
                metrics.stt_latencies, 
                self.config.target_stt_latency
            ),
            "tts": self._build_component_stats(
                metrics.tts_latencies, 
                self.config.target_tts_latency
            ),
            "decisions": self._build_component_stats(
                metrics.decision_latencies, 
                None
            )
        }
    
    def _build_component_stats(self, latencies: list, target: float = None) -> Dict[str, Any]:
        """Build statistics for a component"""
        if not latencies:
            return {
                "average_latency": 0,
                "p95_latency": 0,
                "success_rate": 0,
                "meeting_target": target is None or target == 0
            }
        
        avg_latency = statistics.mean(latencies)
        p95_latency = self._calculate_percentile(latencies, 0.95)
        
        return {
            "average_latency": avg_latency,
            "p95_latency": p95_latency,
            "success_rate": 1.0,  # Simplified for load testing
            "meeting_target": target is None or avg_latency <= target
        }
    
    def _build_resource_results(self, metrics: EndToEndMetrics) -> Dict[str, Any]:
        """Build resource usage results"""
        return {
            "average_memory_mb": statistics.mean(metrics.memory_usage_mb) if metrics.memory_usage_mb else 0,
            "peak_memory_mb": max(metrics.memory_usage_mb) if metrics.memory_usage_mb else 0,
            "average_cpu_percent": statistics.mean(metrics.cpu_usage_percent) if metrics.cpu_usage_percent else 0,
            "peak_cpu_percent": max(metrics.cpu_usage_percent) if metrics.cpu_usage_percent else 0
        }
    
    def _build_status_results(self, metrics: EndToEndMetrics) -> Dict[str, Any]:
        """Build test status results"""
        stt_meeting_target = (
            not metrics.stt_latencies or 
            statistics.mean(metrics.stt_latencies) <= self.config.target_stt_latency
        )
        tts_meeting_target = (
            not metrics.tts_latencies or 
            statistics.mean(metrics.tts_latencies) <= self.config.target_tts_latency
        )
        
        return {
            "completed": True,
            "meeting_targets": (
                metrics.overall_success_rate >= self.config.target_success_rate and
                stt_meeting_target and
                tts_meeting_target
            )
        }
    
    async def _simulate_user_load(self, user_id: int, test_metrics: EndToEndMetrics) -> None:
        """Simulate load for a single user"""
        start_time = time.time()
        
        while (time.time() - start_time) < self.config.test_duration_seconds:
            try:
                await self._process_single_request(test_metrics)
                
                # Small delay between requests
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.warning(f"User {user_id} simulation error: {e}")
                test_metrics.failed_requests += 1
    
    async def _process_single_request(self, test_metrics: EndToEndMetrics) -> None:
        """Process a single simulated request"""
        # Mock STT processing
        stt_latency = await self._mock_stt_request()
        test_metrics.stt_latencies.append(stt_latency)
        
        # Mock decision processing
        decision_latency = await self._mock_decision_request()
        test_metrics.decision_latencies.append(decision_latency)
        
        # Mock TTS processing (based on voice_message_ratio)
        tts_latency = 0
        if time.time() % 1.0 < self.config.voice_message_ratio:
            tts_latency = await self._mock_tts_request()
            test_metrics.tts_latencies.append(tts_latency)
        
        # Calculate total latency
        total_latency = stt_latency + decision_latency + tts_latency
        test_metrics.total_latencies.append(total_latency)
        
        # Record successful request
        test_metrics.total_requests += 1
        test_metrics.successful_requests += 1
    
    async def _mock_stt_request(self) -> float:
        """Mock STT request with realistic latency"""
        await asyncio.sleep(0.1)  # Mock processing time
        return 0.8 + (time.time() % 1.0) * 2.0  # 0.8-2.8s range
    
    async def _mock_decision_request(self) -> float:
        """Mock decision request with realistic latency"""
        await asyncio.sleep(0.05)  # Mock processing time
        return 0.2 + (time.time() % 1.0) * 0.5  # 0.2-0.7s range
    
    async def _mock_tts_request(self) -> float:
        """Mock TTS request with realistic latency"""
        await asyncio.sleep(0.15)  # Mock processing time
        return 1.0 + (time.time() % 1.0) * 2.0  # 1.0-3.0s range
    
    def _calculate_percentile(self, values: list, percentile: float) -> float:
        """Calculate percentile value"""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        index = int(len(sorted_values) * percentile)
        return sorted_values[index] if index < len(sorted_values) else 0.0
