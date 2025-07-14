#!/usr/bin/env python3
"""
Анализ аудио файла для понимания его содержимого
"""

import logging
import os
import tempfile
from minio import Minio

# Импорты для анализа аудио
try:
    import soundfile as sf
    import numpy as np
    HAS_SOUNDFILE = True
except ImportError:
    HAS_SOUNDFILE = False

try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

from app.core.config import settings


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def analyze_audio_file():
    """
    Анализ аудио файла
    """
    logger.info("🎯 Анализ аудиофайла")
    
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
        
        # 2. Базовый анализ бинарных данных
        logger.info("🔍 Базовый анализ:")
        
        # Проверяем заголовки файла
        header = audio_data[:20]
        logger.info(f"   📜 Заголовок файла (hex): {header.hex()}")
        logger.info(f"   📜 Заголовок файла (ascii): {header[:10]}")
        
        # Определяем формат по magic bytes
        if audio_data.startswith(b'OggS'):
            detected_format = "OGG Vorbis/Opus"
        elif audio_data.startswith(b'ID3') or audio_data[1:4] == b'ID3':
            detected_format = "MP3 with ID3"
        elif audio_data.startswith(b'\xff\xfb') or audio_data.startswith(b'\xff\xf3'):
            detected_format = "MP3"
        elif audio_data.startswith(b'RIFF'):
            detected_format = "WAV"
        elif audio_data.startswith(b'fLaC'):
            detected_format = "FLAC"
        else:
            detected_format = "Unknown"
            
        logger.info(f"   🎵 Определенный формат: {detected_format}")
        
        # Проверяем на пустоту
        unique_bytes = len(set(audio_data))
        logger.info(f"   📊 Уникальных байт: {unique_bytes} из {len(audio_data)}")
        
        if unique_bytes < 10:
            logger.warning("⚠️ Файл может содержать в основном одинаковые данные (тишина или шум)")
        
        # 3. Продвинутый анализ с библиотеками (если доступны)
        if HAS_SOUNDFILE or HAS_LIBROSA:
            logger.info("🎧 Продвинутый анализ аудио:")
            
            # Сохраняем временный файл для анализа
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                if HAS_LIBROSA:
                    # Анализ с librosa
                    logger.info("   🔬 Используем librosa...")
                    try:
                        y, sr = librosa.load(temp_path, sr=None)
                        logger.info(f"   📊 Частота дискретизации: {sr} Hz")
                        logger.info(f"   ⏱️  Длительность: {len(y)/sr:.2f} секунд")
                        logger.info(f"   📈 Количество сэмплов: {len(y)}")
                        
                        # Анализ громкости
                        rms = np.sqrt(np.mean(y**2))
                        max_amplitude = np.max(np.abs(y))
                        logger.info(f"   🔊 RMS громкость: {rms:.6f}")
                        logger.info(f"   📢 Максимальная амплитуда: {max_amplitude:.6f}")
                        
                        if rms < 0.001:
                            logger.warning("⚠️ Очень низкая громкость - возможно тишина")
                        if max_amplitude < 0.01:
                            logger.warning("⚠️ Очень низкая амплитуда - возможно тишина")
                            
                    except Exception as e:
                        logger.error(f"Ошибка анализа librosa: {e}")
                
                elif HAS_SOUNDFILE:
                    # Анализ с soundfile
                    logger.info("   🔬 Используем soundfile...")
                    try:
                        data, samplerate = sf.read(temp_path)
                        logger.info(f"   📊 Частота дискретизации: {samplerate} Hz")
                        logger.info(f"   ⏱️  Длительность: {len(data)/samplerate:.2f} секунд")
                        logger.info(f"   📈 Количество сэмплов: {len(data)}")
                        logger.info(f"   🎛️  Каналы: {data.shape[1] if len(data.shape) > 1 else 1}")
                        
                    except Exception as e:
                        logger.error(f"Ошибка анализа soundfile: {e}")
                        
            finally:
                # Удаляем временный файл
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        else:
            logger.warning("📦 Библиотеки для анализа аудио не установлены")
            logger.info("   💡 Установите: pip install soundfile librosa")
        
        # 4. Рекомендации
        logger.info("💡 Рекомендации:")
        if detected_format == "Unknown":
            logger.info("   🔧 Файл имеет неизвестный формат - может быть поврежден")
        elif unique_bytes < 10:
            logger.info("   🔧 Файл содержит однообразные данные - проверьте источник записи")
        else:
            logger.info("   ✅ Файл выглядит как валидное аудио")
            
    except Exception as e:
        logger.error(f"💥 Ошибка анализа: {e}", exc_info=True)


def main():
    """Основная функция"""
    logger.info("🚀 Запуск анализа аудио...")
    
    analyze_audio_file()
    
    logger.info("🎉 Анализ завершен!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ Анализ завершился с ошибками: {e}")
        exit(1)
