#!/usr/bin/env python3
"""
Простой тест OpenAI STT для проверки API ключа и работы сервиса
"""

import asyncio
import logging
import uuid
from datetime import datetime
from minio import Minio

# Импорты для тестирования
from app.core.config import settings
from app.services.voice.stt.openai_stt import OpenAISTTService
from app.api.schemas.voice_schemas import VoiceProvider, STTConfig, AudioFormat, VoiceFileInfo


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_openai_stt():
    """
    Простое тестирование OpenAI STT
    """
    logger.info("🎯 Тестирование OpenAI STT")
    
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
        
        # 2. Создаем OpenAI STT сервис
        logger.info("🔧 Инициализация OpenAI STT сервиса...")
        
        config = STTConfig(
            provider=VoiceProvider.OPENAI,
            model="whisper-1",
            language="ru",
            audio_format=AudioFormat.MP3  # OpenAI принимает разные форматы
        )
        
        service = OpenAISTTService(config, logger)
        
        # Проверяем API ключ
        logger.info("🔑 Проверка API ключа OpenAI...")
        if not settings.OPENAI_API_KEY:
            logger.error("❌ OPENAI_API_KEY не настроен!")
            return
        
        api_key_preview = settings.OPENAI_API_KEY.get_secret_value()[:20] + "..."
        logger.info(f"   📝 API Key: {api_key_preview}")
        
        await service.initialize()
        
        # 3. Создаем file_info
        # Файл имеет MIME тип audio/mpeg, но OpenAI поддерживает множество форматов
        file_info = VoiceFileInfo(
            file_id=str(uuid.uuid4()),
            original_filename="test.mp3",  # OpenAI определит формат сам
            mime_type="audio/mpeg",
            size_bytes=len(audio_data),
            format=AudioFormat.MP3,
            created_at=datetime.now().isoformat(),
            minio_bucket=bucket_name,
            minio_key=minio_file_path
        )
        
        # 4. Выполняем STT
        logger.info("🎙️ Выполнение распознавания речи...")
        logger.info(f"   📝 Модель: {config.model}")
        logger.info(f"   🌐 Язык: {config.language}")
        
        result = await service.transcribe_audio(audio_data, file_info)
        
        # 5. Проверяем результат
        if result.success:
            logger.info("✅ OpenAI STT успешно:")
            logger.info(f"   📝 Распознанный текст: '{result.text}'")
            logger.info(f"   ⏱️  Время обработки: {result.processing_time:.2f}s")
            logger.info(f"   🔧 Использован провайдер: {result.provider_used.value}")
            if result.metadata:
                logger.info(f"   📊 Метаданные: {result.metadata}")
                
            # Проверяем ключевые слова
            if result.text:
                # Если есть текст, то всё работает отлично
                logger.info("🎉 STT работает корректно - текст распознан!")
            else:
                logger.warning("⚠️ Текст пустой - возможно аудио содержит тишину")
                
        else:
            logger.error(f"❌ OpenAI STT вернул ошибку:")
            if result.error_message:
                logger.error(f"   💬 Ошибка: {result.error_message}")
            if result.metadata:
                logger.error(f"   📊 Метаданные: {result.metadata}")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        return
        
    finally:
        if 'service' in locals():
            await service.cleanup()


async def main():
    """Основная функция"""
    logger.info("🚀 Запуск простого тестирования OpenAI STT...")
    
    # Проверяем настройки
    if not settings.OPENAI_API_KEY:
        logger.error("❌ OPENAI_API_KEY не найден в переменных окружения!")
        return
        
    if not settings.MINIO_VOICE_BUCKET_NAME:
        logger.error("❌ MINIO_VOICE_BUCKET_NAME не настроен!")
        return
    
    await test_openai_stt()
    
    logger.info("🎉 Тестирование завершено успешно!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Тестирование завершилось с ошибками: {e}")
        exit(1)
