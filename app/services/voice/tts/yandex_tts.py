"""
Yandex SpeechKit Text-to-Speech сервис
"""

import logging
from typing import Optional, AsyncGenerator
import json

import aiohttp

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, VoiceProcessingResult, TTSConfig
from app.services.voice.base import TTSServiceBase, VoiceServiceError, VoiceServiceTimeout


class YandexTTSService(TTSServiceBase):
    """
    Сервис Text-to-Speech на основе Yandex SpeechKit API
    """

    def __init__(self, config: TTSConfig, logger: Optional[logging.Logger] = None):
        super().__init__(VoiceProvider.YANDEX, config, logger)
        self.api_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
        self.folder_id = settings.YANDEX_FOLDER_ID
        self.tts_url = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self) -> None:
        """Инициализация Yandex SpeechKit клиента"""
        try:
            if not self.api_key:
                raise VoiceServiceError(
                    "Yandex API key не настроен (YANDEX_API_KEY)",
                    provider=self.provider
                )

            if not self.folder_id:
                raise VoiceServiceError(
                    "Yandex Folder ID не настроен (YANDEX_FOLDER_ID)",
                    provider=self.provider
                )

            # Создаем HTTP сессию
            timeout = aiohttp.ClientTimeout(total=60)
            self.session = aiohttp.ClientSession(timeout=timeout)
            
            # Проверяем доступность API
            await self.health_check()
            self._initialized = True
            self.logger.info("Yandex TTS service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Yandex TTS service: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка инициализации Yandex TTS: {str(e)}",
                provider=self.provider
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.session:
            await self.session.close()
        self._initialized = False
        self.logger.info("Yandex TTS service cleaned up")

    async def health_check(self) -> bool:
        """Проверка доступности Yandex SpeechKit API"""
        if not self.session:
            return False

        try:
            # Проверяем доступность API через минимальный запрос
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
            }
            
            data = {
                "text": "test",
                "lang": "ru-RU",
                "voice": "jane",
            }
            
            async with self.session.post(
                self.tts_url,
                headers=headers,
                data=data
            ) as response:
                # Даже если запрос не удался, проверяем, что это не ошибка авторизации
                return response.status != 401
                
        except Exception as e:
            self.logger.warning(f"Yandex TTS health check failed: {e}")
            return False

    async def synthesize_speech(self, 
                               text: str,
                               **kwargs) -> VoiceProcessingResult:
        """
        Преобразование текста в аудио через Yandex SpeechKit
        
        Args:
            text: Текст для синтеза
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат с аудиоданными
        """
        if not self._initialized or not self.session:
            raise VoiceServiceError(
                "Yandex TTS service не инициализирован",
                provider=self.provider
            )

        import time
        start_time = time.time()
        self.logger.info("Starting Yandex SpeechKit TTS synthesis")

        try:
            # Валидация длины текста (Yandex лимит 5000 символов)
            if len(text) > 5000:
                raise VoiceServiceError(
                    f"Текст слишком длинный: {len(text)} символов (макс. 5000)",
                    provider=self.provider
                )

            # Подготовка заголовков
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
            }

            # Подготовка данных запроса
            data = {
                "text": text,
                "lang": self.config.language or "ru-RU",
                "voice": self.config.voice or "jane",
                "speed": getattr(self.config, 'speed', 1.0),
                "format": self._get_yandex_format(),
                # Убираем sampleRateHertz, так как Yandex не поддерживает 22050
            }

            # Дополнительные параметры из конфигурации
            if hasattr(self.config, 'custom_params') and self.config.custom_params:
                data.update(self.config.custom_params)

            # Переопределяем параметрами из вызова
            data.update(kwargs)

            self.logger.debug(f"TTS request data: {data}")

            # Выполняем запрос к API
            timeout_seconds = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30)
            
            async with self.session.post(
                self.tts_url,
                headers=headers,
                data=data
            ) as response:
                
                self.logger.debug(f"TTS response status: {response.status}")
                
                if response.status != 200:
                    error_text = await response.text()
                    self.logger.error(f"Yandex TTS API error {response.status}: {error_text}")
                    raise VoiceServiceError(
                        f"Yandex TTS API error {response.status}: {error_text}",
                        provider=self.provider
                    )

                # Получаем аудиоданные
                audio_data = await response.read()
                self.logger.debug(f"Received audio data: {len(audio_data)} bytes")

            processing_time = time.time() - start_time
            
            return VoiceProcessingResult(
                success=True,
                provider_used=self.provider,
                processing_time=processing_time,
                audio_data=audio_data,  # Возвращаем аудиоданные в правильном поле
                metadata={
                    "voice": data["voice"],
                    "language": data["lang"],
                    "format": data["format"],
                    "speed": data["speed"],
                    "text_length": len(text),
                    "audio_size": len(audio_data)
                }
            )

        except VoiceServiceError:
            # Передаем VoiceServiceError как есть
            raise
        except Exception as e:
            self.logger.error(f"Yandex TTS synthesis failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка синтеза речи Yandex: {str(e)}",
                provider=self.provider
            )

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
        if not self._initialized or not self.session:
            raise VoiceServiceError(
                "Yandex TTS service не инициализирован",
                provider=self.provider
            )

        try:
            # Yandex SpeechKit не поддерживает нативный streaming,
            # поэтому разбиваем текст на части и синтезируем их
            
            # Разбиваем текст на предложения
            sentences = self._split_text_into_sentences(text)
            
            for sentence in sentences:
                if sentence.strip():
                    result = await self.synthesize_speech(sentence, **kwargs)
                    if result.audio_data:
                        yield result.audio_data

        except Exception as e:
            self.logger.error(f"Yandex streaming TTS failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка потокового синтеза Yandex: {str(e)}",
                provider=self.provider
            )

    def _get_yandex_format(self) -> str:
        """Определить формат для Yandex SpeechKit на основе конфигурации"""
        if not hasattr(self.config, 'audio_format'):
            return "wav"
            
        format_mapping = {
            'wav': 'wav',
            'mp3': 'mp3',
            'opus': 'opus',
            'ogg': 'opus',
        }
        
        format_value = getattr(self.config, 'audio_format', 'wav')
        if hasattr(format_value, 'value'):
            format_value = format_value.value
            
        return format_mapping.get(format_value, 'wav')

    def _split_text_into_sentences(self, text: str, max_length: int = 1000) -> list[str]:
        """Разбить текст на предложения с ограничением длины для Yandex"""
        import re
        
        # Разбиваем по предложениям
        sentences = re.split(r'[.!?]+', text)
        
        result = []
        current_chunk = ""
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Если добавление предложения не превысит лимит
            if len(current_chunk) + len(sentence) + 1 <= max_length:
                if current_chunk:
                    current_chunk += ". " + sentence
                else:
                    current_chunk = sentence
            else:
                # Сохраняем текущий блок и начинаем новый
                if current_chunk:
                    result.append(current_chunk)
                current_chunk = sentence
        
        # Добавляем последний блок
        if current_chunk:
            result.append(current_chunk)
            
        return result

    async def get_available_voices(self, language_code: Optional[str] = None) -> list[dict]:
        """Получить список доступных голосов Yandex"""
        # Yandex SpeechKit имеет фиксированный набор голосов
        voices = [
            {
                "name": "jane",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "oksana",
                "language_codes": ["ru-RU"],
                "gender": "female", 
                "description": "Женский голос"
            },
            {
                "name": "alyss",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "omazh",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "zahar",
                "language_codes": ["ru-RU"],
                "gender": "male",
                "description": "Мужской голос"
            },
            {
                "name": "ermil",
                "language_codes": ["ru-RU"],
                "gender": "male",
                "description": "Мужской голос"
            },
            {
                "name": "madirus",
                "language_codes": ["ru-RU"],
                "gender": "male",
                "description": "Мужской голос"
            },
            {
                "name": "dasha",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Детский женский голос"
            },
            {
                "name": "julia",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "lera",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "masha",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "marina",
                "language_codes": ["ru-RU"],
                "gender": "female",
                "description": "Женский голос"
            },
            {
                "name": "alexander",
                "language_codes": ["ru-RU"],
                "gender": "male",
                "description": "Мужской голос"
            },
            {
                "name": "kirill",
                "language_codes": ["ru-RU"],
                "gender": "male",
                "description": "Мужской голос"
            },
            {
                "name": "anton",
                "language_codes": ["ru-RU"],
                "gender": "male",
                "description": "Мужской голос"
            }
        ]
        
        # Фильтруем по языку если указан
        if language_code:
            voices = [v for v in voices if language_code in v["language_codes"]]
            
        return voices

    def _validate_speed(self, speed: float) -> bool:
        """Валидация скорости речи для Yandex"""
        return 0.1 <= speed <= 3.0
    
    def _validate_language(self, language: str) -> bool:
        """Валидация языка для Yandex"""
        supported_languages = ["ru-RU", "en-US", "tr-TR"]
        return language in supported_languages
    
    def _get_headers(self) -> dict[str, str]:
        """Получение заголовков для API запросов"""
        return {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-folder-id": self.folder_id
        }
    
    def _build_synthesis_request(self, text: str, voice: str, speed: float, format: str) -> dict[str, str]:
        """Построение запроса для синтеза речи"""
        return {
            "text": text,
            "voice": voice,
            "speed": speed,
            "format": format,
            "lang": self.config.language,
        }
