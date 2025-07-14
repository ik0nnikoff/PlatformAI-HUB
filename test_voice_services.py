"""
Тестовый скрипт для проверки голосовых сервисов
"""

import asyncio
import logging
import sys
import os

# Добавляем корень проекта в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceSettings, VoiceProviderConfig, STTConfig, TTSConfig, VoiceProvider, STTModel, TTSModel, OpenAIVoice, AudioFormat
from app.services.voice import VoiceServiceOrchestrator
from app.services.redis_wrapper import RedisService

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_test")

async def test_voice_services():
    """Тестирование голосовых сервисов"""
    
    logger.info("Starting voice services test...")
    
    try:
        # Инициализируем Redis
        redis_service = RedisService()
        await redis_service.initialize()
        
        # Инициализируем оркестратор
        orchestrator = VoiceServiceOrchestrator(redis_service, logger)
        await orchestrator.initialize()
        
        # Создаем тестовую конфигурацию агента
        test_agent_config = {
            "voice_settings": {
                "enabled": True,
                "intent_detection_mode": "keywords",
                "intent_keywords": ["голос", "скажи", "произнеси"],
                "auto_stt": True,
                "auto_tts_on_keywords": True,
                "max_file_size_mb": 25,
                "cache_enabled": True,
                "cache_ttl_hours": 1,
                "rate_limit_per_minute": 10,
                "providers": [
                    {
                        "provider": "openai",
                        "priority": 1,
                        "fallback_enabled": True,
                        "stt_config": {
                            "enabled": True,
                            "model": "whisper-1",
                            "language": "ru",
                            "max_duration": 120
                        },
                        "tts_config": {
                            "enabled": True,
                            "model": "tts-1",
                            "voice": "alloy",
                            "language": "ru",
                            "speed": 1.0,
                            "audio_format": "mp3"
                        }
                    }
                ]
            }
        }
        
        # Инициализируем голосовые сервисы для тестового агента
        agent_id = "test_agent"
        success = await orchestrator.initialize_voice_services_for_agent(agent_id, test_agent_config)
        
        if success:
            logger.info("✅ Voice services initialized successfully")
        else:
            logger.error("❌ Failed to initialize voice services")
            return
        
        # Проверяем здоровье сервисов
        health = await orchestrator.get_service_health()
        logger.info(f"Service health: {health}")
        
        # Тестируем TTS (синтез речи)
        logger.info("Testing TTS...")
        test_text = "Привет! Это тест голосового синтеза речи."
        tts_success, file_info, error = await orchestrator.synthesize_response(
            agent_id=agent_id,
            user_id="test_user",
            text=f"голос {test_text}",  # Добавляем ключевое слово
            agent_config=test_agent_config
        )
        
        if tts_success and file_info:
            logger.info(f"✅ TTS successful: {file_info.original_filename}")
            logger.info(f"   File size: {file_info.size_bytes} bytes")
            logger.info(f"   MinIO key: {file_info.minio_key}")
        else:
            logger.warning(f"❌ TTS failed: {error}")
        
        # Очистка
        await orchestrator.cleanup()
        await redis_service.cleanup()
        
        logger.info("Voice services test completed!")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(test_voice_services())
