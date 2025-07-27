#!/usr/bin/env python3
"""
Performance Benchmark для app/services/voice системы
Фаза 1.1.3 - Performance benchmarking
"""

import asyncio
import time
import logging
import json
import statistics
from typing import Dict, List, Any
from pathlib import Path

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_performance_test")

async def test_voice_orchestrator_performance():
    """
    Тестирование производительности VoiceServiceOrchestrator
    """
    try:
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        from app.services.redis_wrapper import RedisService
        from app.core.config import settings
        
        logger.info("🚀 Starting Voice Orchestrator Performance Test")
        
        # Инициализация зависимостей
        redis_service = RedisService()
        await redis_service.initialize()
        
        orchestrator = VoiceServiceOrchestrator(
            redis_service=redis_service,
            logger=logger
        )
        
        # Benchmark инициализации
        start_time = time.time()
        await orchestrator.initialize()
        init_time = time.time() - start_time
        
        logger.info(f"✅ Orchestrator initialization time: {init_time:.3f}s")
        
        # Тест конфигурации агента
        test_agent_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "providers": [
                                {"provider": "openai", "priority": 1, "enabled": True}
                            ]
                        }
                    }
                }
            }
        }
        
        start_time = time.time()
        result = await orchestrator.initialize_voice_services_for_agent(
            agent_id="test_agent",
            agent_config=test_agent_config
        )
        config_time = time.time() - start_time
        
        logger.info(f"✅ Agent config initialization time: {config_time:.3f}s")
        logger.info(f"✅ Agent config initialization result: {result}")
        
        # Очистка
        await orchestrator.cleanup()
        await redis_service.cleanup()
        
        return {
            "orchestrator_init_time": init_time,
            "agent_config_time": config_time,
            "success": True
        }
        
    except Exception as e:
        logger.error(f"❌ Voice orchestrator test failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "success": False
        }

async def test_metrics_collector_performance():
    """
    Тестирование производительности VoiceMetricsCollector
    """
    try:
        from app.services.voice.voice_metrics import VoiceMetricsCollector, VoiceMetrics
        from app.services.redis_wrapper import RedisService
        
        logger.info("📊 Starting Metrics Collector Performance Test")
        
        redis_service = RedisService()
        await redis_service.initialize()
        
        metrics_collector = VoiceMetricsCollector(
            redis_service=redis_service,
            logger=logger
        )
        
        # Создаем тестовые метрики
        test_metrics = []
        for i in range(10):
            metric = VoiceMetrics(
                timestamp=time.time(),
                agent_id=f"test_agent_{i % 3}",
                user_id=f"user_{i}",
                operation="stt" if i % 2 == 0 else "tts",
                provider="openai",
                success=True,
                processing_time=0.5 + (i * 0.1),
                input_size_bytes=1024 * (i + 1),
                output_size_bytes=512 * (i + 1)
            )
            test_metrics.append(metric)
        
        # Benchmark записи метрик
        times = []
        for metric in test_metrics:
            start_time = time.time()
            await metrics_collector.record_metric(metric)
            record_time = time.time() - start_time
            times.append(record_time)
        
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        
        logger.info(f"✅ Metrics recording - Avg: {avg_time:.4f}s, Max: {max_time:.4f}s, Min: {min_time:.4f}s")
        
        # Тест получения статистики
        start_time = time.time()
        daily_stats = await metrics_collector.get_daily_stats("test_agent_0")
        stats_time = time.time() - start_time
        
        logger.info(f"✅ Daily stats retrieval time: {stats_time:.4f}s")
        logger.info(f"✅ Daily stats result: {daily_stats}")
        
        await redis_service.cleanup()
        
        return {
            "avg_record_time": avg_time,
            "max_record_time": max_time,
            "min_record_time": min_time,
            "stats_retrieval_time": stats_time,
            "metrics_count": len(test_metrics),
            "success": True
        }
        
    except Exception as e:
        logger.error(f"❌ Metrics collector test failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "success": False
        }

async def test_intent_detector_performance():
    """
    Тестирование производительности VoiceIntentDetector
    """
    try:
        from app.services.voice.intent_utils import VoiceIntentDetector
        
        logger.info("🎯 Starting Intent Detector Performance Test")
        
        detector = VoiceIntentDetector(logger=logger)
        
        # Тестовые данные
        test_texts = [
            "Скажи мне что-то голосом",
            "Отправь голосовое сообщение",
            "Расскажи историю",
            "Привет, как дела?",
            "озвучь это сообщение",
            "голосом скажи результат",
            "просто текстовый ответ",
            "никаких голосовых функций",
            "ГОЛОСОМ ГРОМКО СКАЖИ",
            "голос отправь мне песню"
        ]
        
        intent_keywords = ["голос", "озвучь", "скажи голосом", "голосовое", "голосом"]
        
        # Benchmark intent detection
        times = []
        results = []
        
        for text in test_texts:
            start_time = time.time()
            result = detector.detect_tts_intent(text, intent_keywords)
            detection_time = time.time() - start_time
            times.append(detection_time)
            results.append(result)
        
        avg_time = statistics.mean(times)
        total_time = sum(times)
        detected_count = sum(results)
        
        logger.info(f"✅ Intent detection - Avg: {avg_time:.6f}s, Total: {total_time:.6f}s")
        logger.info(f"✅ Detected intents: {detected_count}/{len(test_texts)}")
        
        # Тест извлечения voice settings
        test_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "providers": [{"provider": "openai", "priority": 1}]
                        }
                    }
                }
            }
        }
        
        start_time = time.time()
        voice_settings = detector.extract_voice_settings(test_config)
        extract_time = time.time() - start_time
        
        logger.info(f"✅ Voice settings extraction time: {extract_time:.6f}s")
        logger.info(f"✅ Extracted settings: {voice_settings}")
        
        return {
            "avg_detection_time": avg_time,
            "total_detection_time": total_time,
            "detected_count": detected_count,
            "total_tests": len(test_texts),
            "settings_extraction_time": extract_time,
            "success": True
        }
        
    except Exception as e:
        logger.error(f"❌ Intent detector test failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "success": False
        }

async def test_redis_performance():
    """
    Тестирование производительности Redis операций
    """
    try:
        from app.services.redis_wrapper import RedisService
        
        logger.info("🔴 Starting Redis Performance Test")
        
        redis_service = RedisService()
        await redis_service.initialize()
        
        # Тест базовых операций
        operations = []
        
        # SET операции
        start_time = time.time()
        for i in range(100):
            await redis_service.set(f"test_key_{i}", f"test_value_{i}", ex=60)
        set_time = time.time() - start_time
        operations.append(("SET 100 keys", set_time))
        
        # GET операции
        start_time = time.time()
        for i in range(100):
            await redis_service.get(f"test_key_{i}")
        get_time = time.time() - start_time
        operations.append(("GET 100 keys", get_time))
        
        # ZADD операции (для метрик)
        start_time = time.time()
        for i in range(50):
            await redis_service.zadd(f"test_zset_{i}", {f"member_{i}": i})
        zadd_time = time.time() - start_time
        operations.append(("ZADD 50 operations", zadd_time))
        
        # Очистка тестовых данных
        start_time = time.time()
        for i in range(100):
            await redis_service.delete(f"test_key_{i}")
        for i in range(50):
            await redis_service.delete(f"test_zset_{i}")
        cleanup_time = time.time() - start_time
        operations.append(("Cleanup", cleanup_time))
        
        await redis_service.cleanup()
        
        for op_name, op_time in operations:
            logger.info(f"✅ Redis {op_name}: {op_time:.4f}s")
        
        return {
            "operations": dict(operations),
            "success": True
        }
        
    except Exception as e:
        logger.error(f"❌ Redis performance test failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "success": False
        }

async def main():
    """
    Главная функция для запуска всех performance тестов
    """
    logger.info("🎯 Starting Voice System Performance Benchmark")
    logger.info("=" * 60)
    
    results = {}
    
    # Тесты производительности
    tests = [
        ("Redis Performance", test_redis_performance),
        ("Intent Detector Performance", test_intent_detector_performance),
        ("Metrics Collector Performance", test_metrics_collector_performance),
        ("Voice Orchestrator Performance", test_voice_orchestrator_performance),
    ]
    
    for test_name, test_func in tests:
        logger.info(f"\n🔄 Running {test_name}...")
        try:
            result = await test_func()
            results[test_name] = result
            if result.get("success", False):
                logger.info(f"✅ {test_name} completed successfully")
            else:
                logger.error(f"❌ {test_name} failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"❌ {test_name} crashed: {e}", exc_info=True)
            results[test_name] = {"error": str(e), "success": False}
    
    logger.info("\n" + "=" * 60)
    logger.info("📊 PERFORMANCE BENCHMARK RESULTS")
    logger.info("=" * 60)
    
    # Сводка результатов
    for test_name, result in results.items():
        if result.get("success", False):
            logger.info(f"✅ {test_name}: SUCCESS")
        else:
            logger.info(f"❌ {test_name}: FAILED - {result.get('error', 'Unknown error')}")
    
    # Сохранение результатов
    results_file = Path("voice_performance_results.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\n📄 Results saved to: {results_file}")
    return results

if __name__ == "__main__":
    asyncio.run(main())
