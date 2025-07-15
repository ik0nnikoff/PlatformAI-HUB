"""
Основной сервис-оркестратор для голосовых функций
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List, Tuple
import json

from app.core.config import settings
from app.api.schemas.voice_schemas import (
    VoiceSettings, VoiceProvider, VoiceProcessingResult, VoiceFileInfo,
    STTConfig, TTSConfig, VoiceProviderConfig
)
from app.services.voice.base import VoiceServiceError, VoiceConfigMixin, RateLimiter
from app.services.voice.redis_rate_limiter import RedisRateLimiter
from app.services.voice.voice_metrics import VoiceMetricsCollector, VoiceMetrics
from app.services.voice.minio_manager import MinioFileManager
from app.services.voice.stt.openai_stt import OpenAISTTService
from app.services.voice.stt.google_stt import GoogleSTTService
from app.services.voice.stt.yandex_stt import YandexSTTService
from app.services.voice.tts.openai_tts import OpenAITTSService
from app.services.voice.tts.google_tts import GoogleTTSService
from app.services.voice.tts.yandex_tts import YandexTTSService
from app.services.redis_wrapper import RedisService


class VoiceServiceOrchestrator(VoiceConfigMixin):
    """
    Главный оркестратор для управления голосовыми сервисами.
    Координирует работу STT, TTS провайдеров, кэширование и fallback логику.
    """

    def __init__(self, 
                 redis_service: RedisService,
                 logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("voice_orchestrator")
        self.redis_service = redis_service
        self.minio_manager = MinioFileManager(logger=self.logger)
        
        # Metrics collector
        self.metrics_collector = VoiceMetricsCollector(
            redis_service=redis_service,
            logger=self.logger
        )
        
        # Инициализированные сервисы
        self.stt_services: Dict[VoiceProvider, Any] = {}
        self.tts_services: Dict[VoiceProvider, Any] = {}
        
        # Redis-based rate limiters для пользователей
        self.rate_limiters: Dict[str, RedisRateLimiter] = {}
        
        self._initialized = False

    async def initialize(self) -> None:
        """Инициализация оркестратора"""
        try:
            # Инициализируем MinIO менеджер
            await self.minio_manager.initialize()
            
            self._initialized = True
            self.logger.info("Voice service orchestrator initialized")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize voice orchestrator: {e}", exc_info=True)
            raise

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        try:
            # Очищаем все STT сервисы
            for service in self.stt_services.values():
                await service.cleanup()
            
            # Очищаем все TTS сервисы  
            for service in self.tts_services.values():
                await service.cleanup()
            
            # Очищаем MinIO менеджер
            await self.minio_manager.cleanup()
            
            self.stt_services.clear()
            self.tts_services.clear()
            self.rate_limiters.clear()
            
            self._initialized = False
            self.logger.info("Voice service orchestrator cleaned up")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}", exc_info=True)

    async def initialize_voice_services_for_agent(self, 
                                                 agent_id: str,
                                                 agent_config: Dict[str, Any]) -> bool:
        """
        Инициализация голосовых сервисов для конкретного агента
        
        Args:
            agent_id: ID агента
            agent_config: Конфигурация агента
            
        Returns:
            True если инициализация успешна
        """
        try:
            voice_config = self.get_voice_settings_from_config(agent_config)
            if not voice_config:
                self.logger.debug(f"No voice settings found for agent {agent_id}")
                return False

            # Валидируем конфигурацию
            if not self.validate_voice_config_structure(voice_config):
                self.logger.error(f"Invalid voice config structure for agent {agent_id}")
                return False

            voice_settings = VoiceSettings(**voice_config)
            
            if not voice_settings.enabled:
                self.logger.debug(f"Voice features disabled for agent {agent_id}")
                return False

            # Инициализируем сервисы для каждого провайдера
            initialized_providers = []
            for provider_config in voice_settings.providers:
                try:
                    await self._initialize_provider_services(provider_config)
                    initialized_providers.append(provider_config.provider)
                except Exception as e:
                    self.logger.warning(f"Skipping provider {provider_config.provider} due to initialization error: {e}")

            if not initialized_providers:
                self.logger.error(f"No voice providers could be initialized for agent {agent_id}")
                return False

            # Создаем rate limiter для агента
            if agent_id not in self.rate_limiters:
                self.rate_limiters[agent_id] = RedisRateLimiter(
                    redis_service=self.redis_service,
                    max_requests=voice_settings.rate_limit_per_minute,
                    window_seconds=60,
                    key_prefix=f"voice_rate_limit:{agent_id}:",
                    logger=self.logger
                )

            self.logger.info(f"Voice services initialized for agent {agent_id} with {len(initialized_providers)} providers: {initialized_providers}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize voice services for agent {agent_id}: {e}", exc_info=True)
            return False

    def _check_provider_credentials(self, provider: VoiceProvider) -> bool:
        """
        Проверяет наличие необходимых credentials для провайдера
        
        Args:
            provider: Провайдер голосовых сервисов
            
        Returns:
            True если credentials доступны
        """
        from app.core.config import settings
        
        if provider == VoiceProvider.OPENAI:
            return bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.get_secret_value())
        elif provider == VoiceProvider.GOOGLE:
            return bool(settings.GOOGLE_APPLICATION_CREDENTIALS and settings.GOOGLE_CLOUD_PROJECT_ID)
        elif provider == VoiceProvider.YANDEX:
            api_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
            iam_token = settings.YANDEX_IAM_TOKEN.get_secret_value() if settings.YANDEX_IAM_TOKEN else None
            return bool(api_key or iam_token)
        
        return False

    async def _initialize_provider_services(self, provider_config: VoiceProviderConfig) -> None:
        """Инициализация сервисов для конкретного провайдера"""
        provider = provider_config.provider

        # Проверяем наличие credentials для провайдера
        if not self._check_provider_credentials(provider):
            self.logger.debug(f"Skipping {provider} provider - missing credentials")
            return

        # Инициализируем STT сервис если настроен
        if provider_config.stt_config and provider_config.stt_config.enabled:
            try:
                if provider == VoiceProvider.OPENAI:
                    if provider not in self.stt_services:
                        service = OpenAISTTService(provider_config.stt_config, self.logger)
                        await service.initialize()
                        self.stt_services[provider] = service
                elif provider == VoiceProvider.GOOGLE:
                    if provider not in self.stt_services:
                        service = GoogleSTTService(provider_config.stt_config, self.logger)
                        await service.initialize()
                        self.stt_services[provider] = service
                elif provider == VoiceProvider.YANDEX:
                    if provider not in self.stt_services:
                        service = YandexSTTService(provider_config.stt_config, self.logger)
                        await service.initialize()
                        self.stt_services[provider] = service
                        
                self.logger.info(f"Successfully initialized {provider} STT service")
            except Exception as e:
                self.logger.warning(f"Failed to initialize {provider} STT service: {e}")

        # Инициализируем TTS сервис если настроен
        if provider_config.tts_config and provider_config.tts_config.enabled:
            try:
                if provider == VoiceProvider.OPENAI:
                    if provider not in self.tts_services:
                        service = OpenAITTSService(provider_config.tts_config, self.logger)
                        await service.initialize()
                        self.tts_services[provider] = service
                elif provider == VoiceProvider.GOOGLE:
                    if provider not in self.tts_services:
                        service = GoogleTTSService(provider_config.tts_config, self.logger)
                        await service.initialize()
                        self.tts_services[provider] = service
                elif provider == VoiceProvider.YANDEX:
                    if provider not in self.tts_services:
                        service = YandexTTSService(provider_config.tts_config, self.logger)
                        await service.initialize()
                        self.tts_services[provider] = service
                        
                self.logger.info(f"Successfully initialized {provider} TTS service")
            except Exception as e:
                self.logger.warning(f"Failed to initialize {provider} TTS service: {e}")

    async def process_voice_message(self,
                                   agent_id: str,
                                   user_id: str,
                                   audio_data: bytes,
                                   original_filename: str,
                                   agent_config: Dict[str, Any]) -> VoiceProcessingResult:
        """
        Обработка голосового сообщения (STT)
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            audio_data: Аудиоданные
            original_filename: Оригинальное имя файла
            agent_config: Конфигурация агента
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        start_time = time.time()
        
        try:
            # Проверяем rate limit
            if not await self._check_rate_limit(agent_id, user_id):
                return VoiceProcessingResult(
                    success=False,
                    error_message="Превышен лимит запросов на обработку голоса",
                    processing_time=time.time() - start_time
                )

            # Получаем настройки голоса
            voice_settings = await self._get_voice_settings(agent_config)
            if not voice_settings or not voice_settings.enabled:
                return VoiceProcessingResult(
                    success=False,
                    error_message="Голосовые функции отключены для этого агента",
                    processing_time=0.0
                )

            # Валидируем размер файла
            if not self._validate_file_size(audio_data, voice_settings.max_file_size_mb):
                return VoiceProcessingResult(
                    success=False,
                    error_message=f"Файл слишком большой (макс. {voice_settings.max_file_size_mb}MB)",
                    processing_time=0.0
                )

            # Определяем формат аудио
            from app.services.voice.base import AudioFileProcessor
            audio_format = AudioFileProcessor.detect_audio_format(audio_data, original_filename)
            
            # Сохраняем файл в MinIO
            file_info = await self.minio_manager.upload_audio_file(
                audio_data=audio_data,
                agent_id=agent_id,
                user_id=user_id,
                original_filename=original_filename,
                audio_format=audio_format,
                metadata={"type": "voice_input"}
            )

            # Проверяем кэш
            cache_key = self._generate_stt_cache_key(file_info, voice_settings)
            cached_result = await self._get_cached_stt_result(cache_key)
            if cached_result:
                self.logger.debug(f"Using cached STT result for {file_info.file_id}")
                cached_result.processing_time = time.time() - start_time
                return cached_result

            # Получаем список STT провайдеров по приоритету
            stt_providers = voice_settings.get_stt_providers()
            if not stt_providers:
                return VoiceProcessingResult(
                    success=False,
                    error_message="Нет доступных STT провайдеров",
                    processing_time=time.time() - start_time
                )

            # Пробуем каждого провайдера по очереди
            last_error = None
            for provider_config in stt_providers:
                try:
                    result = await self._process_stt_with_provider(
                        provider_config.provider,
                        audio_data,
                        file_info
                    )
                    
                    if result.success:
                        # Кэшируем успешный результат
                        if voice_settings.cache_enabled:
                            await self._cache_stt_result(cache_key, result, voice_settings.cache_ttl_hours)
                        
                        result.processing_time = time.time() - start_time
                        self.logger.info(f"STT successful with provider {provider_config.provider.value}")
                        return result
                    else:
                        last_error = result.error_message
                        self.logger.warning(f"STT failed with provider {provider_config.provider.value}: {last_error}")
                        
                except Exception as e:
                    last_error = str(e)
                    self.logger.error(f"STT error with provider {provider_config.provider.value}: {e}")
                    continue

            # Все провайдеры неудачны
            return VoiceProcessingResult(
                success=False,
                error_message=f"Все STT провайдеры неудачны. Последняя ошибка: {last_error}",
                processing_time=time.time() - start_time
            )

        except Exception as e:
            self.logger.error(f"Unexpected error in voice message processing: {e}", exc_info=True)
            return VoiceProcessingResult(
                success=False,
                error_message=f"Неожиданная ошибка обработки: {str(e)}",
                processing_time=time.time() - start_time
            )

    async def synthesize_response(self,
                                 agent_id: str,
                                 user_id: str,
                                 text: str,
                                 agent_config: Dict[str, Any]) -> Tuple[bool, Optional[VoiceFileInfo], Optional[str]]:
        """
        Синтез речи для ответа агента (TTS)
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            text: Текст для синтеза
            agent_config: Конфигурация агента
            
        Returns:
            Tuple[success, voice_file_info, error_message]
        """
        try:
            # Проверяем rate limit
            if not await self._check_rate_limit(agent_id, user_id):
                return False, None, "Превышен лимит запросов на синтез речи"

            # Получаем настройки голоса
            voice_settings = await self._get_voice_settings(agent_config)
            if not voice_settings or not voice_settings.enabled:
                return False, None, "Голосовые функции отключены"

            # Проверяем намерение пользователя
            if not voice_settings.should_process_voice_intent(text):
                self.logger.debug("No voice intent detected in text")
                return False, None, "Не обнаружено намерение озвучивания"

            # Получаем список TTS провайдеров
            tts_providers = voice_settings.get_tts_providers()
            if not tts_providers:
                return False, None, "Нет доступных TTS провайдеров"

            # Пробуем каждого провайдера
            last_error = None
            for provider_config in tts_providers:
                try:
                    result = await self._process_tts_with_provider(
                        provider_config.provider,
                        text
                    )
                    
                    if result.success and result.audio_data:
                        # Сохраняем аудио в MinIO
                        audio_data = result.audio_data
                        file_info = await self.minio_manager.upload_audio_file(
                            audio_data=audio_data,
                            agent_id=agent_id,
                            user_id=user_id,
                            original_filename=f"response_{int(time.time())}.mp3",
                            mime_type="audio/mpeg",
                            metadata={"type": "tts_output", "text_length": len(text)}
                        )
                        
                        self.logger.info(f"TTS successful with provider {provider_config.provider.value}")
                        return True, file_info, None
                    else:
                        last_error = result.error_message
                        self.logger.warning(f"TTS failed with provider {provider_config.provider.value}: {last_error}")
                        
                except Exception as e:
                    last_error = str(e)
                    self.logger.error(f"TTS error with provider {provider_config.provider.value}: {e}")
                    continue

            return False, None, f"Все TTS провайдеры неудачны. Последняя ошибка: {last_error}"

        except Exception as e:
            self.logger.error(f"Unexpected error in speech synthesis: {e}", exc_info=True)
            return False, None, f"Неожиданная ошибка синтеза: {str(e)}"

    async def synthesize_response_with_intent(self,
                                             agent_id: str,
                                             user_id: str,
                                             response_text: str,
                                             user_message: str,
                                             agent_config: Dict[str, Any]) -> Tuple[bool, Optional[VoiceFileInfo], Optional[str]]:
        """
        Синтез речи для ответа агента (TTS) с проверкой намерения по пользовательскому сообщению
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            response_text: Текст ответа агента для синтеза
            user_message: Оригинальное сообщение пользователя для проверки intent
            agent_config: Конфигурация агента
            
        Returns:
            Tuple[success, voice_file_info, error_message]
        """
        try:
            # Проверяем rate limit
            if not await self._check_rate_limit(agent_id, user_id):
                return False, None, "Превышен лимит запросов на синтез речи"

            # Получаем настройки голоса
            voice_settings = await self._get_voice_settings(agent_config)
            if not voice_settings or not voice_settings.enabled:
                return False, None, "Голосовые функции отключены"

            # Проверяем намерение пользователя по ОРИГИНАЛЬНОМУ сообщению
            if not voice_settings.should_process_voice_intent(user_message):
                self.logger.debug(f"No voice intent detected in user message: '{user_message[:50]}...'")
                return False, None, "Не обнаружено намерение озвучивания"

            self.logger.info(f"Voice intent detected in user message: '{user_message[:50]}...'")

            # Получаем список TTS провайдеров
            tts_providers = voice_settings.get_tts_providers()
            if not tts_providers:
                return False, None, "Нет доступных TTS провайдеров"

            # Пробуем каждого провайдера для синтеза ОТВЕТА агента
            last_error = None
            for provider_config in tts_providers:
                try:
                    result = await self._process_tts_with_provider(
                        provider_config.provider,
                        response_text  # Синтезируем ответ агента
                    )
                    
                    if result.success and result.audio_data:
                        # Сохраняем аудио в MinIO
                        audio_data = result.audio_data
                        file_info = await self.minio_manager.upload_audio_file(
                            audio_data=audio_data,
                            agent_id=agent_id,
                            user_id=user_id,
                            original_filename=f"response_{int(time.time())}.mp3",
                            mime_type="audio/mpeg",
                            metadata={"type": "tts_output", "text_length": len(response_text)}
                        )
                        
                        self.logger.info(f"TTS successful with provider {provider_config.provider.value}")
                        return True, file_info, None
                    else:
                        last_error = result.error_message
                        self.logger.warning(f"TTS failed with provider {provider_config.provider.value}: {last_error}")
                        
                except Exception as e:
                    last_error = str(e)
                    self.logger.error(f"TTS error with provider {provider_config.provider.value}: {e}")
                    continue

            return False, None, f"Все TTS провайдеры недоступны. Последняя ошибка: {last_error}"

        except Exception as e:
            self.logger.error(f"Error in synthesize_response_with_intent: {e}", exc_info=True)
            return False, None, f"Ошибка синтеза речи: {str(e)}"

    async def process_voice_message_with_intent(self,
                                               agent_id: str,
                                               user_id: str,
                                               audio_data: bytes,
                                               original_filename: str,
                                               agent_config: Dict[str, Any],
                                               user_message: str) -> VoiceProcessingResult:
        """
        Обработка голосового сообщения (STT) с учетом намерения пользователя
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            audio_data: Аудиоданные
            original_filename: Оригинальное имя файла
            agent_config: Конфигурация агента
            user_message: Оригинальное сообщение пользователя
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        start_time = time.time()
        
        try:
            # Проверяем rate limit
            if not await self._check_rate_limit(agent_id, user_id):
                return VoiceProcessingResult(
                    success=False,
                    error_message="Превышен лимит запросов на обработку голоса",
                    processing_time=time.time() - start_time
                )

            # Получаем настройки голоса
            voice_settings = await self._get_voice_settings(agent_config)
            if not voice_settings or not voice_settings.enabled:
                return VoiceProcessingResult(
                    success=False,
                    error_message="Голосовые функции отключены для этого агента",
                    processing_time=0.0
                )

            # Валидируем размер файла
            if not self._validate_file_size(audio_data, voice_settings.max_file_size_mb):
                return VoiceProcessingResult(
                    success=False,
                    error_message=f"Файл слишком большой (макс. {voice_settings.max_file_size_mb}MB)",
                    processing_time=0.0
                )

            # Определяем формат аудио
            from app.services.voice.base import AudioFileProcessor
            audio_format = AudioFileProcessor.detect_audio_format(audio_data, original_filename)
            
            # Сохраняем файл в MinIO
            file_info = await self.minio_manager.upload_audio_file(
                audio_data=audio_data,
                agent_id=agent_id,
                user_id=user_id,
                original_filename=original_filename,
                audio_format=audio_format,
                metadata={"type": "voice_input"}
            )

            # Проверяем кэш
            cache_key = self._generate_stt_cache_key(file_info, voice_settings)
            cached_result = await self._get_cached_stt_result(cache_key)
            if cached_result:
                self.logger.debug(f"Using cached STT result for {file_info.file_id}")
                cached_result.processing_time = time.time() - start_time
                return cached_result

            # Получаем список STT провайдеров по приоритету
            stt_providers = voice_settings.get_stt_providers()
            if not stt_providers:
                return VoiceProcessingResult(
                    success=False,
                    error_message="Нет доступных STT провайдеров",
                    processing_time=time.time() - start_time
                )

            # Пробуем каждого провайдера по очереди
            last_error = None
            for provider_config in stt_providers:
                try:
                    result = await self._process_stt_with_provider(
                        provider_config.provider,
                        audio_data,
                        file_info
                    )
                    
                    if result.success:
                        # Кэшируем успешный результат
                        if voice_settings.cache_enabled:
                            await self._cache_stt_result(cache_key, result, voice_settings.cache_ttl_hours)
                        
                        result.processing_time = time.time() - start_time
                        self.logger.info(f"STT successful with provider {provider_config.provider.value}")
                        return result
                    else:
                        last_error = result.error_message
                        self.logger.warning(f"STT failed with provider {provider_config.provider.value}: {last_error}")
                        
                except Exception as e:
                    last_error = str(e)
                    self.logger.error(f"STT error with provider {provider_config.provider.value}: {e}")
                    continue

            # Все провайдеры неудачны
            return VoiceProcessingResult(
                success=False,
                error_message=f"Все STT провайдеры неудачны. Последняя ошибка: {last_error}",
                processing_time=time.time() - start_time
            )

        except Exception as e:
            self.logger.error(f"Unexpected error in voice message processing: {e}", exc_info=True)
            return VoiceProcessingResult(
                success=False,
                error_message=f"Неожиданная ошибка обработки: {str(e)}",
                processing_time=time.time() - start_time
            )

    async def synthesize_response_with_intent_and_cache(self,
                                                       agent_id: str,
                                                       user_id: str,
                                                       response_text: str,
                                                       user_message: str,
                                                       agent_config: Dict[str, Any]) -> Tuple[bool, Optional[VoiceFileInfo], Optional[str]]:
        """
        Синтез речи для ответа агента (TTS) с проверкой намерения по пользовательскому сообщению и кэшированием результата
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            response_text: Текст ответа агента для синтеза
            user_message: Оригинальное сообщение пользователя для проверки intent
            agent_config: Конфигурация агента
            
        Returns:
            Tuple[success, voice_file_info, error_message]
        """
        try:
            # Проверяем rate limit
            if not await self._check_rate_limit(agent_id, user_id):
                return False, None, "Превышен лимит запросов на синтез речи"

            # Получаем настройки голоса
            voice_settings = await self._get_voice_settings(agent_config)
            if not voice_settings or not voice_settings.enabled:
                return False, None, "Голосовые функции отключены"

            # Проверяем намерение пользователя по ОРИГИНАЛЬНОМУ сообщению
            if not voice_settings.should_process_voice_intent(user_message):
                self.logger.debug(f"No voice intent detected in user message: '{user_message[:50]}...'")
                return False, None, "Не обнаружено намерение озвучивания"

            self.logger.info(f"Voice intent detected in user message: '{user_message[:50]}...'")

            # Получаем список TTS провайдеров
            tts_providers = voice_settings.get_tts_providers()
            if not tts_providers:
                return False, None, "Нет доступных TTS провайдеров"

            # Пробуем каждого провайдера для синтеза ОТВЕТА агента
            last_error = None
            for provider_config in tts_providers:
                try:
                    result = await self._process_tts_with_provider(
                        provider_config.provider,
                        response_text  # Синтезируем ответ агента
                    )
                    
                    if result.success and result.audio_data:
                        # Сохраняем аудио в MinIO
                        audio_data = result.audio_data
                        file_info = await self.minio_manager.upload_audio_file(
                            audio_data=audio_data,
                            agent_id=agent_id,
                            user_id=user_id,
                            original_filename=f"response_{int(time.time())}.mp3",
                            mime_type="audio/mpeg",
                            metadata={"type": "tts_output", "text_length": len(response_text)}
                        )
                        
                        # Кэшируем результат
                        cache_key = f"tts_response_cache:{agent_id}:{user_id}:{int(time.time())}"
                        await self.redis_service.setex(
                            cache_key,
                            voice_settings.cache_ttl_hours * 3600,
                            json.dumps({
                                "success": True,
                                "file_info": file_info.dict(),
                                "response_text": response_text
                            })
                        )
                        
                        self.logger.info(f"TTS successful with provider {provider_config.provider.value}")
                        return True, file_info, None
                    else:
                        last_error = result.error_message
                        self.logger.warning(f"TTS failed with provider {provider_config.provider.value}: {last_error}")
                        
                except Exception as e:
                    last_error = str(e)
                    self.logger.error(f"TTS error with provider {provider_config.provider.value}: {e}")
                    continue

            return False, None, f"Все TTS провайдеры недоступны. Последняя ошибка: {last_error}"

        except Exception as e:
            self.logger.error(f"Error in synthesize_response_with_intent_and_cache: {e}", exc_info=True)
            return False, None, f"Ошибка синтеза речи: {str(e)}"

    async def get_service_health(self) -> Dict[str, Any]:
        """Получение статуса здоровья всех сервисов"""
        health_status = {
            "orchestrator_initialized": self._initialized,
            "minio_health": await self.minio_manager.health_check(),
            "stt_services": {},
            "tts_services": {},
            "total_rate_limiters": len(self.rate_limiters)
        }

        # Проверяем STT сервисы
        for provider, service in self.stt_services.items():
            try:
                health_status["stt_services"][provider.value] = await service.health_check()
            except Exception as e:
                health_status["stt_services"][provider.value] = f"Error: {e}"

        # Проверяем TTS сервисы
        for provider, service in self.tts_services.items():
            try:
                health_status["tts_services"][provider.value] = await service.health_check()
            except Exception as e:
                health_status["tts_services"][provider.value] = f"Error: {e}"

        return health_status

    def get_voice_settings_from_config(self, agent_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Извлекает голосовые настройки из конфигурации агента
        
        Args:
            agent_config: Конфигурация агента
            
        Returns:
            Голосовые настройки или None
        """
        try:
            # Извлекаем из config.simple.settings.voice_settings
            return agent_config.get("config", {}).get("simple", {}).get("settings", {}).get("voice_settings")
        except Exception as e:
            self.logger.error(f"Error extracting voice settings from config: {e}")
            return None

    def validate_voice_config_structure(self, voice_config: Dict[str, Any]) -> bool:
        """
        Валидирует структуру голосовой конфигурации
        
        Args:
            voice_config: Голосовая конфигурация
            
        Returns:
            True если структура валидна
        """
        try:
            # Проверяем базовые поля
            required_fields = ['enabled', 'providers']
            for field in required_fields:
                if field not in voice_config:
                    self.logger.error(f"Missing required field '{field}' in voice config")
                    return False
            
            # Проверяем, что providers - это список
            if not isinstance(voice_config.get('providers'), list):
                self.logger.error("'providers' field must be a list")
                return False
                
            # Проверяем каждый провайдер
            for provider in voice_config['providers']:
                if not isinstance(provider, dict) or 'provider' not in provider:
                    self.logger.error("Each provider must be a dict with 'provider' field")
                    return False
                    
            return True
            
        except Exception as e:
            self.logger.error(f"Error validating voice config structure: {e}")
            return False

    async def _check_rate_limit(self, agent_id: str, user_id: str) -> bool:
        """
        Проверяет rate limit для пользователя
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            
        Returns:
            True если rate limit не превышен
        """
        # Простая реализация - всегда разрешаем
        # TODO: Реализовать полноценный rate limiting через Redis
        return True

    async def _get_voice_settings(self, agent_config: Dict[str, Any]) -> Optional[VoiceSettings]:
        """
        Асинхронная версия получения голосовых настроек из конфигурации агента
        
        Args:
            agent_config: Конфигурация агента
            
        Returns:
            VoiceSettings объект или None
        """
        voice_config = self.get_voice_settings_from_config(agent_config)
        if not voice_config:
            return None
        
        try:
            return VoiceSettings(**voice_config)
        except Exception as e:
            self.logger.error(f"Failed to create VoiceSettings object: {e}")
            return None

    def _generate_stt_cache_key(self, file_info: VoiceFileInfo, voice_settings: VoiceSettings) -> str:
        """
        Генерирует ключ кэша для STT результата
        
        Args:
            file_info: Информация о файле
            voice_settings: Настройки голоса
            
        Returns:
            Ключ кэша
        """
        import hashlib
        cache_data = f"{file_info.size_bytes}:{file_info.mime_type}:{voice_settings.providers[0].provider.value if voice_settings.providers else 'none'}"
        cache_hash = hashlib.md5(cache_data.encode()).hexdigest()
        return f"stt_cache:{cache_hash}"

    async def _get_cached_stt_result(self, cache_key: str) -> Optional[VoiceProcessingResult]:
        """
        Получает кэшированный STT результат
        
        Args:
            cache_key: Ключ кэша
            
        Returns:
            Кэшированный результат или None
        """
        try:
            cached_data = await self.redis_service.get(cache_key)
            if cached_data:
                result_data = json.loads(cached_data)
                return VoiceProcessingResult(**result_data)
        except Exception as e:
            self.logger.warning(f"Failed to get cached STT result: {e}")
        return None

    async def _cache_stt_result(self, cache_key: str, result: VoiceProcessingResult, ttl_hours: int) -> None:
        """
        Кэширует STT результат
        
        Args:
            cache_key: Ключ кэша
            result: Результат для кэширования
            ttl_hours: TTL в часах
        """
        try:
            await self.redis_service.setex(
                cache_key,
                ttl_hours * 3600,
                json.dumps(result.dict())
            )
        except Exception as e:
            self.logger.warning(f"Failed to cache STT result: {e}")

    def _validate_file_size(self, audio_data: bytes, max_size_mb: int) -> bool:
        """
        Валидирует размер файла
        
        Args:
            audio_data: Аудиоданные
            max_size_mb: Максимальный размер в МБ
            
        Returns:
            True если размер допустимый
        """
        file_size_mb = len(audio_data) / (1024 * 1024)
        return file_size_mb <= max_size_mb

    async def _process_stt_with_provider(self, provider: VoiceProvider, audio_data: bytes, file_info: VoiceFileInfo) -> VoiceProcessingResult:
        """
        Обрабатывает STT с помощью указанного провайдера
        
        Args:
            provider: Провайдер голосовых сервисов
            audio_data: Аудиоданные
            file_info: Информация о файле
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        if provider not in self.stt_services:
            raise VoiceServiceError(f"STT service для провайдера {provider.value} не инициализирован")
        
        stt_service = self.stt_services[provider]
        return await stt_service.transcribe_audio(audio_data, file_info)

    async def _process_tts_with_provider(self, provider: 'VoiceProvider', text: str) -> 'VoiceProcessingResult':
        """
        Обрабатывает TTS синтез с помощью указанного провайдера
        
        Args:
            provider: Провайдер голосовых сервисов
            text: Текст для синтеза
            
        Returns:
            VoiceProcessingResult: Результат обработки
        """
        if provider not in self.tts_services:
            raise VoiceServiceError(f"TTS service для провайдера {provider.value} не инициализирован")
        
        tts_service = self.tts_services[provider]
        return await tts_service.synthesize_speech(text)
