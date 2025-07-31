"""
Performance Validation Models - Shared Data Models

Содержит все модели данных для системы валидации производительности:
- ValidationStatus, ProductionReadiness enums
- ValidationTestCase dataclass
- ValidationReport dataclass
- Shared type definitions
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ValidationStatus(Enum):
    """Performance validation status"""
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class ProductionReadiness(Enum):
    """Production readiness levels"""
    READY = "ready"
    NEEDS_OPTIMIZATION = "needs_optimization"
    NOT_READY = "not_ready"
    CRITICAL_ISSUES = "critical_issues"


@dataclass
class ValidationTestCase:
    """Individual validation test case"""
    name: str
    category: str
    target_value: float
    actual_value: Optional[float] = None
    status: ValidationStatus = ValidationStatus.SKIPPED
    error_message: Optional[str] = None
    execution_time: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    """Comprehensive validation report"""
    report_id: str
    timestamp: datetime
    total_duration: float
    test_cases: List[ValidationTestCase]
    overall_status: ValidationStatus
    production_readiness: ProductionReadiness
    performance_summary: Dict[str, Any]
    recommendations: List[str]
    critical_issues: List[str]
    improvement_areas: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed_tests(self) -> int:
        """Number of passed tests"""
        return sum(1 for tc in self.test_cases if tc.status == ValidationStatus.PASSED)

    @property
    def failed_tests(self) -> int:
        """Number of failed tests"""
        return sum(1 for tc in self.test_cases if tc.status == ValidationStatus.FAILED)

    @property
    def warning_tests(self) -> int:
        """Number of warning tests"""
        return sum(1 for tc in self.test_cases if tc.status == ValidationStatus.WARNING)

    @property
    def success_rate(self) -> float:
        """Overall success rate percentage"""
        total = len(self.test_cases)
        if total == 0:
            return 0.0
        return (self.passed_tests / total) * 100.0


# Type aliases for better readability
ValidationResults = Dict[str, ValidationTestCase]
PerformanceMetrics = Dict[str, float]
ComponentHealth = Dict[str, bool]
