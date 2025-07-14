#!/usr/bin/env python3
"""
Проверка валидности Yandex API ключа
"""

import asyncio
import aiohttp
import sys
sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

async def test_yandex_api_validity():
    """Тест валидности Yandex API ключа через реальный запрос"""
    
    print("🔍 Проверяем валидность Yandex API ключа...")
    
    try:
        from app.core.config import settings
        
        if not settings.YANDEX_API_KEY:
            print("❌ YANDEX_API_KEY не настроен")
            return False
            
        api_key = settings.YANDEX_API_KEY.get_secret_value()
        folder_id = settings.YANDEX_FOLDER_ID
        
        if not api_key or not folder_id:
            print("❌ API ключ или Folder ID отсутствуют")
            return False
        
        print(f"🔑 API ключ: {api_key[:10]}... (длина: {len(api_key)})")
        print(f"📂 Folder ID: {folder_id}")
        
        # Простой тест синтеза речи
        tts_url = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
        
        headers = {
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "text": "Тест",
            "lang": "ru-RU",
            "voice": "jane",
            "format": "oggopus",
            "folderId": folder_id
        }
        
        async with aiohttp.ClientSession() as session:
            print("🌐 Отправляем тестовый запрос к Yandex TTS API...")
            
            async with session.post(tts_url, headers=headers, data=data) as response:
                print(f"📊 Статус ответа: {response.status}")
                
                if response.status == 200:
                    content = await response.read()
                    print(f"✅ API ключ валидный! Получено {len(content)} байт аудио")
                    return True
                    
                elif response.status == 401:
                    error_text = await response.text()
                    print(f"❌ Ошибка авторизации (401):")
                    print(f"   {error_text}")
                    
                    # Проверим формат ключа
                    if len(api_key) != 40:
                        print(f"⚠️ Неверная длина API ключа: {len(api_key)} (должно быть 40)")
                    
                    if not api_key.startswith(('AQVN', 'AQIk', 'y0_')):
                        print(f"⚠️ API ключ не похож на Yandex Cloud API key")
                        
                    return False
                    
                else:
                    error_text = await response.text()
                    print(f"❌ Неожиданная ошибка ({response.status}):")
                    print(f"   {error_text}")
                    return False
        
    except Exception as e:
        print(f"❌ Ошибка проверки API ключа: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Главная функция"""
    print("🔍 ПРОВЕРКА ВАЛИДНОСТИ YANDEX API КЛЮЧА")
    print("=" * 60)
    
    result = await test_yandex_api_validity()
    
    print("\n" + "=" * 60)
    if result:
        print("🎉 YANDEX API КЛЮЧ ВАЛИДНЫЙ!")
        print("✅ Можно использовать для синтеза речи")
    else:
        print("❌ ПРОБЛЕМА С YANDEX API КЛЮЧОМ!")
        print("\n💡 РЕКОМЕНДАЦИИ:")
        print("1. Проверьте API ключ на cloud.yandex.com")
        print("2. Убедитесь, что у ключа есть права на SpeechKit")
        print("3. Проверьте, что ключ не истек")
        print("4. Убедитесь, что Folder ID корректный")
        print("5. Проверьте баланс в Yandex Cloud")
    
    print("🏁 Проверка завершена")

if __name__ == "__main__":
    asyncio.run(main())
