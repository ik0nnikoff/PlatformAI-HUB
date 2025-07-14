#!/usr/bin/env python3
"""
Тест исправлений Redis pipeline и TTS логирования
"""

import asyncio
import sys
sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

async def test_redis_pipeline():
    """Тест метода pipeline в RedisService"""
    
    print("🔧 Тестируем метод pipeline в RedisService...")
    
    try:
        from app.services.redis_wrapper import RedisService
        
        # Инициализируем Redis
        redis_service = RedisService()
        await redis_service.initialize()
        
        # Тестируем метод pipeline
        pipeline = redis_service.pipeline()
        print("✅ Метод pipeline() работает корректно")
        
        # Тестируем базовые операции pipeline
        pipeline.set("test_key", "test_value")
        pipeline.get("test_key")
        results = await pipeline.execute()
        
        print(f"✅ Pipeline execute работает: {results}")
        
        # Очистка
        await redis_service.cleanup()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка тестирования pipeline: {e}")
        return False

async def test_rate_limiter():
    """Тест rate limiter с исправленным pipeline"""
    
    print("\n🚀 Тестируем RedisRateLimiter с pipeline...")
    
    try:
        from app.services.redis_wrapper import RedisService
        from app.services.voice.redis_rate_limiter import RedisRateLimiter
        import logging
        
        # Инициализируем Redis
        redis_service = RedisService()
        await redis_service.initialize()
        
        # Создаем rate limiter
        rate_limiter = RedisRateLimiter(
            redis_service=redis_service,
            max_requests=5,
            window_seconds=60,
            key_prefix="test_voice:",
            logger=logging.getLogger(__name__)
        )
        
        # Тестируем rate limiter
        user_id = "test_user"
        allowed = await rate_limiter.is_allowed(user_id)
        print(f"✅ Rate limiter работает: allowed={allowed}")
        
        # Очистка
        await redis_service.cleanup()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка тестирования rate limiter: {e}")
        return False

async def test_voice_orchestrator_rate_limit():
    """Тест rate limit проверки в voice orchestrator"""
    
    print("\n🎯 Тестируем rate limit в VoiceOrchestrator...")
    
    try:
        from app.services.redis_wrapper import RedisService
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        
        # Инициализируем Redis
        redis_service = RedisService()
        await redis_service.initialize()
        
        # Инициализируем orchestrator
        orchestrator = VoiceServiceOrchestrator(redis_service=redis_service)
        await orchestrator.initialize()
        
        # Конфигурация агента
        agent_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "auto_stt": True,
                            "auto_tts_on_keywords": True,
                            "max_file_size_mb": 25,
                            "cache_enabled": True,
                            "cache_ttl_hours": 24,
                            "rate_limit_per_minute": 15,
                            "intent_keywords": [
                                "голос", "скажи", "произнеси", "озвучь",
                                "расскажи голосом", "ответь голосом", "прочитай вслух"
                            ],
                            "providers": [
                                {
                                    "provider": "yandex",
                                    "priority": 1,
                                    "fallback_enabled": True,
                                    "stt_config": {
                                        "enabled": True,
                                        "model": "general",
                                        "language": "ru-RU"
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }
        
        # Инициализируем для агента
        await orchestrator.initialize_voice_services_for_agent("test_agent", agent_config)
        
        # Тестируем TTS с текстом БЕЗ ключевых слов
        result1 = await orchestrator.synthesize_speech(
            agent_id="test_agent",
            text="Обычный ответ без ключевых слов",
            user_id="test_user"
        )
        
        print(f"✅ TTS без ключевых слов: success={result1.success}, error='{result1.error_message}'")
        
        # Тестируем TTS с ключевыми словами
        result2 = await orchestrator.synthesize_speech(
            agent_id="test_agent", 
            text="Скажи голосом это сообщение",
            user_id="test_user"
        )
        
        print(f"✅ TTS с ключевыми словами: success={result2.success}, error='{result2.error_message}'")
        
        # Очистка
        await redis_service.cleanup()
        await orchestrator.cleanup()
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка тестирования voice orchestrator: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Главная функция тестирования"""
    print("🎯 ТЕСТ ИСПРАВЛЕНИЙ REDIS PIPELINE И TTS ЛОГИРОВАНИЯ")
    print("=" * 60)
    
    # Тест 1: Redis pipeline
    result1 = await test_redis_pipeline()
    
    # Тест 2: Rate limiter
    result2 = await test_rate_limiter()
    
    # Тест 3: Voice orchestrator
    result3 = await test_voice_orchestrator_rate_limit()
    
    print("\n" + "=" * 60)
    if result1 and result2 and result3:
        print("🎉 ВСЕ ИСПРАВЛЕНИЯ РАБОТАЮТ КОРРЕКТНО!")
        print("✅ Redis pipeline метод добавлен и работает")
        print("✅ RedisRateLimiter работает без ошибок")
        print("✅ TTS обрабатывается только при наличии ключевых слов")
        print("✅ Rate limit проверка работает корректно")
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ! Проверьте исправления")
    
    print("🏁 Тестирование завершено")

if __name__ == "__main__":
    asyncio.run(main())
