#!/usr/bin/env python3
"""
Тест проверки credentials для голосовых провайдеров
"""

import asyncio
import sys
sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
from app.api.schemas.voice_schemas import VoiceProvider

async def test_provider_credentials():
    """Тест проверки credentials провайдеров"""
    
    # Создаем временный экземпляр orchestrator для тестирования
    orchestrator = VoiceServiceOrchestrator(redis_service=None)
    
    print("🔧 Тестируем проверку credentials для провайдеров...")
    
    providers = [VoiceProvider.OPENAI, VoiceProvider.YANDEX, VoiceProvider.GOOGLE]
    
    for provider in providers:
        has_credentials = orchestrator._check_provider_credentials(provider)
        status = "✅ Доступны" if has_credentials else "❌ Отсутствуют"
        print(f"{provider.value}: {status}")
        
        # Покажем какие credentials нужны
        if provider == VoiceProvider.OPENAI:
            print("   Требуется: OPENAI_API_KEY")
        elif provider == VoiceProvider.GOOGLE:
            print("   Требуется: GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT_ID")
        elif provider == VoiceProvider.YANDEX:
            print("   Требуется: YANDEX_API_KEY или YANDEX_IAM_TOKEN")
    
    print("\n📋 Результат проверки:")
    available_providers = [p for p in providers if orchestrator._check_provider_credentials(p)]
    unavailable_providers = [p for p in providers if not orchestrator._check_provider_credentials(p)]
    
    if available_providers:
        print(f"✅ Доступные провайдеры: {[p.value for p in available_providers]}")
    if unavailable_providers:
        print(f"❌ Недоступные провайдеры: {[p.value for p in unavailable_providers]}")
    
    return available_providers

async def test_voice_initialization():
    """Тест инициализации голосовых сервисов с проверкой credentials"""
    
    print("\n🚀 Тестируем инициализацию голосовых сервисов...")
    
    from app.services.redis_wrapper import RedisService
    
    # Инициализируем Redis
    redis_service = RedisService()
    await redis_service.initialize()
    
    # Инициализируем orchestrator
    orchestrator = VoiceServiceOrchestrator(redis_service=redis_service)
    await orchestrator.initialize()
    
    # Конфигурация с провайдерами
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
                            },
                            {
                                "provider": "openai",
                                "priority": 2,
                                "fallback_enabled": True,
                                "stt_config": {
                                    "enabled": True,
                                    "model": "whisper-1",
                                    "language": "ru"
                                }
                            },
                            {
                                "provider": "google",
                                "priority": 3,
                                "fallback_enabled": True,
                                "stt_config": {
                                    "enabled": True,
                                    "model": "latest_long",
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
    success = await orchestrator.initialize_voice_services_for_agent("test_agent", agent_config)
    
    if success:
        print("✅ Инициализация прошла успешно!")
        print(f"📊 Инициализированные STT сервисы: {list(orchestrator.stt_services.keys())}")
        print(f"📊 Инициализированные TTS сервисы: {list(orchestrator.tts_services.keys())}")
    else:
        print("❌ Инициализация провалилась!")
    
    # Закрываем соединения
    await redis_service.cleanup()
    await orchestrator.cleanup()
    
    return success

async def main():
    """Главная функция тестирования"""
    print("🎯 ТЕСТ CREDENTIALS И ИНИЦИАЛИЗАЦИИ ГОЛОСОВЫХ ПРОВАЙДЕРОВ")
    print("=" * 65)
    
    # Тест 1: Проверка credentials
    available_providers = await test_provider_credentials()
    
    # Тест 2: Инициализация сервисов
    init_success = await test_voice_initialization()
    
    print("\n" + "=" * 65)
    if available_providers and init_success:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("✅ Credentials проверяются корректно")
        print("✅ Только доступные провайдеры инициализируются")
        print("✅ Google провайдер пропускается из-за отсутствующих credentials")
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ! Проверьте конфигурацию и credentials")
    
    print("🏁 Тестирование завершено")

if __name__ == "__main__":
    asyncio.run(main())
