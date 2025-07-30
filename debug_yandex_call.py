#!/usr/bin/env python3
"""Debug скрипт для понимания вызова Yandex TTS"""

import asyncio
from app.services.voice_v2.providers.tts.yandex_tts import YandexTTSProvider
from app.services.voice_v2.core.schemas import TTSRequest
from app.core.config import settings

async def debug_yandex():
    # Создаем провайдер
    config = {
        'api_key': settings.YANDEX_API_KEY.get_secret_value(),
        'folder_id': settings.YANDEX_FOLDER_ID
    }
    
    provider = YandexTTSProvider(config)
    await provider.initialize()
    
    print("Готовимся к вызову Yandex TTS...")
    
    request = TTSRequest(
        text='Привет! Это тест.',
        voice='jane',
        language='ru-RU',
        speed=1.0
    )
    
    # Готовим параметры как в реальном коде
    synthesis_params = provider._prepare_synthesis_params(request)
    print(f"synthesis_params: {synthesis_params}")
    
    # Показываем что передается в ConnectionManager
    print("\nВызов _execute_with_connection_manager:")
    print(f"operation_name: yandex_tts_synthesis")
    print(f"request_func: {provider._execute_yandex_synthesis}")
    print(f"synthesis_params: {synthesis_params}")
    
    # Показываем что получает функция
    print(f"\nФункция _execute_yandex_synthesis получит:")
    print(f"args[0]: session (aiohttp session)")
    print(f"kwargs: {{'synthesis_params': {synthesis_params}, 'operation_name': 'yandex_tts_synthesis', 'provider_name': 'yandex_tts', ...}}")
    
    print("\nПроблема: kwargs содержит operation_name, но функция его не ожидает!")

if __name__ == '__main__':
    asyncio.run(debug_yandex())
