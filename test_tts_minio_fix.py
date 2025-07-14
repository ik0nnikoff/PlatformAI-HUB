#!/usr/bin/env python3
"""
Тест исправлений MinioFileManager для TTS
"""

import asyncio
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, '/Users/jb/Projects/PlatformAI/PlatformAI-HUB')

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, TTSConfig, AudioFormat
from app.services.voice.tts.yandex_tts import YandexTTSService
from app.services.voice.minio_manager import MinioFileManager

async def test_tts_with_minio():
    """Тест TTS с сохранением в MinIO"""
    print("🔧 ТЕСТ TTS С MINIO")
    print("=" * 60)
    
    try:
        # Инициализируем Yandex TTS
        config = TTSConfig(
            enabled=True,
            model="jane",
            language="ru-RU",
            voice="jane",
            audio_format=AudioFormat.MP3
        )
        
        print("🎯 Инициализируем Yandex TTS...")
        yandex_tts = YandexTTSService(config)
        await yandex_tts.initialize()
        print("✅ Yandex TTS инициализирован")
        
        # Синтезируем речь
        print("🗣️ Синтезируем речь...")
        result = await yandex_tts.synthesize_speech("Привет! Это тест.")
        
        if result.success and result.metadata.get('audio_data'):
            audio_data = result.metadata['audio_data']
            print(f"✅ Синтез успешен: {len(audio_data)} байт")
            
            # Инициализируем MinIO менеджер
            print("💾 Инициализируем MinIO...")
            minio_manager = MinioFileManager()
            await minio_manager.initialize()
            print("✅ MinIO инициализирован")
            
            # Загружаем файл в MinIO
            print("📤 Загружаем аудио в MinIO...")
            file_info = await minio_manager.upload_audio_file(
                audio_data=audio_data,
                agent_id="test_agent",
                user_id="test_user",
                original_filename="test_tts.mp3",
                mime_type="audio/mpeg",
                audio_format=AudioFormat.MP3,
                metadata={
                    "type": "tts_output",
                    "text_length": 17,
                    "provider": "yandex"
                }
            )
            
            print(f"✅ Файл загружен: {file_info.file_id}")
            print(f"📁 Bucket: {file_info.minio_bucket}")
            print(f"🔑 Key: {file_info.minio_key}")
            print(f"📊 Размер: {file_info.size_bytes} байт")
            
            # Очистка
            await minio_manager.cleanup()
            await yandex_tts.cleanup()
            
        else:
            print(f"❌ Ошибка синтеза: {result.error_message}")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    print("🏁 Тест завершен")

if __name__ == "__main__":
    asyncio.run(test_tts_with_minio())
