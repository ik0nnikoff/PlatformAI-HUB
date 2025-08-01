"""Minimal STT Base Provider - Phase 3.5.2.2 Enhanced with RetryMixin."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.services.voice_v2.core.schemas import STTRequest
from .models import STTResult, STTCapabilities
from ...core.exceptions import VoiceServiceError, ProviderNotAvailableError
from ..retry_mixin import RetryMixin

if TYPE_CHECKING:
    from ..enhanced_connection_manager import IConnectionManager

logger = logging.getLogger(__name__)


class BaseSTTProvider(ABC, RetryMixin):
    """
    Minimal abstract base for STT providers with RetryMixin integration.

    Phase 3.5.2.2 Enhancement:
    - RetryMixin integration for centralized retry configuration
    - ConnectionManager support standardization
    - SOLID principles compliance
    """

    def __init__(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int = 1,
        enabled: bool = True,
        connection_manager: Optional['IConnectionManager'] = None
    ):
        self.provider_name = provider_name
        self.config = config
        self.priority = priority
        self.enabled = enabled
        self._initialized = False

        # Enhanced Connection Manager Integration (Phase 3.4.2.2)
        self._connection_manager = connection_manager

        # Initialize retry configuration через RetryMixin
        if self._has_connection_manager():
            # Get retry config for validation only
            self._get_retry_config(config)
            logger.debug("%s STT provider using ConnectionManager with retry config", provider_name)

        # Quick config validation
        missing = [f for f in self.get_required_config_fields() if f not in config]
        if missing:
            raise VoiceServiceError(f"Missing config: {missing}")

    def get_required_config_fields(self) -> List[str]:
        """
        Default implementation - providers can override for specific requirements.

        Returns:
            List of required configuration field names
        """
        return []  # Base implementation requires no fields

    @abstractmethod
    async def get_capabilities(self) -> STTCapabilities:
        pass

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        pass

    @abstractmethod
    async def _transcribe_implementation(self, request: STTRequest) -> STTResult:
        pass

    async def transcribe_audio(self, request: STTRequest) -> STTResult:
        """Main transcription with validation."""
        if not self.enabled:
            raise ProviderNotAvailableError(f"Provider {self.provider_name} disabled")

        if not self._initialized:
            await self.initialize()
            self._initialized = True

        validation_result = self._validate_request(
            request.audio_data,
            request.audio_format,
            request.language
        )

        if not validation_result.get("valid", False):
            raise VoiceServiceError(
                f"Ошибка валидации аудио: {validation_result.get('error', 'Unknown error')}"
            )

        try:
            start_time = asyncio.get_event_loop().time()
            result = await self._transcribe_implementation(request)

            # Enrich result
            if result.processing_time is None:
                result.processing_time = asyncio.get_event_loop().time() - start_time
            if result.word_count is None and result.text:
                result.word_count = len(result.text.split())

            return result

        except Exception as e:
            logger.error("STT failed: %s", e)
            if isinstance(e, VoiceServiceError):
                raise
            raise VoiceServiceError(f"STT error: {e}") from e

    def _validate_request(
        self,
        audio_data: bytes,
        audio_format: str,
        language: str
    ) -> Dict[str, Any]:
        """
        Валидирует параметры запроса на транскрипцию.

        Args:
            audio_data: Байты аудио данных
            audio_format: Формат аудио файла
            language: Язык для распознавания

        Returns:
            Результат валидации с информацией об ошибках
        """
        # Базовая валидация аудио данных
        audio_validation = self._validate_audio_data(audio_data)
        if not audio_validation["valid"]:
            return audio_validation

        # Валидация формата аудио
        format_validation = self._validate_audio_format(audio_format)
        if not format_validation["valid"]:
            return format_validation

        # Валидация языка
        language_validation = self._validate_language_parameter(language)
        if not language_validation["valid"]:
            return language_validation

        # Валидация размера файла
        size_validation = self._validate_file_size(audio_data)
        if not size_validation["valid"]:
            return size_validation

        # Валидация провайдер-специфичных ограничений
        provider_validation = self._validate_provider_specific_constraints(
            audio_data, audio_format, language
        )
        if not provider_validation["valid"]:
            return provider_validation

        return {"valid": True, "message": "Валидация прошла успешно"}

    def _validate_audio_data(self, audio_data: bytes) -> Dict[str, Any]:
        """Валидирует аудио данные."""
        if not audio_data:
            self.logger.error("Получены пустые аудио данные")
            return {
                "valid": False,
                "error": "Аудио данные отсутствуют",
                "error_code": "EMPTY_AUDIO_DATA"
            }

        if not isinstance(audio_data, bytes):
            self.logger.error(f"Неверный тип аудио данных: {type(audio_data)}")
            return {
                "valid": False,
                "error": "Неверный тип аудио данных",
                "error_code": "INVALID_AUDIO_TYPE"
            }

        return {"valid": True}

    def _validate_audio_format(self, audio_format: str) -> Dict[str, Any]:
        """Валидирует формат аудио."""
        if not audio_format:
            self.logger.warning("Формат аудио не указан, используем автоопределение")
            return {"valid": True}

        supported_formats = self.get_supported_formats()
        if audio_format.lower() not in [fmt.lower() for fmt in supported_formats]:
            self.logger.error(f"Неподдерживаемый формат: {audio_format}")
            return {
                "valid": False,
                "error": f"Формат {audio_format} не поддерживается",
                "error_code": "UNSUPPORTED_FORMAT",
                "supported_formats": supported_formats
            }

        return {"valid": True}

    def _validate_language_parameter(self, language: str) -> Dict[str, Any]:
        """Валидирует параметр языка."""
        if not language:
            self.logger.debug("Язык не указан, используем автоопределение")
            return {"valid": True}

        supported_languages = self.get_supported_languages()
        if language not in supported_languages:
            self.logger.warning(f"Язык {language} может быть не поддержан")
            # Не блокируем запрос, просто предупреждаем

        return {"valid": True}

    def _validate_file_size(self, audio_data: bytes) -> Dict[str, Any]:
        """Валидирует размер аудио файла."""
        file_size_mb = len(audio_data) / (1024 * 1024)
        max_size_mb = getattr(self, 'max_file_size_mb', 25)  # По умолчанию 25MB

        if file_size_mb > max_size_mb:
            self.logger.error(f"Файл слишком большой: {file_size_mb:.2f}MB > {max_size_mb}MB")
            return {
                "valid": False,
                "error": f"Размер файла превышает лимит ({file_size_mb:.2f}MB > {max_size_mb}MB)",
                "error_code": "FILE_TOO_LARGE",
                "file_size_mb": file_size_mb,
                "max_size_mb": max_size_mb
            }

        return {"valid": True}

    def _validate_provider_specific_constraints(
        self,
        audio_data: bytes,
        audio_format: str,
        language: str
    ) -> Dict[str, Any]:
        """
        Валидирует специфичные для провайдера ограничения.
        Переопределяется в дочерних классах.
        """
        return {"valid": True}

    async def health_check(self) -> bool:
        """Health check."""
        try:
            if not self.enabled:
                return False

            if not self._initialized:
                await self.initialize()
                self._initialized = True

            caps = await self.get_capabilities()
            return len(caps.supported_formats) > 0

        except Exception:
            return False

    def get_status_info(self) -> Dict[str, Any]:
        """Status info."""
        return {
            "provider_name": self.provider_name,
            "enabled": self.enabled,
            "initialized": self._initialized,
            "priority": self.priority
        }

    def __str__(self) -> str:
        """String representation for logging."""
        return f"STTProvider({self.provider_name}, enabled={self.enabled}, priority={self.priority})"
