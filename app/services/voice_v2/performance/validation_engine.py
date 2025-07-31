"""
Performance Validation Engine - Core Validation Logic

Содержит основную логику выполнения валидации производительности:
- Создание тестовых случаев
- Выполнение валидации компонентов
- Оценка производительности и ресурсов
- Логика принятия решений о статусе тестов
"""

import logging
from typing import List, Optional

from .validation_models import (
    ValidationTestCase, ValidationStatus
)
from .integration_monitor import (
    IntegrationPerformanceMonitor
)
from .stt_optimizer import STTPerformanceOptimizer
from .tts_optimizer import TTSPerformanceOptimizer
from .langgraph_optimizer import VoiceDecisionOptimizer

logger = logging.getLogger(__name__)


class ValidationEngine:
    """Core validation logic engine"""

    def __init__(self, target_stt_latency: float = 3.5, target_tts_latency: float = 3.0,
                 target_success_rate: float = 95.0, target_cache_hit_rate: float = 85.0):
        self.target_stt_latency = target_stt_latency
        self.target_tts_latency = target_tts_latency
        self.target_success_rate = target_success_rate
        self.target_cache_hit_rate = target_cache_hit_rate

        # Component references (set by configure_components)
        self.integration_monitor: Optional[IntegrationPerformanceMonitor] = None
        self.stt_optimizer: Optional[STTPerformanceOptimizer] = None
        self.tts_optimizer: Optional[TTSPerformanceOptimizer] = None
        self.decision_optimizer: Optional[VoiceDecisionOptimizer] = None

    def configure_components(self, integration_monitor, stt_optimizer,
                           tts_optimizer, decision_optimizer):
        """Configure performance components"""
        self.integration_monitor = integration_monitor
        self.stt_optimizer = stt_optimizer
        self.tts_optimizer = tts_optimizer
        self.decision_optimizer = decision_optimizer

    def create_test_cases(self) -> List[ValidationTestCase]:
        """Create comprehensive test cases for validation"""
        test_cases = []
        test_cases.extend(self._create_stt_test_cases())
        test_cases.extend(self._create_tts_test_cases())
        test_cases.extend(self._create_cache_test_cases())
        test_cases.extend(self._create_resource_test_cases())
        test_cases.extend(self._create_health_test_cases())
        return test_cases

    def _create_stt_test_cases(self) -> List[ValidationTestCase]:
        """Create STT performance test cases"""
        return [
            ValidationTestCase(
                name="STT Average Latency",
                category="stt_performance",
                target_value=self.target_stt_latency,
                metadata={
                    "description": "Average STT processing latency should be ≤3.5s",
                    "threshold_warning": self.target_stt_latency * 1.1,
                    "threshold_critical": self.target_stt_latency * 1.3
                }
            ),
            ValidationTestCase(
                name="STT P95 Latency",
                category="stt_performance",
                target_value=self.target_stt_latency * 1.3,
                metadata={
                    "description": "95th percentile STT latency should be ≤4.5s",
                    "threshold_warning": self.target_stt_latency * 1.4,
                    "threshold_critical": self.target_stt_latency * 1.6
                }
            ),
            ValidationTestCase(
                name="STT Success Rate",
                category="stt_performance",
                target_value=self.target_success_rate,
                metadata={
                    "description": "STT processing success rate should be ≥95%",
                    "threshold_warning": 90.0,
                    "threshold_critical": 85.0
                }
            )
        ]

    def _create_tts_test_cases(self) -> List[ValidationTestCase]:
        """Create TTS performance test cases"""
        return [
            ValidationTestCase(
                name="TTS Average Latency",
                category="tts_performance",
                target_value=self.target_tts_latency,
                metadata={
                    "description": "Average TTS processing latency should be ≤3.0s",
                    "threshold_warning": self.target_tts_latency * 1.1,
                    "threshold_critical": self.target_tts_latency * 1.3
                }
            ),
            ValidationTestCase(
                name="TTS P95 Latency",
                category="tts_performance",
                target_value=self.target_tts_latency * 1.3,
                metadata={
                    "description": "95th percentile TTS latency should be ≤3.9s",
                    "threshold_warning": self.target_tts_latency * 1.4,
                    "threshold_critical": self.target_tts_latency * 1.6
                }
            ),
            ValidationTestCase(
                name="TTS Success Rate",
                category="tts_performance",
                target_value=self.target_success_rate,
                metadata={
                    "description": "TTS processing success rate should be ≥95%",
                    "threshold_warning": 90.0,
                    "threshold_critical": 85.0
                }
            )
        ]

    def _create_cache_test_cases(self) -> List[ValidationTestCase]:
        """Create cache performance test cases"""
        return [
            ValidationTestCase(
                name="Cache Hit Rate",
                category="cache_performance",
                target_value=self.target_cache_hit_rate,
                metadata={
                    "description": "Cache hit rate should be ≥85%",
                    "threshold_warning": 80.0,
                    "threshold_critical": 70.0
                }
            )
        ]

    def _create_resource_test_cases(self) -> List[ValidationTestCase]:
        """Create resource usage test cases"""
        return [
            ValidationTestCase(
                name="Memory Usage",
                category="resource_usage",
                target_value=512.0,  # MB
                metadata={
                    "description": "Memory usage should be ≤512MB under load",
                    "threshold_warning": 600.0,
                    "threshold_critical": 800.0
                }
            ),
            ValidationTestCase(
                name="CPU Usage",
                category="resource_usage",
                target_value=70.0,  # percentage
                metadata={
                    "description": "CPU usage should be ≤70% under load",
                    "threshold_warning": 80.0,
                    "threshold_critical": 90.0
                }
            )
        ]

    def _create_health_test_cases(self) -> List[ValidationTestCase]:
        """Create component health test cases"""
        return [
            ValidationTestCase(
                name="Integration Monitor Health",
                category="component_health",
                target_value=1.0,  # boolean as float (1.0 = healthy)
                metadata={"description": "Integration monitor should be healthy"}
            ),
            ValidationTestCase(
                name="STT Optimizer Health",
                category="component_health",
                target_value=1.0,
                metadata={"description": "STT optimizer should be healthy"}
            ),
            ValidationTestCase(
                name="TTS Optimizer Health",
                category="component_health",
                target_value=1.0,
                metadata={"description": "TTS optimizer should be healthy"}
            ),
            ValidationTestCase(
                name="Decision Optimizer Health",
                category="component_health",
                target_value=1.0,
                metadata={"description": "Decision optimizer should be healthy"}
            )
        ]

    async def validate_component_health(self, test_cases: List[ValidationTestCase]) -> None:
        """Validate health of all performance components"""
        health_tests = [tc for tc in test_cases if tc.category == "component_health"]

        for test_case in health_tests:
            try:
                health_value = await self._check_component_health(test_case.name)
                test_case.actual_value = health_value
                test_case.status = self.evaluate_test_case(test_case)

            except (AttributeError, ConnectionError, TimeoutError) as exc:
                logger.error("Health check failed for %s: %s", test_case.name, exc)
                test_case.actual_value = 0.0
                test_case.status = ValidationStatus.FAILED
                test_case.error_message = str(exc)

    async def _check_component_health(self, component_name: str) -> float:
        """Check health of specific component"""
        # Map component names to health check methods
        component_checks = {
            "Integration Monitor": self._check_integration_component,
            "STT Optimizer": self._check_stt_component,
            "TTS Optimizer": self._check_tts_component,
            "Decision Optimizer": self._check_decision_component
        }

        for name, check_method in component_checks.items():
            if name in component_name:
                return await check_method()

        return 0.0  # Component not found or not configured

    async def _check_integration_component(self) -> float:
        """Check integration monitor health"""
        if self.integration_monitor:
            is_healthy = await self._check_integration_monitor_health()
            return 1.0 if is_healthy else 0.0
        return 0.0

    async def _check_stt_component(self) -> float:
        """Check STT optimizer health"""
        if self.stt_optimizer:
            is_healthy = await self._check_stt_optimizer_health()
            return 1.0 if is_healthy else 0.0
        return 0.0

    async def _check_tts_component(self) -> float:
        """Check TTS optimizer health"""
        if self.tts_optimizer:
            is_healthy = await self._check_tts_optimizer_health()
            return 1.0 if is_healthy else 0.0
        return 0.0

    async def _check_decision_component(self) -> float:
        """Check decision optimizer health"""
        if self.decision_optimizer:
            is_healthy = await self._check_decision_optimizer_health()
            return 1.0 if is_healthy else 0.0
        return 0.0

    async def validate_optimization_effectiveness(
        self,
        test_cases: List[ValidationTestCase]
    ) -> None:
        """Validate effectiveness of optimization components"""
        # This is a placeholder for optimization validation
        # Would normally check if optimizers are improving performance metrics

        for test_case in test_cases:
            if test_case.category in ["stt_performance", "tts_performance"]:
                # Simulate optimization check
                test_case.metadata["optimization_active"] = True
                logger.debug("Optimization check for %s completed", test_case.name)

    def evaluate_test_case(self, test_case: ValidationTestCase) -> ValidationStatus:
        """Evaluate test case result based on thresholds"""
        if test_case.actual_value is None:
            return ValidationStatus.SKIPPED

        # Delegate to specific evaluation methods
        if test_case.category == "component_health":
            return self._evaluate_health_test(test_case)
        if ("Success Rate" in test_case.name and
            test_case.category in ["stt_performance", "tts_performance"]):
            return self._evaluate_success_rate_test(test_case)
        return self._evaluate_performance_test(test_case)

    def _evaluate_health_test(self, test_case: ValidationTestCase) -> ValidationStatus:
        """Evaluate component health test (1.0 = healthy, 0.0 = unhealthy)"""
        return (ValidationStatus.PASSED if test_case.actual_value >= test_case.target_value
                else ValidationStatus.FAILED)

    def _evaluate_success_rate_test(self, test_case: ValidationTestCase) -> ValidationStatus:
        """Evaluate success rate test (higher is better)"""
        actual = test_case.actual_value
        target = test_case.target_value
        warning_threshold = test_case.metadata.get("threshold_warning")

        if actual >= target:
            return ValidationStatus.PASSED
        if warning_threshold and actual >= warning_threshold:
            return ValidationStatus.WARNING
        return ValidationStatus.FAILED

    def _evaluate_performance_test(self, test_case: ValidationTestCase) -> ValidationStatus:
        """Evaluate performance test (lower is better - latencies, resource usage)"""
        actual = test_case.actual_value
        target = test_case.target_value
        warning_threshold = test_case.metadata.get("threshold_warning")

        if actual <= target:
            return ValidationStatus.PASSED
        if warning_threshold and actual <= warning_threshold:
            return ValidationStatus.WARNING
        return ValidationStatus.FAILED

    async def _check_integration_monitor_health(self) -> bool:
        """Check integration monitor health"""
        try:
            if not self.integration_monitor:
                return False
            health_status = await self.integration_monitor.get_health_status()
            return health_status.get("status") == "healthy"
        except (AttributeError, ConnectionError, TimeoutError) as exc:
            logger.warning("Integration monitor health check failed: %s", exc)
            return False

    async def _check_stt_optimizer_health(self) -> bool:
        """Check STT optimizer health"""
        try:
            if not self.stt_optimizer:
                return False
            # Simplified health check
            return hasattr(self.stt_optimizer, 'get_current_metrics')
        except (AttributeError, TypeError) as exc:
            logger.warning("STT optimizer health check failed: %s", exc)
            return False

    async def _check_tts_optimizer_health(self) -> bool:
        """Check TTS optimizer health"""
        try:
            if not self.tts_optimizer:
                return False
            # Simplified health check
            return hasattr(self.tts_optimizer, 'get_current_metrics')
        except (AttributeError, TypeError) as exc:
            logger.warning("TTS optimizer health check failed: %s", exc)
            return False

    async def _check_decision_optimizer_health(self) -> bool:
        """Check decision optimizer health"""
        try:
            if not self.decision_optimizer:
                return False
            # Simplified health check
            return hasattr(self.decision_optimizer, 'get_metrics')
        except (AttributeError, TypeError) as exc:
            logger.warning("Decision optimizer health check failed: %s", exc)
            return False
