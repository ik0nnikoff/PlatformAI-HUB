"""
Модуль обработки аудио для voice_v2.

Реализует высокопроизводительные утилиты для работы с аудиофайлами:
- Определение формата и валидация
- Конвертация между форматами  
- Извлечение метаданных
- Оптимизация для провайдеров

Архитектура основана на принципах SOLID и performance-first подходе.
Все операции асинхронные для максимальной производительности.
"""

import asyncio
import io
import logging
import hashlib
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any
from dataclasses import dataclass

# Импорты для аудио обработки
try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    
from app.api.schemas.voice_schemas import AudioFormat


# ==================== КОНСТАНТЫ И КОНФИГУРАЦИЯ ====================

class AudioLimits:
    """Константы лимитов для аудиофайлов."""
    MAX_FILE_SIZE_MB = 25
    MAX_DURATION_SECONDS = 600  # 10 минут
    MIN_DURATION_SECONDS = 0.1
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1


class AudioMimeTypes:
    """MIME типы для аудиоформатов."""
    MAP = {
        AudioFormat.MP3: "audio/mpeg",
        AudioFormat.WAV: "audio/wav", 
        AudioFormat.OGG: "audio/ogg",
        AudioFormat.OPUS: "audio/opus",
        AudioFormat.FLAC: "audio/flac",
        AudioFormat.AAC: "audio/aac",
        AudioFormat.PCM: "audio/pcm"
    }


# ==================== ТИПЫ ДАННЫХ ====================

@dataclass
class AudioMetadata:
    """Метаданные аудиофайла."""
    format: AudioFormat
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bitrate: Optional[int] = None
    size_bytes: Optional[int] = None
    is_valid: bool = True
    error_message: Optional[str] = None


@dataclass
class ConversionResult:
    """Результат конвертации аудио."""
    success: bool
    audio_data: Optional[bytes] = None
    output_format: Optional[AudioFormat] = None
    original_size: Optional[int] = None
    converted_size: Optional[int] = None
    conversion_time_ms: Optional[float] = None
    error_message: Optional[str] = None


# ==================== ОСНОВНОЙ ПРОЦЕССОР ====================

class AudioProcessor:
    """
    Высокопроизводительный процессор аудиофайлов.
    
    Реализует Single Responsibility Principle:
    - Только обработка аудио
    - Без бизнес-логики
    - Без зависимостей от внешних сервисов
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Инициализация процессора.
        
        Args:
            logger: Логгер для отладки (опционально)
        """
        self.logger = logger or logging.getLogger(__name__)
        self._validate_dependencies()
    
    def _validate_dependencies(self) -> None:
        """Валидация зависимостей при инициализации."""
        if not PYDUB_AVAILABLE:
            self.logger.warning(
                "pydub не доступен. Некоторые функции конвертации будут недоступны."
            )
    
    # ==================== ОПРЕДЕЛЕНИЕ ФОРМАТА ====================
    
    @staticmethod
    def detect_format(
        audio_data: bytes, 
        filename: Optional[str] = None
    ) -> AudioFormat:
        """
        Определение формата аудио по magic numbers и расширению.
        
        Performance: Синхронная операция, < 1ms для обычных файлов.
        
        Args:
            audio_data: Бинарные данные аудио
            filename: Имя файла для fallback (опционально)
            
        Returns:
            AudioFormat: Определенный формат
            
        Raises:
            ValueError: Если формат не поддерживается
        """
        if not audio_data:
            raise ValueError("Пустые аудиоданные")
        
        # Определение по magic numbers (более надежно)
        if len(audio_data) >= 12:
            # MP3: ID3 tag или MPEG frame header
            if (audio_data.startswith(b'ID3') or 
                audio_data.startswith(b'\xff\xfb') or
                audio_data.startswith(b'\xff\xf3') or
                audio_data.startswith(b'\xff\xf2')):
                return AudioFormat.MP3
            
            # FLAC: fLaC (должен быть перед WAV)
            if audio_data.startswith(b'fLaC'):
                return AudioFormat.FLAC
            
            # OPUS: OpusHead в OGG контейнере (должен быть перед OGG)
            if (audio_data.startswith(b'OggS') and b'OpusHead' in audio_data[:32]):
                return AudioFormat.OPUS
            
            # WAV: RIFF + WAVE
            if (audio_data.startswith(b'RIFF') and 
                len(audio_data) >= 12 and audio_data[8:12] == b'WAVE'):
                return AudioFormat.WAV
            
            # OGG: OggS (общий случай)
            if audio_data.startswith(b'OggS'):
                return AudioFormat.OGG
            
            # AAC: может иметь различные заголовки
            if (audio_data.startswith(b'\xff\xf1') or
                audio_data.startswith(b'\xff\xf9')):
                return AudioFormat.AAC
        
        # Fallback по расширению файла
        if filename:
            return AudioProcessor._detect_format_by_extension(filename)
        
        # По умолчанию считаем WAV (наиболее универсальный)
        return AudioFormat.WAV
    
    @staticmethod
    def _detect_format_by_extension(filename: str) -> AudioFormat:
        """Определение формата по расширению файла."""
        ext = Path(filename).suffix.lower()
        
        extension_map = {
            '.mp3': AudioFormat.MP3,
            '.wav': AudioFormat.WAV,
            '.wave': AudioFormat.WAV,
            '.ogg': AudioFormat.OGG,
            '.opus': AudioFormat.OPUS,
            '.flac': AudioFormat.FLAC,
            '.aac': AudioFormat.AAC,
            '.m4a': AudioFormat.AAC,
            '.pcm': AudioFormat.PCM
        }
        
        return extension_map.get(ext, AudioFormat.WAV)
    
    # ==================== ВАЛИДАЦИЯ ====================
    
    def validate_audio(
        self, 
        audio_data: bytes, 
        max_size_mb: Optional[int] = None,
        max_duration_seconds: Optional[int] = None
    ) -> AudioMetadata:
        """
        Комплексная валидация аудиофайла.
        
        Args:
            audio_data: Бинарные данные аудио
            max_size_mb: Максимальный размер в МБ
            max_duration_seconds: Максимальная длительность в секундах
            
        Returns:
            AudioMetadata: Результат валидации с метаданными
        """
        try:
            # Базовые проверки
            if not audio_data:
                return AudioMetadata(
                    format=AudioFormat.WAV,
                    is_valid=False,
                    error_message="Пустые аудиоданные"
                )
            
            # Определение формата
            audio_format = self.detect_format(audio_data)
            
            # Проверка размера
            size_bytes = len(audio_data)
            max_size = max_size_mb or AudioLimits.MAX_FILE_SIZE_MB
            if size_bytes > max_size * 1024 * 1024:
                return AudioMetadata(
                    format=audio_format,
                    size_bytes=size_bytes,
                    is_valid=False,
                    error_message=f"Файл слишком большой: {size_bytes / (1024*1024):.1f}MB > {max_size}MB"
                )
            
            # Извлечение дополнительных метаданных
            metadata = AudioMetadata(
                format=audio_format,
                size_bytes=size_bytes,
                is_valid=True
            )
            
            # Если доступен pydub, получаем детальные метаданные
            if PYDUB_AVAILABLE:
                try:
                    audio_segment = AudioSegment.from_file(io.BytesIO(audio_data))
                    duration_seconds = len(audio_segment) / 1000.0
                    
                    metadata.duration_seconds = duration_seconds
                    metadata.sample_rate = audio_segment.frame_rate
                    metadata.channels = audio_segment.channels
                    
                    # Проверка длительности
                    max_duration = max_duration_seconds or AudioLimits.MAX_DURATION_SECONDS
                    if duration_seconds > max_duration:
                        metadata.is_valid = False
                        metadata.error_message = f"Аудио слишком длинное: {duration_seconds:.1f}s > {max_duration}s"
                    elif duration_seconds < AudioLimits.MIN_DURATION_SECONDS:
                        metadata.is_valid = False
                        metadata.error_message = f"Аудио слишком короткое: {duration_seconds:.1f}s < {AudioLimits.MIN_DURATION_SECONDS}s"
                        
                except Exception as e:
                    self.logger.warning(f"Не удалось извлечь метаданные с pydub: {e}")
            
            return metadata
            
        except Exception as e:
            self.logger.error(f"Ошибка валидации аудио: {e}")
            return AudioMetadata(
                format=AudioFormat.WAV,
                is_valid=False,
                error_message=f"Ошибка валидации: {str(e)}"
            )
    
    # ==================== КОНВЕРТАЦИЯ ====================
    
    async def convert_audio(
        self,
        audio_data: bytes,
        target_format: AudioFormat,
        source_format: Optional[AudioFormat] = None,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None
    ) -> ConversionResult:
        """
        Асинхронная конвертация аудио между форматами.
        
        Performance target: < 2s для файлов до 10MB.
        
        Args:
            audio_data: Исходные аудиоданные
            target_format: Целевой формат
            source_format: Исходный формат (автоопределение если None)
            sample_rate: Целевая частота дискретизации
            channels: Количество каналов
            
        Returns:
            ConversionResult: Результат конвертации
        """
        if not PYDUB_AVAILABLE:
            return ConversionResult(
                success=False,
                error_message="pydub недоступен для конвертации"
            )
        
        start_time = asyncio.get_event_loop().time()
        original_size = len(audio_data)
        
        try:
            # Определение исходного формата
            if not source_format:
                source_format = self.detect_format(audio_data)
            
            # Если форматы одинаковые и параметры не заданы, возвращаем исходные данные
            if (source_format == target_format and 
                not sample_rate and not channels):
                return ConversionResult(
                    success=True,
                    audio_data=audio_data,
                    output_format=target_format,
                    original_size=original_size,
                    converted_size=original_size,
                    conversion_time_ms=0
                )
            
            # Выполнение конвертации в executor для неблокирующей операции
            loop = asyncio.get_event_loop()
            converted_data = await loop.run_in_executor(
                None, 
                self._perform_conversion,
                audio_data, source_format, target_format, sample_rate, channels
            )
            
            end_time = asyncio.get_event_loop().time()
            conversion_time_ms = (end_time - start_time) * 1000
            
            return ConversionResult(
                success=True,
                audio_data=converted_data,
                output_format=target_format,
                original_size=original_size,
                converted_size=len(converted_data),
                conversion_time_ms=conversion_time_ms
            )
            
        except Exception as e:
            self.logger.error(f"Ошибка конвертации аудио: {e}")
            end_time = asyncio.get_event_loop().time()
            conversion_time_ms = (end_time - start_time) * 1000
            
            return ConversionResult(
                success=False,
                conversion_time_ms=conversion_time_ms,
                error_message=f"Ошибка конвертации: {str(e)}"
            )
    
    def _perform_conversion(
        self,
        audio_data: bytes,
        source_format: AudioFormat,
        target_format: AudioFormat,
        sample_rate: Optional[int],
        channels: Optional[int]
    ) -> bytes:
        """
        Синхронная конвертация аудио (выполняется в executor).
        
        Args:
            audio_data: Исходные данные
            source_format: Исходный формат
            target_format: Целевой формат
            sample_rate: Частота дискретизации
            channels: Количество каналов
            
        Returns:
            bytes: Сконвертированные аудиоданные
            
        Raises:
            Exception: При ошибке конвертации
        """
        # Загрузка аудио с указанием формата
        audio_segment = AudioSegment.from_file(
            io.BytesIO(audio_data),
            format=source_format.value
        )
        
        # Применение параметров конвертации
        if sample_rate:
            audio_segment = audio_segment.set_frame_rate(sample_rate)
        
        if channels:
            if channels == 1:
                audio_segment = audio_segment.set_channels(1)
            elif channels == 2:
                audio_segment = audio_segment.set_channels(2)
        
        # Экспорт в целевой формат
        output_buffer = io.BytesIO()
        
        # Параметры экспорта для разных форматов
        export_params = self._get_export_parameters(target_format)
        
        audio_segment.export(
            output_buffer,
            format=target_format.value,
            **export_params
        )
        
        return output_buffer.getvalue()
    
    def _get_export_parameters(self, target_format: AudioFormat) -> Dict[str, Any]:
        """Получение оптимальных параметров экспорта для формата."""
        params = {}
        
        if target_format == AudioFormat.MP3:
            params.update({
                'bitrate': '128k',
                'parameters': ['-q:a', '2']  # Хорошее качество
            })
        elif target_format == AudioFormat.WAV:
            params.update({
                'parameters': ['-acodec', 'pcm_s16le']  # 16-bit PCM
            })
        elif target_format == AudioFormat.OGG:
            params.update({
                'codec': 'libvorbis',
                'parameters': ['-q:a', '5']  # Хорошее качество
            })
        elif target_format == AudioFormat.FLAC:
            params.update({
                'parameters': ['-compression_level', '5']  # Баланс скорость/размер
            })
        
        return params
    
    # ==================== УТИЛИТАРНЫЕ МЕТОДЫ ====================
    
    @staticmethod
    def calculate_audio_hash(audio_data: bytes) -> str:
        """
        Вычисление хеша аудиоданных для кэширования.
        
        Args:
            audio_data: Бинарные данные аудио
            
        Returns:
            str: MD5 хеш данных
        """
        return hashlib.md5(audio_data).hexdigest()
    
    @staticmethod
    def get_mime_type(audio_format: AudioFormat) -> str:
        """
        Получение MIME типа для аудиоформата.
        
        Args:
            audio_format: Формат аудио
            
        Returns:
            str: MIME тип
        """
        return AudioMimeTypes.MAP.get(audio_format, "audio/wav")
    
    @staticmethod
    def is_format_supported(audio_format: AudioFormat) -> bool:
        """
        Проверка поддержки формата.
        
        Args:
            audio_format: Формат для проверки
            
        Returns:
            bool: True если формат поддерживается
        """
        return audio_format in AudioMimeTypes.MAP
