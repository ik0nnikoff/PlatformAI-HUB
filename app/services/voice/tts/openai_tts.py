"""
OpenAI Text-to-Speech сервис
"""

import logging
from typing import Optional, AsyncGenerator
import io

import openai
from openai import AsyncOpenAI

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, VoiceProcessingResult, TTSConfig, AudioFormat
from app.services.voice.base import TTSServiceBase, VoiceServiceError, VoiceServiceTimeout


class OpenAITTSService(TTSServiceBase):
    """
    Сервис Text-to-Speech на основе OpenAI TTS API
    """

    def __init__(self, config: TTSConfig, logger: Optional[logging.Logger] = None):
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
                self.logger.warning("OpenAI TTS health check failed, but service will still initialize")
            
            self._initialized = True
            self.logger.info("OpenAI TTS service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize OpenAI TTS service: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка инициализации OpenAI TTS: {str(e)}",
                provider=self.provider
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.client:
            await self.client.close()
        self._initialized = False
        self.logger.info("OpenAI TTS service cleaned up")

    async def health_check(self) -> bool:
        """Проверка доступности OpenAI API"""
        if not self.client:
            return False

        try:
            # Для health check просто вернем True если клиент создан
            # Реальная проверка API будет происходить при первом запросе synthesis
            return True
        except Exception as e:
            self.logger.warning(f"OpenAI TTS health check failed: {e}")
            return False

    def validate_text_length(self, text: str) -> bool:
        """Проверить длину текста для OpenAI TTS"""
        # OpenAI TTS имеет лимит 4096 символов за запрос
        return len(text) <= 4096

    async def synthesize_speech(self, 
                               text: str,
                               **kwargs) -> VoiceProcessingResult:
        """
        Преобразование текста в аудио через OpenAI TTS
        
        Args:
            text: Текст для синтеза
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат с аудиоданными
        """
        if not self._initialized or not self.client:
            raise VoiceServiceError(
                "OpenAI TTS service не инициализирован",
                provider=self.provider
            )

        start_time = self.logger.info("Starting OpenAI speech synthesis")

        try:
            # Валидация длины текста
            if not self.validate_text_length(text):
                raise VoiceServiceError(
                    f"Текст слишком длинный: {len(text)} символов (макс. 4096)",
                    provider=self.provider
                )

            if not text.strip():
                raise VoiceServiceError(
                    "Пустой текст для синтеза",
                    provider=self.provider
                )

            # Подготавливаем параметры для API
            synthesis_params = {
                "model": self.config.model.value,
                "voice": self.config.voice,
                "input": text.strip(),
            }

            # OpenAI TTS поддерживает только определенные форматы
            response_format = "mp3"  # По умолчанию
            if self.config.audio_format == AudioFormat.OPUS:
                response_format = "opus"
            elif self.config.audio_format == AudioFormat.AAC:
                response_format = "aac"
            elif self.config.audio_format == AudioFormat.FLAC:
                response_format = "flac"
            elif self.config.audio_format == AudioFormat.WAV:
                response_format = "wav"
            elif self.config.audio_format == AudioFormat.PCM:
                response_format = "pcm"

            synthesis_params["response_format"] = response_format

            # Добавляем параметр скорости если поддерживается
            if hasattr(self.config, 'speed') and 0.25 <= self.config.speed <= 4.0:
                synthesis_params["speed"] = self.config.speed

            # Дополнительные параметры из конфигурации
            if hasattr(self.config, 'custom_params') and self.config.custom_params:
                synthesis_params.update(self.config.custom_params)

            # Переопределяем параметрами из вызова
            synthesis_params.update(kwargs)

            # Выполняем синтез с таймаутом
            timeout_seconds = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30)
            
            response = await self._with_timeout(
                self.client.audio.speech.create(**synthesis_params),
                timeout_seconds
            )

            # Читаем аудиоданные
            audio_data = response.content

            processing_time = self.logger.info("OpenAI speech synthesis completed")

            if not audio_data:
                return VoiceProcessingResult(
                    success=False,
                    error_message="Получены пустые аудиоданные от OpenAI TTS",
                    provider_used=self.provider,
                    processing_time=processing_time,
                    metadata={
                        "model": self.config.model.value,
                        "voice": self.config.voice,
                        "text_length": len(text),
                        "response_format": response_format
                    }
                )

            self.logger.info(f"OpenAI TTS synthesis successful: {len(audio_data)} bytes")

            return VoiceProcessingResult(
                success=True,
                provider_used=self.provider,
                processing_time=processing_time,
                metadata={
                    "model": self.config.model.value,
                    "voice": self.config.voice,
                    "text_length": len(text),
                    "audio_size": len(audio_data),
                    "response_format": response_format,
                    "audio_data": audio_data  # Сохраняем данные в метаданных
                }
            )

        except openai.BadRequestError as e:
            error_msg = f"Ошибка запроса к OpenAI TTS: {str(e)}"
            self.logger.error(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

        except openai.AuthenticationError as e:
            error_msg = f"Ошибка аутентификации OpenAI TTS: {str(e)}"
            self.logger.error(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

        except openai.RateLimitError as e:
            error_msg = f"Превышен лимит запросов OpenAI TTS: {str(e)}"
            self.logger.warning(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

        except VoiceServiceTimeout as e:
            error_msg = f"Таймаут обработки OpenAI TTS: {str(e)}"
            self.logger.error(error_msg)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=timeout_seconds
            )

        except Exception as e:
            error_msg = f"Неожиданная ошибка OpenAI TTS: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return VoiceProcessingResult(
                success=False,
                error_message=error_msg,
                provider_used=self.provider,
                processing_time=0.0
            )

    async def synthesize_stream(self, 
                               text: str,
                               **kwargs) -> AsyncGenerator[bytes, None]:
        """
        Потоковый синтез речи
        
        Note: OpenAI TTS API не поддерживает потоковую обработку в традиционном смысле,
        но мы можем разбить длинный текст на части и обрабатывать их последовательно.
        """
        if not self._initialized:
            raise VoiceServiceError(
                "OpenAI TTS service не инициализирован",
                provider=self.provider
            )

        # Если текст короткий, обрабатываем его целиком
        if len(text) <= 4096:
            result = await self.synthesize_speech(text, **kwargs)
            if result.success and result.metadata.get('audio_data'):
                yield result.metadata['audio_data']
            else:
                raise VoiceServiceError(
                    result.error_message or "Ошибка синтеза речи",
                    provider=self.provider
                )
            return

        # Для длинного текста разбиваем на части
        self.logger.info(f"Splitting long text ({len(text)} chars) for streaming synthesis")
        
        # Разбиваем текст на предложения или части по 4000 символов
        chunks = self._split_text_for_synthesis(text, max_chunk_size=4000)
        
        for i, chunk in enumerate(chunks):
            try:
                self.logger.debug(f"Processing chunk {i+1}/{len(chunks)}: {len(chunk)} chars")
                result = await self.synthesize_speech(chunk, **kwargs)
                
                if result.success and result.metadata.get('audio_data'):
                    yield result.metadata['audio_data']
                else:
                    self.logger.error(f"Failed to synthesize chunk {i+1}: {result.error_message}")
                    # Продолжаем с следующим chunk вместо остановки
                    continue
                    
            except Exception as e:
                self.logger.error(f"Error processing chunk {i+1}: {e}")
                # Продолжаем с следующим chunk
                continue

    def _split_text_for_synthesis(self, text: str, max_chunk_size: int = 4000) -> list[str]:
        """
        Разбить текст на части для синтеза, стараясь сохранить целостность предложений
        
        Args:
            text: Исходный текст
            max_chunk_size: Максимальный размер части
            
        Returns:
            Список частей текста
        """
        if len(text) <= max_chunk_size:
            return [text]

        chunks = []
        current_chunk = ""
        
        # Разбиваем по предложениям
        sentences = text.replace('!', '.').replace('?', '.').split('.')
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Добавляем точку обратно
            sentence += '.'
            
            # Если добавление предложения превысит лимит
            if len(current_chunk) + len(sentence) > max_chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = sentence
                else:
                    # Если даже одно предложение слишком длинное, разбиваем по словам
                    word_chunks = self._split_sentence_by_words(sentence, max_chunk_size)
                    chunks.extend(word_chunks)
            else:
                current_chunk += " " + sentence if current_chunk else sentence

        # Добавляем последний chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _split_sentence_by_words(self, sentence: str, max_chunk_size: int) -> list[str]:
        """
        Разбить длинное предложение по словам
        
        Args:
            sentence: Предложение для разбивки
            max_chunk_size: Максимальный размер части
            
        Returns:
            Список частей предложения
        """
        words = sentence.split()
        chunks = []
        current_chunk = ""
        
        for word in words:
            if len(current_chunk) + len(word) + 1 > max_chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = word
                else:
                    # Если даже одно слово слишком длинное, принудительно обрезаем
                    chunks.append(word[:max_chunk_size])
            else:
                current_chunk += " " + word if current_chunk else word

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks
