"""
Performance Validation Reporter - Report Generation and Export

Отвечает за создание и экспорт отчетов о валидации производительности:
- Генерация ValidationReport объектов
- JSON и Markdown экспорт
- Анализ результатов и рекомендации
- Форматирование данных для различных потребителей
"""

import json
import logging
import time
from typing import Dict, List, Any
from datetime import datetime

try:
    from .validation_models import (
        ValidationReport,
        ValidationTestCase,
        ValidationStatus,
        ProductionReadiness
    )
except ImportError:
    # Fallback for direct execution
    from validation_models import (
        ValidationReport,
        ValidationTestCase,
        ValidationStatus,
        ProductionReadiness
    )

logger = logging.getLogger(__name__)


class ValidationReporter:
    """Handles validation report generation and export"""

    def generate_validation_report(self, report_id: str, start_time: float,
                                   test_cases: List[ValidationTestCase]) -> ValidationReport:
        """Generate comprehensive validation report"""
        end_time = time.time()
        total_duration = end_time - start_time

        # Calculate overall status
        overall_status = self._calculate_overall_status(test_cases)
        production_readiness = self._assess_production_readiness(test_cases)

        # Generate performance summary
        performance_summary = self._generate_performance_summary(test_cases)

        # Generate recommendations and issues
        recommendations = self._generate_recommendations(test_cases)
        critical_issues = self._identify_critical_issues(test_cases)
        improvement_areas = self._identify_improvement_areas(test_cases)

        return ValidationReport(
            report_id=report_id,
            timestamp=datetime.now(),
            total_duration=total_duration,
            test_cases=test_cases,
            overall_status=overall_status,
            production_readiness=production_readiness,
            performance_summary=performance_summary,
            recommendations=recommendations,
            critical_issues=critical_issues,
            improvement_areas=improvement_areas,
            metadata={
                "validation_engine_version": "5.4.0",
                "test_categories": list(set(tc.category for tc in test_cases)),
                "execution_environment": "voice_v2_validation"
            }
        )

    def generate_error_report(
        self,
        report_id: str,
        start_time: float,
        error_message: str,
        test_cases: List[ValidationTestCase]
    ) -> ValidationReport:
        """Generate error report when validation fails"""
        end_time = time.time()
        total_duration = end_time - start_time

        return ValidationReport(
            report_id=report_id,
            timestamp=datetime.now(),
            total_duration=total_duration,
            test_cases=test_cases,
            overall_status=ValidationStatus.FAILED,
            production_readiness=ProductionReadiness.CRITICAL_ISSUES,
            performance_summary={"error": error_message},
            recommendations=[
                "Fix validation execution error before proceeding",
                "Check component initialization and configuration",
                "Review logs for detailed error information"
            ],
            critical_issues=[
                f"Validation execution failed: {error_message}"
            ],
            improvement_areas=["Validation stability", "Error handling"],
            metadata={
                "validation_engine_version": "5.4.0",
                "error_type": "execution_failure",
                "execution_environment": "voice_v2_validation"
            }
        )

    def export_json_report(self, report: ValidationReport) -> str:
        """Export validation report as JSON"""
        try:
            report_dict = {
                "report_id": report.report_id,
                "timestamp": report.timestamp.isoformat(),
                "total_duration": report.total_duration,
                "overall_status": report.overall_status.value,
                "production_readiness": report.production_readiness.value,
                "summary": {
                    "total_tests": len(report.test_cases),
                    "passed_tests": report.passed_tests,
                    "failed_tests": report.failed_tests,
                    "warning_tests": report.warning_tests,
                    "success_rate": report.success_rate
                },
                "test_cases": [
                    {
                        "name": tc.name,
                        "category": tc.category,
                        "target_value": tc.target_value,
                        "actual_value": tc.actual_value,
                        "status": tc.status.value,
                        "error_message": tc.error_message,
                        "execution_time": tc.execution_time,
                        "metadata": tc.metadata
                    }
                    for tc in report.test_cases
                ],
                "performance_summary": report.performance_summary,
                "recommendations": report.recommendations,
                "critical_issues": report.critical_issues,
                "improvement_areas": report.improvement_areas,
                "metadata": report.metadata
            }

            return json.dumps(report_dict, indent=2, ensure_ascii=False)

        except (TypeError, ValueError, AttributeError) as exc:
            logger.error("Failed to export JSON report: %s", exc)
            return json.dumps({"error": f"Report export failed: {exc}"}, indent=2)

    def export_markdown_report(self, report: ValidationReport) -> str:
        """Export validation report as Markdown"""
        try:
            md_content = self._generate_markdown_header(report)
            md_content += self._generate_markdown_summary(report)
            md_content += self._generate_markdown_test_results(report)
            md_content += self._generate_markdown_sections(report)
            return md_content

        except (TypeError, ValueError, AttributeError) as exc:
            logger.error("Failed to export Markdown report: %s", exc)
            return f"# Validation Report Export Error\n\nFailed to generate report: {exc}"

    def _generate_markdown_header(self, report: ValidationReport) -> str:
        """Generate markdown header section"""
        return f"""# Performance Validation Report

**Report ID**: {report.report_id}
**Timestamp**: {report.timestamp}
**Duration**: {report.total_duration:.2f}s
**Overall Status**: {report.overall_status.value.upper()}
**Production Readiness**: {report.production_readiness.value.replace('_', ' ').title()}

"""

    def _generate_markdown_summary(self, report: ValidationReport) -> str:
        """Generate markdown summary section"""
        return f"""## Summary

- **Total Tests**: {len(report.test_cases)}
- **Passed**: {report.passed_tests}
- **Failed**: {report.failed_tests}
- **Warnings**: {report.warning_tests}
- **Success Rate**: {report.success_rate:.1f}%

"""

    def _generate_markdown_test_results(self, report: ValidationReport) -> str:
        """Generate markdown test results table"""
        md_content = """## Test Results

| Test Name | Category | Target | Actual | Status |
|-----------|----------|--------|--------|--------|
"""

        for tc in report.test_cases:
            status_emoji = {
                ValidationStatus.PASSED: "✅",
                ValidationStatus.FAILED: "❌",
                ValidationStatus.WARNING: "⚠️",
                ValidationStatus.SKIPPED: "⏭️"
            }.get(tc.status, "❓")

            actual_str = f"{tc.actual_value:.2f}" if tc.actual_value is not None else "N/A"
            md_content += (f"| {tc.name} | {tc.category} | {tc.target_value:.2f} | "
                           f"{actual_str} | {status_emoji} {tc.status.value} |\n")

        return md_content

    def _generate_markdown_sections(self, report: ValidationReport) -> str:
        """Generate additional markdown sections"""
        sections = []

        if report.performance_summary:
            sections.append(self._generate_performance_section(report.performance_summary))

        if report.critical_issues:
            sections.append(self._generate_issues_section(report.critical_issues))

        if report.recommendations:
            sections.append(self._generate_recommendations_section(report.recommendations))

        if report.improvement_areas:
            sections.append(self._generate_improvements_section(report.improvement_areas))

        return "".join(sections)

    def _generate_performance_section(self, performance_summary: dict) -> str:
        """Generate performance summary section"""
        content = "\n## Performance Summary\n\n"
        for key, value in performance_summary.items():
            if isinstance(value, (int, float)):
                content += f"- **{key.replace('_', ' ').title()}**: {value:.2f}\n"
            else:
                content += f"- **{key.replace('_', ' ').title()}**: {value}\n"
        return content

    def _generate_issues_section(self, critical_issues: List[str]) -> str:
        """Generate critical issues section"""
        content = "\n## Critical Issues\n\n"
        content += "\n".join(f"- ❌ {issue}" for issue in critical_issues)
        content += "\n"
        return content

    def _generate_recommendations_section(self, recommendations: List[str]) -> str:
        """Generate recommendations section"""
        content = "\n## Recommendations\n\n"
        content += "\n".join(f"- 💡 {rec}" for rec in recommendations)
        content += "\n"
        return content

    def _generate_improvements_section(self, improvement_areas: List[str]) -> str:
        """Generate improvement areas section"""
        content = "\n## Improvement Areas\n\n"
        content += "\n".join(f"- 🔧 {area}" for area in improvement_areas)
        content += "\n"
        return content

    def _calculate_overall_status(self, test_cases: List[ValidationTestCase]) -> ValidationStatus:
        """Calculate overall validation status"""
        if not test_cases:
            return ValidationStatus.SKIPPED

        statuses = [tc.status for tc in test_cases]

        status_checks = [
            (ValidationStatus.FAILED,
             lambda s: any(status == ValidationStatus.FAILED for status in s)),
            (ValidationStatus.WARNING,
             lambda s: any(status == ValidationStatus.WARNING for status in s)),
            (ValidationStatus.PASSED,
             lambda s: all(status == ValidationStatus.PASSED for status in s))
        ]

        for target_status, check_func in status_checks:
            if check_func(statuses):
                return target_status

        return ValidationStatus.WARNING

    def _assess_production_readiness(
        self,
        test_cases: List[ValidationTestCase]
    ) -> ProductionReadiness:
        """Assess production readiness based on test results"""
        failed_tests = [
            tc for tc in test_cases
            if tc.status == ValidationStatus.FAILED
        ]
        warning_tests = [
            tc for tc in test_cases
            if tc.status == ValidationStatus.WARNING
        ]

        if self._has_critical_health_failures(failed_tests):
            return ProductionReadiness.CRITICAL_ISSUES

        if self._has_multiple_performance_failures(failed_tests):
            return ProductionReadiness.CRITICAL_ISSUES

        if failed_tests:
            return ProductionReadiness.NOT_READY

        if warning_tests:
            return ProductionReadiness.NEEDS_OPTIMIZATION

        return ProductionReadiness.READY

    def _has_critical_health_failures(
        self,
        failed_tests: List[ValidationTestCase]
    ) -> bool:
        """Check for critical component health failures"""
        return any(tc.category == "component_health" for tc in failed_tests)

    def _has_multiple_performance_failures(
        self,
        failed_tests: List[ValidationTestCase]
    ) -> bool:
        """Check for multiple performance test failures"""
        perf_failures = [
            tc for tc in failed_tests
            if tc.category in ["stt_performance", "tts_performance"]
        ]
        return len(perf_failures) >= 2

    def _generate_performance_summary(self, test_cases: List[ValidationTestCase]) -> Dict[str, Any]:
        """Generate performance summary from test results"""
        summary = {}

        # Group by category
        categories = {}
        for tc in test_cases:
            if tc.category not in categories:
                categories[tc.category] = []
            categories[tc.category].append(tc)

        # Calculate category summaries
        for category, tests in categories.items():
            passed = sum(1 for tc in tests if tc.status == ValidationStatus.PASSED)
            total = len(tests)
            success_rate = (passed / total * 100) if total > 0 else 0

            summary[f"{category}_success_rate"] = success_rate
            summary[f"{category}_tests_passed"] = passed
            summary[f"{category}_tests_total"] = total

        return summary

    def _generate_recommendations(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Generate actionable recommendations based on test results"""
        recommendations = []

        recommendations.extend(self._get_stt_recommendations(test_cases))
        recommendations.extend(self._get_tts_recommendations(test_cases))
        recommendations.extend(self._get_resource_recommendations(test_cases))
        recommendations.extend(self._get_health_recommendations(test_cases))

        if not recommendations:
            recommendations.extend(self._get_default_recommendations())

        return recommendations

    def _get_stt_recommendations(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Get STT-specific recommendations"""
        stt_tests = [tc for tc in test_cases if tc.category == "stt_performance"]
        failed_stt = [tc for tc in stt_tests if tc.status == ValidationStatus.FAILED]

        if failed_stt:
            return [
                "Optimize STT provider selection and caching strategies",
                "Review STT timeout configurations and retry policies"
            ]
        return []

    def _get_tts_recommendations(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Get TTS-specific recommendations"""
        tts_tests = [tc for tc in test_cases if tc.category == "tts_performance"]
        failed_tts = [tc for tc in tts_tests if tc.status == ValidationStatus.FAILED]

        if failed_tts:
            return [
                "Optimize TTS voice selection and synthesis parameters",
                "Implement TTS result caching and compression"
            ]
        return []

    def _get_resource_recommendations(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Get resource usage recommendations"""
        resource_tests = [tc for tc in test_cases if tc.category == "resource_usage"]
        failed_resources = [tc for tc in resource_tests if tc.status == ValidationStatus.FAILED]

        if failed_resources:
            return [
                "Optimize memory usage through better resource management",
                "Review CPU-intensive operations and add async patterns"
            ]
        return []

    def _get_health_recommendations(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Get component health recommendations"""
        health_tests = [tc for tc in test_cases if tc.category == "component_health"]
        failed_health = [tc for tc in health_tests if tc.status == ValidationStatus.FAILED]

        if failed_health:
            return [
                "Fix component initialization and health monitoring",
                "Review component dependencies and error handling"
            ]
        return []

    def _get_default_recommendations(self) -> List[str]:
        """Get default recommendations when no issues found"""
        return [
            "Performance targets met - consider production deployment",
            "Monitor performance metrics in production environment"
        ]

    def _identify_critical_issues(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Identify critical issues that block production deployment"""
        issues = []

        failed_tests = [tc for tc in test_cases if tc.status == ValidationStatus.FAILED]

        for test_case in failed_tests:
            if test_case.category == "component_health":
                issues.append(f"Component health failure: {test_case.name}")
            elif test_case.category in ["stt_performance", "tts_performance"]:
                issues.append(
                    f"Performance target missed: {
                        test_case.name} " f"(target: {
                        test_case.target_value}, actual: {
                        test_case.actual_value})")
            elif test_case.category == "resource_usage":
                issues.append(f"Resource limit exceeded: {test_case.name}")

        return issues

    def _identify_improvement_areas(self, test_cases: List[ValidationTestCase]) -> List[str]:
        """Identify areas for improvement based on warnings and near-failures"""
        areas = []

        warning_tests = [tc for tc in test_cases if tc.status == ValidationStatus.WARNING]

        for test_case in warning_tests:
            areas.append(f"{test_case.category.replace('_', ' ').title()}: {test_case.name}")

        # Add general improvement areas based on patterns
        categories = set(tc.category for tc in test_cases)

        if "cache_performance" in categories:
            areas.append("Cache optimization and hit rate improvement")

        if "stt_performance" in categories or "tts_performance" in categories:
            areas.append("Voice processing pipeline optimization")

        return list(set(areas))  # Remove duplicates
