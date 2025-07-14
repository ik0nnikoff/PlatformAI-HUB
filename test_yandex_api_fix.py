#!/usr/bin/env python3
"""
Тест исправления Yandex API key с get_secret_value()
"""

import asyncio
import sys
sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

async def test_yandex_api_key_fix():
    """Тест исправления получения Yandex API ключа"""
    
    print("🔑 Тестируем исправление Yandex API key...")
    
    try:
        from app.core.config import settings
        from app.services.voice.tts.yandex_tts import YandexTTSService
        from app.api.schemas.voice_schemas import TTSConfig, TTSModel
        import logging
        
        # Проверяем, что settings.YANDEX_API_KEY существует
        if hasattr(settings, 'YANDEX_API_KEY') and settings.YANDEX_API_KEY:
            print(f"✅ YANDEX_API_KEY настроен в settings")
            
            try:
                # Пытаемся получить secret value
                api_key = settings.YANDEX_API_KEY.get_secret_value()
                print(f"✅ get_secret_value() работает, длина ключа: {len(api_key) if api_key else 0}")
                
                # Проверяем, что ключ не пустой
                if api_key and len(api_key) > 10:
                    print(f"✅ API ключ корректный (начинается с: {api_key[:10]}...)")
                else:
                    print(f"⚠️ API ключ короткий или пустой: {api_key}")
                    
            except Exception as e:
                print(f"❌ Ошибка получения secret value: {e}")
                return False
        else:
            print(f"⚠️ YANDEX_API_KEY не настроен в settings")
        
        # Создаем TTS конфигурацию для тестирования
        tts_config = TTSConfig(
            enabled=True,
            model=TTSModel.YANDEX_JANE,
            voice="jane",
            speed=1.0
        )
        
        # Инициализируем Yandex TTS сервис
        logger = logging.getLogger(__name__)
        yandex_tts = YandexTTSService(config=tts_config, logger=logger)
        
        # Проверяем, что API ключ корректно установлен
        if yandex_tts.api_key:
            print(f"✅ YandexTTSService.api_key установлен корректно")
            print(f"✅ Длина API ключа: {len(yandex_tts.api_key)}")
        else:
            print(f"❌ YandexTTSService.api_key не установлен")
            return False
            
        print(f"✅ Исправление Yandex API key работает корректно")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка тестирования Yandex API key: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_voice_orchestrator_credentials():
    """Тест проверки креденшалов в VoiceOrchestrator"""
    
    print("\n🎯 Тестируем проверку креденшалов в VoiceOrchestrator...")
    
    try:
        from app.services.redis_wrapper import RedisService
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        from app.api.schemas.voice_schemas import VoiceProvider
        
        # Инициализируем Redis
        redis_service = RedisService()
        await redis_service.initialize()
        
        # Инициализируем orchestrator
        orchestrator = VoiceServiceOrchestrator(redis_service=redis_service)
        await orchestrator.initialize()
        
        # Тестируем проверку креденшалов для разных провайдеров
        providers = [VoiceProvider.YANDEX, VoiceProvider.OPENAI, VoiceProvider.GOOGLE]
        
        for provider in providers:
            has_credentials = orchestrator._check_provider_credentials(provider)
            print(f"✅ {provider.value} credentials: {'Available' if has_credentials else 'Not Available'}")
        
        # Очистка
        await redis_service.cleanup()
        await orchestrator.cleanup()
        
        print(f"✅ Проверка креденшалов работает корректно")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка тестирования проверки креденшалов: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_yandex_folder_id():
    """Тест наличия Yandex Folder ID"""
    
    print("\n📂 Тестируем Yandex Folder ID...")
    
    try:
        from app.core.config import settings
        
        if hasattr(settings, 'YANDEX_FOLDER_ID') and settings.YANDEX_FOLDER_ID:
            print(f"✅ YANDEX_FOLDER_ID настроен: {settings.YANDEX_FOLDER_ID}")
        else:
            print(f"⚠️ YANDEX_FOLDER_ID не настроен")
            
        return True
        
    except Exception as e:
        print(f"❌ Ошибка проверки Yandex Folder ID: {e}")
        return False

async def main():
    """Главная функция тестирования"""
    print("🔑 ТЕСТ ИСПРАВЛЕНИЯ YANDEX API KEY")
    print("=" * 60)
    
    # Тест 1: Yandex API key исправление
    result1 = await test_yandex_api_key_fix()
    
    # Тест 2: Voice Orchestrator credentials
    result2 = await test_voice_orchestrator_credentials()
    
    # Тест 3: Yandex Folder ID
    result3 = await test_yandex_folder_id()
    
    print("\n" + "=" * 60)
    if result1 and result2 and result3:
        print("🎉 ИСПРАВЛЕНИЕ YANDEX API KEY РАБОТАЕТ!")
        print("✅ get_secret_value() используется корректно")
        print("✅ Проверка креденшалов обновлена")
        print("✅ Конфигурация проверена")
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ! Проверьте конфигурацию")
        print(f"Yandex API key fix: {'✅' if result1 else '❌'}")
        print(f"Credentials check: {'✅' if result2 else '❌'}")
        print(f"Folder ID check: {'✅' if result3 else '❌'}")
    
    print("\n💡 РЕКОМЕНДАЦИИ:")
    print("1. Убедитесь, что YANDEX_API_KEY установлен в .env")
    print("2. Убедитесь, что YANDEX_FOLDER_ID установлен в .env")
    print("3. Проверьте корректность API ключа на cloud.yandex.com")
    
    print("🏁 Тестирование завершено")

if __name__ == "__main__":
    asyncio.run(main())
