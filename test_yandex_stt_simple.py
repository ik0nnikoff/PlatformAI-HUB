#!/usr/bin/env python3
"""
Простое тестирование Yandex STT с реальным аудиофайлом
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
from minio import Minio
import uuid
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_yandex_stt():
    """
    Простое тестирование Yandex STT
    """
    logger.info("🎯 Тестирование Yandex STT")
    
    # Параметры тестового файла
    minio_file_path = "voice/agent_airsoft_0faa9616/144641834/2025/07/14/09/5c80d71d-53bd-4dd2-90ed-043a74ef75eb.ogg"
    bucket_name = settings.MINIO_VOICE_BUCKET_NAME
    
    try:
        # 1. Загрузка файла из MinIO
        logger.info(f"📁 Загрузка аудиофайла из MinIO: {minio_file_path}")
        
        minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )
        
        # Проверяем файл
        stat = minio_client.stat_object(bucket_name, minio_file_path)
        logger.info(f"✅ Файл найден: размер {stat.size} байт, тип {stat.content_type}")
        
        # Загружаем файл
        response = minio_client.get_object(bucket_name, minio_file_path)
        audio_data = response.read()
        logger.info(f"📥 Загружено {len(audio_data)} байт аудиоданных")
        
        # 2. Создаем Yandex STT сервис
        logger.info("🔧 Инициализация Yandex STT сервиса...")
        
        config = STTConfig(
            provider=VoiceProvider.YANDEX,
            model="general",
            language="ru-RU",
            audio_format=AudioFormat.OGG  # Правильный OGG формат
        )
        
        service = YandexSTTService(config, logger)
        await service.initialize()
        
        # 3. Создаем file_info
        # MIME тип audio/mpeg говорит о том, что это MP3 файл
        # Но файл на самом деле OGG Opus - пусть сервис сам определит по расширению
        file_info = VoiceFileInfo(
            file_id=str(uuid.uuid4()),
            original_filename="test.ogg",  # Правильное расширение .ogg
            mime_type="audio/ogg",  # Правильный MIME тип
            size_bytes=len(audio_data),
            format=AudioFormat.OGG,  # Указываем OGG формат
            created_at=datetime.now().isoformat(),
            minio_bucket=bucket_name,
            minio_key=minio_file_path
        )
        
        # 4. Выполняем STT
        logger.info("🎙️ Выполнение распознавания речи...")
        result = await service.transcribe_audio(audio_data, file_info)
        
        # 5. Проверяем результат
        if result.success:
            logger.info("✅ Yandex STT успешно:")
            logger.info(f"   📝 Распознанный текст: '{result.text}'")
            logger.info(f"   ⏱️  Время обработки: {result.processing_time:.2f}s")
            logger.info(f"   🔧 Использован провайдер: {result.provider_used.value}")
            if result.metadata:
                logger.info(f"   📊 Метаданные: {result.metadata}")
                
            # Проверяем ключевые слова
            if result.text:
                keywords_to_check = [
                    "страйкбол", "airsoft", "привет", "добро пожаловать",
                    "оружие", "пистолет", "команда", "игра"
                ]
                
                found_keywords = []
                text_lower = result.text.lower()
                
                for keyword in keywords_to_check:
                    if keyword.lower() in text_lower:
                        found_keywords.append(keyword)
                
                if found_keywords:
                    logger.info(f"   🎯 Найденные ключевые слова: {', '.join(found_keywords)}")
                else:
                    logger.info(f"   🔍 Знакомые ключевые слова не найдены")
                    
                logger.info(f"   📊 Слов в тексте: {len(result.text.split())}")
                logger.info(f"   🔤 Символов в тексте: {len(result.text)}")
            
            return True
        else:
            logger.error("❌ Yandex STT неудачно:")
            logger.error(f"   🚫 Ошибка: {result.error_message}")
            return False
            
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        return False
    finally:
        # Cleanup
        if 'service' in locals():
            await service.cleanup()


async def main():
    """Главная функция тестирования"""
    logger.info("🚀 Запуск простого тестирования Yandex STT...")
    
    success = await test_yandex_stt()
    
    if success:
        logger.info("🎉 Тестирование завершено успешно!")
        sys.exit(0)
    else:
        logger.error("❌ Тестирование завершилось с ошибками!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
