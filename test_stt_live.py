#!/usr/bin/env python3
"""
Живое тестирование STT (Speech-to-Text) функциональности
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Добавляем путь к проекту
sys.path.append(str(Path(__file__).parent))

from app.core.config import settings
from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
from app.api.schemas.voice_schemas import VoiceProvider, STTConfig, AudioFormat
from app.services.redis_wrapper import RedisService
from minio import Minio
import io

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_stt_with_minio_file():
    """
    Тестирование STT с аудиофайлом из MinIO
    """
    logger.info("🎯 Начинаем живое тестирование STT функциональности")
    
    # Параметры тестового файла
    minio_file_path = "voice/agent_airsoft_0faa9616/144641834/2025/07/14/09/5c80d71d-53bd-4dd2-90ed-043a74ef75eb.ogg"
    bucket_name = settings.MINIO_VOICE_BUCKET_NAME
    
    try:
        # 1. Инициализация Redis
        logger.info("📡 Инициализация Redis...")
        redis_service = RedisService()
        await redis_service.initialize()
        
        # 2. Инициализация Voice Orchestrator
        logger.info("🎙️ Инициализация Voice Orchestrator...")
        voice_orchestrator = VoiceServiceOrchestrator(
            redis_service=redis_service,
            logger=logger
        )
        await voice_orchestrator.initialize()
        
        # 3. Получение аудиофайла из MinIO
        logger.info(f"📁 Загрузка аудиофайла из MinIO: {minio_file_path}")
        
        # Инициализация MinIO клиента
        minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        
        # Проверяем существование файла
        try:
            stat = minio_client.stat_object(bucket_name, minio_file_path)
            logger.info(f"✅ Файл найден: размер {stat.size} байт, тип {stat.content_type}")
        except Exception as e:
            logger.error(f"❌ Файл не найден в MinIO: {e}")
            return False
        
        # Загружаем файл
        response = minio_client.get_object(bucket_name, minio_file_path)
        audio_data = response.read()
        logger.info(f"📥 Загружено {len(audio_data)} байт аудиоданных")
        
        # 4. Тестирование STT с разными провайдерами
        providers_to_test = [
            VoiceProvider.YANDEX,
            VoiceProvider.OPENAI,
            VoiceProvider.GOOGLE
        ]
        
        results = {}
        
        # Инициализируем voice сервисы для агента
        agent_config = {
            "voice": {
                "enabled": True,
                "stt_providers": [
                    {"provider": "yandex", "priority": 1, "enabled": True},
                    {"provider": "openai", "priority": 2, "enabled": True},
                    {"provider": "google", "priority": 3, "enabled": True}
                ],
                "tts_providers": [
                    {"provider": "yandex", "priority": 1, "enabled": True}
                ],
                "max_file_size_mb": 25,
                "cache_enabled": False
            }
        }
        
        await voice_orchestrator.initialize_voice_services_for_agent("test_agent", agent_config)
        
        # Простое тестирование через process_voice_message
        logger.info(f"🔄 Тестирование STT через process_voice_message...")
        
        try:
            result = await voice_orchestrator.process_voice_message(
                agent_id="test_agent",
                user_id="test_user",
                audio_data=audio_data,
                original_filename="test.ogg",
                agent_config=agent_config
            )
            
            if result.success:
                logger.info(f"✅ STT успешно:")
                logger.info(f"   📝 Распознанный текст: '{result.text}'")
                logger.info(f"   ⏱️  Время обработки: {result.processing_time:.2f}s")
                logger.info(f"   🔧 Использован провайдер: {result.provider_used.value if result.provider_used else 'неизвестно'}")
                if result.metadata:
                    logger.info(f"   📊 Метаданные: {result.metadata}")
                
                results["combined"] = {
                    "success": True,
                    "text": result.text,
                    "processing_time": result.processing_time,
                    "provider_used": result.provider_used.value if result.provider_used else "unknown",
                    "metadata": result.metadata
                }
            else:
                logger.error(f"❌ STT неудачно:")
                logger.error(f"   🚫 Ошибка: {result.error_message}")
                
                results["combined"] = {
                    "success": False,
                    "error": result.error_message,
                    "processing_time": result.processing_time
                }
                
        except Exception as e:
            logger.error(f"💥 Исключение при тестировании STT: {e}", exc_info=True)
            results["combined"] = {
                "success": False,
                "error": str(e),
                "processing_time": 0.0
            }
        
        # 5. Сводка результатов
        logger.info("\n" + "="*60)
        logger.info("📊 СВОДКА РЕЗУЛЬТАТОВ STT ТЕСТИРОВАНИЯ")
        logger.info("="*60)
        
        successful_providers = []
        failed_providers = []
        
        for provider, result in results.items():
            if result["success"]:
                successful_providers.append(provider)
                logger.info(f"✅ {provider}: '{result['text']}' ({result['processing_time']:.2f}s)")
            else:
                failed_providers.append(provider)
                logger.error(f"❌ {provider}: {result['error']}")
        
        logger.info(f"\n📈 Статистика:")
        logger.info(f"   ✅ Успешно: {len(successful_providers)}/{len(results)} провайдеров")
        logger.info(f"   ❌ Неудачно: {len(failed_providers)}/{len(results)} провайдеров")
        
        if successful_providers:
            logger.info(f"   🏆 Работающие провайдеры: {', '.join(successful_providers)}")
        
        if failed_providers:
            logger.info(f"   🚫 Неработающие провайдеры: {', '.join(failed_providers)}")
        
        # 6. Тестирование keyword detection
        if successful_providers:
            logger.info(f"\n🔍 Тестирование keyword detection...")
            
            # Получаем текст от лучшего провайдера
            best_result = None
            for provider_name in results:
                if results[provider_name]["success"]:
                    best_result = results[provider_name]
                    break
            
            if best_result and best_result["text"]:
                # Проверяем наличие ключевых слов
                keywords_to_check = [
                    "страйкбол", "airsoft", "оружие", "пистолет", "автомат", 
                    "BB", "шарики", "игра", "команда", "тактика"
                ]
                
                found_keywords = []
                text_lower = best_result["text"].lower()
                
                for keyword in keywords_to_check:
                    if keyword.lower() in text_lower:
                        found_keywords.append(keyword)
                
                logger.info(f"   📝 Анализируемый текст: '{best_result['text']}'")
                if found_keywords:
                    logger.info(f"   🎯 Найденные ключевые слова: {', '.join(found_keywords)}")
                else:
                    logger.info(f"   🔍 Ключевые слова страйкбола не найдены")
        
        # Cleanup
        await voice_orchestrator.cleanup()
        await redis_service.cleanup()
        
        logger.info("\n🎉 Тестирование STT завершено!")
        return len(successful_providers) > 0
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка при тестировании STT: {e}", exc_info=True)
        return False


async def main():
    """Главная функция тестирования"""
    logger.info("🚀 Запуск live тестирования STT...")
    
    success = await test_stt_with_minio_file()
    
    if success:
        logger.info("🎉 Тестирование завершено успешно!")
        sys.exit(0)
    else:
        logger.error("❌ Тестирование завершилось с ошибками!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
