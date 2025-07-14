#!/usr/bin/env python3
"""
Прямой тест VoiceServiceOrchestrator с исправленной конфигурацией
"""

import asyncio
import time
import sys
import os

# Добавим путь к проекту
sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

async def test_voice_orchestrator_direct():
    """Тестируем VoiceServiceOrchestrator напрямую"""
    
    try:
        # Импорты
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        from app.services.redis_wrapper import RedisService
        
        print("🔧 Инициализируем VoiceServiceOrchestrator...")
        
        # Создаем Redis wrapper
        redis_wrapper = RedisService()
        await redis_wrapper.initialize()
        
        # Создаем orchestrator
        orchestrator = VoiceServiceOrchestrator(redis_service=redis_wrapper)
        
        # Ждем инициализации
        await asyncio.sleep(1)
        
        print("✅ VoiceServiceOrchestrator инициализирован")
        
        # Конфигурация агента с правильной структурой  
        agent_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "intent_detection_mode": "keywords",
                            "intent_keywords": [
                                "голос",
                                "скажи",
                                "произнеси", 
                                "озвучь",
                                "расскажи голосом",
                                "ответь голосом",
                                "прочитай вслух"
                            ],
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
                                        "language": "ru-RU",
                                        "max_duration": 60,
                                        "sample_rate_hertz": 16000,
                                        "enable_automatic_punctuation": True
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
                                }
                            ]
                        }
                    }
                }
            }
        }
        
        # Читаем тестовый аудиофайл
        audio_path = "/tmp/test_audio_voice.ogg"
        
        if not os.path.exists(audio_path):
            print("❌ Аудиофайл не найден, создаем минимальный OGG...")
            # Минимальный OGG Opus заголовок для тестирования
            ogg_header = b'OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
            with open(audio_path, 'wb') as f:
                f.write(ogg_header)
        
        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        
        print(f"📁 Размер аудиофайла: {len(audio_data)} байт")
        
        # Тестируем обработку голосового сообщения
        print("🎤 Обрабатываем голосовое сообщение...")
        start_time = time.time()
        
        result = await orchestrator.process_voice_message(
            agent_id="test_agent",
            user_id="test_user", 
            audio_data=audio_data,
            original_filename="test_voice.ogg",
            agent_config=agent_config
        )
        
        processing_time = time.time() - start_time
        print(f"⏱️ Время обработки: {processing_time:.2f}с")
        
        # Проверяем результат
        if result.success:
            print(f"✅ Успешно! Распознанный текст: '{result.text}'")
            print(f"🔧 Использован провайдер: {result.provider}")
            print(f"🕒 Время обработки: {result.processing_time:.2f}с")
        else:
            print(f"❌ Ошибка: {result.error_message}")
            print(f"🕒 Время обработки: {result.processing_time:.2f}с")
            
        return result
        
    except Exception as e:
        print(f"❌ Исключение: {e}")
        import traceback
        traceback.print_exc()
        return None

async def main():
    """Главная функция"""
    print("🎯 Прямое тестирование VoiceServiceOrchestrator")
    print("=" * 60)
    
    result = await test_voice_orchestrator_direct()
    
    print("\n" + "=" * 60)
    if result and result.success:
        print("🏁 Тест завершен успешно!")
    else:
        print("🏁 Тест завершен с ошибками")

if __name__ == "__main__":
    asyncio.run(main())
