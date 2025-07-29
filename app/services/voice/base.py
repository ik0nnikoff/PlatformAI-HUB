"""
Базовые классы и миксины для голосовых сервисов
"""

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union, AsyncGenerator
from pathlib import Path

from app.api.schemas.voice_schemas import (
    VoiceProvider, VoiceProcessingResult, VoiceFileInfo,
    STTConfig, TTSConfig, AudioFormat
)


class VoiceServiceError(Exception):
    """Базовое исключение для голосовых сервисов"""
    
    def __init__(self, message: str, provider: Optional[VoiceProvider] = None, 
                 error_code: Optional[str] = None):
        self.message = message
        self.provider = provider
        self.error_code = error_code
        super().__init__(self.message)


class VoiceServiceTimeout(VoiceServiceError):
    """Исключение для таймаутов голосовых сервисов"""
    pass


class VoiceServiceQuotaExceeded(VoiceServiceError):
    """Исключение для превышения квот"""
    pass


class VoiceServiceInvalidInput(VoiceServiceError):
    """Исключение для некорректного ввода"""
    pass


class VoiceServiceBase(ABC):
    """
    Базовый абстрактный класс для всех голосовых сервисов.
    Определяет общий интерфейс для STT и TTS провайдеров.
    """
    
    def __init__(self, 
                 provider: VoiceProvider,
                 logger: Optional[logging.Logger] = None):
        self.provider = provider
        self.logger = logger or logging.getLogger(f"voice_service.{provider.value}")
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> None:
        """Инициализация сервиса (подключение к API, проверка ключей и т.д.)"""
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """Очистка ресурсов сервиса"""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Проверка доступности сервиса"""
        pass

    def is_initialized(self) -> bool:
        """Проверить, инициализирован ли сервис"""
        return self._initialized

    def _generate_cache_key(self, *args) -> str:
        """Генерация ключа для кэширования"""
        content = "|".join(str(arg) for arg in args)
        return hashlib.sha256(content.encode()).hexdigest()

    async def _with_timeout(self, coro, timeout_seconds: float):
        """Выполнить корутину с таймаутом"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise VoiceServiceTimeout(
                f"Операция превысила таймаут {timeout_seconds}с",
                provider=self.provider
            )


class STTServiceBase(VoiceServiceBase):
    """
    Базовый класс для сервисов Speech-to-Text
    """

    def __init__(self, 
                 provider: VoiceProvider,
                 config: STTConfig,
                 logger: Optional[logging.Logger] = None):
        super().__init__(provider, logger)
        self.config = config

    @abstractmethod
    async def transcribe_audio(self, 
                              audio_data: bytes,
                              file_info: VoiceFileInfo,
                              **kwargs) -> VoiceProcessingResult:
        """
        Преобразование аудио в текст
        
        Args:
            audio_data: Бинарные данные аудиофайла
            file_info: Информация о файле
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        pass

    @abstractmethod
    async def transcribe_stream(self, 
                               audio_stream: AsyncGenerator[bytes, None],
                               **kwargs) -> AsyncGenerator[str, None]:
        """
        Потоковое преобразование аудио в текст
        
        Args:
            audio_stream: Асинхронный генератор аудиоданных
            **kwargs: Дополнительные параметры
            
        Yields:
            str: Частичные результаты распознавания
        """
        pass

    def validate_audio_format(self, file_info: VoiceFileInfo) -> bool:
        """Проверить поддерживаемость формата аудио"""
        return True  # По умолчанию поддерживаем все форматы

    def validate_audio_duration(self, file_info: VoiceFileInfo) -> bool:
        """Проверить длительность аудио"""
        if file_info.duration_seconds is None:
            return True
        return file_info.duration_seconds <= self.config.max_duration


class TTSServiceBase(VoiceServiceBase):
    """
    Базовый класс для сервисов Text-to-Speech
    """

    def __init__(self, 
                 provider: VoiceProvider,
                 config: TTSConfig,
                 logger: Optional[logging.Logger] = None):
        super().__init__(provider, logger)
        self.config = config

    @abstractmethod
    async def synthesize_speech(self, 
                               text: str,
                               **kwargs) -> VoiceProcessingResult:
        """
        Преобразование текста в аудио
        
        Args:
            text: Текст для синтеза
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат с аудиоданными
        """
        pass

    @abstractmethod
    async def synthesize_stream(self, 
                               text: str,
                               **kwargs) -> AsyncGenerator[bytes, None]:
        """
        Потоковый синтез речи
        
        Args:
            text: Текст для синтеза
            **kwargs: Дополнительные параметры
            
        Yields:
            bytes: Части аудиоданных
        """
        pass

    def validate_text_length(self, text: str) -> bool:
        """Проверить длину текста"""
        # Большинство сервисов имеют лимиты на длину текста
        return len(text) <= 5000  # По умолчанию 5000 символов

    def estimate_audio_duration(self, text: str) -> float:
        """Оценить длительность аудио по тексту"""
        # Примерная оценка: 150 слов в минуту, средняя длина слова 5 символов
        words = len(text) / 5
        return (words / 150) * 60


class VoiceConfigMixin:
    """
    Миксин для работы с голосовыми настройками из JSON конфигурации агента
    """

    def get_voice_settings_from_config(self, agent_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Извлечь голосовые настройки из конфигурации агента
        
        Args:
            agent_config: Конфигурация агента в формате JSON
            
        Returns:
            Dict с голосовыми настройками или None
        """
        return agent_config.get("voice_settings")

    def update_voice_settings_in_config(self, 
                                       agent_config: Dict[str, Any], 
                                       voice_settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обновить голосовые настройки в конфигурации агента
        
        Args:
            agent_config: Конфигурация агента
            voice_settings: Новые голосовые настройки
            
        Returns:
            Обновленная конфигурация
        """
        updated_config = agent_config.copy()
        updated_config["voice_settings"] = voice_settings
        return updated_config

    def validate_voice_config_structure(self, voice_config: Dict[str, Any]) -> bool:
        """
        Проверить структуру голосовой конфигурации
        
        Args:
            voice_config: Голосовая конфигурация
            
        Returns:
            True если структура корректна
        """
        try:
            from app.api.schemas.voice_schemas import VoiceSettings
            VoiceSettings(**voice_config)
            return True
        except Exception as e:
            self.logger.error(f"Ошибка валидации голосовой конфигурации: {e}")
            return False


class AudioFileProcessor:
    """
    Утилиты для обработки аудиофайлов
    """

    @staticmethod
    def detect_audio_format(file_data: bytes, filename: str = "") -> Optional[AudioFormat]:
        """
        Определить формат аудиофайла по заголовку
        
        Args:
            file_data: Бинарные данные файла
            filename: Имя файла (опционально)
            
        Returns:
            AudioFormat или None
        """
        if not file_data:
            return None

        # Проверка по magic numbers
        if file_data.startswith(b'ID3') or file_data[6:10] == b'ftyp':
            return AudioFormat.MP3
        elif file_data.startswith(b'OggS'):
            return AudioFormat.OGG
        elif file_data.startswith(b'RIFF') and file_data[8:12] == b'WAVE':
            return AudioFormat.WAV
        elif file_data.startswith(b'fLaC'):
            return AudioFormat.FLAC
        elif file_data.startswith(b'OpusHead'):
            return AudioFormat.OPUS

        # Fallback по расширению файла
        if filename:
            ext = Path(filename).suffix.lower()
            format_map = {
                '.mp3': AudioFormat.MP3,
                '.wav': AudioFormat.WAV,
                '.ogg': AudioFormat.OGG,
                '.opus': AudioFormat.OPUS,
                '.flac': AudioFormat.FLAC,
                '.aac': AudioFormat.AAC,
                '.m4a': AudioFormat.AAC,
            }
            return format_map.get(ext)

        return None

    @staticmethod
    def validate_audio_file_size(file_data: bytes, max_size_mb: int) -> bool:
        """
        Проверить размер аудиофайла
        
        Args:
            file_data: Бинарные данные файла
            max_size_mb: Максимальный размер в МБ
            
        Returns:
            True если размер допустим
        """
        file_size_mb = len(file_data) / (1024 * 1024)
        return file_size_mb <= max_size_mb

    @staticmethod
    async def get_audio_duration(file_data: bytes) -> Optional[float]:
        """
        Получить длительность аудиофайла
        
        Args:
            file_data: Бинарные данные файла
            
        Returns:
            Длительность в секундах или None
        """
        try:
            # Попытка использовать pydub для получения длительности
            from pydub import AudioSegment
            import io
            
            audio = AudioSegment.from_file(io.BytesIO(file_data))
            return len(audio) / 1000.0  # Конвертация из миллисекунд
        except Exception:
            # Если pydub недоступен или файл не поддерживается
            return None


class RateLimiter:
    """
    Простой rate limiter для голосовых сервисов
    """

    def __init__(self, max_requests: int, time_window: int = 60):
        """
        Args:
            max_requests: Максимальное количество запросов
            time_window: Временное окно в секундах
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []

    async def acquire(self, user_id: str) -> bool:
        """
        Попытаться получить разрешение на выполнение запроса
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            True если запрос разрешен
        """
        now = time.time()
        
        # Очистить старые запросы
        self.requests = [req_time for req_time in self.requests 
                        if now - req_time < self.time_window]
        
        # Проверить лимит
        if len(self.requests) >= self.max_requests:
            return False
        
        # Добавить текущий запрос
        self.requests.append(now)
        return True

    async def is_allowed(self, user_id: str) -> bool:
        """
        Проверить, разрешен ли запрос (алиас для acquire)
        
        Args:
            user_id: Идентификатор пользователя
            
        Returns:
            True если запрос разрешен
        """
        return await self.acquire(user_id)

    def get_remaining_requests(self) -> int:
        """Получить количество оставшихся запросов"""
        now = time.time()
        self.requests = [req_time for req_time in self.requests 
                        if now - req_time < self.time_window]
        return max(0, self.max_requests - len(self.requests))

    def get_reset_time(self) -> float:
        """Получить время до сброса лимита"""
        if not self.requests:
            return 0.0
        oldest_request = min(self.requests)
        return max(0.0, self.time_window - (time.time() - oldest_request))
