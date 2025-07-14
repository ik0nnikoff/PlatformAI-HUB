"""
Мастер-скрипт для запуска всех типов тестирования голосового workflow
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
import argparse

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('voice_testing_master.log')
    ]
)

logger = logging.getLogger("voice_testing_master")


class VoiceTestingMaster:
    """Мастер-класс для управления всеми видами тестирования"""
    
    def __init__(self):
        self.results = {
            "session_id": f"test_session_{int(time.time())}",
            "start_time": datetime.now().isoformat(),
            "tests": {},
            "summary": {}
        }
    
    async def run_workflow_automation_tests(self) -> dict:
        """Запуск автоматизированных workflow тестов"""
        logger.info("🎙️ Starting workflow automation tests...")
        
        try:
            from test_voice_workflow_automation import VoiceWorkflowTester
            
            tester = VoiceWorkflowTester()
            results = await tester.run_comprehensive_test_suite()
            
            return {
                "type": "workflow_automation",
                "status": "completed",
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Workflow automation tests failed: {e}")
            return {
                "type": "workflow_automation",
                "status": "failed",
                "error": str(e)
            }
    
    async def run_performance_tests(self) -> dict:
        """Запуск тестов производительности"""
        logger.info("🎪 Starting performance tests...")
        
        try:
            from test_voice_performance import run_performance_test_suite
            
            results = await run_performance_test_suite()
            
            return {
                "type": "performance",
                "status": "completed",
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Performance tests failed: {e}")
            return {
                "type": "performance", 
                "status": "failed",
                "error": str(e)
            }
    
    async def run_integration_tests(self) -> dict:
        """Запуск интеграционных тестов"""
        logger.info("🔌 Starting integration tests...")
        
        try:
            from test_voice_integration import run_integration_test_suite
            
            results = await run_integration_test_suite()
            
            return {
                "type": "integration",
                "status": "completed", 
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Integration tests failed: {e}")
            return {
                "type": "integration",
                "status": "failed",
                "error": str(e)
            }
    
    async def run_all_tests(self, test_types: list = None) -> dict:
        """Запуск всех или выбранных типов тестов"""
        
        if test_types is None:
            test_types = ["workflow", "integration", "performance"]
        
        logger.info(f"🚀 Starting comprehensive testing suite: {test_types}")
        
        # Запускаем тесты последовательно для стабильности
        if "workflow" in test_types:
            workflow_results = await self.run_workflow_automation_tests()
            self.results["tests"]["workflow_automation"] = workflow_results
            
            # Пауза между тестами
            await asyncio.sleep(2)
        
        if "integration" in test_types:
            integration_results = await self.run_integration_tests()
            self.results["tests"]["integration"] = integration_results
            
            # Пауза между тестами
            await asyncio.sleep(2)
        
        if "performance" in test_types:
            performance_results = await self.run_performance_tests()
            self.results["tests"]["performance"] = performance_results
        
        # Генерируем итоговую сводку
        self.results["end_time"] = datetime.now().isoformat()
        self.results["summary"] = self._generate_summary()
        
        return self.results
    
    def _generate_summary(self) -> dict:
        """Генерация итоговой сводки результатов"""
        summary = {
            "total_test_types": len(self.results["tests"]),
            "completed_test_types": sum(1 for t in self.results["tests"].values() if t.get("status") == "completed"),
            "failed_test_types": sum(1 for t in self.results["tests"].values() if t.get("status") == "failed"),
            "overall_success": True,
            "detailed_stats": {}
        }
        
        # Анализ по типам тестов
        for test_type, test_result in self.results["tests"].items():
            if test_result.get("status") == "completed":
                test_data = test_result.get("results", {})
                
                if test_type == "workflow_automation":
                    workflow_summary = test_data.get("summary", {})
                    summary["detailed_stats"]["workflow"] = {
                        "total_tests": workflow_summary.get("total_tests", 0),
                        "passed_tests": workflow_summary.get("passed_tests", 0),
                        "success_rate": workflow_summary.get("success_rate", "0%")
                    }
                
                elif test_type == "integration":
                    integration_tests = test_data.get("tests", {})
                    passed_integration = sum(1 for t in integration_tests.values() 
                                           if t.get("status") == "passed" or t.get("overall_status") == "passed")
                    total_integration = len(integration_tests)
                    
                    summary["detailed_stats"]["integration"] = {
                        "total_tests": total_integration,
                        "passed_tests": passed_integration,
                        "success_rate": f"{(passed_integration/total_integration)*100:.1f}%" if total_integration > 0 else "0%"
                    }
                
                elif test_type == "performance":
                    performance_tests = test_data.get("tests", {})
                    summary["detailed_stats"]["performance"] = {
                        "stress_test_completed": "stress_test" in performance_tests,
                        "load_test_completed": "load_test" in performance_tests,
                        "memory_test_completed": "memory_test" in performance_tests
                    }
            
            else:
                summary["overall_success"] = False
        
        return summary
    
    def print_final_report(self):
        """Печать финального отчета"""
        print("\n" + "="*60)
        print("🎯 VOICE WORKFLOW TESTING - FINAL REPORT")
        print("="*60)
        
        print(f"\n📋 Session ID: {self.results['session_id']}")
        print(f"⏰ Start Time: {self.results['start_time']}")
        print(f"🏁 End Time: {self.results['end_time']}")
        
        summary = self.results["summary"]
        
        print(f"\n📊 OVERALL SUMMARY:")
        print(f"   Test Types Run: {summary['completed_test_types']}/{summary['total_test_types']}")
        print(f"   Failed Test Types: {summary['failed_test_types']}")
        print(f"   Overall Success: {'✅ YES' if summary['overall_success'] else '❌ NO'}")
        
        # Детализированная статистика
        print(f"\n📈 DETAILED STATISTICS:")
        
        for test_type, stats in summary["detailed_stats"].items():
            print(f"\n   {test_type.upper()}:")
            
            if test_type in ["workflow", "integration"]:
                print(f"     Total Tests: {stats.get('total_tests', 0)}")
                print(f"     Passed Tests: {stats.get('passed_tests', 0)}")
                print(f"     Success Rate: {stats.get('success_rate', '0%')}")
            
            elif test_type == "performance":
                print(f"     Stress Test: {'✅' if stats.get('stress_test_completed') else '❌'}")
                print(f"     Load Test: {'✅' if stats.get('load_test_completed') else '❌'}")
                print(f"     Memory Test: {'✅' if stats.get('memory_test_completed') else '❌'}")
        
        # Информация о неудачных тестах
        failed_tests = [name for name, result in self.results["tests"].items() 
                       if result.get("status") == "failed"]
        
        if failed_tests:
            print(f"\n❌ FAILED TEST TYPES:")
            for failed_test in failed_tests:
                error = self.results["tests"][failed_test].get("error", "Unknown error")
                print(f"   {failed_test}: {error}")
        
        # Рекомендации
        print(f"\n💡 RECOMMENDATIONS:")
        if summary["overall_success"]:
            print("   ✅ All tests passed! Voice workflow is functioning correctly.")
            print("   ✅ System is ready for production use.")
        else:
            print("   ❌ Some tests failed. Please review the errors above.")
            print("   ❌ Fix the issues before deploying to production.")
        
        print("\n" + "="*60)
    
    def save_results(self, filename: str = None):
        """Сохранение результатов в файл"""
        if filename is None:
            filename = f"voice_testing_master_results_{self.results['session_id']}.json"
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False, default=str)
        
        logger.info(f"💾 Master results saved to: {filename}")
        return filename


async def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(description="Voice Workflow Testing Master")
    parser.add_argument(
        "--tests", 
        nargs="+", 
        choices=["workflow", "integration", "performance", "all"],
        default=["all"],
        help="Types of tests to run"
    )
    parser.add_argument(
        "--save-to",
        type=str,
        help="Custom filename to save results"
    )
    
    args = parser.parse_args()
    
    # Определяем какие тесты запускать
    if "all" in args.tests:
        test_types = ["workflow", "integration", "performance"]
    else:
        test_types = args.tests
    
    # Создаем мастер-тестер
    master = VoiceTestingMaster()
    
    try:
        # Запускаем тесты
        results = await master.run_all_tests(test_types)
        
        # Выводим финальный отчет
        master.print_final_report()
        
        # Сохраняем результаты
        results_file = master.save_results(args.save_to)
        
        print(f"\n📁 All results saved to: {results_file}")
        
        # Возвращаем код выхода
        return 0 if results["summary"]["overall_success"] else 1
        
    except KeyboardInterrupt:
        logger.info("Testing interrupted by user")
        return 130
    
    except Exception as e:
        logger.error(f"Testing failed with unexpected error: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
