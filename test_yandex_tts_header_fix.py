#!/usr/bin/env python3
"""
Тест исправления Yandex TTS - использование x-folder-id в заголовках
"""

import asyncio
import aiohttp
import sys
sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

async def test_yandex_tts_with_header():
    """Тест Yandex TTS с folder ID в заголовке"""
    
    print("🔧 Тестируем Yandex TTS с x-folder-id в заголовке...")
    
    try:
        from app.core.config import settings
        
        if not settings.YANDEX_API_KEY:
            print("❌ YANDEX_API_KEY не настроен")
            return False
            
        api_key = settings.YANDEX_API_KEY.get_secret_value()
        folder_id = settings.YANDEX_FOLDER_ID
        
        print(f"🔑 API ключ: {api_key[:10]}... (длина: {len(api_key)})")
        print(f"📂 Folder ID: {folder_id}")
        
        # Тест с folder ID в заголовке (как в STT)
        tts_url = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
        
        headers = {
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-folder-id": folder_id  # Folder ID в заголовке
        }
        
        data = {
            "text": "Тест голосовой синтез",
            "lang": "ru-RU",
            "voice": "jane",
            "format": "oggopus",
            # НЕ включаем folderId в тело запроса
        }
        
        async with aiohttp.ClientSession() as session:
            print("🌐 Отправляем запрос с folder ID в заголовке...")
            
            async with session.post(tts_url, headers=headers, data=data) as response:
                print(f"📊 Статус ответа: {response.status}")
                
                if response.status == 200:
                    content = await response.read()
                    print(f"✅ Успех! Получено {len(content)} байт аудио")
                    print("✅ Folder ID в заголовке работает корректно")
                    return True
                    
                elif response.status == 401:
                    error_text = await response.text()
                    print(f"❌ Ошибка авторизации (401):")
                    print(f"   {error_text}")
                    return False
                    
                else:
                    error_text = await response.text()
                    print(f"❌ Неожиданная ошибка ({response.status}):")
                    print(f"   {error_text}")
                    return False
        
    except Exception as e:
        print(f"❌ Ошибка тестирования: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_yandex_tts_service():
    """Тест Yandex TTS сервиса с исправлениями"""
    
    print("\n🎯 Тестируем исправленный YandexTTSService...")
    
    try:
        from app.services.voice.tts.yandex_tts import YandexTTSService
        from app.api.schemas.voice_schemas import TTSConfig, TTSModel
        import logging
        
        # Создаем TTS конфигурацию
        tts_config = TTSConfig(
            enabled=True,
            model=TTSModel.YANDEX_JANE,
            voice="jane",
            speed=1.0,
            language="ru-RU"
        )
        
        # Инициализируем сервис
        logger = logging.getLogger(__name__)
        yandex_tts = YandexTTSService(config=tts_config, logger=logger)
        
        # Инициализируем
        await yandex_tts.initialize()
        print("✅ YandexTTSService инициализирован")
        
        # Проверяем health
        is_healthy = await yandex_tts.health_check()
        print(f"✅ Health check: {'OK' if is_healthy else 'FAIL'}")
        
        if is_healthy:
            print("✅ Yandex TTS готов к использованию")
            
            # Попробуем синтез (с таймаутом)
            try:
                result = await asyncio.wait_for(
                    yandex_tts.synthesize_speech("Привет, это тест голосового синтеза"),
                    timeout=10.0
                )
                
                if result.success:
                    print("✅ Синтез речи успешен!")
                    if result.metadata.get('audio_data'):
                        print(f"✅ Получено {len(result.metadata['audio_data'])} байт аудио")
                else:
                    print(f"❌ Синтез неудачен: {result.error_message}")
                    
            except asyncio.TimeoutError:
                print("⚠️ Синтез прерван по таймауту")
            except Exception as e:
                print(f"❌ Ошибка синтеза: {e}")
        
        # Очистка
        await yandex_tts.cleanup()
        return is_healthy
        
    except Exception as e:
        print(f"❌ Ошибка тестирования сервиса: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Главная функция"""
    print("🔧 ТЕСТ ИСПРАВЛЕНИЯ YANDEX TTS (x-folder-id)")
    print("=" * 60)
    
    # Тест 1: Прямой запрос с folder ID в заголовке
    result1 = await test_yandex_tts_with_header()
    
    # Тест 2: Yandex TTS сервис
    result2 = await test_yandex_tts_service()
    
    print("\n" + "=" * 60)
    if result1 and result2:
        print("🎉 ИСПРАВЛЕНИЕ YANDEX TTS РАБОТАЕТ!")
        print("✅ Folder ID в заголовке x-folder-id работает")
        print("✅ YandexTTSService использует правильный формат")
        print("✅ Совместимость с STT обеспечена")
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ!")
        print(f"Прямой запрос: {'✅' if result1 else '❌'}")
        print(f"TTS сервис: {'✅' if result2 else '❌'}")
    
    print("🏁 Тестирование завершено")

if __name__ == "__main__":
    asyncio.run(main())
