"""
Performance Validation Suite - Phase 5.3.4 Final Implementation

Реализует комплексную валидацию производительности voice_v2 integration:
- End-to-end performance validation (STT ≤3.5s, TTS ≤3.0s)
- Production readiness assessment и compliance check
- Performance regression testing suite
- Comprehensive reporting и metrics validation
- Production deployment readiness certification

Target Metrics:
- STT Performance: ≤3.5s (vs current 4.2-4.8s baseline)
- TTS Performance: ≤3.0s (vs current 3.8-4.5s baseline)
- Overall Success Rate: ≥95%
- Cache Hit Rate: ≥85%
- Memory Usage: ≤512MB under load
- CPU Usage: ≤70% under load

Architecture Compliance:
- SOLID principles validation
- Production monitoring standards
- Performance testing best practices
- Quality assurance methodology
"""

import asyncio
import logging
import json
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import statistics

from app.services.voice_v2.performance.integration_monitor import (
    IntegrationPerformanceMonitor, LoadTestConfig, PerformanceBaseline,
    PerformanceStatus, AlertSeverity
)
from app.services.voice_v2.performance.stt_optimizer import STTPerformanceOptimizer
from app.services.voice_v2.performance.tts_optimizer import TTSPerformanceOptimizer
from app.services.voice_v2.performance.langgraph_optimizer import VoiceDecisionOptimizer

logger = logging.getLogger(__name__)


class ValidationStatus(Enum):
    """Performance validation status"""
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    NOT_TESTED = "not_tested"


class ProductionReadiness(Enum):
    """Production readiness levels"""
    READY = "ready"              # ≥95% compliance
    CONDITIONAL = "conditional"   # 85-94% compliance
    NOT_READY = "not_ready"      # <85% compliance
    UNKNOWN = "unknown"          # Insufficient data


@dataclass
class ValidationTestCase:
    """Individual validation test case"""
    test_id: str
    name: str
    description: str
    target_value: float
    threshold_warning: float
    threshold_critical: float
    actual_value: Optional[float] = None
    status: ValidationStatus = ValidationStatus.NOT_TESTED
    message: str = ""
    timestamp: Optional[datetime] = None


@dataclass
class ValidationReport:
    """Comprehensive validation report"""
    report_id: str
    timestamp: datetime
    test_duration_seconds: float
    overall_status: ValidationStatus
    production_readiness: ProductionReadiness
    compliance_percentage: float
    
    # Test results
    test_cases: List[ValidationTestCase]
    passed_tests: int
    failed_tests: int
    warning_tests: int
    
    # Performance metrics
    stt_performance: Dict[str, float]
    tts_performance: Dict[str, float]
    integration_performance: Dict[str, float]
    resource_usage: Dict[str, float]
    
    # Load test results
    load_test_results: Optional[Dict[str, Any]] = None
    
    # Recommendations
    recommendations: List[str] = field(default_factory=list)
    critical_issues: List[str] = field(default_factory=list)
    improvement_areas: List[str] = field(default_factory=list)


class PerformanceValidationSuite:
    """
    Performance Validation Suite
    
    Comprehensive validation system for voice_v2 integration performance:
    - End-to-end performance testing
    - Production readiness assessment
    - Regression testing capability
    - Detailed reporting и certification
    """
    
    def __init__(self):
        # Target performance metrics
        self.target_stt_latency = 3.5  # seconds
        self.target_tts_latency = 3.0  # seconds
        self.target_success_rate = 95.0  # percentage
        self.target_cache_hit_rate = 85.0  # percentage
        self.target_memory_usage = 512.0  # MB
        self.target_cpu_usage = 70.0  # percentage
        
        # Validation components
        self.monitor: Optional[IntegrationPerformanceMonitor] = None
        self.stt_optimizer: Optional[STTPerformanceOptimizer] = None
        self.tts_optimizer: Optional[TTSPerformanceOptimizer] = None
        self.decision_optimizer: Optional[VoiceDecisionOptimizer] = None
        
        # Test results
        self.validation_history: List[ValidationReport] = []
        
        logger.info("PerformanceValidationSuite initialized")
    
    def configure_components(self,
                           monitor: IntegrationPerformanceMonitor,
                           stt_optimizer: STTPerformanceOptimizer,
                           tts_optimizer: TTSPerformanceOptimizer,
                           decision_optimizer: VoiceDecisionOptimizer) -> None:
        """Configure validation components"""
        self.monitor = monitor
        self.stt_optimizer = stt_optimizer
        self.tts_optimizer = tts_optimizer
        self.decision_optimizer = decision_optimizer
        
        # Configure monitor with optimizers
        monitor.set_optimizers(stt_optimizer, tts_optimizer, decision_optimizer)
        
        logger.info("Validation components configured")
    
    async def run_full_validation(self, test_duration_minutes: int = 10) -> ValidationReport:
        """
        Run comprehensive performance validation.
        
        Args:
            test_duration_minutes: Duration for load testing
            
        Returns:
            Detailed validation report
        """
        if not all([self.monitor, self.stt_optimizer, self.tts_optimizer, self.decision_optimizer]):
            raise ValueError("All components must be configured before validation")
        
        logger.info(f"Starting full performance validation ({test_duration_minutes} minutes)")
        
        start_time = time.time()
        report_id = f"validation_{int(start_time)}"
        
        # Initialize test cases
        test_cases = self._create_test_cases()
        
        try:
            # 1. Pre-validation component health check
            await self._validate_component_health(test_cases)
            
            # 2. Performance optimization validation
            await self._validate_optimization_effectiveness(test_cases)
            
            # 3. Load testing validation
            load_test_results = await self._run_load_test_validation(test_duration_minutes, test_cases)
            
            # 4. Resource usage validation
            await self._validate_resource_usage(test_cases)
            
            # 5. Cache performance validation
            await self._validate_cache_performance(test_cases)
            
            # 6. End-to-end latency validation
            await self._validate_end_to_end_performance(test_cases)
            
            # Generate comprehensive report
            validation_report = self._generate_validation_report(
                report_id, start_time, test_cases, load_test_results
            )
            
            self.validation_history.append(validation_report)
            
            logger.info(f"Validation completed: {validation_report.overall_status.value}, "
                       f"Compliance: {validation_report.compliance_percentage:.1f}%")
            
            return validation_report
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            # Return failed validation report
            return self._generate_error_report(report_id, start_time, str(e), test_cases)
    
    def _create_test_cases(self) -> List[ValidationTestCase]:
        """Create comprehensive test cases for validation"""
        return [
            # STT Performance Tests
            ValidationTestCase(
                test_id="stt_average_latency",
                name="STT Average Latency",
                description="Average STT processing latency should be ≤3.5s",
                target_value=self.target_stt_latency,
                threshold_warning=self.target_stt_latency * 1.1,
                threshold_critical=self.target_stt_latency * 1.3
            ),
            ValidationTestCase(
                test_id="stt_p95_latency",
                name="STT P95 Latency",
                description="95th percentile STT latency should be ≤4.5s",
                target_value=self.target_stt_latency * 1.3,
                threshold_warning=self.target_stt_latency * 1.4,
                threshold_critical=self.target_stt_latency * 1.6
            ),
            ValidationTestCase(
                test_id="stt_success_rate",
                name="STT Success Rate",
                description="STT processing success rate should be ≥95%",
                target_value=self.target_success_rate,
                threshold_warning=90.0,
                threshold_critical=85.0
            ),
            
            # TTS Performance Tests
            ValidationTestCase(
                test_id="tts_average_latency",
                name="TTS Average Latency",
                description="Average TTS processing latency should be ≤3.0s",
                target_value=self.target_tts_latency,
                threshold_warning=self.target_tts_latency * 1.1,
                threshold_critical=self.target_tts_latency * 1.3
            ),
            ValidationTestCase(
                test_id="tts_p95_latency",
                name="TTS P95 Latency",
                description="95th percentile TTS latency should be ≤4.0s",
                target_value=self.target_tts_latency * 1.3,
                threshold_warning=self.target_tts_latency * 1.4,
                threshold_critical=self.target_tts_latency * 1.6
            ),
            ValidationTestCase(
                test_id="tts_success_rate",
                name="TTS Success Rate",
                description="TTS processing success rate should be ≥95%",
                target_value=self.target_success_rate,
                threshold_warning=90.0,
                threshold_critical=85.0
            ),
            
            # Integration Performance Tests
            ValidationTestCase(
                test_id="decision_latency",
                name="Voice Decision Latency",
                description="Voice decision processing should be ≤500ms",
                target_value=0.5,
                threshold_warning=0.7,
                threshold_critical=1.0
            ),
            ValidationTestCase(
                test_id="end_to_end_latency",
                name="End-to-End Latency",
                description="Total voice processing latency should be ≤7.0s",
                target_value=7.0,
                threshold_warning=8.0,
                threshold_critical=10.0
            ),
            
            # Cache Performance Tests
            ValidationTestCase(
                test_id="stt_cache_hit_rate",
                name="STT Cache Hit Rate",
                description="STT cache hit rate should be ≥85%",
                target_value=self.target_cache_hit_rate,
                threshold_warning=75.0,
                threshold_critical=60.0
            ),
            ValidationTestCase(
                test_id="tts_cache_hit_rate",
                name="TTS Cache Hit Rate",
                description="TTS cache hit rate should be ≥80%",
                target_value=80.0,
                threshold_warning=70.0,
                threshold_critical=55.0
            ),
            
            # Resource Usage Tests
            ValidationTestCase(
                test_id="memory_usage",
                name="Memory Usage",
                description="Peak memory usage should be ≤512MB",
                target_value=self.target_memory_usage,
                threshold_warning=self.target_memory_usage * 1.2,
                threshold_critical=self.target_memory_usage * 1.5
            ),
            ValidationTestCase(
                test_id="cpu_usage",
                name="CPU Usage",
                description="Average CPU usage should be ≤70%",
                target_value=self.target_cpu_usage,
                threshold_warning=self.target_cpu_usage * 1.1,
                threshold_critical=self.target_cpu_usage * 1.3
            ),
            
            # Throughput Tests
            ValidationTestCase(
                test_id="throughput_rps",
                name="Request Throughput",
                description="System should handle ≥10 requests/second",
                target_value=10.0,
                threshold_warning=8.0,
                threshold_critical=5.0
            )
        ]
    
    async def _validate_component_health(self, test_cases: List[ValidationTestCase]) -> None:
        """Validate component health and basic functionality"""
        logger.info("Validating component health")
        
        # Check STT optimizer health
        if self.stt_optimizer:
            summary = self.stt_optimizer.get_performance_summary()
            if summary.get("health", {}).get("status") != "healthy":
                logger.warning("STT optimizer health check failed")
        
        # Check TTS optimizer health
        if self.tts_optimizer:
            summary = self.tts_optimizer.get_performance_summary()
            if summary.get("health", {}).get("status") != "healthy":
                logger.warning("TTS optimizer health check failed")
        
        # Check decision optimizer health
        if self.decision_optimizer:
            summary = self.decision_optimizer.get_performance_summary()
            if summary.get("overall", {}).get("health_status") != "healthy":
                logger.warning("Decision optimizer health check failed")
    
    async def _validate_optimization_effectiveness(self, test_cases: List[ValidationTestCase]) -> None:
        """Validate optimization effectiveness"""
        logger.info("Validating optimization effectiveness")
        
        # Trigger optimizations and measure improvement
        if self.stt_optimizer:
            await self.stt_optimizer.optimize_if_needed()
        
        if self.tts_optimizer:
            await self.tts_optimizer.optimize_if_needed()
        
        if self.decision_optimizer:
            await self.decision_optimizer.optimize_if_needed()
        
        # Allow time for optimizations to take effect
        await asyncio.sleep(5)
    
    async def _run_load_test_validation(self, duration_minutes: int, 
                                      test_cases: List[ValidationTestCase]) -> Dict[str, Any]:
        """Run load test validation"""
        logger.info(f"Running load test validation ({duration_minutes} minutes)")
        
        # Configure load test
        load_config = LoadTestConfig(
            concurrent_users=15,  # Increased load for validation
            test_duration_seconds=duration_minutes * 60,
            ramp_up_seconds=30,
            voice_message_ratio=0.8,  # High voice usage
            target_stt_latency=self.target_stt_latency,
            target_tts_latency=self.target_tts_latency,
            target_success_rate=self.target_success_rate
        )
        
        # Update monitor config
        self.monitor.config = load_config
        
        # Run load test
        load_results = await self.monitor.run_load_test()
        
        # Update test cases with load test results
        self._update_test_cases_from_load_test(test_cases, load_results)
        
        return load_results
    
    def _update_test_cases_from_load_test(self, test_cases: List[ValidationTestCase], 
                                        load_results: Dict[str, Any]) -> None:
        """Update test cases with load test results"""
        component_perf = load_results.get("component_performance", {})
        resource_usage = load_results.get("resource_usage", {})
        performance_results = load_results.get("performance_results", {})
        
        # Update test cases
        test_case_map = {tc.test_id: tc for tc in test_cases}
        
        # STT metrics
        if "stt" in component_perf:
            stt_data = component_perf["stt"]
            
            if "stt_average_latency" in test_case_map:
                tc = test_case_map["stt_average_latency"]
                tc.actual_value = stt_data.get("average_latency", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
            
            if "stt_p95_latency" in test_case_map:
                tc = test_case_map["stt_p95_latency"]
                tc.actual_value = stt_data.get("p95_latency", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
            
            if "stt_success_rate" in test_case_map:
                tc = test_case_map["stt_success_rate"]
                tc.actual_value = stt_data.get("success_rate", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
        
        # TTS metrics
        if "tts" in component_perf:
            tts_data = component_perf["tts"]
            
            if "tts_average_latency" in test_case_map:
                tc = test_case_map["tts_average_latency"]
                tc.actual_value = tts_data.get("average_latency", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
            
            if "tts_p95_latency" in test_case_map:
                tc = test_case_map["tts_p95_latency"]
                tc.actual_value = tts_data.get("p95_latency", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
            
            if "tts_success_rate" in test_case_map:
                tc = test_case_map["tts_success_rate"]
                tc.actual_value = tts_data.get("success_rate", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
        
        # Decision metrics
        if "decisions" in component_perf:
            decision_data = component_perf["decisions"]
            
            if "decision_latency" in test_case_map:
                tc = test_case_map["decision_latency"]
                tc.actual_value = decision_data.get("average_latency", 0)
                tc.status = self._evaluate_test_case(tc)
                tc.timestamp = datetime.now()
        
        # Overall metrics
        if "end_to_end_latency" in test_case_map:
            tc = test_case_map["end_to_end_latency"]
            tc.actual_value = performance_results.get("average_total_latency", 0)
            tc.status = self._evaluate_test_case(tc)
            tc.timestamp = datetime.now()
        
        if "throughput_rps" in test_case_map:
            tc = test_case_map["throughput_rps"]
            tc.actual_value = performance_results.get("throughput_rps", 0)
            tc.status = self._evaluate_test_case(tc)
            tc.timestamp = datetime.now()
        
        # Resource usage
        if "memory_usage" in test_case_map:
            tc = test_case_map["memory_usage"]
            tc.actual_value = resource_usage.get("peak_memory_mb", 0)
            tc.status = self._evaluate_test_case(tc)
            tc.timestamp = datetime.now()
        
        if "cpu_usage" in test_case_map:
            tc = test_case_map["cpu_usage"]
            tc.actual_value = resource_usage.get("average_cpu_percent", 0)
            tc.status = self._evaluate_test_case(tc)
            tc.timestamp = datetime.now()
    
    async def _validate_resource_usage(self, test_cases: List[ValidationTestCase]) -> None:
        """Validate resource usage patterns"""
        logger.info("Validating resource usage")
        
        # Resource validation already handled in load test
        # This could include additional specific resource tests
        pass
    
    async def _validate_cache_performance(self, test_cases: List[ValidationTestCase]) -> None:
        """Validate cache performance"""
        logger.info("Validating cache performance")
        
        # Get cache metrics from optimizers
        test_case_map = {tc.test_id: tc for tc in test_cases}
        
        if self.stt_optimizer and "stt_cache_hit_rate" in test_case_map:
            summary = self.stt_optimizer.get_performance_summary()
            tc = test_case_map["stt_cache_hit_rate"]
            tc.actual_value = summary.get("cache", {}).get("hit_rate", 0)
            tc.status = self._evaluate_test_case(tc)
            tc.timestamp = datetime.now()
        
        if self.tts_optimizer and "tts_cache_hit_rate" in test_case_map:
            summary = self.tts_optimizer.get_performance_summary()
            tc = test_case_map["tts_cache_hit_rate"]
            tc.actual_value = summary.get("cache", {}).get("hit_rate", 0)
            tc.status = self._evaluate_test_case(tc)
            tc.timestamp = datetime.now()
    
    async def _validate_end_to_end_performance(self, test_cases: List[ValidationTestCase]) -> None:
        """Validate end-to-end performance"""
        logger.info("Validating end-to-end performance")
        
        # End-to-end validation already handled in load test
        # This could include additional specific E2E tests
        pass
    
    def _evaluate_test_case(self, test_case: ValidationTestCase) -> ValidationStatus:
        """Evaluate test case status based on actual vs target values"""
        if test_case.actual_value is None:
            return ValidationStatus.NOT_TESTED
        
        actual = test_case.actual_value
        
        # For latency and resource metrics (lower is better)
        if test_case.test_id.endswith("_latency") or test_case.test_id.endswith("_usage"):
            if actual <= test_case.target_value:
                test_case.message = f"PASSED: {actual:.2f} ≤ {test_case.target_value:.2f}"
                return ValidationStatus.PASSED
            elif actual <= test_case.threshold_warning:
                test_case.message = f"WARNING: {actual:.2f} > {test_case.target_value:.2f}"
                return ValidationStatus.WARNING
            else:
                test_case.message = f"FAILED: {actual:.2f} > {test_case.threshold_critical:.2f}"
                return ValidationStatus.FAILED
        
        # For success rates and hit rates (higher is better)
        else:
            if actual >= test_case.target_value:
                test_case.message = f"PASSED: {actual:.1f}% ≥ {test_case.target_value:.1f}%"
                return ValidationStatus.PASSED
            elif actual >= test_case.threshold_warning:
                test_case.message = f"WARNING: {actual:.1f}% < {test_case.target_value:.1f}%"
                return ValidationStatus.WARNING
            else:
                test_case.message = f"FAILED: {actual:.1f}% < {test_case.threshold_critical:.1f}%"
                return ValidationStatus.FAILED
    
    def _generate_validation_report(self, report_id: str, start_time: float,
                                  test_cases: List[ValidationTestCase],
                                  load_test_results: Optional[Dict[str, Any]]) -> ValidationReport:
        """Generate comprehensive validation report"""
        end_time = time.time()
        test_duration = end_time - start_time
        
        # Count test results
        passed_tests = sum(1 for tc in test_cases if tc.status == ValidationStatus.PASSED)
        failed_tests = sum(1 for tc in test_cases if tc.status == ValidationStatus.FAILED)
        warning_tests = sum(1 for tc in test_cases if tc.status == ValidationStatus.WARNING)
        
        # Calculate compliance percentage
        total_tests = len([tc for tc in test_cases if tc.status != ValidationStatus.NOT_TESTED])
        compliance_percentage = (passed_tests / total_tests * 100) if total_tests > 0 else 0
        
        # Determine overall status
        if failed_tests > 0:
            overall_status = ValidationStatus.FAILED
        elif warning_tests > 0:
            overall_status = ValidationStatus.WARNING
        else:
            overall_status = ValidationStatus.PASSED
        
        # Determine production readiness
        if compliance_percentage >= 95:
            production_readiness = ProductionReadiness.READY
        elif compliance_percentage >= 85:
            production_readiness = ProductionReadiness.CONDITIONAL
        else:
            production_readiness = ProductionReadiness.NOT_READY
        
        # Extract performance metrics
        stt_performance = {}
        tts_performance = {}
        integration_performance = {}
        resource_usage = {}
        
        if load_test_results:
            component_perf = load_test_results.get("component_performance", {})
            resource_data = load_test_results.get("resource_usage", {})
            
            stt_performance = component_perf.get("stt", {})
            tts_performance = component_perf.get("tts", {})
            integration_performance = load_test_results.get("performance_results", {})
            resource_usage = resource_data
        
        # Generate recommendations
        recommendations = self._generate_recommendations(test_cases, overall_status)
        critical_issues = self._identify_critical_issues(test_cases)
        improvement_areas = self._identify_improvement_areas(test_cases)
        
        return ValidationReport(
            report_id=report_id,
            timestamp=datetime.now(),
            test_duration_seconds=test_duration,
            overall_status=overall_status,
            production_readiness=production_readiness,
            compliance_percentage=compliance_percentage,
            test_cases=test_cases,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            warning_tests=warning_tests,
            stt_performance=stt_performance,
            tts_performance=tts_performance,
            integration_performance=integration_performance,
            resource_usage=resource_usage,
            load_test_results=load_test_results,
            recommendations=recommendations,
            critical_issues=critical_issues,
            improvement_areas=improvement_areas
        )
    
    def _generate_error_report(self, report_id: str, start_time: float, 
                             error_message: str, test_cases: List[ValidationTestCase]) -> ValidationReport:
        """Generate error validation report"""
        return ValidationReport(
            report_id=report_id,
            timestamp=datetime.now(),
            test_duration_seconds=time.time() - start_time,
            overall_status=ValidationStatus.FAILED,
            production_readiness=ProductionReadiness.NOT_READY,
            compliance_percentage=0.0,
            test_cases=test_cases,
            passed_tests=0,
            failed_tests=len(test_cases),
            warning_tests=0,
            stt_performance={},
            tts_performance={},
            integration_performance={},
            resource_usage={},
            recommendations=[f"Resolve validation error: {error_message}"],
            critical_issues=[f"Validation failed with error: {error_message}"],
            improvement_areas=["Fix validation setup and configuration"]
        )
    
    def _generate_recommendations(self, test_cases: List[ValidationTestCase], 
                                overall_status: ValidationStatus) -> List[str]:
        """Generate recommendations based on test results"""
        recommendations = []
        
        if overall_status == ValidationStatus.PASSED:
            recommendations.append("✅ All performance targets met - ready for production deployment")
            recommendations.append("Continue monitoring performance metrics in production")
            recommendations.append("Consider implementing performance regression testing")
        
        elif overall_status == ValidationStatus.WARNING:
            recommendations.append("⚠️ Some performance targets not met - conditional production readiness")
            recommendations.append("Monitor warning metrics closely during initial deployment")
            recommendations.append("Plan performance optimizations for next iteration")
        
        else:
            recommendations.append("❌ Critical performance issues identified - not ready for production")
            recommendations.append("Address failing test cases before deployment")
            recommendations.append("Consider scaling infrastructure or optimizing algorithms")
        
        # Specific recommendations based on failing tests
        for tc in test_cases:
            if tc.status == ValidationStatus.FAILED:
                if "latency" in tc.test_id:
                    recommendations.append(f"Optimize {tc.test_id} - consider caching, parallel processing, or provider tuning")
                elif "cache" in tc.test_id:
                    recommendations.append(f"Improve {tc.test_id} - adjust cache TTL, size, or invalidation strategy")
                elif "usage" in tc.test_id:
                    recommendations.append(f"Optimize {tc.test_id} - review resource allocation and algorithms")
        
        return recommendations
    
    def _identify_critical_issues(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Identify critical issues from test results"""
        critical_issues = []
        
        for tc in test_cases:
            if tc.status == ValidationStatus.FAILED:
                critical_issues.append(f"{tc.name}: {tc.message}")
        
        return critical_issues
    
    def _identify_improvement_areas(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Identify improvement areas from test results"""
        improvement_areas = []
        
        for tc in test_cases:
            if tc.status == ValidationStatus.WARNING:
                improvement_areas.append(f"{tc.name}: {tc.message}")
        
        return improvement_areas
    
    def export_validation_report(self, report: ValidationReport, 
                               format_type: str = "json") -> str:
        """Export validation report in specified format"""
        if format_type == "json":
            return self._export_json_report(report)
        elif format_type == "markdown":
            return self._export_markdown_report(report)
        else:
            raise ValueError(f"Unsupported format: {format_type}")
    
    def _export_json_report(self, report: ValidationReport) -> str:
        """Export report as JSON"""
        report_dict = {
            "report_id": report.report_id,
            "timestamp": report.timestamp.isoformat(),
            "test_duration_seconds": report.test_duration_seconds,
            "overall_status": report.overall_status.value,
            "production_readiness": report.production_readiness.value,
            "compliance_percentage": report.compliance_percentage,
            "summary": {
                "passed_tests": report.passed_tests,
                "failed_tests": report.failed_tests,
                "warning_tests": report.warning_tests,
                "total_tests": len(report.test_cases)
            },
            "test_cases": [
                {
                    "test_id": tc.test_id,
                    "name": tc.name,
                    "description": tc.description,
                    "target_value": tc.target_value,
                    "actual_value": tc.actual_value,
                    "status": tc.status.value,
                    "message": tc.message,
                    "timestamp": tc.timestamp.isoformat() if tc.timestamp else None
                }
                for tc in report.test_cases
            ],
            "performance_metrics": {
                "stt_performance": report.stt_performance,
                "tts_performance": report.tts_performance,
                "integration_performance": report.integration_performance,
                "resource_usage": report.resource_usage
            },
            "recommendations": report.recommendations,
            "critical_issues": report.critical_issues,
            "improvement_areas": report.improvement_areas
        }
        
        return json.dumps(report_dict, indent=2)
    
    def _export_markdown_report(self, report: ValidationReport) -> str:
        """Export report as Markdown"""
        md_content = f"""# Performance Validation Report

**Report ID:** {report.report_id}  
**Timestamp:** {report.timestamp.strftime('%Y-%m-%d %H:%M:%S')}  
**Test Duration:** {report.test_duration_seconds:.1f} seconds  
**Overall Status:** {report.overall_status.value.upper()}  
**Production Readiness:** {report.production_readiness.value.upper()}  
**Compliance:** {report.compliance_percentage:.1f}%

## Summary

- ✅ **Passed Tests:** {report.passed_tests}
- ⚠️ **Warning Tests:** {report.warning_tests}
- ❌ **Failed Tests:** {report.failed_tests}
- 📊 **Total Tests:** {len(report.test_cases)}

## Test Results

| Test Case | Target | Actual | Status | Message |
|-----------|--------|--------|--------|---------|
"""
        
        for tc in report.test_cases:
            status_emoji = {"passed": "✅", "warning": "⚠️", "failed": "❌", "not_tested": "⏸️"}.get(tc.status.value, "❓")
            actual_val = f"{tc.actual_value:.2f}" if tc.actual_value is not None else "N/A"
            md_content += f"| {tc.name} | {tc.target_value:.2f} | {actual_val} | {status_emoji} {tc.status.value} | {tc.message} |\n"
        
        if report.critical_issues:
            md_content += f"\n## Critical Issues\n\n"
            for issue in report.critical_issues:
                md_content += f"- ❌ {issue}\n"
        
        if report.improvement_areas:
            md_content += f"\n## Improvement Areas\n\n"
            for area in report.improvement_areas:
                md_content += f"- ⚠️ {area}\n"
        
        if report.recommendations:
            md_content += f"\n## Recommendations\n\n"
            for rec in report.recommendations:
                md_content += f"- {rec}\n"
        
        return md_content
