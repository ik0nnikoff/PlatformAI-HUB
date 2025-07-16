"""
Google Cloud Text-to-Speech сервис
"""

import logging
from typing import Optional, AsyncGenerator
import io

from google.cloud import texttospeech_v1
from google.cloud.texttospeech_v1 import types

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, VoiceProcessingResult, TTSConfig, AudioFormat
from app.services.voice.base import TTSServiceBase, VoiceServiceError, VoiceServiceTimeout


class GoogleTTSService(TTSServiceBase):
    """
    Сервис Text-to-Speech на основе Google Cloud Text-to-Speech API
    """

    def __init__(self, config: TTSConfig, logger: Optional[logging.Logger] = None):
        super().__init__(VoiceProvider.GOOGLE, config, logger)
        self.client: Optional[texttospeech_v1.TextToSpeechAsyncClient] = None
        self.credentials_path = settings.GOOGLE_APPLICATION_CREDENTIALS

    async def initialize(self) -> None:
        """Инициализация Google Cloud Text-to-Speech клиента"""
        try:
            if not self.credentials_path:
                raise VoiceServiceError(
                    "Google Cloud credentials не настроены (GOOGLE_APPLICATION_CREDENTIALS)",
                    provider=self.provider
                )

            # Инициализируем асинхронный клиент
            self.client = texttospeech_v1.TextToSpeechAsyncClient()
            
            # Проверяем доступность API
            await self.health_check()
            self._initialized = True
            self.logger.info("Google TTS service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Google TTS service: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка инициализации Google TTS: {str(e)}",
                provider=self.provider
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.client:
            # Google client не требует explicit close
            pass
        self._initialized = False
        self.logger.info("Google TTS service cleaned up")

    async def health_check(self) -> bool:
        """Проверка доступности Google Cloud Text-to-Speech API"""
        if not self.client:
            return False

        try:
            # Простая проверка доступности API через список голосов
            voices = await self.client.list_voices()
            return len(voices.voices) > 0
        except Exception as e:
            self.logger.warning(f"Google TTS health check failed: {e}")
            return False

    async def synthesize_speech(self, 
                               text: str,
                               **kwargs) -> VoiceProcessingResult:
        """
        Преобразование текста в аудио через Google Cloud Text-to-Speech
        
        Args:
            text: Текст для синтеза
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат с аудиоданными
        """
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "Google TTS service не инициализирован",
                provider=self.provider
            )

        start_time = time.time()
        self.logger.info("Starting Google Cloud TTS synthesis")

        try:
            # Валидация длины текста
            if not self.validate_text_length(text):
                raise VoiceServiceError(
                    f"Текст слишком длинный: {len(text)} символов (макс. 5000)",
                    provider=self.provider
                )

            # Подготовка входного текста
            synthesis_input = types.SynthesisInput(text=text)

            # Настройка голоса
            voice = types.VoiceSelectionParams(
                language_code=self.config.language or "ru-RU",
                name=self.config.voice or "ru-RU-Wavenet-A",
                ssml_gender=types.SsmlVoiceGender.NEUTRAL
            )

            # Настройка аудио конфигурации
            audio_format = self._get_audio_encoding()
            audio_config = types.AudioConfig(
                audio_encoding=audio_format,
                speaking_rate=getattr(self.config, 'speed', 1.0),
                pitch=getattr(self.config, 'pitch', 0.0),
                volume_gain_db=getattr(self.config, 'volume_gain_db', 0.0),
                sample_rate_hertz=getattr(self.config, 'sample_rate', 22050)
            )

            # Дополнительные параметры из конфигурации
            if hasattr(self.config, 'custom_params') and self.config.custom_params:
                for key, value in self.config.custom_params.items():
                    if hasattr(voice, key):
                        setattr(voice, key, value)
                    elif hasattr(audio_config, key):
                        setattr(audio_config, key, value)

            # Переопределяем параметрами из вызова
            for key, value in kwargs.items():
                if hasattr(voice, key):
                    setattr(voice, key, value)
                elif hasattr(audio_config, key):
                    setattr(audio_config, key, value)

            # Выполняем синтез с таймаутом
            timeout_seconds = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30)
            
            response = await self._with_timeout(
                self.client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config
                ),
                timeout_seconds
            )

            # Получаем аудиоданные
            audio_data = response.audio_content

            processing_time = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30) - timeout_seconds
            
            return VoiceProcessingResult(
                audio_data=audio_data,
                processing_time_ms=int(processing_time * 1000),
                provider=self.provider,
                metadata={
                    "voice_name": voice.name,
                    "language_code": voice.language_code,
                    "audio_encoding": audio_config.audio_encoding.name,
                    "sample_rate": audio_config.sample_rate_hertz,
                    "speaking_rate": audio_config.speaking_rate,
                    "text_length": len(text)
                }
            )

        except Exception as e:
            self.logger.error(f"Google TTS synthesis failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка синтеза речи Google: {str(e)}",
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
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "Google TTS service не инициализирован",
                provider=self.provider
            )

        try:
            # Google Cloud TTS не поддерживает нативный streaming,
            # поэтому разбиваем текст на части и синтезируем их
            
            # Разбиваем текст на предложения
            sentences = self._split_text_into_sentences(text)
            
            for sentence in sentences:
                if sentence.strip():
                    result = await self.synthesize_speech(sentence, **kwargs)
                    if result.audio_data:
                        yield result.audio_data

        except Exception as e:
            self.logger.error(f"Google streaming TTS failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка потокового синтеза Google: {str(e)}",
                provider=self.provider
            )

    def _get_audio_encoding(self) -> types.AudioEncoding:
        """Определить encoding для Google Cloud TTS на основе конфигурации"""
        if not hasattr(self.config, 'audio_format'):
            return types.AudioEncoding.MP3
            
        format_mapping = {
            'mp3': types.AudioEncoding.MP3,
            'wav': types.AudioEncoding.LINEAR16,
            'ogg': types.AudioEncoding.OGG_OPUS,
            'flac': types.AudioEncoding.FLAC,
        }
        
        format_value = getattr(self.config, 'audio_format', 'mp3')
        if hasattr(format_value, 'value'):
            format_value = format_value.value
            
        return format_mapping.get(format_value, types.AudioEncoding.MP3)

    def _split_text_into_sentences(self, text: str, max_length: int = 500) -> list[str]:
        """Разбить текст на предложения с ограничением длины"""
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
        """Получить список доступных голосов"""
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "Google TTS service не инициализирован",
                provider=self.provider
            )

        try:
            response = await self.client.list_voices(
                language_code=language_code
            )
            
            voices = []
            for voice in response.voices:
                voices.append({
                    "name": voice.name,
                    "language_codes": list(voice.language_codes),
                    "ssml_gender": voice.ssml_gender.name,
                    "natural_sample_rate_hertz": voice.natural_sample_rate_hertz
                })
                
            return voices
            
        except Exception as e:
            self.logger.error(f"Failed to get Google TTS voices: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка получения списка голосов Google: {str(e)}",
                provider=self.provider
            )
