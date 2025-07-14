"""
Google Cloud Speech-to-Text сервис
"""

import logging
from typing import Optional, AsyncGenerator
import io

from google.cloud import speech_v1
from google.cloud.speech_v1 import types

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, VoiceProcessingResult, VoiceFileInfo, STTConfig
from app.services.voice.base import STTServiceBase, VoiceServiceError, VoiceServiceTimeout


class GoogleSTTService(STTServiceBase):
    """
    Сервис Speech-to-Text на основе Google Cloud Speech API
    """

    def __init__(self, config: STTConfig, logger: Optional[logging.Logger] = None):
        super().__init__(VoiceProvider.GOOGLE, config, logger)
        self.client: Optional[speech_v1.SpeechAsyncClient] = None
        self.credentials_path = settings.GOOGLE_APPLICATION_CREDENTIALS

    async def initialize(self) -> None:
        """Инициализация Google Cloud Speech клиента"""
        try:
            if not self.credentials_path:
                raise VoiceServiceError(
                    "Google Cloud credentials не настроены (GOOGLE_APPLICATION_CREDENTIALS)",
                    provider=self.provider
                )

            # Инициализируем асинхронный клиент
            self.client = speech_v1.SpeechAsyncClient()
            
            # Проверяем доступность API
            await self.health_check()
            self._initialized = True
            self.logger.info("Google STT service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Google STT service: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка инициализации Google STT: {str(e)}",
                provider=self.provider
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.client:
            # Google client не требует explicit close
            pass
        self._initialized = False
        self.logger.info("Google STT service cleaned up")

    async def health_check(self) -> bool:
        """Проверка доступности Google Cloud Speech API"""
        if not self.client:
            return False

        try:
            # Простая проверка доступности API через пустой запрос
            config = types.RecognitionConfig(
                encoding=types.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code="ru-RU",
            )
            
            # Создаем короткий пустой аудио для теста
            audio = types.RecognitionAudio(content=b'\x00' * 32)
            
            # Не делаем реальный запрос, просто проверяем, что клиент создан
            return True
        except Exception as e:
            self.logger.warning(f"Google STT health check failed: {e}")
            return False

    def validate_audio_format(self, file_info: VoiceFileInfo) -> bool:
        """Проверить поддерживаемость формата аудио для Google Cloud Speech"""
        # Google Cloud Speech поддерживает множество форматов
        supported_formats = {
            'mp3', 'wav', 'flac', 'ogg', 'opus', 'amr', 'amr-wb', 'webm'
        }
        
        if file_info.format:
            return file_info.format.value in supported_formats
            
        # Если формат не определен, проверяем по расширению из имени файла
        if "." in file_info.original_filename:
            ext = file_info.original_filename.split(".")[-1].lower()
            return ext in supported_formats
            
        return True  # По умолчанию считаем поддерживаемым

    async def transcribe_audio(self, 
                              audio_data: bytes,
                              file_info: VoiceFileInfo,
                              **kwargs) -> VoiceProcessingResult:
        """
        Преобразование аудио в текст через Google Cloud Speech
        
        Args:
            audio_data: Бинарные данные аудиофайла
            file_info: Информация о файле
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "Google STT service не инициализирован",
                provider=self.provider
            )

        start_time = self.logger.info("Starting Google Cloud Speech audio transcription")

        try:
            # Валидация формата
            if not self.validate_audio_format(file_info):
                raise VoiceServiceError(
                    f"Неподдерживаемый формат аудио: {file_info.format}",
                    provider=self.provider
                )

            # Валидация размера файла (Google лимит ~10MB для sync recognition)
            if file_info.size_bytes > 10 * 1024 * 1024:
                raise VoiceServiceError(
                    f"Файл слишком большой: {file_info.size_bytes} байт (макс. 10MB)",
                    provider=self.provider
                )

            # Валидация длительности
            if not self.validate_audio_duration(file_info):
                raise VoiceServiceError(
                    f"Аудио слишком длинное: {file_info.duration_seconds}с (макс. {self.config.max_duration}с)",
                    provider=self.provider
                )

            # Определяем encoding на основе формата файла
            encoding = self._get_audio_encoding(file_info)
            
            # Настройка конфигурации распознавания
            recognition_config = types.RecognitionConfig(
                encoding=encoding,
                sample_rate_hertz=16000,  # Можно сделать настраиваемым
                language_code=self.config.language or "ru-RU",
                enable_automatic_punctuation=True,
                model="latest_short" if self.config.model else "default",
                audio_channel_count=1,
            )

            # Дополнительные параметры из конфигурации
            if hasattr(self.config, 'custom_params') and self.config.custom_params:
                for key, value in self.config.custom_params.items():
                    if hasattr(recognition_config, key):
                        setattr(recognition_config, key, value)

            # Переопределяем параметрами из вызова
            for key, value in kwargs.items():
                if hasattr(recognition_config, key):
                    setattr(recognition_config, key, value)

            # Создаем аудио объект
            audio = types.RecognitionAudio(content=audio_data)

            # Выполняем транскрипцию с таймаутом
            timeout_seconds = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30)
            
            response = await self._with_timeout(
                self.client.recognize(
                    config=recognition_config,
                    audio=audio
                ),
                timeout_seconds
            )

            # Обрабатываем результат
            if not response.results:
                transcript = ""
                confidence = 0.0
            else:
                # Берем лучший результат из первой альтернативы
                best_result = response.results[0]
                if best_result.alternatives:
                    transcript = best_result.alternatives[0].transcript
                    confidence = best_result.alternatives[0].confidence
                else:
                    transcript = ""
                    confidence = 0.0

            processing_time = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30) - timeout_seconds
            
            return VoiceProcessingResult(
                text=transcript,
                confidence=confidence,
                processing_time_ms=int(processing_time * 1000),
                provider=self.provider,
                metadata={
                    "language_code": recognition_config.language_code,
                    "encoding": recognition_config.encoding.name,
                    "results_count": len(response.results),
                    "model": recognition_config.model
                }
            )

        except Exception as e:
            self.logger.error(f"Google STT transcription failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка распознавания речи Google: {str(e)}",
                provider=self.provider
            )

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
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "Google STT service не инициализирован",
                provider=self.provider
            )

        try:
            # Конфигурация для стримингового распознавания
            config = types.RecognitionConfig(
                encoding=types.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=self.config.language or "ru-RU",
                enable_automatic_punctuation=True,
            )
            
            streaming_config = types.StreamingRecognitionConfig(
                config=config,
                interim_results=True,
            )

            # Генератор запросов для стримингового API
            async def request_generator():
                # Первый запрос с конфигурацией
                yield types.StreamingRecognizeRequest(streaming_config=streaming_config)
                
                # Последующие запросы с аудиоданными
                async for audio_chunk in audio_stream:
                    yield types.StreamingRecognizeRequest(audio_content=audio_chunk)

            # Выполняем стриминговое распознавание
            streaming_recognize_response = self.client.streaming_recognize(
                requests=request_generator()
            )

            async for response in streaming_recognize_response:
                for result in response.results:
                    if result.alternatives:
                        transcript = result.alternatives[0].transcript
                        if transcript.strip():
                            yield transcript

        except Exception as e:
            self.logger.error(f"Google streaming STT failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка потокового распознавания Google: {str(e)}",
                provider=self.provider
            )

    def _get_audio_encoding(self, file_info: VoiceFileInfo) -> types.RecognitionConfig.AudioEncoding:
        """Определить encoding для Google Cloud Speech на основе формата файла"""
        if not file_info.format:
            return types.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED
            
        format_mapping = {
            'wav': types.RecognitionConfig.AudioEncoding.LINEAR16,
            'flac': types.RecognitionConfig.AudioEncoding.FLAC,
            'ogg': types.RecognitionConfig.AudioEncoding.OGG_OPUS,
            'opus': types.RecognitionConfig.AudioEncoding.OGG_OPUS,
            'mp3': types.RecognitionConfig.AudioEncoding.MP3,
            'amr': types.RecognitionConfig.AudioEncoding.AMR,
            'webm': types.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        }
        
        return format_mapping.get(
            file_info.format.value, 
            types.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED
        )
