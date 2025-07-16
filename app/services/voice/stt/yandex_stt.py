"""
Yandex SpeechKit Speech-to-Text сервис
"""

import logging
from typing import Optional, AsyncGenerator
import json
import base64

import aiohttp

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceProvider, VoiceProcessingResult, VoiceFileInfo, STTConfig
from app.services.voice.base import STTServiceBase, VoiceServiceError, VoiceServiceTimeout


class YandexSTTService(STTServiceBase):
    """
    Сервис Speech-to-Text на основе Yandex SpeechKit API
    """

    def __init__(self, config: STTConfig, logger: Optional[logging.Logger] = None):
        super().__init__(VoiceProvider.YANDEX, config, logger)
        self.api_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
        self.folder_id = settings.YANDEX_FOLDER_ID
        self.stt_url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
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
            self.logger.info("Yandex STT service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize Yandex STT service: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка инициализации Yandex STT: {str(e)}",
                provider=self.provider
            )

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        if self.session:
            await self.session.close()
        self._initialized = False
        self.logger.info("Yandex STT service cleaned up")

    async def health_check(self) -> bool:
        """Проверка доступности Yandex SpeechKit API"""
        if not self.session:
            return False

        try:
            # Проверяем доступность API через заголовки
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
            }
            
            # Делаем пустой запрос для проверки авторизации
            async with self.session.post(
                self.stt_url,
                headers=headers,
                data=b'\x00' * 16  # Минимальные данные для теста
            ) as response:
                # Даже если запрос не удался, проверяем, что это не ошибка авторизации
                return response.status != 401
                
        except Exception as e:
            self.logger.warning(f"Yandex STT health check failed: {e}")
            return False

    def validate_audio_format(self, file_info: VoiceFileInfo) -> bool:
        """Проверить поддерживаемость формата аудио для Yandex SpeechKit"""
        # Yandex SpeechKit поддерживает определенные форматы
        supported_formats = {
            'wav', 'opus', 'mp3', 'flac', 'speex', 'alaw', 'mulaw', 'ogg'
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
        Преобразование аудио в текст через Yandex SpeechKit
        
        Args:
            audio_data: Бинарные данные аудиофайла
            file_info: Информация о файле
            **kwargs: Дополнительные параметры
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        if not self._initialized or not self.session:
            raise VoiceServiceError(
                "Yandex STT service не инициализирован",
                provider=self.provider
            )

        start_time = self.logger.info("Starting Yandex SpeechKit audio transcription")

        try:
            # Валидация формата
            if not self.validate_audio_format(file_info):
                raise VoiceServiceError(
                    f"Неподдерживаемый формат аудио: {file_info.format}",
                    provider=self.provider
                )

            # Валидация размера файла (Yandex лимит 1MB для sync recognition)
            if file_info.size_bytes > 1 * 1024 * 1024:
                raise VoiceServiceError(
                    f"Файл слишком большой: {file_info.size_bytes} байт (макс. 1MB)",
                    provider=self.provider
                )

            # Валидация длительности
            if not self.validate_audio_duration(file_info):
                raise VoiceServiceError(
                    f"Аудио слишком длинное: {file_info.duration_seconds}с (макс. {self.config.max_duration}с)",
                    provider=self.provider
                )

            # Подготовка заголовков
            headers = {
                "Authorization": f"Api-Key {self.api_key}",
                "x-folder-id": self.folder_id,
            }

            # Конвертация OGG файлов от WhatsApp в WAV для совместимости
            converted_audio_data = audio_data
            yandex_format = self._get_yandex_format(file_info)
            
            # WhatsApp OGG файлы нужно конвертировать в WAV, т.к. они не содержат Opus
            if (file_info.original_filename.lower().endswith('.ogg') and 
                yandex_format == 'oggopus'):
                try:
                    from pydub import AudioSegment
                    import io
                    
                    self.logger.info("Converting WhatsApp OGG file to WAV for Yandex compatibility")
                    
                    # Проверим заголовок файла
                    if audio_data[:4] == b'OggS':
                        self.logger.debug("File has valid OGG header")
                    else:
                        self.logger.warning(f"File does not have OGG header: {audio_data[:10].hex()}")
                    
                    # Попробуем различные способы загрузки
                    audio_segment = None
                    
                    # Способ 1: Попробовать как OGG
                    try:
                        audio_segment = AudioSegment.from_ogg(io.BytesIO(audio_data))
                        self.logger.debug("Successfully loaded as OGG")
                    except Exception as e:
                        self.logger.debug(f"Failed to load as OGG: {e}")
                        
                        # Способ 2: Попробовать автоопределение
                        try:
                            audio_segment = AudioSegment.from_file(io.BytesIO(audio_data))
                            self.logger.debug("Successfully loaded with auto-detection")
                        except Exception as e2:
                            self.logger.debug(f"Failed to load with auto-detection: {e2}")
                            raise e2
                    
                    if audio_segment:
                        # Конвертируем в WAV с параметрами для Yandex
                        wav_buffer = io.BytesIO()
                        audio_segment.export(
                            wav_buffer, 
                            format="wav",
                            parameters=["-ar", "16000", "-ac", "1"]  # 16kHz, mono
                        )
                        converted_audio_data = wav_buffer.getvalue()
                        
                        # Обновляем формат для Yandex
                        yandex_format = 'lpcm'
                        
                        self.logger.info(f"Converted OGG to WAV: {len(audio_data)} -> {len(converted_audio_data)} bytes")
                    else:
                        raise Exception("Could not load audio file")
                    
                except Exception as e:
                    self.logger.warning(f"Failed to convert OGG to WAV: {e}")
                    
                    # Если конвертация не удалась, возможно это не настоящий OGG файл
                    # Попробуем обработать его как WAV напрямую
                    if audio_data[:4] == b'RIFF' or audio_data[8:12] == b'WAVE':
                        self.logger.info("Audio data appears to be WAV format, using as-is")
                        converted_audio_data = audio_data
                        yandex_format = 'lpcm'
                    else:
                        # Если это неизвестный формат, попробуем оригинальные данные
                        self.logger.warning("Unknown audio format, trying original data")
                        converted_audio_data = audio_data
                        # Возможно стоит выбросить исключение для fallback на другой провайдер
                        raise VoiceServiceError(
                            f"Неподдерживаемый формат аудио файла. Заголовок: {audio_data[:10].hex()}",
                            provider=self.provider
                        )

            # Подготовка параметров запроса
            params = {
                "lang": self.config.language or "ru-RU",
                "format": yandex_format,
                "sampleRateHertz": getattr(self.config, 'sample_rate', 16000),
            }

            # Дополнительные параметры из конфигурации
            if hasattr(self.config, 'custom_params') and self.config.custom_params:
                params.update(self.config.custom_params)

            # Переопределяем параметрами из вызова
            params.update(kwargs)

            # Выполняем запрос к API
            timeout_seconds = getattr(settings, 'VOICE_PROCESSING_TIMEOUT', 30)
            
            async with self.session.post(
                self.stt_url,
                headers=headers,
                params=params,
                data=converted_audio_data
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    raise VoiceServiceError(
                        f"Yandex API error {response.status}: {error_text}",
                        provider=self.provider
                    )

                result = await response.json()

            # Обрабатываем результат
            if "result" in result:
                transcript = result["result"]
                confidence = 1.0  # Yandex не всегда возвращает confidence
            else:
                transcript = ""
                confidence = 0.0

            processing_time = 0  # Yandex не возвращает время обработки
            
            return VoiceProcessingResult(
                success=True,
                text=transcript,
                provider_used=self.provider,
                processing_time=processing_time / 1000.0,  # конвертируем в секунды
                metadata={
                    "language": params["lang"],
                    "format": params["format"],
                    "sample_rate": params["sampleRateHertz"],
                    "response_data": result
                }
            )

        except Exception as e:
            self.logger.error(f"Yandex STT transcription failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка распознавания речи Yandex: {str(e)}",
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
        if not self._initialized or not self.session:
            raise VoiceServiceError(
                "Yandex STT service не инициализирован",
                provider=self.provider
            )

        try:
            # Yandex SpeechKit поддерживает gRPC streaming API,
            # но для простоты реализуем через накопление chunks
            
            accumulated_data = b""
            chunk_size = 16000  # 1 секунда аудио при 16kHz
            
            async for audio_chunk in audio_stream:
                accumulated_data += audio_chunk
                
                # Когда накопилось достаточно данных, обрабатываем
                if len(accumulated_data) >= chunk_size:
                    # Создаем временный VoiceFileInfo
                    temp_file_info = VoiceFileInfo(
                        original_filename="stream.wav",
                        size_bytes=len(accumulated_data),
                        format=None,
                        duration_seconds=None
                    )
                    
                    try:
                        result = await self.transcribe_audio(
                            accumulated_data, 
                            temp_file_info, 
                            **kwargs
                        )
                        
                        if result.text and result.text.strip():
                            yield result.text
                            
                    except Exception as e:
                        self.logger.warning(f"Stream chunk processing failed: {e}")
                    
                    # Очищаем накопленные данные
                    accumulated_data = b""

        except Exception as e:
            self.logger.error(f"Yandex streaming STT failed: {e}", exc_info=True)
            raise VoiceServiceError(
                f"Ошибка потокового распознавания Yandex: {str(e)}",
                provider=self.provider
            )

    def _get_yandex_format(self, file_info: VoiceFileInfo) -> str:
        """Определить формат для Yandex SpeechKit на основе файла"""
        if not file_info.format:
            # Попробуем определить по расширению
            if "." in file_info.original_filename:
                ext = file_info.original_filename.split(".")[-1].lower()
                format_mapping = {
                    'wav': 'lpcm',
                    'opus': 'oggopus',
                    'mp3': 'mp3',
                    'flac': 'flac',
                    'ogg': 'oggopus'
                }
                return format_mapping.get(ext, 'lpcm')
            return 'lpcm'
            
        format_mapping = {
            'wav': 'lpcm',
            'opus': 'oggopus', 
            'mp3': 'mp3',
            'flac': 'flac',
            'ogg': 'oggopus',
            'speex': 'speex'
        }
        
        return format_mapping.get(
            file_info.format.value, 
            'lpcm'
        )

    def _validate_language(self, language: str) -> bool:
        """Валидация языка для Yandex STT"""
        supported_languages = ["ru-RU", "en-US", "tr-TR", "kk-KZ"]
        return language in supported_languages
    
    def _validate_model(self, model: str) -> bool:
        """Валидация модели для Yandex STT"""
        supported_models = ["general", "general:rc", "general:deprecated"]
        return model in supported_models
    
    def _get_headers(self) -> dict[str, str]:
        """Получение заголовков для API запросов"""
        return {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def _build_recognition_request(self, audio_data: bytes, format: str, sample_rate: int, language: str) -> dict:
        """Построение запроса для распознавания речи"""
        audio_content = base64.b64encode(audio_data).decode('utf-8')
        
        return {
            "config": {
                "specification": {
                    "languageCode": language,
                    "model": self.config.model or "general",
                    "audioEncoding": format,
                    "sampleRateHertz": sample_rate,
                    "audioChannelCount": 1
                },
                "languageCode": language
            },
            "audio": {
                "content": audio_content
            }
        }
    
    async def _handle_invalid_audio(self, audio_data: bytes) -> str:
        """Обработка невалидных аудиоданных"""
        self.logger.warning("Invalid audio data provided")
        return "Невалидные аудиоданные"
