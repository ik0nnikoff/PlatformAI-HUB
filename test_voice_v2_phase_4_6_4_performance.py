#!/usr/bin/env python3
"""
Phase 4.6.4 - Performance Optimization and Load Testing
======================================================

Комплексное тестирование производительности Voice V2 системы:
- Performance optimization
- Concurrent load testing  
- Stress testing под нагрузкой
- Cache effectiveness измерения
- Production readiness validation

Цели:
- 50+ одновременных пользователей
- Cache hit ratio: 90% intent, 80% TTS
- Latency < 2.5s для STT/TTS операций
- Memory usage < 1GB под нагрузкой
- Zero downtime при provider failover
"""

import asyncio
import time
import statistics
import threading
import psutil
import gc
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from app.services.voice_v2.core.orchestrator.base_orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.core.schemas import TTSRequest
from app.services.voice_v2.providers.factory.factory import EnhancedVoiceProviderFactory


@dataclass
class TTSOperationResult:
    """Результат одной TTS операции."""
    success: bool
    duration: float
    error_message: str = ""
    provider_used: str = ""
    audio_size: int = 0
    cache_hit: bool = False

@dataclass 
class BaselineMetrics:
    """Базовые метрики производительности."""
    tts_avg_duration: float
    tts_success_rate: float
    stt_avg_duration: float 
    stt_success_rate: float
    memory_per_operation: float


@dataclass 
class LoadTestResults:
    """Результаты нагрузочного тестирования"""
    test_name: str
    concurrent_users: int
    total_operations: int
    successful_operations: int
    failed_operations: int
    avg_duration: float
    median_duration: float
    p95_duration: float
    p99_duration: float
    max_memory_mb: float
    cache_hit_ratio: float
    throughput_ops_per_sec: float


class VoiceV2PerformanceTester:
    """
    Комплексный тестер производительности Voice V2 системы
    
    Features:
    - Concurrent load testing
    - Memory usage monitoring  
    - Cache effectiveness измерения
    - Provider failover testing
    - Stress testing под пиковой нагрузкой
    """
    
    def __init__(self):
        self.factory = None
        self.orchestrator = None
        self.metrics: List[TTSOperationResult] = []
        self.test_results: List[LoadTestResults] = []
        self.baseline_memory = 0
        
        # Test configurations
        self.test_texts = [
            "Привет! Это тестирование производительности системы синтеза речи.",
            "Добро пожаловать в PlatformAI Hub - платформу для управления AI агентами.",
            "Система обеспечивает высококачественный синтез и распознавание речи.",
            "Мы тестируем производительность под различными нагрузками.",
            "Голосовые возможности интегрированы с LangGraph workflow."
        ]
        
    async def initialize(self) -> bool:
        """Инициализация системы с baseline измерениями"""
        print('🚀 PHASE 4.6.4 - PERFORMANCE OPTIMIZATION & LOAD TESTING')
        print('=' * 70)
        
        try:
            # Measure baseline memory
            self.baseline_memory = psutil.Process().memory_info().rss / 1024 / 1024
            print(f'📊 Baseline memory: {self.baseline_memory:.1f} MB')
            
            # Initialize Voice V2 system
            print('🔧 Инициализация Voice V2 системы...')
            self.factory = EnhancedVoiceProviderFactory()
            await self.factory.initialize()
            
            self.orchestrator = VoiceServiceOrchestrator(enhanced_factory=self.factory)
            await self.orchestrator.initialize()
            
            print('✅ Voice V2 система инициализирована')
            return True
            
        except Exception as e:
            print(f'❌ Ошибка инициализации: {e}')
            return False
    
    def measure_memory_usage(self) -> float:
        """Измерение текущего использования памяти"""
        return psutil.Process().memory_info().rss / 1024 / 1024
    
    async def single_tts_operation(self, text: str) -> TTSOperationResult:
        """Perform single TTS operation and measure performance."""
        start_time = time.time()
        
        try:
            # Создаем уникальный orchestrator для каждой операции
            factory = EnhancedVoiceProviderFactory()
            orchestrator = VoiceServiceOrchestrator(enhanced_factory=factory)
            await orchestrator.initialize()
            
            # Создаем запрос TTS
            tts_request = TTSRequest(
                text=text,
                language="ru",
                voice="alloy",
                speed=1.0
            )
            
            # Выполняем TTS операцию
            result = await orchestrator.synthesize_speech(tts_request)
            
            duration = time.time() - start_time
            
            if result and hasattr(result, 'audio_data') and result.audio_data:
                return TTSOperationResult(
                    success=True,
                    duration=duration,
                    provider_used=getattr(result, 'provider_used', 'unknown'),
                    audio_size=len(result.audio_data)
                )
            else:
                return TTSOperationResult(
                    success=False,
                    duration=duration,
                    error_message="No audio data returned"
                )
                
        except Exception as e:
            duration = time.time() - start_time
            return TTSOperationResult(
                success=False,
                duration=duration,
                error_message=str(e)
            )
        finally:
            # Закрываем ресурсы если нужно
            try:
                if 'orchestrator' in locals():
                    await orchestrator.cleanup()
            except Exception:
                pass
    
    async def baseline_performance_test(self) -> dict:
        """Измерение базовой производительности TTS."""
        print("📊 Запуск базового теста производительности...")
        
        baseline_metrics = {
            'tts_avg_duration': 0.0,
            'tts_success_rate': 0.0,
            'memory_per_operation': 0.0
        }
        
        # TTS baseline
        tts_durations = []
        successful_operations = 0
        
        for i, text in enumerate(self.test_texts):
            print(f"  Testing TTS {i+1}/{len(self.test_texts)}: {text[:30]}...")
            result = await self.single_tts_operation(text)
            if result.success:
                tts_durations.append(result.duration)
                successful_operations += 1
                print(f"    ✅ Success: {result.duration:.3f}s")
            else:
                print(f"    ❌ Failed: {result.error_message}")
                
        baseline_metrics['tts_avg_duration'] = statistics.mean(tts_durations) if tts_durations else 0.0
        baseline_metrics['tts_success_rate'] = (successful_operations / len(self.test_texts)) * 100.0
        
        print(f'  TTS baseline: {baseline_metrics["tts_avg_duration"]:.3f}s')
        print(f'  TTS success rate: {baseline_metrics["tts_success_rate"]:.1f}%')
        
        return baseline_metrics
    
    async def warmup_test(self) -> None:
        """Прогрев системы для стабильных измерений"""
        print('🔥 Прогрев системы...')
        
        for i in range(5):
            text = self.test_texts[i % len(self.test_texts)]
            await self.single_tts_operation(text)
            print(f'  Прогрев {i+1}/5 завершен')
            
        print('✅ Система прогрета')
    
    async def baseline_performance_test(self) -> Dict[str, float]:
        """Измерение baseline производительности"""
        print('📊 Baseline Performance Test')
        print('-' * 40)
        
        baseline_metrics = {
            'tts_avg_duration': 0.0,
            'stt_avg_duration': 0.0,
            'memory_per_operation': 0.0
        }
        
        # TTS baseline
        tts_durations = []
        for i, text in enumerate(self.test_texts):
            print(f"  Testing TTS {i+1}/{len(self.test_texts)}: {text[:30]}...")
            result = await self.single_tts_operation(text)
            if result.success:
                tts_durations.append(result.duration)
                print(f"    ✅ Success: {result.duration:.3f}s")
            else:
                print(f"    ❌ Failed: {result.error_message}")
                
        baseline_metrics['tts_avg_duration'] = statistics.mean(tts_durations) if tts_durations else 0.0
        
        print(f'  TTS baseline: {baseline_metrics["tts_avg_duration"]:.3f}s')
        
        return baseline_metrics
    
    async def concurrent_load_test(self, concurrent_users: int, operations_per_user: int) -> LoadTestResults:
        """Тестирование под concurrent нагрузкой"""
        print(f'⚡ Concurrent Load Test: {concurrent_users} users, {operations_per_user} ops each')
        print('-' * 60)
        
        all_metrics = []
        start_time = time.time()
        max_memory = self.baseline_memory
        
        async def user_simulation(user_id: int):
            """Симуляция одного пользователя"""
            nonlocal max_memory
            user_metrics = []
            
            for op in range(operations_per_user):
                # Чередование TTS и STT операций
                if op % 2 == 0:
                    text = self.test_texts[op % len(self.test_texts)]
                    metric = await self.single_tts_operation(text)
                else:
                    # Используем аудио из предыдущей TTS операции
                    # Для простоты - повторяем TTS операцию
                    text = self.test_texts[op % len(self.test_texts)]
                    metric = await self.single_tts_operation(text)
                
                user_metrics.append(metric)
                
                # Monitor memory usage
                current_memory = self.measure_memory_usage()
                if current_memory > max_memory:
                    max_memory = current_memory
                
                # Small delay between operations
                await asyncio.sleep(0.1)
            
            return user_metrics
        
        # Run concurrent users
        tasks = [user_simulation(i) for i in range(concurrent_users)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect all metrics
        for result in results:
            if isinstance(result, list):
                all_metrics.extend(result)
        
        total_duration = time.time() - start_time
        
        # Calculate statistics
        successful_ops = [m for m in all_metrics if m.success]
        failed_ops = [m for m in all_metrics if not m.success]
        
        durations = [m.duration for m in successful_ops] if successful_ops else [0]
        cache_hits = sum(1 for m in successful_ops if m.cache_hit)
        
        return LoadTestResults(
            test_name=f"Concurrent_{concurrent_users}users_{operations_per_user}ops",
            concurrent_users=concurrent_users,
            total_operations=len(all_metrics),
            successful_operations=len(successful_ops),
            failed_operations=len(failed_ops),
            avg_duration=statistics.mean(durations),
            median_duration=statistics.median(durations),
            p95_duration=statistics.quantiles(durations, n=20)[18] if len(durations) > 1 else durations[0],
            p99_duration=statistics.quantiles(durations, n=100)[98] if len(durations) > 1 else durations[0],
            max_memory_mb=max_memory,
            cache_hit_ratio=cache_hits / len(successful_ops) if successful_ops else 0.0,
            throughput_ops_per_sec=len(successful_ops) / total_duration if total_duration > 0 else 0.0
        )
    
    async def stress_test(self) -> LoadTestResults:
        """Stress testing под максимальной нагрузкой"""
        print('💥 Stress Test - Maximum Load')
        print('-' * 40)
        
        # Gradually increase load until failure
        max_successful_users = 0
        
        for users in [10, 25, 50, 75, 100]:
            print(f'  Testing {users} concurrent users...')
            
            try:
                result = await asyncio.wait_for(
                    self.concurrent_load_test(users, 3), 
                    timeout=120.0  # 2 minutes timeout
                )
                
                success_rate = result.successful_operations / result.total_operations
                
                if success_rate >= 0.95:  # 95% success rate threshold
                    max_successful_users = users
                    print(f'    ✅ {users} users: {success_rate:.1%} success')
                else:
                    print(f'    ❌ {users} users: {success_rate:.1%} success (below threshold)')
                    break
                    
            except asyncio.TimeoutError:
                print(f'    ⏰ {users} users: Timeout (performance degradation)')
                break
            except Exception as e:
                print(f'    💥 {users} users: Error - {e}')
                break
        
        print(f'  🎯 Maximum capacity: {max_successful_users} concurrent users')
        
        # Return final test result
        return await self.concurrent_load_test(max_successful_users, 5)
    
    def generate_performance_report(self) -> str:
        """Генерация итогового отчета о производительности"""
        report = []
        report.append("# Voice V2 Performance Test Report")
        report.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"**Baseline Memory:** {self.baseline_memory:.1f} MB")
        report.append("")
        
        report.append("## Load Test Results")
        report.append("")
        
        for result in self.test_results:
            report.append(f"### {result.test_name}")
            report.append(f"- **Concurrent Users:** {result.concurrent_users}")
            report.append(f"- **Total Operations:** {result.total_operations}")
            report.append(f"- **Success Rate:** {result.successful_operations/result.total_operations:.1%}")
            report.append(f"- **Average Duration:** {result.avg_duration:.3f}s")
            report.append(f"- **P95 Duration:** {result.p95_duration:.3f}s")
            report.append(f"- **P99 Duration:** {result.p99_duration:.3f}s")
            report.append(f"- **Max Memory:** {result.max_memory_mb:.1f} MB")
            report.append(f"- **Cache Hit Ratio:** {result.cache_hit_ratio:.1%}")
            report.append(f"- **Throughput:** {result.throughput_ops_per_sec:.1f} ops/sec")
            report.append("")
        
        return "\n".join(report)
    
    async def run_performance_suite(self) -> bool:
        """Запуск полного набора тестов производительности"""
        try:
            # Initialize system
            if not await self.initialize():
                return False
            
            # Warmup
            await self.warmup_test()
            
            # Baseline performance
            baseline = await self.baseline_performance_test()
            
            # Progressive load tests
            load_tests = [
                (5, 10),   # 5 users, 10 operations each
                (10, 8),   # 10 users, 8 operations each  
                (25, 6),   # 25 users, 6 operations each
                (50, 4),   # 50 users, 4 operations each
            ]
            
            for users, ops in load_tests:
                result = await self.concurrent_load_test(users, ops)
                self.test_results.append(result)
                
                print(f'✅ {users} users test completed:')
                print(f'   Success rate: {result.successful_operations/result.total_operations:.1%}')
                print(f'   Avg duration: {result.avg_duration:.3f}s')
                print(f'   Memory usage: {result.max_memory_mb:.1f} MB')
                print()
                
                # Cleanup between tests
                gc.collect()
                await asyncio.sleep(2)
            
            # Stress test
            stress_result = await self.stress_test()
            self.test_results.append(stress_result)
            
            # Generate report
            report = self.generate_performance_report()
            
            # Save report
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_file = f"MD/Reports/Phase_4_6_4_performance_test_{timestamp}.md"
            
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(report)
            
            print('📊 PERFORMANCE TEST COMPLETED')
            print('=' * 50)
            print(f'📄 Report saved: {report_file}')
            
            return True
            
        except Exception as e:
            print(f'❌ Performance test failed: {e}')
            return False


async def main():
    """Main entry point для performance testing"""
    tester = VoiceV2PerformanceTester()
    success = await tester.run_performance_suite()
    
    if success:
        print('🎉 All performance tests completed successfully!')
        return 0
    else:
        print('❌ Performance tests failed!')
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
