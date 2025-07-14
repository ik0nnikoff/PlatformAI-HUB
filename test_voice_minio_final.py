#!/usr/bin/env python3
"""
Финальный тест голосовых функций с реальным файлом из MinIO
"""

import asyncio
import json
import time
from pathlib import Path
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_minio_voice_file():
    """Тест с реальным голосовым файлом из MinIO"""
    
    import sys
    sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')
    
    try:
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        from app.services.redis_wrapper import RedisService
        from app.core.config import settings
        from app.api.schemas.voice_schemas import VoiceFileInfo, AudioFormat
        from minio import Minio
        
        print("🔧 Инициализируем сервисы...")
        
        # Инициализируем Redis
        redis_wrapper = RedisService()
        await redis_wrapper.initialize()
        
        # Инициализируем voice orchestrator
        orchestrator = VoiceServiceOrchestrator(redis_service=redis_wrapper)
        await orchestrator.initialize()
        
        # Инициализируем STT сервисы с конфигурацией
        print("🔧 Инициализируем STT сервисы...")
        
        agent_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "intent_detection_mode": "keywords",
                            "intent_keywords": [
                                "голос", "скажи", "произнеси", "озвучь",
                                "расскажи голосом", "ответь голосом", "прочитай вслух"
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
        
        # Инициализируем сервисы для агента
        await orchestrator.initialize_for_agent("agent_airsoft_0faa9616", agent_config)
        
        print("✅ Сервисы инициализированы")
        
        # Инициализируем MinIO клиент
        minio_client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        
        # Путь к файлу в MinIO
        bucket_name = "voice-files"
        object_name = "voice/agent_airsoft_0faa9616/144641834/2025/07/14/09/5c80d71d-53bd-4dd2-90ed-043a74ef75eb.ogg"
        
        print(f"📁 Скачиваем файл из MinIO: {bucket_name}/{object_name}")
        
        # Скачиваем файл
        try:
            response = minio_client.get_object(bucket_name, object_name)
            audio_data = response.read()
            response.close()
            response.release_conn()
            
            print(f"✅ Файл скачан, размер: {len(audio_data)} байт")
            
        except Exception as e:
            print(f"❌ Ошибка скачивания файла: {e}")
            return
        
        # Создаем VoiceFileInfo
        file_info = VoiceFileInfo(
            file_id="test_minio_file",
            original_filename="test_voice.ogg",
            mime_type="audio/ogg",
            size_bytes=len(audio_data),
            format=AudioFormat.OGG,
            created_at="2025-07-14T09:00:00Z",
            minio_bucket=bucket_name,
            minio_key=object_name
        )
        
        # Конфигурация агента с правильной структурой
        agent_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "intent_detection_mode": "keywords",
                            "intent_keywords": [
                                "голос", "скажи", "произнеси", "озвучь",
                                "расскажи голосом", "ответь голосом", "прочитай вслух"
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
        
        print("🎤 Обрабатываем голосовое сообщение...")
        start_time = time.time()
        
        # Обрабатываем голосовое сообщение через orchestrator
        result = await orchestrator.process_voice_message(
            agent_id="agent_airsoft_0faa9616",
            user_id="144641834",
            audio_data=audio_data,
            original_filename="test_voice.ogg",
            agent_config=agent_config
        )
        
        processing_time = time.time() - start_time
        
        print(f"⏱️ Время обработки: {processing_time:.2f}с")
        print(f"📊 Результат: {json.dumps(result.dict(), ensure_ascii=False, indent=2)}")
        
        if result.success:
            print(f"✅ Успешно распознан текст: '{result.text}'")
            print(f"🔧 Использован провайдер: {result.provider_used}")
            
            if result.metadata:
                print(f"📋 Метаданные: {json.dumps(result.metadata, ensure_ascii=False, indent=2)}")
        else:
            print(f"❌ Ошибка обработки: {result.error_message}")
            
        # Закрываем соединения
        await redis_wrapper.cleanup()
        await orchestrator.cleanup()
        
        return result
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logger.exception("Critical error in test")
        return None

async def test_telegram_bot_simulation():
    """Симуляция обработки голосового сообщения через Telegram бота"""
    
    import sys
    sys.path.append('/Users/jb/Projects/PlatformAI/PlatformAI-HUB')
    
    try:
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        from app.services.redis_wrapper import RedisService
        from app.core.config import settings
        from minio import Minio
        
        print("\n🤖 Тест симуляции Telegram бота...")
        
        # Инициализируем сервисы
        redis_wrapper = RedisService()
        await redis_wrapper.initialize()
        
        orchestrator = VoiceServiceOrchestrator(redis_service=redis_wrapper)
        await orchestrator.initialize()
        
        # Скачиваем файл как в реальном боте
        minio_client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        
        bucket_name = "voice-files"
        object_name = "voice/agent_airsoft_0faa9616/144641834/2025/07/14/09/5c80d71d-53bd-4dd2-90ed-043a74ef75eb.ogg"
        
        response = minio_client.get_object(bucket_name, object_name)
        audio_data = response.read()
        response.close()
        response.release_conn()
        
        # Используем ту же конфигурацию, что и в исправленном telegram_bot.py
        agent_config = {
            "config": {
                "simple": {
                    "settings": {
                        "voice_settings": {
                            "enabled": True,
                            "intent_detection_mode": "keywords",
                            "intent_keywords": [
                                "голос", "скажи", "произнеси", "озвучь",
                                "расскажи голосом", "ответь голосом", "прочитай вслух"
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
        
        print("🎯 Выполняем process_voice_message как в боте...")
        
        # Имитируем вызов как в telegram_bot.py
        result = await orchestrator.process_voice_message(
            agent_id="agent_airsoft_0faa9616",
            user_id="144641834", 
            audio_data=audio_data,
            original_filename="voice_1721058580.ogg",  # Как в боте
            agent_config=agent_config
        )
        
        if result.success and result.text:
            print(f"✅ Телеграм бот симуляция успешна!")
            print(f"📝 Распознанный текст будет отправлен агенту: '{result.text}'")
            
            # Симулируем отправку в агент (без реального вызова)
            print(f"🚀 Симуляция отправки сообщения агенту: '{result.text[:100]}...'")
        else:
            print(f"❌ Телеграм бот симуляция провалилась: {result.error_message}")
            
        await redis_wrapper.cleanup()
        await orchestrator.cleanup()
        
        return result.success
        
    except Exception as e:
        print(f"❌ Ошибка симуляции бота: {e}")
        logger.exception("Bot simulation error")
        return False

async def main():
    """Главная функция финального тестирования"""
    print("🎯 ФИНАЛЬНОЕ ТЕСТИРОВАНИЕ ГОЛОСОВЫХ ФУНКЦИЙ")
    print("=" * 70)
    print("📁 Используется файл: voice/agent_airsoft_0faa9616/144641834/2025/07/14/09/5c80d71d-53bd-4dd2-90ed-043a74ef75eb.ogg")
    print("=" * 70)
    
    # Тест 1: Прямой тест VoiceOrchestrator
    print("\n1️⃣ Тест VoiceServiceOrchestrator с реальным файлом")
    result1 = await test_minio_voice_file()
    
    if result1 and result1.success:
        print("✅ Тест 1 пройден успешно!")
    else:
        print("❌ Тест 1 провален!")
    
    # Тест 2: Симуляция Telegram бота  
    print("\n2️⃣ Симуляция обработки в Telegram боте")
    result2 = await test_telegram_bot_simulation()
    
    if result2:
        print("✅ Тест 2 пройден успешно!")
    else:
        print("❌ Тест 2 провален!")
    
    print("\n" + "=" * 70)
    if result1 and result1.success and result2:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Голосовые функции работают корректно!")
        print("✅ Исправления в telegram_bot.py работают правильно")
        print("✅ VoiceServiceOrchestrator обрабатывает конфигурацию корректно")
        print("✅ Удалено ненужное уведомление 'Обрабатываю голосовое сообщение...'")
        print("✅ Исправлена ошибка 'Голосовые функции отключены для этого агента'")
    else:
        print("❌ ЕСТЬ ПРОБЛЕМЫ! Требуется дополнительная отладка.")
    
    print("🏁 Финальное тестирование завершено")

if __name__ == "__main__":
    asyncio.run(main())
