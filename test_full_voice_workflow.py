#!/usr/bin/env python3
"""
Тест полного голосового workflow с исправлениями
"""

import asyncio
import sys
import os
import time

# Добавляем путь к проекту
sys.path.insert(0, '/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

from app.core.config import settings
from app.api.schemas.voice_schemas import (
    VoiceProvider, VoiceProcessingResult, 
    TTSConfig, STTConfig, AudioFormat
)
from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
from app.services.redis_wrapper import RedisService

async def test_full_voice_workflow():
    """Тест полного голосового workflow"""
    print("🎙️ ТЕСТ ПОЛНОГО ГОЛОСОВОГО WORKFLOW")
    print("=" * 60)
    
    try:
        # Создаем Redis service
        print("🔧 Инициализируем Redis Service...")
        redis_service = RedisService()
        await redis_service.initialize()
        print("✅ Redis Service инициализирован")
        
        # Создаем Voice Orchestrator
        print("🔧 Инициализируем Voice Orchestrator...")
        orchestrator = VoiceServiceOrchestrator(redis_service)
        
        # Конфигурация агента
        agent_config = {
            "voice_settings": {
                "enabled": True,
                "intent_detection_mode": "always",
                "max_file_size_mb": 25,
                "providers": [
                    {
                        "provider": "yandex",
                        "priority": 1,
                        "stt_config": {
                            "enabled": True,
                            "model": "general",
                            "language": "ru-RU"
                        },
                        "tts_config": {
                            "enabled": True,
                            "model": "jane",
                            "language": "ru-RU",
                            "voice": "jane",
                            "audio_format": "mp3"
                        }
                    },
                    {
                        "provider": "openai",
                        "priority": 2,
                        "stt_config": {
                            "enabled": True,
                            "model": "whisper-1",
                            "language": "ru"
                        },
                        "tts_config": {
                            "enabled": True,
                            "model": "tts-1",
                            "voice": "nova",
                            "audio_format": "mp3"
                        }
                    }
                ]
            }
        }
        
        # Инициализируем сервисы
        agent_id = "test_agent"
        success = await orchestrator.initialize_voice_services_for_agent(agent_id, agent_config)
        
        if success:
            print("✅ Voice Orchestrator инициализирован")
            
            # Тестируем TTS
            print("🗣️ Тестируем TTS...")
            result = await orchestrator.synthesize_speech(
                agent_id=agent_id,
                user_id="test_user",
                text="Привет! Это тест голосового сообщения. Yandex TTS теперь работает корректно!",
                agent_config=agent_config
            )
            
            if result.success:
                print("✅ TTS синтез успешен!")
                print(f"🎵 Audio URL: {result.audio_url}")
                print(f"⏱️ Время обработки: {result.processing_time:.2f}s")
                print(f"🔧 Провайдер: {result.provider_used}")
            else:
                print(f"❌ TTS ошибка: {result.error_message}")
                
        else:
            print("❌ Не удалось инициализировать Voice Orchestrator")
            
        # Очистка
        await orchestrator.cleanup()
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    print("🏁 Тест завершен")

if __name__ == "__main__":
    asyncio.run(test_full_voice_workflow())
