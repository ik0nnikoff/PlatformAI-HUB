#!/usr/bin/env python3
"""
Живое тестирование STT (Speech-to-Text) функциональности
Простая версия с прямым использованием STT сервисов
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Добавляем путь к проекту
sys.path.append(str(Path(__file__).parent))

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, STTConfig, AudioFormat, VoiceFileInfo
from app.services.voice.stt.yandex_stt import YandexSTTService
from app.services.voice.stt.openai_stt import OpenAISTTService
from app.services.voice.stt.google_stt import GoogleSTTService
from minio import Minio
import uuid
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_stt_service_direct(provider: VoiceProvider, audio_data: bytes) -> dict:
    """
    Прямое тестирование STT сервиса
    """
    logger.info(f"🔄 Тестирование {provider.value} STT сервиса...")
    
    try:
        # Создаем конфигурацию для STT
        if provider == VoiceProvider.YANDEX:
            config = STTConfig(
                provider=provider,
                model="general",
                language="ru-RU",
                audio_format=AudioFormat.OGG
            )
            service = YandexSTTService(config, logger)
            
        elif provider == VoiceProvider.OPENAI:
            config = STTConfig(
                provider=provider,
                model="whisper-1",
                language="ru",
                audio_format=AudioFormat.OGG
            )
            service = OpenAISTTService(config, logger)
            
        elif provider == VoiceProvider.GOOGLE:
            config = STTConfig(
                provider=provider,
                model="latest_long",
                language="ru-RU",
                audio_format=AudioFormat.OGG
            )
            service = GoogleSTTService(config, logger)
        else:
            return {
                "success": False,
                "error": f"Неподдерживаемый провайдер: {provider.value}",
                "processing_time": 0.0
            }
        
        # Инициализируем сервис
        await service.initialize()
        
        # Создаем file_info для тестирования
        # Попробуем с форматом WAV/LPCM, который лучше поддерживается Yandex
        file_info = VoiceFileInfo(
            file_id=str(uuid.uuid4()),
            original_filename="test.wav",  # Изменено на wav
            mime_type="audio/wav", 
            size_bytes=len(audio_data),
            format=AudioFormat.WAV,  # Изменено на WAV
            created_at=datetime.now().isoformat(),
            minio_bucket="voice-files",
            minio_key="test/path/test.wav"
        )
        
        # Выполняем STT
        result = await service.transcribe_audio(audio_data, file_info)
        
        # Cleanup
        await service.cleanup()
        
        if result.success:
            return {
                "success": True,
                "text": result.text,
                "processing_time": result.processing_time,
                "provider_used": result.provider_used.value if result.provider_used else provider.value,
                "metadata": result.metadata
            }
        else:
            return {
                "success": False,
                "error": result.error_message,
                "processing_time": result.processing_time
            }
            
    except Exception as e:
        logger.error(f"💥 Исключение при тестировании {provider.value}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "processing_time": 0.0
        }


async def test_stt_with_minio_file():
    """
    Тестирование STT с аудиофайлом из MinIO
    """
    logger.info("🎯 Начинаем прямое тестирование STT функциональности")
    
    # Параметры тестового файла
    minio_file_path = "voice/agent_airsoft_0faa9616/144641834/2025/07/14/09/5c80d71d-53bd-4dd2-90ed-043a74ef75eb.ogg"
    bucket_name = settings.MINIO_VOICE_BUCKET_NAME
    
    try:
        # 1. Получение аудиофайла из MinIO
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
        
        # 2. Тестирование STT с разными провайдерами
        providers_to_test = [
            VoiceProvider.YANDEX,  # Начнем только с Yandex
            # VoiceProvider.OPENAI,
            # VoiceProvider.GOOGLE
        ]
        
        results = {}
        
        for provider in providers_to_test:
            result = await test_stt_service_direct(provider, audio_data)
            results[provider.value] = result
        
        # 3. Сводка результатов
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
        
        # 4. Тестирование keyword detection
        if successful_providers:
            logger.info(f"\n🔍 Тестирование keyword detection...")
            
            # Получаем текст от лучшего провайдера
            best_result = None
            for provider_name in ["yandex", "openai", "google"]:
                if provider_name in results and results[provider_name]["success"]:
                    best_result = results[provider_name]
                    break
            
            if best_result and best_result["text"]:
                # Проверяем наличие ключевых слов
                keywords_to_check = [
                    "страйкбол", "airsoft", "оружие", "пистолет", "автомат", 
                    "BB", "шарики", "игра", "команда", "тактика", "привет", "добро пожаловать"
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
                    logger.info(f"   🔍 Знакомые ключевые слова не найдены (но это нормально)")
                    
                # Дополнительный анализ содержимого
                text_words = best_result["text"].lower().split()
                logger.info(f"   📊 Количество слов: {len(text_words)}")
                logger.info(f"   🔤 Длина текста: {len(best_result['text'])} символов")
        
        logger.info("\n🎉 Тестирование STT завершено!")
        return len(successful_providers) > 0
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка при тестировании STT: {e}", exc_info=True)
        return False


async def main():
    """Главная функция тестирования"""
    logger.info("🚀 Запуск прямого live тестирования STT...")
    
    success = await test_stt_with_minio_file()
    
    if success:
        logger.info("🎉 Тестирование завершено успешно!")
        sys.exit(0)
    else:
        logger.error("❌ Тестирование завершилось с ошибками!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
