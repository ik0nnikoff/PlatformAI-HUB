#!/usr/bin/env python3
"""
Тест Yandex STT с правильным форматом OGG Opus
"""

import asyncio
import logging
import uuid
from datetime import datetime
from minio import Minio

# Импорты для тестирования
from app.core.config import settings
from app.services.voice.stt.yandex_stt import YandexSTTService
from app.api.schemas.voice_schemas import VoiceProvider, STTConfig, AudioFormat, VoiceFileInfo


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_yandex_stt_opus():
    """
    Тестирование Yandex STT с правильным форматом OGG Opus
    """
    logger.info("🎯 Тестирование Yandex STT с OGG Opus")
    
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
        
        # 2. Создаем Yandex STT сервис с OPUS форматом
        logger.info("🔧 Инициализация Yandex STT сервиса с OPUS...")
        
        config = STTConfig(
            provider=VoiceProvider.YANDEX,
            model="general",
            language="ru-RU",
            audio_format=AudioFormat.OGG  # OGG Opus формат
        )
        
        service = YandexSTTService(config, logger)
        await service.initialize()
        
        # 3. Создаем file_info с OPUS указанием
        file_info = VoiceFileInfo(
            file_id=str(uuid.uuid4()),
            original_filename="test.opus",  # Указываем .opus расширение
            mime_type="audio/ogg",  # OGG контейнер
            size_bytes=len(audio_data),
            format=AudioFormat.OGG,  # OGG формат
            created_at=datetime.now().isoformat(),
            minio_bucket=bucket_name,
            minio_key=minio_file_path
        )
        
        # 4. Прямой вызов с параметром format=opus
        logger.info("🎙️ Выполнение распознавания речи с format=opus...")
        result = await service.transcribe_audio(audio_data, file_info, format="opus")
        
        # 5. Проверяем результат
        if result.success:
            logger.info("✅ Yandex STT с OPUS успешно:")
            logger.info(f"   📝 Распознанный текст: '{result.text}'")
            logger.info(f"   ⏱️  Время обработки: {result.processing_time:.2f}s")
            logger.info(f"   🔧 Использован провайдер: {result.provider_used.value}")
            if result.metadata:
                logger.info(f"   📊 Метаданные: {result.metadata}")
                
            # Проверяем ключевые слова
            if result.text:
                logger.info("🎉 STT работает корректно - текст распознан!")
            else:
                logger.warning("⚠️ Текст пустой - возможно проблема с кодеком")
                
        else:
            logger.error(f"❌ Yandex STT вернул ошибку:")
            if result.error_message:
                logger.error(f"   💬 Ошибка: {result.error_message}")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        return
        
    finally:
        if 'service' in locals():
            await service.cleanup()


async def main():
    """Основная функция"""
    logger.info("🚀 Запуск тестирования Yandex STT с правильным форматом...")
    
    await test_yandex_stt_opus()
    
    logger.info("🎉 Тестирование завершено!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Тестирование завершилось с ошибками: {e}")
        exit(1)
