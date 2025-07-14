"""
Продвинутые тесты производительности и нагрузки для голосового workflow
"""

import asyncio
import time
import statistics
import json
import logging
from typing import List, Dict, Any
from dataclasses import dataclass
import aiohttp
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("voice_performance_test")


@dataclass
class PerformanceMetrics:
    """Метрики производительности"""
    test_name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_response_time: float
    min_response_time: float
    max_response_time: float
    p95_response_time: float
    throughput: float  # запросов в секунду
    error_rate: float


class VoicePerformanceTester:
    """Тестировщик производительности голосового workflow"""
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def make_voice_request(self, agent_id: str, message: str, chat_id: int) -> Dict[str, Any]:
        """Отправка запроса для голосового ответа"""
        url = f"{self.base_url}/api/v1/agents/{agent_id}/chat"
        
        payload = {
            "message": message,
            "chat_id": chat_id,
            "metadata": {
                "source": "performance_test",
                "require_voice": True
            }
        }
        
        start_time = time.time()
        
        try:
            async with self.session.post(url, json=payload) as resp:
                end_time = time.time()
                response_time = end_time - start_time
                
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "success": True,
                        "response_time": response_time,
                        "status_code": resp.status,
                        "response": data
                    }
                else:
                    return {
                        "success": False,
                        "response_time": response_time,
                        "status_code": resp.status,
                        "error": f"HTTP {resp.status}"
                    }
                    
        except Exception as e:
            end_time = time.time()
            response_time = end_time - start_time
            return {
                "success": False,
                "response_time": response_time,
                "error": str(e)
            }
    
    async def stress_test_concurrent_requests(self, 
                                            agent_id: str,
                                            concurrent_users: int = 10,
                                            requests_per_user: int = 5) -> PerformanceMetrics:
        """Стресс-тест с конкурентными запросами"""
        logger.info(f"🔥 Starting stress test: {concurrent_users} users, {requests_per_user} requests each")
        
        test_messages = [
            "Отвечай голосом. Что такое airsoft?",
            "Скажи мне о правилах страйкбола",
            "Произнеси информацию о экипировке", 
            "Озвучь правила безопасности",
            "Расскажи голосом о тактических играх"
        ]
        
        async def user_simulation(user_id: int) -> List[Dict[str, Any]]:
            """Симуляция поведения одного пользователя"""
            results = []
            for i in range(requests_per_user):
                message = test_messages[i % len(test_messages)]
                chat_id = user_id * 1000 + i
                result = await self.make_voice_request(agent_id, message, chat_id)
                result["user_id"] = user_id
                result["request_id"] = i
                results.append(result)
                
                # Небольшая пауза между запросами
                await asyncio.sleep(0.1)
            
            return results
        
        # Запускаем всех пользователей одновременно
        start_time = time.time()
        tasks = [user_simulation(user_id) for user_id in range(concurrent_users)]
        all_results = await asyncio.gather(*tasks)
        end_time = time.time()
        
        # Собираем все результаты
        flat_results = [result for user_results in all_results for result in user_results]
        
        # Анализируем метрики
        response_times = [r["response_time"] for r in flat_results]
        successful_requests = sum(1 for r in flat_results if r["success"])
        failed_requests = len(flat_results) - successful_requests
        
        total_duration = end_time - start_time
        throughput = len(flat_results) / total_duration
        
        metrics = PerformanceMetrics(
            test_name="concurrent_stress_test",
            total_requests=len(flat_results),
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            avg_response_time=statistics.mean(response_times),
            min_response_time=min(response_times),
            max_response_time=max(response_times),
            p95_response_time=statistics.quantiles(response_times, n=20)[18],  # 95-й процентиль
            throughput=throughput,
            error_rate=(failed_requests / len(flat_results)) * 100
        )
        
        logger.info(f"✅ Stress test completed: {successful_requests}/{len(flat_results)} successful")
        return metrics
    
    async def load_test_sustained_traffic(self, 
                                        agent_id: str,
                                        duration_seconds: int = 60,
                                        requests_per_second: float = 2.0) -> PerformanceMetrics:
        """Тест нагрузки с устойчивым трафиком"""
        logger.info(f"📈 Starting load test: {requests_per_second} RPS for {duration_seconds}s")
        
        results = []
        start_time = time.time()
        request_interval = 1.0 / requests_per_second
        
        request_count = 0
        while (time.time() - start_time) < duration_seconds:
            loop_start = time.time()
            
            # Отправляем запрос
            message = f"Отвечай голосом. Запрос номер {request_count + 1}"
            chat_id = 90000 + request_count
            
            result = await self.make_voice_request(agent_id, message, chat_id)
            result["request_number"] = request_count
            result["timestamp"] = time.time()
            results.append(result)
            
            request_count += 1
            
            # Вычисляем сколько ждать до следующего запроса
            elapsed = time.time() - loop_start
            sleep_time = max(0, request_interval - elapsed)
            
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        
        # Анализируем результаты
        end_time = time.time()
        total_duration = end_time - start_time
        
        response_times = [r["response_time"] for r in results]
        successful_requests = sum(1 for r in results if r["success"])
        failed_requests = len(results) - successful_requests
        
        metrics = PerformanceMetrics(
            test_name="sustained_load_test",
            total_requests=len(results),
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            avg_response_time=statistics.mean(response_times) if response_times else 0,
            min_response_time=min(response_times) if response_times else 0,
            max_response_time=max(response_times) if response_times else 0,
            p95_response_time=statistics.quantiles(response_times, n=20)[18] if len(response_times) > 20 else max(response_times) if response_times else 0,
            throughput=len(results) / total_duration,
            error_rate=(failed_requests / len(results)) * 100 if results else 0
        )
        
        logger.info(f"✅ Load test completed: {successful_requests}/{len(results)} successful")
        return metrics
    
    async def memory_leak_test(self, agent_id: str, iterations: int = 100) -> Dict[str, Any]:
        """Тест на утечки памяти при повторных запросах"""
        logger.info(f"🧠 Starting memory leak test: {iterations} iterations")
        
        results = []
        
        for i in range(iterations):
            message = f"Отвечай голосом. Итерация {i + 1}"
            chat_id = 80000 + i
            
            result = await self.make_voice_request(agent_id, message, chat_id)
            results.append(result)
            
            if (i + 1) % 20 == 0:
                logger.info(f"   Completed {i + 1}/{iterations} iterations")
                # Небольшая пауза для сборки мусора
                await asyncio.sleep(0.5)
        
        successful_requests = sum(1 for r in results if r["success"])
        response_times = [r["response_time"] for r in results if r["success"]]
        
        return {
            "test": "memory_leak_test",
            "iterations": iterations,
            "successful_requests": successful_requests,
            "success_rate": (successful_requests / iterations) * 100,
            "avg_response_time": statistics.mean(response_times) if response_times else 0,
            "response_time_trend": response_times[-20:] if len(response_times) >= 20 else response_times
        }


async def run_performance_test_suite():
    """Запуск полного набора тестов производительности"""
    print("🎪 Voice Workflow Performance Testing Suite")
    print("=" * 50)
    
    agent_id = "agent_airsoft_0faa9616"
    
    async with VoicePerformanceTester() as tester:
        results = {
            "start_time": time.time(),
            "agent_id": agent_id,
            "tests": {}
        }
        
        # 1. Стресс-тест с конкурентными пользователями
        print("\n🔥 Running concurrent stress test...")
        stress_metrics = await tester.stress_test_concurrent_requests(
            agent_id=agent_id,
            concurrent_users=5,
            requests_per_user=3
        )
        results["tests"]["stress_test"] = stress_metrics.__dict__
        
        # 2. Тест нагрузки с устойчивым трафиком
        print("\n📈 Running sustained load test...")
        load_metrics = await tester.load_test_sustained_traffic(
            agent_id=agent_id,
            duration_seconds=30,
            requests_per_second=1.5
        )
        results["tests"]["load_test"] = load_metrics.__dict__
        
        # 3. Тест на утечки памяти
        print("\n🧠 Running memory leak test...")
        memory_results = await tester.memory_leak_test(
            agent_id=agent_id,
            iterations=50
        )
        results["tests"]["memory_test"] = memory_results
        
        results["end_time"] = time.time()
        results["total_duration"] = results["end_time"] - results["start_time"]
        
        # Выводим результаты
        print("\n📊 PERFORMANCE TEST RESULTS:")
        print("-" * 40)
        
        # Стресс-тест
        stress = results["tests"]["stress_test"]
        print(f"🔥 Stress Test:")
        print(f"   Requests: {stress['total_requests']}")
        print(f"   Success Rate: {((stress['successful_requests']/stress['total_requests'])*100):.1f}%")
        print(f"   Avg Response Time: {stress['avg_response_time']:.2f}s")
        print(f"   Throughput: {stress['throughput']:.2f} RPS")
        
        # Тест нагрузки
        load = results["tests"]["load_test"]
        print(f"\n📈 Load Test:")
        print(f"   Requests: {load['total_requests']}")
        print(f"   Success Rate: {((load['successful_requests']/load['total_requests'])*100):.1f}%")
        print(f"   Avg Response Time: {load['avg_response_time']:.2f}s")
        print(f"   P95 Response Time: {load['p95_response_time']:.2f}s")
        print(f"   Throughput: {load['throughput']:.2f} RPS")
        
        # Тест памяти
        memory = results["tests"]["memory_test"]
        print(f"\n🧠 Memory Test:")
        print(f"   Iterations: {memory['iterations']}")
        print(f"   Success Rate: {memory['success_rate']:.1f}%")
        print(f"   Avg Response Time: {memory['avg_response_time']:.2f}s")
        
        print(f"\n⏱️ Total Test Duration: {results['total_duration']:.1f}s")
        
        # Сохраняем результаты
        results_file = "voice_performance_test_results.json"
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"\n💾 Performance results saved to: {results_file}")
        
        return results


if __name__ == "__main__":
    asyncio.run(run_performance_test_suite())
