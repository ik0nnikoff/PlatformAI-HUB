#!/usr/bin/env python3
"""
Финальный тест голосовых функций после исправления конфигурации
"""

import asyncio
import aiofiles
import aiohttp
import json
import time
from pathlib import Path

async def test_voice_processing():
    """Тест обработки голосового сообщения с исправленной конфигурацией"""
    
    # URL для тестирования голосовой обработки
    url = "http://localhost:8000/api/voice/test-stt"
    
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
    
    # Путь к тестовому аудиофайлу
    audio_path = Path("/tmp/test_audio_voice.ogg")
    
    try:
        async with aiofiles.open(audio_path, 'rb') as audio_file:
            audio_data = await audio_file.read()
            
        print(f"📁 Размер аудиофайла: {len(audio_data)} байт")
        
        # Подготавливаем данные для отправки
        data = aiohttp.FormData()
        data.add_field('audio_file', audio_data, filename='test_voice.ogg', content_type='audio/ogg')
        data.add_field('agent_id', 'test_agent')
        data.add_field('user_id', 'test_user')
        data.add_field('agent_config', json.dumps(agent_config))
        
        async with aiohttp.ClientSession() as session:
            print("🎤 Отправляем голосовое сообщение для обработки...")
            start_time = time.time()
            
            async with session.post(url, data=data) as response:
                result = await response.json()
                processing_time = time.time() - start_time
                
                print(f"⏱️ Время обработки: {processing_time:.2f}с")
                print(f"📊 Статус ответа: {response.status}")
                print(f"📝 Результат: {json.dumps(result, ensure_ascii=False, indent=2)}")
                
                if result.get('success'):
                    print(f"✅ Успешно распознан текст: '{result.get('text', '')}'")
                    print(f"🔧 Использован провайдер: {result.get('provider', 'unknown')}")
                else:
                    print(f"❌ Ошибка обработки: {result.get('error_message', 'unknown error')}")
                    
                return result
                
    except FileNotFoundError:
        print("❌ Тестовый аудиофайл не найден. Создаем новый...")
        
        # Создаем простой тестовый OGG файл
        test_data = b'OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00'  # Простой OGG заголовок
        async with aiofiles.open(audio_path, 'wb') as f:
            await f.write(test_data)
        print(f"✅ Создан тестовый файл: {audio_path}")
        return {"success": False, "error": "Test file created, run again"}
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return {"success": False, "error": str(e)}

async def test_voice_settings_validation():
    """Тест валидации настроек голоса"""
    
    # Простой тест без создания полного orchestrator
    print("🔧 Тестируем валидацию настроек голоса...")
    
    # Тестовая конфигурация
    agent_config = {
        "config": {
            "simple": {
                "settings": {
                    "voice_settings": {
                        "enabled": True,
                        "auto_stt": True,
                        "providers": [
                            {
                                "provider": "yandex",
                                "priority": 1,
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
    
    # Извлекаем настройки по пути как в voice_orchestrator
    voice_settings_dict = agent_config.get("config", {}).get("simple", {}).get("settings", {}).get("voice_settings")
    print(f"📄 Извлеченные настройки: {voice_settings_dict}")
    
    if voice_settings_dict:
        # Проверяем базовые поля
        has_enabled = "enabled" in voice_settings_dict
        has_providers = "providers" in voice_settings_dict
        is_enabled = voice_settings_dict.get("enabled", False)
        providers_count = len(voice_settings_dict.get("providers", []))
        
        print(f"✅ Поле 'enabled' присутствует: {has_enabled}")
        print(f"✅ Поле 'providers' присутствует: {has_providers}")
        print(f"✅ Голосовые функции включены: {is_enabled}")
        print(f"📋 Количество провайдеров: {providers_count}")
        
        if has_enabled and has_providers and is_enabled and providers_count > 0:
            print("✅ Конфигурация выглядит валидной")
        else:
            print("❌ Конфигурация имеет проблемы")
    else:
        print("❌ Настройки голоса не найдены в конфигурации")

async def main():
    """Главная функция тестирования"""
    print("🎯 Начинаем финальное тестирование голосовых функций")
    print("=" * 60)
    
    # Тест 1: Валидация настроек
    print("\n1️⃣ Тест валидации настроек голоса")
    await test_voice_settings_validation()
    
    # Тест 2: Обработка голосового сообщения
    print("\n2️⃣ Тест обработки голосового сообщения")
    result = await test_voice_processing()
    
    print("\n" + "=" * 60)
    print("🏁 Тестирование завершено")

if __name__ == "__main__":
    asyncio.run(main())
