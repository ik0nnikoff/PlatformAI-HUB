"""
OpenAI Speech-to-Text сервис
"""

import logging
from typing import Optional, AsyncGenerator
import io
import time

import openai
from openai import AsyncOpenAI

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, VoiceProcessingResult, VoiceFileInfo, STTConfig
from app.services.voice.base import STTServiceBase, VoiceServiceError, VoiceServiceTimeout


class OpenAISTTService(STTServiceBase):
    """
    Сервис Speech-to-Text на основе OpenAI Whisper API
    """

    def __init__(self, config: STTConfig, logger: Optional[logging.Logger] = None):
        super().__init__(VoiceProvider.OPENAI, config, logger)
        self.client: Optional[AsyncOpenAI] = None
        self.api_key = settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None

    async def initialize(self) -> None:
        """Инициализация OpenAI клиента"""
        try:
            if not self.api_key:
                raise VoiceServiceError(
                    "OpenAI API key не настроен",
                    provider=self.provider
                )

            self.client = AsyncOpenAI(api_key=self.api_key)
            
            # Проверяем доступность API (не блокируем инициализацию при ошибке)
            health_ok = await self.health_check()
            if not health_ok:
                self.logger.warning("OpenAI STT health check failed, but service will still initialize")
            
            self._initialized = True
            self.logger.info("OpenAI STT service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize OpenAI STT service: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка инициализации OpenAI STT: {str(e)}",
                provider=self.provider
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.client:
            await self.client.close()
        self._initialized = False
        self.logger.info("OpenAI STT service cleaned up")

    async def health_check(self) -> bool:
        """Проверка доступности OpenAI API"""
        if not self.client:
            return False

        try:
            # Для health check просто вернем True если клиент создан
            # Реальная проверка API будет происходить при первом запросе transcription
            return True
        except Exception as e:
            self.logger.warning(f"OpenAI STT health check failed: {e}")
            return False

    def validate_audio_format(self, file_info: VoiceFileInfo) -> bool:
        """Проверить поддерживаемость формата аудио для OpenAI"""
        # OpenAI Whisper поддерживает множество форматов
        supported_formats = {
            'mp3', 'mp4', 'm4a', 'wav', 'flac', 'ogg', 'opus', 'webm'
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
        Преобразование аудио в текст через OpenAI Whisper
        
        Args:
            audio_data: Бинарные данные аудиофайла
            file_info: Информация о файле
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "OpenAI STT service не инициализирован",
                provider=self.provider
            )

        start_time = time.time()
        self.logger.info("Starting OpenAI audio transcription")

        try:
            # Валидация формата
            if not self.validate_audio_format(file_info):
                raise VoiceServiceError(
                    f"Неподдерживаемый формат аудио: {file_info.format}",
                    provider=self.provider
                )

            # Валидация размера файла (OpenAI лимит 25MB)
            if file_info.size_bytes > 25 * 1024 * 1024:
                raise VoiceServiceError(
                    f"Файл слишком большой: {file_info.size_bytes} байт (макс. 25MB)",
                    provider=self.provider
                )

            # Валидация длительности
            if not self.validate_audio_duration(file_info):
                raise VoiceServiceError(
                    f"Аудио слишком длинное: {file_info.duration_seconds}с (макс. {self.config.max_duration}с)",
                    provider=self.provider
                )

            # Подготавливаем параметры для API
            transcription_params = {
                "model": self.config.model.value,
                "language": self.config.language,
                "response_format": "text",
            }

            # Дополнительные параметры из конфигурации
            if hasattr(self.config, 'custom_params') and self.config.custom_params:
                transcription_params.update(self.config.custom_params)

            # Переопределяем параметрами из вызова
            transcription_params.update(kwargs)

            # Создаем file-like объект для API
            audio_file = io.BytesIO(audio_data)
            audio_file.name = file_info.original_filename

            # Выполняем транскрипцию с таймаутом
            timeout_seconds = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30)
            
            transcript = await self._with_timeout(
                self.client.audio.transcriptions.create(
                    file=audio_file,
                    **transcription_params
                ),
                timeout_seconds
            )

            processing_time = time.time() - start_time
            self.logger.info("OpenAI transcription completed")

            # Проверяем результат
            if not transcript or (hasattr(transcript, 'text') and not transcript.text.strip()):
                return VoiceProcessingResult(
                    success=False,
                    error_message="Не удалось распознать речь в аудиофайле",
                    provider_used=self.provider,
                    processing_time=processing_time,
                    metadata={
                        "model": self.config.model.value,
                        "language": self.config.language,
                        "file_size": file_info.size_bytes,
                        "file_format": file_info.format.value if file_info.format else None
                    }
                )

            # Извлекаем текст из результата
            text_result = transcript if isinstance(transcript, str) else transcript.text

            self.logger.info(f"OpenAI transcription successful: {len(text_result)} characters")

            return VoiceProcessingResult(
                success=True,
                text=text_result.strip(),
                provider_used=self.provider,
                processing_time=processing_time,
                metadata={
                    "model": self.config.model.value,
                    "language": self.config.language,
                    "file_size": file_info.size_bytes,
                    "file_format": file_info.format.value if file_info.format else None,
                    "text_length": len(text_result)
                }
            )

        except openai.BadRequestError as e:
            error_msg = f"Ошибка запроса к OpenAI: {str(e)}"
            self.logger.error(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

        except openai.AuthenticationError as e:
            error_msg = f"Ошибка аутентификации OpenAI: {str(e)}"
            self.logger.error(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

        except openai.RateLimitError as e:
            error_msg = f"Превышен лимит запросов OpenAI: {str(e)}"
            self.logger.warning(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

        except VoiceServiceTimeout as e:
            error_msg = f"Таймаут обработки OpenAI: {str(e)}"
            self.logger.error(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=timeout_seconds
            )

        except Exception as e:
            error_msg = f"Неожиданная ошибка OpenAI STT: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

    async def transcribe_stream(self, 
                               audio_stream: AsyncGenerator[bytes, None],
                               **kwargs) -> AsyncGenerator[str, None]:
        """
        Потоковое преобразование аудио в текст
        
        Note: OpenAI Whisper API не поддерживает потоковую обработку,
        поэтому этот метод накапливает данные и обрабатывает их целиком.
        """
        if not self._initialized:
            raise VoiceServiceError(
                "OpenAI STT service не инициализирован",
                provider=self.provider
            )

        self.logger.warning("OpenAI Whisper не поддерживает потоковую обработку, накапливаем данные")

        # Накапливаем аудиоданные
        audio_chunks = []
        total_size = 0
        max_size = 25 * 1024 * 1024  # 25MB лимит OpenAI

        try:
            async for chunk in audio_stream:
                if total_size + len(chunk) > max_size:
                    raise VoiceServiceError(
                        f"Поток превысил максимальный размер {max_size} байт",
                        provider=self.provider
                    )
                
                audio_chunks.append(chunk)
                total_size += len(chunk)

            # Объединяем все chunks
            audio_data = b''.join(audio_chunks)
            
            # Создаем временную информацию о файле
            temp_file_info = VoiceFileInfo(
                file_id="stream_temp",
                original_filename="stream.mp3",
                mime_type="audio/mpeg",
                size_bytes=len(audio_data),
                created_at="",
                minio_bucket="",
                minio_key=""
            )

            # Обрабатываем как обычный файл
            result = await self.transcribe_audio(audio_data, temp_file_info, **kwargs)
            
            if result.success and result.text:
                yield result.text
            else:
                raise VoiceServiceError(
                    result.error_message or "Ошибка обработки потока",
                    provider=self.provider
                )

        except Exception as e:
            self.logger.error(f"Error in stream transcription: {e}", exc_info=True)
            raise
